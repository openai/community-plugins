"""Current local request evidence and a durable one-attempt claim, never a grant.

There is no writer/branch admission or network dispatcher here. A claimed local
request cannot authorize a POST. A later dispatcher still needs a separately
reviewed current writer grant and effect reconciliation boundary.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
import os
import threading
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import _open_directory
from devsecops.factory.git_base_possession import require_observed_git_base
from devsecops.factory.git_transport import require_prepared_git_transport, _identity, _directory_at
from devsecops.factory.git_native_request import prepare_native_git_request, _pin
from devsecops.factory.git_request_framing import command_pack
from devsecops.factory.mission_security import _read_retained
from devsecops.factory.state import RunJournal, StateError, _read, _json, MAX_DOCUMENT_BYTES, GIT_REQUEST_ACTIONS
from devsecops.runtime.cli import _verify_output_path
from devsecops.runtime.context import canonical_bytes

_REQUESTS = WeakKeyDictionary()
_CLAIMS = WeakKeyDictionary()
_SCOPE = ('run_scope', 'source_commit', 'deployment_commit',
          'proposal_configuration_sha256', 'configuration_sha256')
_NAMES = ('request.body', 'native-request.json', 'claim.json')


class PreparedNativeRequest:
    """Process-local request evidence. Not delivery or omission permission."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('Requests require the direct consumer and current journal')

    def __reduce__(self):
        raise TypeError('Current request handles cannot be serialized')

    def cancel(self):
        record = _request_record(self)
        record['cancelled'] = True

    def describe(self):
        record = _request_record(self)
        _audit_request(self, record['journal'], record['phase_id'])
        return deepcopy(record['description'])


