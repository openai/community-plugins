"""Select an exact packaged schema set without changing the legacy default."""
from copy import deepcopy
import re


def select_schema_set(lock, kubernetes_version=None):
    default = lock['schema_source']['kubernetes_version']
    version = default if kubernetes_version is None else kubernetes_version
    additional = lock.get('schema_sets', {})
    if (not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version)
            or not isinstance(additional, dict) or default in additional):
        raise ValueError('Invalid or ambiguous packaged Kubernetes schema selection')
    if version == default:
        source, schemas = lock['schema_source'], lock['schemas']
    elif version in additional:
        source, schemas = additional[version]['schema_source'], additional[version]['schemas']
    else:
        raise ValueError('Selected Kubernetes version lacks bundled qualified schemas')
    if (source['kubernetes_version'] != version
            or source['repository'] != 'https://github.com/yannh/kubernetes-json-schema'
            or not re.fullmatch(r'[a-f0-9]{40}', source['commit']) or not isinstance(schemas, list) or not schemas):
        raise ValueError('Packaged Kubernetes schema provenance is inconsistent')
    names = set()
    for item in schemas:
        name = item['path']
        expected_url = ('https://raw.githubusercontent.com/yannh/kubernetes-json-schema/'
                        + source['commit'] + '/' + name)
        if (not re.fullmatch(re.escape('v' + version + '-standalone-strict/') + r'[a-z0-9-]+\.json', name)
                or name in names or item['url'] != expected_url or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])):
            raise ValueError('Packaged Kubernetes schema pins are inconsistent')
        names.add(name)
    return deepcopy(source), deepcopy(schemas)
