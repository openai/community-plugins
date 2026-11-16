"""Replace only two existing mission image scalar spans; preserve other bytes."""
from __future__ import annotations

from copy import deepcopy
import json

import yaml
from yaml.nodes import MappingNode, ScalarNode

from devsecops.adapters.oci import _digest
from devsecops.adapters.signatures import _repository_ref
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.resources import strict_load_yaml
from devsecops.runtime.context import canonical_bytes


def replace_mission_image(raw, *, repository, digest, deadline):
    checkpoint(deadline)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 64000 or not _repository_ref(repository) or not _digest(digest):
        raise ValueError('Bounded values and exact mission repository/digest required')
    text = raw.decode('utf-8')
    documents = strict_load_yaml(text)
    if len(documents) != 1 or not isinstance(documents[0], dict):
        raise ValueError('One mission values mapping is required')
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        checkpoint(deadline)
        if getattr(event, 'anchor', None) is not None:
            raise ValueError('Anchored values are unsupported for scalar replacement')
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    def child(node, key):
        if not isinstance(node, MappingNode):
            raise ValueError('Mission image selectors must traverse mappings')
        found = [value for name, value in node.value if name.tag == 'tag:yaml.org,2002:str' and name.value == key]
        if len(found) != 1:
            raise ValueError('Mission image selectors must exist exactly once')
        return found[0]
    image = child(root, 'image')
    selected = documents[0].get('image')
    if not isinstance(selected, dict):
        raise ValueError('Mission image values must be a mapping')
    changes, spans = {}, []
    for key, new in [('repository', repository), ('digest', digest)]:
        checkpoint(deadline)
        node = child(image, key)
        if (not isinstance(node, ScalarNode) or node.tag != 'tag:yaml.org,2002:str'
                or node.style not in (None, "'", '"') or not isinstance(selected.get(key), str)):
            raise ValueError('Mission image values must be existing string scalars')
        start, end = node.start_mark.index, node.end_mark.index
        if not 0 <= start < end <= len(text) or '\n' in text[start:end] or '\r' in text[start:end]:
            raise ValueError('Mission image scalar spans must be single-line and unambiguous')
        old = selected[key]
        if old and not (_repository_ref(old) if key == 'repository' else _digest(old)):
            raise ValueError('Baseline image must already use an exact repository and digest')
        changes[key] = {'before': old, 'after': new, 'changed': old != new}
        if old != new:
            spans.append((start, end, json.dumps(new, ensure_ascii=False)))
    after = text
    previous = len(text)
    for start, end, new in sorted(spans, reverse=True):
        checkpoint(deadline)
        if end > previous:
            raise ValueError('Mission image scalar spans overlap')
        after = after[:start] + new + after[end:]
        previous = start
    expected = deepcopy(documents[0])
    expected['image']['repository'], expected['image']['digest'] = repository, digest
    if canonical_bytes(strict_load_yaml(after)) != canonical_bytes([expected]):
        raise ValueError('Mission image replacement changed other values')
    result = after.encode('utf-8')
    if len(result) > 64000:
        raise ValueError('Proposed values exceed the bound')
    checkpoint(deadline)
    return result, changes
