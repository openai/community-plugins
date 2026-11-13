"""Validate a proposed patch in a private, frozen repository; never deliver it.

Only an explicit trusted contract may define the allowed file and rendered-field
changes. The validator never obtains a replacement patch from a fixture.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from jsonschema import Draft202012Validator

from .engine import review
from .factory.contracts import read_json
from .process import run
from .profiles import load_profile, relative_path
from .render import tool_path
from .snapshot import freeze_commit, freeze_worktree, git, resolve_ref


CONTRACT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:devsecops:correction-contract:1",
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "id", "base_commit", "profile_sha256", "allowed_files", "allowed_rendered_changes"],
    "properties": {
        "schema_version": {"const": 1},
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{2,62}$"},
        "base_commit": {"type": "string", "pattern": "^(?:[0-9a-f]{40}|[0-9a-f]{64})$"},
        "profile_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "allowed_files": {"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True,
                          "items": {"type": "string", "minLength": 1, "maxLength": 300}},
        "allowed_rendered_changes": {"type": "array", "minItems": 1, "maxItems": 64, "items": {
            "type": "object", "additionalProperties": False, "required": ["resource", "pointers"],
            "properties": {
                "resource": {"type": "string", "pattern": "^[A-Za-z0-9._:/<>-]{1,1000}$"},
                "pointers": {"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True,
                             "items": {"type": "string", "minLength": 2, "maxLength": 400}},
            },
        }},
    },
}


def _patch_bytes(path: Path) -> bytes:
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Patch path must not contain symlinks")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= 256_000:
            raise ValueError("Patch must be a nonempty regular file of at most 256000 bytes")
        raw = handle.read(256_001)
    if not 1 <= len(raw) <= 256_000 or b"\0" in raw:
        raise ValueError("Patch is empty, binary or oversized")
    try:
        raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("Patch must be UTF-8 text") from None
    forbidden = (b"new file mode ", b"deleted file mode ", b"old mode ", b"new mode ",
                 b"rename from ", b"rename to ", b"copy from ", b"copy to ", b"GIT binary patch", b"Binary files ")
    if any(line.startswith(forbidden) for line in raw.splitlines()):
        raise ValueError("Correction v1 permits in-place text edits only; creation, deletion, rename and mode changes are unsupported")
    return raw


def _pointers(before, after, pointer=""):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            child = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            result.extend([child] if key not in before or key not in after else _pointers(before[key], after[key], child))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [change for index, (old, new) in enumerate(zip(before, after))
                for change in _pointers(old, new, pointer + '/' + str(index))]
    if type(before) is type(after) and json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True):
        return []
    # Length changes affect the whole list. For equal lengths, comparing every
    # position exposes reordering while allowing a contract to name one field.
    return [pointer]


def _images(value, pointer=""):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            location = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key == "image":
                result[location] = child
            result.update(_images(child, location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(_images(child, pointer + "/" + str(index)))
    return result


class ScopeValidator:
    def __init__(self, changes):
        self.allowed = {item["resource"]: item["pointers"] for item in changes}
        if len(self.allowed) != len(changes):
            raise ValueError("Correction scope contains duplicate resource identities")
        for pointers in self.allowed.values():
            for pointer in pointers:
                if not pointer.startswith("/") or any(not part for part in pointer.split("/")[1:]) or re.search(r"~(?![01])", pointer):
                    raise ValueError("Correction scope must contain nonempty RFC6901 pointers")
                if any(ord(char) < 32 for char in pointer):
                    raise ValueError("Correction scope contains invalid pointer characters")
        self.seen = set()
        self.changed = False
        self.receipts = []

    def __call__(self, target, before, after):
        base = {record["id"]: record["body"] for record in before}
        head = {record["id"]: record["body"] for record in after}
        self.seen.update(base)
        violations, changes = [], []
        if set(base) != set(head):
            violations.append({"code": "resource-inventory-changed"})
        for identity in sorted(set(base) & set(head)):
            if _images(base[identity]) != _images(head[identity]):
                violations.append({"code": "image-identity-changed", "resource": identity})
            for pointer in _pointers(base[identity], head[identity]):
                self.changed = True
                permitted = any(pointer == allowed or pointer.startswith(allowed + "/") for allowed in self.allowed.get(identity, []))
                changes.append({"resource": identity, "pointer": pointer, "permitted": permitted})
                if not permitted:
                    violations.append({"code": "rendered-change-outside-contract", "resource": identity, "pointer": pointer})
        if len(changes) + len(violations) > 1000:
            raise ValueError("Correction scope comparison exceeds its bound")
        receipt = {"tool": "correction-scope", "target": target["name"], "decision": "fail" if violations else "pass",
                   "changes": changes, "violations": violations, "preserves_images": not any(v["code"] == "image-identity-changed" for v in violations)}
        self.receipts.append(receipt)
        return receipt


class _RenderCapture:
    """Freeze the exact normalized bodies passed to the scope validator once.

    The raw inventories are private evidence, never embedded in readable reports.
    Their format is the existing recovery verifier's {resources: [{id, body}]}.
    """
    def __init__(self, checker, targets):
        self.checker = checker
        self.expected = set(targets)
        self.pairs = {}
        self.bytes = 0

    def __call__(self, target, before, after):
        from .runtime.context import canonical_bytes
        name = target['name']
        if name not in self.expected or name in self.pairs:
            raise ValueError('Exact render export has an unexpected target')
        receipt = self.checker(target, before, after)
        pair = []
        for records in (before, after):
            if not 1 <= len(records) <= 1000:
                raise ValueError('Exact render export exceeds its resource bound')
            raw = canonical_bytes({'resources': [{'id': r['id'], 'body': r['body']} for r in records]})
            self.bytes += len(raw)
            if self.bytes > 32_000_000:
                raise ValueError('Exact render export exceeds its total byte bound')
            pair.append(raw)
        self.pairs[name] = pair
        return receipt

    def artifacts(self):
        from .runtime.context import canonical_bytes
        if set(self.pairs) != self.expected:
            raise ValueError('Exact render export did not cover every selected target')
        artifacts = {}
        for side, filename in enumerate(('rendered-before.json', 'rendered-after.json')):
            resources = [row for name in sorted(self.pairs)
                         for row in json.loads(self.pairs[name][side])['resources']]
            if not 1 <= len(resources) <= 1000 or len({r['id'] for r in resources}) != len(resources):
                raise ValueError('Exact render export has an ambiguous resource inventory')
            artifacts[filename] = canonical_bytes({'resources': sorted(resources, key=lambda r: r['id'])})
        return artifacts


def validate(args, *, rendered_artifacts: dict[str, bytes] | None = None) -> tuple[dict, dict, bytes]:
    """Validate a patch; optionally retain its exact private render evidence.

    The three-item return remains compatible with existing callers. Supply an
    empty dict to receive the immutable bytes required by recovery; the result
    then binds those bytes to the same frozen patch, trees, profile and contract.
    """
    if rendered_artifacts is not None and (type(rendered_artifacts) is not dict or rendered_artifacts):
        raise ValueError('Exact render export requires an empty output mapping')
    repo = Path(args.repo).resolve(strict=True)
    if Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve() != repo:
        raise ValueError("Repository path must be its Git worktree root")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", args.base or ""):
        raise ValueError("Correction requires the full frozen base commit, not a movable reference")
    base = resolve_ref(repo, args.base)
    contract_path = Path(args.contract).resolve()
    if contract_path.is_relative_to(repo):
        raise ValueError("Correction contract must be a trusted external input outside the candidate checkout")
    contract, contract_hash = read_json(Path(args.contract), limit=128_000)
    if not Draft202012Validator(CONTRACT_SCHEMA).is_valid(contract):
        raise ValueError("Correction contract does not match its versioned schema")
    if contract["base_commit"] != base:
        raise ValueError("Correction contract does not bind the selected base commit")
    profile, profile_hash = load_profile(Path(args.profile), repo, None, base_sha=base)
    if args.target and set(args.target) != {target['name'] for target in profile['targets']}:
        raise ValueError("Correction must review every target in the contract-bound profile")
    if contract["profile_sha256"] != profile_hash:
        raise ValueError("Correction contract does not bind the trusted review profile")
    if Path(args.schema_location).resolve().is_relative_to(repo):
        raise ValueError("Trusted schemas must be outside the candidate checkout")
    for name in ("helm", "kustomize", "kubeconform"):
        try:
            executable = tool_path(getattr(args, name))
        except ValueError:
            continue  # The ordinary review retains missing-tool coverage gaps.
        if executable.is_relative_to(repo):
            raise ValueError("Trusted executables must be outside the candidate checkout")
    allowed_files = {relative_path(path) for path in contract["allowed_files"]}
    checker = ScopeValidator(contract["allowed_rendered_changes"])
    capture = (_RenderCapture(checker, (target['name'] for target in profile['targets']))
               if rendered_artifacts is not None else None)
    patch = _patch_bytes(args.patch)
    roots = sorted({root for target in profile["targets"] for root in (
        [target["source_root"]] if target["type"] == "kustomize" else [target["path"], *target.get("values", [])])})
    receipts = []
    with tempfile.TemporaryDirectory(prefix="devsecops-correction-") as directory:
        private = Path(directory).resolve()
        private.chmod(0o700)
        frozen = private / "repository"
        frozen.mkdir(mode=0o700)
        original = freeze_commit(repo, base, roots, frozen)
        if not allowed_files <= {item["path"] for item in original["files"]}:
            raise ValueError("Allowed correction files must exist within the selected frozen inputs")
        frozen_patch = private / "proposal.diff"
        frozen_patch.write_bytes(patch)
        frozen_patch.chmod(0o600)
        def execute(*arguments):
            argv = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.attributesFile=/dev/null",
                    "-c", "commit.gpgSign=false", "-c", "user.name=DevSecOps local validator", "-c", "user.email=validator@example.invalid", *arguments]
            code, out, _, receipt = run(argv, cwd=frozen, timeout=30, limit=1_000_000,
                env={"GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})
            receipts.append(receipt)
            if code:
                raise ValueError("Patch could not be applied and validated against the frozen inputs")
            return out
        execute("init", "--quiet", "--template=", "--initial-branch=correction")
        # git apply also applies worktree conversions. The private repository's
        # highest-precedence attributes must disable all content conversions;
        # the candidate cannot edit this file because .git is outside its scope.
        attributes = frozen / '.git' / 'info' / 'attributes'
        attributes.parent.mkdir(mode=0o700, exist_ok=True)
        attributes.write_text('* -filter -ident -text -eol -crlf -working-tree-encoding\n')
        attributes.chmod(0o600)
        # Bypass in-tree .gitattributes as well as system/global attributes.
        # git add can silently normalize bytes via ident/eol/encoding even when
        # no external filter is configured, producing a false comparison base.
        for offset in range(0, len(original['files']), 64):
            files = original['files'][offset:offset + 64]
            objects = execute('hash-object', '--no-filters', '-w', '--', *(item['path'] for item in files)).decode().splitlines()
            if len(objects) != len(files) or any(not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid) for oid in objects):
                raise ValueError('Could not preserve an exact frozen Git blob')
            index_args = ['update-index', '--add']
            for item, object_id in zip(files, objects):
                index_args.extend(['--cacheinfo', '100644', object_id, item['path']])
            execute(*index_args)
        execute("commit", "--quiet", "-m", "Frozen selected inputs")
        internal_base = execute("rev-parse", "HEAD").decode().strip()
        committed_snapshot = private / 'committed-baseline'
        committed_snapshot.mkdir(mode=0o700)
        committed = freeze_commit(frozen, internal_base, roots, committed_snapshot)
        if committed['sha256'] != original['sha256']:
            raise ValueError('Internal committed inputs differ from the original frozen baseline')
        raw_stat = execute("apply", "--numstat", "-z", "--", str(frozen_patch))
        changed_files = []
        for entry in raw_stat.split(b"\0"):
            if not entry:
                continue
            added, removed, filename = entry.split(b"\t", 2)
            if not added.isdigit() or not removed.isdigit():
                raise ValueError("Binary corrections are unsupported")
            changed_files.append(relative_path(filename.decode("utf-8")))
        if not changed_files or len(changed_files) != len(set(changed_files)) or not set(changed_files) <= allowed_files:
            raise ValueError("Patch changes files outside the frozen correction contract")
        execute("apply", "--check", "--whitespace=error", "--", str(frozen_patch))
        execute("apply", "--whitespace=error", "--", str(frozen_patch))
        patched_snapshot = private / "patched-snapshot"
        patched_snapshot.mkdir(mode=0o700)
        candidate = freeze_worktree(frozen, roots, patched_snapshot)
        if {item["path"] for item in candidate["files"]} != {item["path"] for item in original["files"]}:
            raise ValueError("Patch changes the frozen file inventory")
        trusted_profile = private / "profile.json"
        trusted_profile.write_text(json.dumps(profile))
        trusted_profile.chmod(0o600)
        review_args = deepcopy(args)
        review_args.repo, review_args.base, review_args.head = frozen, internal_base, "WORKTREE"
        review_args.profile = trusted_profile
        review_args.target = None
        review_args.gitlab_url = review_args.gitlab_project = review_args.gitlab_mr = None
        review_args.artifact = None
        static_review = review(review_args, resource_validator=capture if capture is not None else checker)
    gaps = []
    if capture is not None:
        try:
            rendered_artifacts.update(capture.artifacts())
        except ValueError:
            gaps.append({'code': 'rendered-export-incomplete',
                         'message': 'Exact render evidence did not cover every selected resource within its bounds', 'required': True})
    if not set(checker.allowed) <= checker.seen:
        gaps.append({"code": "unbound-rendered-scope", "message": "A contracted resource was not present in the complete rendered baseline", "required": True})
    if static_review["decision"] == "unknown":
        gaps.append({"code": "incomplete-static-review", "message": "Required local rendering or validation did not complete", "required": True})
    decision = "unknown" if gaps else "fail" if not checker.changed or static_review["decision"] == "fail" else "pass"
    result = {
        "schema_version": 1, "execution_status": "partial" if gaps else "complete", "decision": decision,
        "observed_at": datetime.now(timezone.utc).isoformat(), "contract_id": contract["id"], "contract_sha256": contract_hash,
        "source": {"repository": str(repo), "base_commit": base, "profile_sha256": profile_hash},
        "patch_sha256": hashlib.sha256(patch).hexdigest(), "patch_bytes": len(patch), "changed_files": changed_files,
        "base_tree": original, "patched_tree": candidate, "scope_receipts": checker.receipts, "tool_receipts": receipts,
        "static_review_decision": static_review["decision"], "has_rendered_change": checker.changed, "gaps": gaps,
        "delivery_status": "not_performed", "runtime_recovery": "not_evaluated",
        "limitations": ["In-place text edits only; resource inventory and all image fields are held constant", "Local validation does not establish controller parity, network enforcement or recovery"],
    }
    if rendered_artifacts is not None:
        result['rendered_export'] = {
            'status': 'complete' if rendered_artifacts else 'incomplete',
            'encoding': 'devsecops-runtime-json-sha256-v1',
            'input_binding': {'base_commit': base, 'base_tree_sha256': original['sha256'],
                'candidate_tree_sha256': candidate['sha256'], 'profile_sha256': profile_hash,
                'contract_sha256': contract_hash, 'patch_sha256': result['patch_sha256']},
            'artifacts': {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
                          for name, raw in rendered_artifacts.items()},
        }
    return result, static_review, patch


def write_outputs(result: dict, static_review: dict, patch: bytes, output: Path, *, rendered_artifacts=None):
    from .cli import check_output
    from .contracts import validate_report
    from .presentation import render_html, render_markdown
    from .correction_presentation import render_correction_html, render_correction_markdown
    from .runtime.cli import _new_directory, _write, _verify_output_path
    rendered_artifacts = {} if rendered_artifacts is None else rendered_artifacts
    if (not isinstance(rendered_artifacts, dict) or
            not set(rendered_artifacts) <= {'rendered-before.json', 'rendered-after.json'} or
            any(type(raw) is not bytes or len(raw) > 16_000_000 for raw in rendered_artifacts.values())):
        raise ValueError('Exact render artifacts are invalid')
    references = {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
                  for name, raw in rendered_artifacts.items()}
    if references != result.get('rendered_export', {}).get('artifacts', {}):
        raise ValueError('Exact render artifacts do not match the validation result')
    validate_report(static_review)
    check_output(output, Path(result['source']['repository']))
    # Full rendered bodies can contain sensitive values. Keep them private and
    # anchored to the directory descriptor, with no raw payload in stdout/HTML.
    static_artifacts = {'report.json': static_review, 'report.html': render_html(static_review),
                        'report.md': render_markdown(static_review)}
    correction_artifacts = {
        'proposal.patch': patch.decode('utf-8'),
        'correction.json': result,
        'correction.html': render_correction_html(result),
        'correction.md': render_correction_markdown(result),
        **{name: raw.decode('utf-8') for name, raw in rendered_artifacts.items()},
    }
    with _new_directory(output) as directory:
        files = {name: _write(directory, name, value) for name, value in static_artifacts.items()}
        run_manifest = {'schema_version': 1, 'run_id': static_review.get('run_id'),
            'finished_at': static_review.get('finished_at'), 'finished': True,
            'status': static_review['status'], 'decision': static_review['decision'],
            'artifacts': dict(files)}
        files['run-manifest.json'] = _write(directory, 'run-manifest.json', run_manifest)
        for name, value in correction_artifacts.items():
            files[name] = _write(directory, name, value)
        manifest = {'schema_version': 1, 'finished': True, 'execution_status': result['execution_status'],
                    'decision': result['decision'], 'source_base_commit': result['source']['base_commit'],
                    'contract_sha256': result['contract_sha256'], 'artifacts': files}
        _verify_output_path(output, directory)
        _write(directory, 'correction-manifest.json', manifest)
        os.fsync(directory)
        try:
            _verify_output_path(output, directory)
        except (OSError, ValueError):
            os.unlink('correction-manifest.json', dir_fd=directory)
            os.fsync(directory)
            raise
