"""Controlled object-contract adversaries, separate from real native Git tests."""
from copy import deepcopy
import hashlib

import pytest

from devsecops.factory import git_native_request as request
from devsecops.factory.deployment_commit import _object_id


def record(kind, raw):
    return {'kind': kind, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def objects():
    # These bytes test contract identity, not Git commit grammar; native
    # reconstruction and receive-pack validation have their own real fixtures.
    contents = [('commit', b'new-commit'), ('blob', b'changed'), ('blob', b'')]
    rows = {_object_id(kind, raw): record(kind, raw) for kind, raw in contents}
    raw = {_object_id(kind, raw): raw for kind, raw in contents}
    commit, changed, shared = raw.keys()
    parent = _object_id('commit', b'old-commit')
    prepared = {'commit': commit, 'parent': parent, 'objects': deepcopy(rows)}
    binding = {'new_commit': commit, 'base_commit': parent,
               'closure': {'objects': {shared: deepcopy(rows[shared]), parent: record('commit', b'old-commit')}}}
    observed = {'new_commit': commit, 'objects': {shared: {**rows[shared], 'offset': 0}}}
    return dict(prepared=prepared, binding=binding, observed=observed, raw=raw,
                commit=commit, changed=changed, shared=shared)


def classify(state):
    return request._contract(state['prepared'], state['binding'], state['observed'])


def test_current_shared_zero_blob_classifies_without_omission_authority(objects):
    rows, required, optional = classify(objects)
    assert required == {objects['commit'], objects['changed']}
    assert optional == {objects['shared']}
    assert rows == objects['prepared']['objects']
    rows[objects['shared']]['bytes'] = 99
    assert objects['prepared']['objects'][objects['shared']]['bytes'] == 0


@pytest.mark.parametrize('fault', ['empty', 'nineteen', 'new-mismatch', 'parent-mismatch',
    'observed-new-mismatch', 'new-missing', 'bad-oid', 'bad-kind', 'bool-length',
    'negative-length', 'object-too-large', 'bad-sha', 'aggregate-too-large',
    'shared-local-kind', 'shared-local-length', 'shared-local-sha',
    'shared-remote-kind', 'shared-remote-length', 'shared-remote-sha',
    'shared-not-observed', 'commit-shared'])
def test_contract_rejects_bad_scope_bounds_and_unverified_shared_bytes(objects, fault):
    prepared, binding, observed = (objects[k] for k in ('prepared', 'binding', 'observed'))
    rows = prepared['objects']
    if fault == 'empty':
        rows.clear()
    elif fault == 'nineteen':
        for number in range(16):
            raw = str(number).encode()
            rows[_object_id('blob', raw)] = record('blob', raw)
    elif fault == 'new-mismatch':
        binding['new_commit'] = 'f' * 40
    elif fault == 'parent-mismatch':
        binding['base_commit'] = 'f' * 40
    elif fault == 'observed-new-mismatch':
        observed['new_commit'] = 'f' * 40
    elif fault == 'new-missing':
        del rows[objects['commit']]
    elif fault == 'bad-oid':
        rows['not-a-sha1'] = rows.pop(objects['changed'])
    elif fault.startswith('shared-local-'):
        field = fault.removeprefix('shared-local-')
        key = {'kind': 'kind', 'length': 'bytes', 'sha': 'sha256'}[field]
        binding['closure']['objects'][objects['shared']][key] = {'kind': 'tree', 'length': 1, 'sha': 'f'*64}[field]
    elif fault.startswith('shared-remote-'):
        field = fault.removeprefix('shared-remote-')
        key = {'kind': 'kind', 'length': 'bytes', 'sha': 'sha256'}[field]
        observed['objects'][objects['shared']][key] = {'kind': 'tree', 'length': 1, 'sha': 'f'*64}[field]
    elif fault == 'shared-not-observed':
        observed['objects'].clear()
    elif fault == 'commit-shared':
        binding['closure']['objects'][objects['commit']] = deepcopy(rows[objects['commit']])
        observed['objects'][objects['commit']] = {**rows[objects['commit']], 'offset': 0}
    elif fault == 'aggregate-too-large':
        for number in range(2):
            raw = b'additional-' + str(number).encode()
            rows[_object_id('blob', raw)] = record('blob', raw)
        for row in rows.values():
            row['bytes'] = request.MAX_OBJECT_BYTES
    else:
        key, value = {'bad-kind': ('kind', 'tag'), 'bool-length': ('bytes', True),
            'negative-length': ('bytes', -1), 'object-too-large': ('bytes', request.MAX_OBJECT_BYTES+1),
            'bad-sha': ('sha256', 'f'*63)}[fault]
        rows[objects['changed']][key] = value
    with pytest.raises(ValueError):
        classify(objects)


class NativeOutputs:
    """Script only the native interface; every object byte remains independently hashed."""
    def __init__(self, rows, raw, selected):
        self.rows, self.raw, self.selected = rows, raw, sorted(selected)
        self.index = b'pack\t' + b'1' * 40 + b'\n'
        self.lines = [f'{oid} {rows[oid]["kind"]} {rows[oid]["bytes"]} 20 12'.encode()
                      for oid in self.selected]
        self.present = b''.join(oid.encode() + b'\n' for oid in self.selected)
        self.calls = []

    def __call__(self, *args, **kwargs):
        assert kwargs['store'] == 'emitted.git'
        self.calls.append((args, kwargs))
        if args[0] == 'index-pack':
            assert args == ('index-pack', '--stdin')
            return self.index
        if args[0] == 'verify-pack':
            return b'\n'.join(self.lines) + b'\nnon delta: 3 objects\n'
        if args == ('cat-file', '--batch-all-objects', '--batch-check=%(objectname)'):
            return self.present
        if args[0] == 'cat-file':
            return self.raw[args[2]]
        raise AssertionError('Unexpected controlled native command')


def pack_pin_observer(pack, native, *, fail_at=None):
    calls = []
    expected = {'bytes': len(pack), 'sha256': hashlib.sha256(pack).hexdigest()}

    def verify(store, checksum, pin):
        assert store == 'emitted.git'
        assert checksum == native.index[5:].strip().decode()
        assert pin == expected
        calls.append((store, checksum, deepcopy(pin), len(native.calls)))
        if len(calls) == fail_at:
            raise ValueError('Controlled retained pack pin mismatch')

    return verify, calls


@pytest.mark.parametrize('include_shared', [True, False])
@pytest.mark.parametrize('native_checksum', ['1' * 40, '2' * 40])
def test_emitted_inventory_accepts_exact_required_and_optional_only(objects, include_shared, native_checksum):
    rows, required, optional = classify(objects)
    chosen = required | (optional if include_shared else set())
    native = NativeOutputs(rows, objects['raw'], chosen)
    native.index = b'pack\t' + native_checksum.encode() + b'\n'
    pack = b'PACK-fixture-native-validation-is-scripted'
    verify, pin_calls = pack_pin_observer(pack, native)
    report = request._emitted_contract(native, pack, rows, required, optional, verify)
    assert report['emitted'] == sorted(chosen)
    assert report['omitted_shared'] == sorted(optional-chosen)
    assert report['scope'] == 'local-object-classification-only'
    assert report['omission_authorized'] is False
    assert report['pack'] == {'bytes': len(pack), 'sha256': hashlib.sha256(pack).hexdigest()}
    assert native.calls[0][1]['data'] is pack
    assert len(pin_calls) == 2
    # The native checksum locates the indexed pack; it never replaces the
    # original request's independently calculated SHA-256 or length.
    assert pin_calls[0][2] == pin_calls[1][2] == report['pack']
    assert pin_calls[0][3] == 1
    assert pin_calls[1][3] == len(native.calls)
    # Empty blobs remain readable with a positive native stdout budget.
    if include_shared:
        call = next(kw for a, kw in native.calls if a == ('cat-file', 'blob', objects['shared']))
        assert call['limit'] == 1


@pytest.mark.parametrize('fault', ['bad-index', 'missing-required', 'extra-object', 'duplicate-object',
    'delta-object', 'wrong-kind', 'wrong-size', 'wrong-raw', 'wrong-sha', 'wrong-oid',
    'unlisted-object-in-store', 'missing-object-in-store', 'duplicate-object-in-store'])
def test_emitted_contract_rejects_inventory_and_raw_identity_faults(objects, fault):
    rows, required, optional = classify(objects)
    native = NativeOutputs(rows, objects['raw'], set(rows))
    changed = objects['changed']
    if fault == 'bad-index':
        native.index = b'pack\t' + b'1'*40 + b'\nextra\n'
    elif fault == 'missing-required':
        native.lines = [line for line in native.lines if not line.startswith(changed.encode())]
    elif fault == 'extra-object':
        native.lines.append(b'f'*40 + b' blob 1 20 12')
    elif fault == 'duplicate-object':
        native.lines.append(native.lines[0])
    elif fault == 'delta-object':
        native.lines[0] += b' 1 ' + b'f'*40
    elif fault in ('wrong-kind', 'wrong-size'):
        replacement = f'{changed} {"tree" if fault == "wrong-kind" else "blob"} {8 if fault == "wrong-size" else 7} 20 12'.encode()
        native.lines = [replacement if line.startswith(changed.encode()) else line for line in native.lines]
    elif fault == 'wrong-raw':
        native.raw[changed] = b'CHANGED'
    elif fault == 'wrong-sha':
        rows[changed]['sha256'] = 'f'*64
    elif fault == 'wrong-oid':
        rows['f'*40] = rows.pop(changed)
        native.raw['f'*40] = native.raw.pop(changed)
        required.remove(changed)
        required.add('f'*40)
        native = NativeOutputs(rows, native.raw, set(rows))
    elif fault == 'unlisted-object-in-store':
        native.present += b'f'*40 + b'\n'
    elif fault == 'missing-object-in-store':
        native.present = b''
    elif fault == 'duplicate-object-in-store':
        native.present += native.present.splitlines()[0] + b'\n'
    with pytest.raises(ValueError):
        verify, _ = pack_pin_observer(b'PACK-controlled', native)
        request._emitted_contract(native, b'PACK-controlled', rows, required, optional, verify)


@pytest.mark.parametrize('fail_at', [1, 2])
def test_emitted_contract_rejects_pack_pin_drift_before_and_after_reads(objects, fail_at):
    rows, required, optional = classify(objects)
    native = NativeOutputs(rows, objects['raw'], set(rows))
    pack = b'PACK-original-request-pin'
    verify, calls = pack_pin_observer(pack, native, fail_at=fail_at)
    with pytest.raises(ValueError, match='Controlled retained pack pin mismatch'):
        request._emitted_contract(native, pack, rows, required, optional, verify)
    assert len(calls) == fail_at
    assert calls[0][3] == 1  # Admission before any object metadata or raw reads.
    if fail_at == 1:
        assert len(native.calls) == 1
    else:
        assert calls[1][3] == len(native.calls)
        assert sum(args[0] == 'cat-file' and len(args) == 3 and args[1] in ('commit', 'tree', 'blob')
                   for args, _ in native.calls) == len(rows)