class LocalGitRequestClaim:
    """A spent local claim; no writer grant, dispatch method or effect assertion."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('Claims require current request evidence and journal publication')

    def __reduce__(self):
        raise TypeError('Current claim handles cannot be serialized')

    def describe(self):
        record = _claim_record(self)
        return require_local_git_request_claim(self, journal=record['journal'], phase_id=record['phase_id'])


def _admit(journal, phase_id, *, locked=False):
    if (type(journal) is not RunJournal or not journal._lifecycle_locked
            or not journal._environment_locked or journal._process_id != os.getpid()
            or threading.current_thread() is not threading.main_thread()):
        raise StateError('Current direct journal and both lifecycle locks required')
    action = GIT_REQUEST_ACTIONS.get(phase_id)
    if action is None:
        raise StateError('Exact supported canonical Git phase required')
    if locked:
        operation = journal._current_mutation_locked(phase_id, action)
        state = journal._state
    else:
        operation = journal.current_mutation(phase_id, action)
        state = journal.state
    lifecycle = state.get('lifecycle')
    phase = next((p for p in (lifecycle or {}).get('phases', []) if p['id'] == phase_id), None)
    if (lifecycle is None or lifecycle['abort'] is not None or lifecycle['final_result'] is not None
            or lifecycle['driver_id'] != 'owned-linux' or phase is None
            or phase['operation'] != action or lifecycle['active_phase']['phase_id'] != phase_id
            or state['identities'] != journal.identities):
        raise StateError('Current exact owned Linux deliver phase required')
    config_ref = lifecycle['inputs']['config']
    raw = _read(journal._artifact_path(config_ref['path']), MAX_DOCUMENT_BYTES)
    if _pin(raw) != {key: config_ref[key] for key in ('sha256', 'bytes')}:
        raise StateError('Exact current lifecycle configuration bytes changed')
    config = _json(raw)
    try:
        expires = datetime.fromisoformat(config['expires_at'].replace('Z', '+00:00'))
        current = datetime.now(timezone.utc)
        if expires.tzinfo is None or current >= expires or ('build' if action == 'build-promote' else action) not in config['allowed_mutations']:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise StateError('Current bounded local delivery selection required') from None
    return operation, deepcopy(state['identities']), state['run_id'], expires


def _clock(record):
    mono = time.monotonic()-record['started_mono']
    wall = time.time()-record['started_wall']
    if (record['cancelled'] or min(mono,wall) < 0 or max(mono,wall) >= record['timeout']
            or abs(mono-wall) > 2):
        raise StateError('Request cancelled, expired or clock continuity lost')


def _request_record(value):
    record = _REQUESTS.get(value) if type(value) is PreparedNativeRequest else None
    if record is None or record['pid'] != os.getpid() or record['thread'] != threading.get_ident():
        raise StateError('Current direct request handle required in its issuing process and thread')
    return record


def _audit_request(value, journal, phase_id, *, locked=False):
    record = _request_record(value)
    _clock(record)
    if journal is not record['journal'] or phase_id != record['phase_id']:
        raise StateError('Request belongs to a different journal or phase')
    operation, identities, run_id, _ = _admit(journal, phase_id, locked=locked)
    if (operation != record['operation_id'] or identities != record['identities'] or run_id != record['run_id']):
        raise StateError('Request operation binding changed')
    operation_fd = _open_directory(record['operation_path'])
    try:
        if _identity(operation_fd) != record['operation_identity']:
            raise StateError('Prepared request operation directory changed')
        _verify_output_path(record['operation_path'],operation_fd)
    finally:
        os.close(operation_fd)
    args = record['args']
    scope = {key: args[key] for key in _SCOPE}
    binding = require_prepared_git_transport(args['transport'], **scope, deadline=record['deadline'])
    observed = require_observed_git_base(args['observed_base'], transport=args['transport'], **scope,
        expected_config_sha256=args['expected_destination_sha256'], deadline=record['deadline'])
    if binding != record['transport_binding'] or observed != record['base_binding']:
        raise StateError('Current request source binding changed')
    directory = _open_directory(record['output'])
    try:
        if _identity(directory) != record['output_identity'] or set(os.listdir(directory)) != set(record['artifacts']):
            raise StateError('Request output identity or membership changed')
        for name, ref in record['artifacts'].items():
            _read_retained(directory, name, ref, record['deadline'])
        _verify_output_path(record['output'], directory)
    finally:
        os.close(directory)
    body = record['body']
    pack = command_pack(body, new_commit=record['report']['new_commit'],
                        target_ref=record['report']['selection']['target_ref'])
    if (_pin(body) != record['report']['request']
            or _pin(pack) != record['report']['object_contract']['pack']):
        raise StateError('Current request or pack identity changed')
    # Retained-byte reads can outlive current source authority. Recheck that
    # authority after them, including on the final post-publication audit.
    operation, identities, run_id, config_expires = _admit(journal, phase_id, locked=locked)
    if (operation != record['operation_id'] or identities != record['identities'] or run_id != record['run_id']):
        raise StateError('Request operation binding changed during audit')
    binding = require_prepared_git_transport(args['transport'], **scope, deadline=record['deadline'])
    observed = require_observed_git_base(args['observed_base'], transport=args['transport'], **scope,
        expected_config_sha256=args['expected_destination_sha256'], deadline=record['deadline'])
    if binding != record['transport_binding'] or observed != record['base_binding']:
        raise StateError('Current request source binding changed during audit')
    if datetime.now(timezone.utc) >= config_expires:
        raise StateError('Local delivery selection expired during audit')
    _clock(record)
    return record


def prepare_operation_request(journal, phase_id, *, transport, observed_base,
        run_scope, source_commit, deployment_commit, proposal_configuration_sha256,
        configuration_sha256, expected_destination_sha256, advertisement, timeout=120):
    """Generate directly in the current operation; never restore from saved JSON."""
    started_mono, started_wall = time.monotonic(), time.time()
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise StateError('Bounded current request preparation required')
    operation, identities, run_id, _ = _admit(journal,phase_id)
    if run_scope != identities:
        raise StateError('Request scope must match current journal identities')
    args = dict(transport=transport, observed_base=observed_base, run_scope=deepcopy(run_scope),
        source_commit=source_commit, deployment_commit=deployment_commit,
        proposal_configuration_sha256=proposal_configuration_sha256, configuration_sha256=configuration_sha256,
        expected_destination_sha256=expected_destination_sha256, advertisement=advertisement)
    operation_path = journal.run_dir/'operations'/operation
    operation_fd = _open_directory(operation_path)
    try:
        operation_identity = _identity(operation_fd)
    finally:
        os.close(operation_fd)
    output = operation_path/'native-request-preparation'
    if operation in journal._consumed_git_request_operations:
        raise StateError('This operation already consumed its local request attempt')
    report, body = prepare_native_git_request(**args, output=output, timeout=timeout)
    deadline = started_mono+timeout
    scope = {key:args[key] for key in _SCOPE}
    binding = require_prepared_git_transport(transport,**scope,deadline=deadline)
    observed = require_observed_git_base(observed_base,transport=transport,**scope,
        expected_config_sha256=expected_destination_sha256,deadline=deadline)
    report_raw = canonical_bytes(report)
    if (report['decision']!='request-prepared' or report['delivery_authority']!='not-issued'
            or report['write_attempts']!=0 or report['network_requests']!=0
            or report['native_delivery_exit'] is not None or report['omission_authorized'] is not False):
        raise StateError('Only exact local request preparation evidence is accepted')
    directory = _open_directory(output)
    try:
        identity = _identity(directory)
    finally:
        os.close(directory)
    description = {'kind':'prepared-native-request-local-evidence','operation_id':operation,'phase_id':phase_id,
        'run_id':run_id,'identities':identities,'request':_pin(body),'pack':deepcopy(report['object_contract']['pack']),
        'delivery_authority':'not-issued','writer_grant':'missing','remote_effect':'not-established'}
    value = object.__new__(PreparedNativeRequest)
    _REQUESTS[value] = dict(journal=journal,phase_id=phase_id,operation_id=operation,identities=identities,
        run_id=run_id,operation_path=operation_path,operation_identity=operation_identity,
        pid=os.getpid(),thread=threading.get_ident(),started_mono=started_mono,
        started_wall=started_wall,timeout=timeout,deadline=deadline,cancelled=False,
        args=args,body=body,report=deepcopy(report),report_raw=report_raw,output=output,output_identity=identity,
        artifacts={'request.body':_pin(body),'native-request.json':_pin(report_raw)},description=description,
        transport_binding=binding,base_binding=observed)
    _audit_request(value,journal,phase_id)
    return value


def _publish(directory,name,raw):
    """An exclusive partial file is retained on every interruption."""
    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)


def claim_operation_request(journal,phase_id,request):
    """Consume one local attempt durably, without permission to send it anywhere."""
    _audit_request(request,journal,phase_id)
    with journal._run_lock():
        journal._load()
        record=_audit_request(request,journal,phase_id,locked=True)
        operation=record['operation_id']
        if operation in journal._consumed_git_request_operations:
            raise StateError('This operation already consumed its local request attempt')
        operation_path=journal.run_dir/'operations'/operation
        directory=_open_directory(operation_path)
        claim_dir=None
        try:
            operation_identity=_identity(directory)
            if operation_identity != record['operation_identity']:
                raise StateError('Prepared request operation directory changed before claim reservation')
            _verify_output_path(operation_path,directory)
            # Burn before the first filesystem mutation. This set is never reset;
            # an absent/removed orphan cannot restore a live journal's attempt.
            journal._consumed_git_request_operations.add(operation)
            os.mkdir('git-request-claim',0o700,dir_fd=directory)
            os.fsync(directory)
            claim_dir=_directory_at(directory,'git-request-claim')
            claim_path=operation_path/'git-request-claim'
            claim_identity=_identity(claim_dir)
            description={**record['description'],'kind':'local-git-request-claim','claim_status':'consumed',
                'request_consumer_report':_pin(record['report_raw']),
                'expires_at':datetime.fromtimestamp(record['started_wall']+record['timeout'],timezone.utc).isoformat(),
                'created_at':datetime.now(timezone.utc).isoformat(),
                'selection':deepcopy(record['report']['selection']),
                'destination_configuration_sha256':record['args']['expected_destination_sha256'],
                'configuration_sha256':record['args']['configuration_sha256']}
            contents={'request.body':record['body'],'native-request.json':record['report_raw'],
                      'claim.json':canonical_bytes(description)}
            # Reserve first. All orphan/partial states remain consumed; never
            # delete, retry, or infer the encompassing operation's remote effect.
            for name in _NAMES:
                _audit_request(request,journal,phase_id,locked=True)
                _verify_output_path(operation_path,directory)
                _verify_output_path(claim_path,claim_dir)
                _publish(claim_dir,name,contents[name])
            refs=[{'path':str((claim_path/name).relative_to(journal.run_dir)),**_pin(contents[name])}
                  for name in _NAMES]
            for name,ref in zip(_NAMES,refs):
                _read_retained(claim_dir,name,ref,record['deadline'])
            _audit_request(request,journal,phase_id,locked=True)
            _verify_output_path(operation_path,directory)
            _verify_output_path(claim_path,claim_dir)
            journal._record_git_request_claim_locked(phase_id,operation,refs)
            # Publication ambiguity remains spent even when final checks fail.
            _audit_request(request,journal,phase_id,locked=True)
            for name,ref in zip(_NAMES,refs):
                _read_retained(claim_dir,name,ref,record['deadline'])
            _verify_output_path(claim_path,claim_dir)
            _audit_request(request,journal,phase_id,locked=True)
            handle=object.__new__(LocalGitRequestClaim)
            _CLAIMS[handle]=dict(journal=journal,phase_id=phase_id,request=request,
                operation_id=operation,operation_path=operation_path,operation_identity=operation_identity,
                claim_path=claim_path,claim_identity=claim_identity,refs=deepcopy(refs),
                description=deepcopy(description),pid=os.getpid(),thread=threading.get_ident())
            return handle
        finally:
            if claim_dir is not None:
                os.close(claim_dir)
            os.close(directory)


def _claim_record(value):
    record=_CLAIMS.get(value) if type(value) is LocalGitRequestClaim else None
    if record is None or record['pid']!=os.getpid() or record['thread']!=threading.get_ident():
        raise StateError('Current direct local claim handle required')
    return record


def require_local_git_request_claim(value,*,journal,phase_id):
    """Verify a live spent claim; this still supplies no write authorization."""
    record=_claim_record(value)
    if journal is not record['journal'] or phase_id!=record['phase_id']:
        raise StateError('Claim journal or phase differs')
    request_record=_audit_request(record['request'],journal,phase_id)
    with journal._run_lock():
        journal._load()
        _audit_request(record['request'],journal,phase_id,locked=True)
        current=journal._current_mutation_locked(phase_id,GIT_REQUEST_ACTIONS[phase_id])
        active=journal._state['active_mutation']
        if (current!=record['operation_id'] or current not in journal._consumed_git_request_operations
                or any(ref not in active['receipt_artifacts'] for ref in record['refs'])):
            raise StateError('Registered claim differs from the current operation')
        for path,expected in ((record['operation_path'],record['operation_identity']),
                              (record['claim_path'],record['claim_identity'])):
            directory=_open_directory(path)
            try:
                if _identity(directory)!=expected:
                    raise StateError('Claim directory identity changed')
                _verify_output_path(path,directory)
                if path==record['claim_path']:
                    if set(os.listdir(directory))!=set(_NAMES):
                        raise StateError('Claim membership changed')
                    for name,ref in zip(_NAMES,record['refs']):
                        _read_retained(directory,name,ref,request_record['deadline'])
            finally:
                os.close(directory)
        _audit_request(record['request'],journal,phase_id,locked=True)
        return deepcopy(record['description'])
