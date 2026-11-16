"""Canonical, process-local runtime observation production.

The session calls the fixed read collectors and retains their original inputs
and outputs. Portable JSON is useful evidence, but cannot recreate this session
or authenticate a provider, a delivery, an image or a control-path experiment.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import time
from types import MappingProxyType
from uuid import uuid4
from weakref import WeakKeyDictionary

from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.runtime.cli import _new_directory, _write
from devsecops.runtime.collection import start_runtime_collection, finish_runtime_collection, PHASE_TIMEOUT_SECONDS
from devsecops.runtime.context import (MAX_BUNDLE, canonical_bytes, clock, freeze_runtime_context,
    parse_time, seal, validate, verify_runtime_authority)
from devsecops.runtime.recovery import (BASE_ARTIFACTS, KINDS, RECOVERY_CONTRACT_SCHEMA, RECOVERY_INVENTORY_SCHEMA, OriginVerification,
    _approved, verify_recovery)
from .state import StateError, _read, _sha, _json

_ISSUER = object()
_ORIGINS = WeakKeyDictionary()
_PHASE = {'baseline': 'baseline', 'confirm-fault': 'fault', 'probe': 'recovery'}
_BEGIN = {'up': {'baseline'}, 'inject': {'fault'}, 'deliver': {'recovery'},
          'build-promote': {'baseline'}, 'reset': {'baseline'},
          **{key: {value} for key, value in _PHASE.items()}}


def _phase_allowed(context, attempt, phase):
    if attempt == 'f0-network-policy':
        return context.phase['operation'] in {'build-promote', 'reset'} and phase in {'baseline', 'fault', 'recovery'}
    return phase in _BEGIN.get(context.phase['operation'], set()) and context.phase['attempt'] in {attempt, 'shared'}


class RuntimeObservationOrigin:
    """Only the original collecting process can authenticate its observations."""
    __slots__ = ('__weakref__',)

    def __new__(cls, issuer=None):
        if issuer is not _ISSUER:
            raise StateError('Runtime origins require direct collection')
        return super().__new__(cls)

    def __reduce__(self):
        raise StateError('Runtime origins cannot be serialized or restored')


@dataclass(frozen=True)
class RuntimeCollectionSelection:
    """Portable artifact selection; no independent execution authority."""
    context: Path
    context_sha256: str
    pending: Path
    pending_sha256: str
    collection: Path | None
    collection_sha256: str | None
    window: dict | None


def _pin(path, expected=None):
    if not isinstance(path, Path) or not path.is_absolute():
        raise StateError('Runtime selections require explicit absolute Paths')
    raw = _read(path, MAX_BUNDLE)
    if expected is not None and _sha(raw) != expected:
        raise StateError('Runtime selected bytes changed')
    return raw


class RuntimeObservationSession:
    """One concrete driver/journal/bootstrap, with no callback injection surface.

    ``begin`` may run in provision/inject/deliver before their explicitly
    journaled probe actors start. ``finish`` only performs read operations in
    baseline/confirm-fault/probe, and never shifts or restarts the pending window.
    """
    def __init__(self, driver, *, bootstrap):
        from .owned_linux import OwnedLinuxDriver
        from .canonical_bootstrap import OwnedBootstrapCoordinator
        if type(driver) is not OwnedLinuxDriver or type(bootstrap) is not OwnedBootstrapCoordinator:
            raise StateError('Runtime collection requires the concrete owned driver and bootstrap')
        self._driver, self._bootstrap = driver, bootstrap
        self._journal, self._pid = driver.journal, os.getpid()
        self._run = self._journal.state['run_id']
        self._identities = deepcopy(self._journal.identities)
        self._records, self._recovery = {}, {}
        self._network_policy_path = None
        self._mission_publication = None
        self._mission_subject = None
        self._runtime_image = None
        self._locked_images = None
        self._locked_subjects = None
        self._input_producer = None

    def _admit(self, context):
        if (self._pid != os.getpid() or self._driver.journal is not self._journal
                or self._journal.state['run_id'] != self._run
                or self._journal.identities != self._identities):
            raise StateError('Runtime session belongs to another process or journal')
        self._driver._admit(context, context.deadline)
        self._driver._package(self._identities['package_sha256'], context.deadline)
        return self._bootstrap.require_authority(context)

    def _retained(self, attempt, phase):
        if self._pid != os.getpid() or self._journal.identities != self._identities:
            raise StateError('Runtime session identity changed')
        if attempt not in {'primary', 'replay', 'f0-network-policy'}:
            raise StateError('Runtime attempt is unsupported')
        try:
            record = self._records[(attempt, phase)]
        except KeyError:
            raise StateError('Original runtime collection is unavailable') from None
        for path, raw in record['pins'].items():
            if _pin(path) != raw:
                raise StateError('Retained runtime input or output changed')
        verify_runtime_authority(record['context'], record['authority'])
        return record

    def _selection(self, record):
        paths = record['paths']
        bundle = record.get('bundle')
        return RuntimeCollectionSelection(paths['context'], _sha(record['pins'][paths['context']]),
            paths['pending'], _sha(record['pins'][paths['pending']]), paths.get('collection'),
            None if bundle is None else _sha(record['pins'][paths['collection']]),
            deepcopy(record['pending']['window']))

    def _transport(self, context, frozen, authority):
        if context.execution_mode == 'controlled-test':
            return None
        from .owned_runtime_inputs import OwnedRuntimeInputCoordinator
        if type(getattr(self, '_input_producer', None)) is not OwnedRuntimeInputCoordinator:
            raise StateError('Original runtime read transport producer is unavailable')
        return self._input_producer.bind_transport(context, frozen, authority=authority)

    def begin(self, context, *, intent: Path, intent_sha256: str,
              receipt_documents=None, delivery_receipt=None):
        authority = self._admit(context)
        if context.deadline-time.monotonic() <= PHASE_TIMEOUT_SECONDS+1:
            raise StateError('Runtime start cannot fit the fixed read budget')
        raw = _pin(intent, intent_sha256)
        doc = _json(raw)
        attempt, phase = doc.get('attempt_id'), doc.get('phase')
        if (attempt not in {'primary', 'replay', 'f0-network-policy'} or not _phase_allowed(context, attempt, phase)
                or doc.get('run_id') != self._run or doc.get('identities') != self._identities
                or doc.get('environment') != self._journal.state['environment_id']):
            raise StateError('Runtime intent differs from the active canonical phase')
        if (attempt, phase) in self._records:
            raise StateError('Runtime collection already exists; reconcile it instead of restarting')
        inputs = self._journal.state['lifecycle']['inputs']
        selected = {name: self._journal._artifact_path(inputs[name]['path']) for name in ('config', 'lock')}
        selected['intent'] = intent
        pins = {path: _pin(path) for path in selected.values()}
        if pins[intent] != raw:
            raise StateError('Runtime intent changed before collection')
        receipts = {} if receipt_documents is None else dict(receipt_documents)
        for path in receipts.values():
            if isinstance(path, Path):
                pins[path] = _pin(path)
        if delivery_receipt is not None:
            if not isinstance(delivery_receipt, Path):
                raise StateError('Delivery selection requires an explicit retained artifact Path')
            pins[delivery_receipt] = _pin(delivery_receipt)
        frozen = freeze_runtime_context(selected['config'], selected['lock'], selected['intent'], receipt_documents=receipts)
        # A split begin/finish has a later phase budget; the immutable environment
        # expiry still bounds both phases. No clock or transport is injectable.
        transport = self._transport(context, frozen, selected)
        options = {} if transport is None else {'transport': transport}
        pending = start_runtime_collection(frozen, authority=selected, delivery_receipt=delivery_receipt, **options)
        if context.execution_mode == 'controlled-test':
            pending['execution_mode'] = 'controlled-test'
            pending = seal(pending, 'bundle_sha256')
        directory = context.run_dir / 'runtime' / (attempt+'-'+phase+'-'+str(uuid4()))
        paths = {name: directory / (name+'.json') for name in ('context', 'pending')}
        with _new_directory(directory) as fd:
            _write(fd, 'context.json', frozen)
            _write(fd, 'pending.json', pending)
        for path in paths.values():
            pins[path] = _pin(path)
        if self._admit(context) is not authority:
            raise StateError('Bootstrap origin changed during collection')
        record = {'context': frozen, 'authority': selected, 'bootstrap': authority,
                  'pending': pending, 'paths': paths, 'pins': pins,
                  'delivery': delivery_receipt, 'mode': context.execution_mode}
        record['transports'] = [] if transport is None else [transport]
        self._records[(attempt, phase)] = record
        self._retained(attempt, phase)
        return self._selection(record)

    def finish(self, context):
        return self._finish(context, attempt=context.phase['attempt'], phase=_PHASE.get(context.phase['operation']))

    def finish_experiment(self, context, *, phase):
        if not _phase_allowed(context, 'f0-network-policy', phase):
            raise StateError('Independent F0 collection is outside its explicit mutation phase')
        return self._finish(context, attempt='f0-network-policy', phase=phase)

    def _finish(self, context, *, attempt, phase):
        authority = self._admit(context)
        record = self._retained(attempt, phase)
        if authority is not record['bootstrap'] or context.execution_mode != record['mode']:
            raise StateError('Runtime collection bootstrap or execution mode changed')
        if record.get('bundle') is not None:
            raise StateError('Runtime collection already finished')
        window = record['pending']['window']
        if window is not None:
            remaining = parse_time(window['next_eligible_at']).timestamp() - clock().timestamp()
            if max(0, remaining) + PHASE_TIMEOUT_SECONDS + 1 >= context.deadline - time.monotonic():
                raise StateError('Immutable runtime window exceeds the remaining phase budget')
            while remaining > 0:
                checkpoint(context.deadline)
                time.sleep(min(0.25, remaining))
                remaining = parse_time(window['next_eligible_at']).timestamp() - clock().timestamp()
        self._retained(attempt, phase)
        transport = self._transport(context, record['context'], record['authority'])
        options = {} if transport is None else {'transport': transport}
        bundle = finish_runtime_collection(record['context'], record['pending'],
            authority=record['authority'], delivery_receipt=record['delivery'], **options)
        if transport is not None:
            record['transports'].append(transport)
        self._admit(context)
        # Never overwrite a previous observation or promote controlled evidence.
        if record['mode'] == 'controlled-test':
            bundle['execution_mode'] = 'controlled-test'
            bundle = seal(bundle, 'bundle_sha256')
        directory = context.run_dir / 'runtime' / (attempt+'-'+phase+'-finished-'+str(uuid4()))
        with _new_directory(directory) as fd:
            _write(fd, 'collection.json', bundle)
        path = directory/'collection.json'
        record['paths']['collection'] = path
        record['pins'][path] = _pin(path)
        record['bundle'] = bundle
        self._retained(attempt, phase)
        return self._selection(record)

    def collect(self, context, **inputs):
        key = (context.phase['attempt'], _PHASE.get(context.phase['operation']))
        if key not in self._records:
            self.begin(context, **inputs)
        elif inputs:
            raise StateError('Existing collection cannot receive replacement inputs')
        return self.finish(context)

    def frozen_context(self, attempt, phase):
        """Current immutable data for fixed probe planning; no authority grant."""
        return deepcopy(self._retained(attempt, phase)['context'])

    @property
    def driver(self):
        return self._driver

    def prepare_probe_actor(self, context, *, attempt, phase, **selection):
        from devsecops.runtime.probe_observations import prepare_probe_actor
        self._admit(context)
        if (context.phase['operation'] not in {'up', 'build-promote', 'reset', 'inject', 'deliver', 'probe'}
                or not _phase_allowed(context, attempt, phase)):
            raise StateError('Probe creation plans require the corresponding mutation phase')
        record = self._retained(attempt, phase)
        if context.execution_mode != 'controlled-test':
            publication = self.mission_image_binding()
            image = publication['origin'].removeprefix('https://')+'/'+publication['repository']+'@'+publication['manifest_digest']
            if selection.get('image') != image:
                raise StateError('Probe interpreter image differs from the directly verified CI image')
        plan = prepare_probe_actor(record['context'], **selection)
        record.setdefault('probe_plans', []).append(plan)
        return plan

    def require_probe_plan(self, plan, context):
        from devsecops.runtime.probe_observations import validate_probe_plan
        self._admit(context)
        matches = [(key, record) for key, record in self._records.items()
                   if any(item is plan for item in record.get('probe_plans', []))]
        if len(matches) != 1:
            raise StateError('Original session-prepared probe plan is required')
        (attempt, phase), record = matches[0]
        if not _phase_allowed(context, attempt, phase):
            raise StateError('Probe plan is outside its selected mutation phase')
        self._retained(attempt, phase)
        validate_probe_plan(plan, record['context'])
        return deepcopy(record['context'])

    def start_probes(self, context, *, attempt, phase, plans):
        """Observe already-created actors under the explicit effectful phase."""
        from devsecops.runtime.probe_observations import start_probe_observation
        authority = self._admit(context)
        if not _phase_allowed(context, attempt, phase) or not 1 <= len(plans) <= 4:
            raise StateError('Probe observation is outside its canonical phase')
        record = self._retained(attempt, phase)
        if record['bootstrap'] is not authority or record.get('probes'):
            raise StateError('Probe actors were already observed or their bootstrap changed')
        # Each observation retains exact independent config/lock/intent inputs.
        # There is no arbitrary client, command or post-hoc receipt attachment.
        record['probes'] = []
        for plan in plans:
            self.require_probe_plan(plan, context)
            transport = self._transport(context, record['context'], record['authority'])
            record['probes'].append(start_probe_observation(record['context'],
                authority=record['authority'], plan=plan, deadline=context.deadline, transport=transport))
        return len(record['probes'])

    def finish_probes(self, context):
        return self._finish_probes(context, attempt=context.phase['attempt'], phase=_PHASE.get(context.phase['operation']))

    def finish_experiment_probes(self, context, *, phase):
        if not _phase_allowed(context, 'f0-network-policy', phase):
            raise StateError('Independent F0 probes are outside their explicit mutation phase')
        return self._finish_probes(context, attempt='f0-network-policy', phase=phase)

    def _finish_probes(self, context, *, attempt, phase):
        from devsecops.runtime.probe_observations import finish_probe_observation, probe_observation_artifacts
        self._admit(context)
        record = self._retained(attempt, phase)
        origins = record.get('probes', [])
        if not origins or record.get('probe_artifacts'):
            raise StateError('Original incomplete probe observations are required')
        directory = context.run_dir/'runtime'/(attempt+'-'+phase+'-probes-'+str(uuid4()))
        artifacts, sources = {}, []
        with _new_directory(directory) as fd:
            for origin in origins:
                transport = self._transport(context, record['context'], record['authority'])
                finish_probe_observation(origin, deadline=context.deadline, transport=transport)
                source, receipts = probe_observation_artifacts(origin)
                sources.append(source)
                for name, receipt in receipts.items():
                    if name in artifacts:
                        raise StateError('Probe case ID was observed more than once')
                    filename = name.removeprefix('http:')+'.json'
                    _write(fd, filename, receipt)
                    path = directory/filename
                    record['pins'][path] = _pin(path)
                    artifacts[name] = path
            _write(fd, 'sources.json', {'sources': sources})
        record['pins'][directory/'sources.json'] = _pin(directory/'sources.json')
        record['probe_artifacts'], record['probe_sources'] = artifacts, sources
        return dict(artifacts)

    def probe_inputs(self, attempt):
        """Current exact observed sources plus the fourteen retained receipts."""
        sources, artifacts = [], {}
        for phase in ('baseline', 'fault', 'recovery'):
            record = self._retained(attempt, phase)
            if not record.get('probe_artifacts'):
                raise StateError('Original probe phase observations are incomplete')
            for source in record['probe_sources']:
                if any(item['id'] == source['id'] for item in sources):
                    raise StateError('Probe source IDs must be distinct across canonical phases')
                sources.append(deepcopy(source))
            for name, path in record['probe_artifacts'].items():
                if name in artifacts:
                    raise StateError('Probe receipt IDs must be distinct across phases')
                artifacts[name] = (path, _sha(record['pins'][path]))
        if len(artifacts) != 14:
            raise StateError('Canonical probe observation matrix is incomplete')
        return sources, artifacts

    def diagnosis_inputs(self, attempt):
        from .native_phase import NativeDiagnosisSelection
        values = [self._retained(attempt, phase) for phase in ('baseline', 'fault')]
        if any(record.get('bundle', {}).get('stage') != 'measured' for record in values):
            raise StateError('Diagnosis requires both original measured collections')
        b, f = [self._selection(record) for record in values]
        return NativeDiagnosisSelection(b.collection, b.collection_sha256, f.collection, f.collection_sha256)

    def prepare_inventory(self, context):
        """Produce inventory from original probe/cluster/image observations only."""
        from urllib.parse import urlsplit
        from .canonical_bootstrap import require_bootstrap_authority
        from .network_policy_qualification import require_qualified_network_policy_path
        from devsecops.runtime.probe_observations import probe_observation_destinations
        authority = self._admit(context)
        attempt = context.phase['attempt']
        sources, _ = self.probe_inputs(attempt)
        facts = require_bootstrap_authority(authority, self._journal)
        qualification = require_qualified_network_policy_path(self._network_policy_path, runtime=self,
            run_id=self._run, identities=self._identities)
        mission = self.mission_image_binding()
        reference = self.locked_image_binding(context)['reference']
        record = self._retained(attempt, 'recovery')
        targets = record['context']['intent']['targets']
        services = {}
        for phase in ('baseline', 'fault', 'recovery'):
            retained = self._retained(attempt, phase)
            for origin in retained['probes']:
                for observed in probe_observation_destinations(origin):
                    key = observed['target_id']
                    previous = services.get(key)
                    if previous is not None:
                        if any(previous[name] != observed[name] for name in ('uid', 'cluster_ips', 'cluster_domain')):
                            raise StateError('Observed destination changed across canonical phases')
                        previous['observed_before'] = min(previous['observed_before'], observed['observed_before'])
                        previous['observed_after'] = max(previous['observed_after'], observed['observed_after'])
                    else:
                        services[key] = deepcopy(observed)
        if set(services) != {target['id'] for target in targets}:
            raise StateError('Actual Service observations do not cover both canonical targets')
        images, expectations, containers = [], [], {}
        mission_target = None
        for target in targets:
            role = 'mission' if 'mission' in target['roles'] else 'reference' if 'reference-web' in target['roles'] else None
            if role is None:
                raise StateError('Canonical recovery inventory does not support unrelated targets')
            image = mission if role == 'mission' else reference
            manifest = image['manifest_digest'] if role == 'mission' else image['platform_manifest_digest']
            config = image['config_digest']
            mapping = image.get('mapping_receipt_sha256') or image.get('publication_readback', {}).get('subject-manifest.json', {}).get('sha256')
            if mapping is None or target.get('image_mapping_receipt', {}).get('sha256') != mapping:
                raise StateError('Collected intent mapping differs from original verified image content')
            observed = record['bundle']['targets'][target['id']]['after']['kubernetes']['observations']
            unique = {}
            for row in observed['image_snapshot']['containers']:
                key = (row['container_type'], row['container'])
                if row['image_id'].split('://')[-1].split('@')[-1] not in {manifest, config}:
                    raise StateError('Observed runtime image does not match the verified content mapping')
                if key in unique and unique[key] != row['image_id']:
                    raise StateError('Container replicas have different actual image identities')
                unique[key] = row['image_id']
            regular = [name for kind, name in unique if kind == 'regular']
            if len(regular) != 1:
                raise StateError('Canonical inventory needs one unambiguous application container per target')
            containers[role] = regular[0]
            for (kind, name), runtime_id in unique.items():
                images.append({'target_id': target['id'], 'container': name, 'container_type': kind,
                    'runtime_image_id': runtime_id, 'manifest_digest': manifest, 'config_digest': config,
                    'mapping_receipt_sha256': mapping})
                expectations.append({'target_id': target['id'], 'container': name, 'container_type': kind,
                    'platform_manifest_sha256': manifest.removeprefix('sha256:'),
                    'config_sha256': config.removeprefix('sha256:'), 'mapping_receipt_sha256': mapping})
            if role == 'mission':
                mission_target = target
                instance = urlsplit('//'+target['prometheus']['instance'])
                members = [pod for pod in observed['pods'] if instance.hostname in pod.get('pod_ips', [])]
                if len(members) != 1 or instance.port is None:
                    raise StateError('Actual metrics target instance does not identify one observed mission Pod')
                telemetry = {'target_id': target['id'], 'service_uid': services[target['id']]['uid'],
                    'pod_uid': members[0]['uid'], 'job': target['prometheus']['job'], 'instance': target['prometheus']['instance']}
        if mission_target is None:
            raise StateError('Actual mission target is missing')
        inventory = {'schema_version': 1, 'run_id': self._run, 'attempt_id': attempt,
            'environment': self._journal.state['environment_id'], 'identities': deepcopy(self._identities),
            'cluster_uid': facts['kubernetes']['cluster_uid'],
            'kubernetes_origin_sha256': _sha(canonical_bytes(facts['kubernetes']['origin'])),
            'argo_destination_sha256': _sha(canonical_bytes(mission_target['argocd']['destination'])),
            'services': list(services.values()), 'probe_sources': sources, 'images': images,
            'telemetry': telemetry, 'topology': {key: qualification[key] for key in
                ('cni', 'mesh_mode', 'probe_transport', 'qualification_sha256')}}
        validate(inventory, RECOVERY_INVENTORY_SCHEMA)
        directory = context.run_dir/'runtime'/(attempt+'-inventory-'+str(uuid4()))
        with _new_directory(directory) as fd:
            _write(fd, 'inventory.json', inventory)
        path = directory/'inventory.json'
        return {'inventory': (path, _sha(_pin(path))), 'image_expectations': expectations, 'application_containers': containers}

    def prepare_recovery(self, context, *, chain_artifacts, image_expectations,
                         application_containers, metrics_authorization):
        """Produce the exact contract from retained intents and direct probes.

        The chain selection must come from concrete native/delivery producers.
        Its JSON remains data here: image mappings, cluster/F0 inventory and
        delivery need their independent direct origins before full verification.
        No missing inventory or image mapping is invented to satisfy the schema.
        """
        self._admit(context)
        attempt = context.phase['attempt']
        sources, probe_paths = self.probe_inputs(attempt)
        if type(chain_artifacts) is not dict or set(chain_artifacts) != set(BASE_ARTIFACTS):
            raise StateError('Recovery preparation requires the complete explicit chain selection')
        records = {phase: self._retained(attempt, phase) for phase in ('baseline', 'fault', 'recovery')}
        intents = {phase: deepcopy(record['context']['intent']) for phase, record in records.items()}
        targets = intents['recovery']['targets']
        mission = [t for t in targets if 'mission' in t['roles']]
        reference = [t for t in targets if 'reference-web' in t['roles']]
        if len(mission) != 1 or len(reference) != 1 or set(application_containers) != {'mission', 'reference'}:
            raise StateError('Canonical recovery requires exact mission and reference targets')
        delivery = _json(_pin(chain_artifacts['delivery']))
        cases = []
        for name, (path, sha) in sorted(probe_paths.items()):
            receipt = _json(_pin(path, sha))
            request = receipt['request']
            kind = request['case_id'].removeprefix(request['phase']+'-')
            if kind not in KINDS:
                raise StateError('Canonical probe case does not identify its fixed control')
            source = [s for s in sources if {k: s[k] for k in ('namespace', 'name', 'uid')} == request['source']['expected']]
            target = [t for t in targets if (t['kubernetes']['namespace'], t['kubernetes']['service']) ==
                      (request['destination']['namespace'], request['destination']['service'])]
            if len(source) != 1 or len(target) != 1:
                raise StateError('Canonical probe source or target is ambiguous')
            cases.append({'id': request['case_id'], 'phase': request['phase'], 'kind': kind,
                'target_id': target[0]['id'], 'source_id': source[0]['id'],
                'port': request['destination']['port'], 'path': request['destination']['path'],
                'body_sha256': request['expectation']['body_sha256']})
        contract = {'schema_version': 1, 'id': 'canonical-'+attempt+'-recovery', 'run_id': self._run,
            'attempt_id': attempt, 'environment': self._journal.state['environment_id'],
            'identities': deepcopy(self._identities), 'factory_lock': deepcopy(records['recovery']['context']['factory_lock']),
            'intents': intents, 'mission_target': mission[0]['id'], 'reference_target': reference[0]['id'],
            'mission_application_container': application_containers['mission'],
            'reference_application_container': application_containers['reference'],
            'image_expectations': deepcopy(image_expectations), 'delivery_target': delivery['target_id'],
            'delivery_source_position': delivery['source_position'],
            'correction_contract_sha256': _sha(_pin(chain_artifacts['correction_contract'])),
            'probe_cases': cases, 'metrics_authorization': deepcopy(metrics_authorization)}
        _approved(contract)
        inventory = _json(_pin(chain_artifacts['inventory']))
        if sorted(inventory.get('probe_sources', []), key=lambda x: x['id']) != sorted(sources, key=lambda x: x['id']):
            raise StateError('Recovery inventory does not contain the original observed probe sources')
        directory = context.run_dir/'runtime'/(attempt+'-recovery-inputs-'+str(uuid4()))
        with _new_directory(directory) as fd:
            _write(fd, 'contract.json', contract)
        path = directory/'contract.json'
        evidence = dict(chain_artifacts) | {name: value[0] for name, value in probe_paths.items()}
        return self.bind_recovery_inputs(context, contract=path, contract_sha256=_sha(_pin(path)), evidence=evidence)

    def bind_network_policy_path(self, context, origin):
        from .network_policy_qualification import require_qualified_network_policy_path
        self._admit(context)
        if context.phase['operation'] not in {'build-promote', 'reset'} or self._network_policy_path is not None:
            raise StateError('F0 path must be bound once before the incident')
        require_qualified_network_policy_path(origin, runtime=self, run_id=self._run, identities=self._identities)
        self._network_policy_path = origin

    def bind_mission_publication(self, context, publication):
        from .ci_publication import VerifiedCIPublication
        self._admit(context)
        if (type(publication) is not VerifiedCIPublication
                or context.phase['operation'] not in {'build-promote', 'reset', 'deliver', 'probe', 'verify'}
                or (self._mission_publication is None and context.phase['operation'] != 'build-promote')):
            raise StateError('An original verified CI publication is required in its canonical consumer phase')
        binding = publication.describe()
        if binding['run_scope'] != self._identities:
            raise StateError('Mission image publication belongs to another run')
        subject = {name: deepcopy(binding[name]) for name in ('run_scope', 'source_commit', 'architecture',
            'origin', 'repository', 'manifest_digest', 'config_digest', 'policy_sha256', 'trust_policy_sha256',
            'publication_config_sha256', 'base', 'ci')}
        subject['ci'].pop('observed_at', None)
        if self._mission_subject is not None and self._mission_subject != subject:
            raise StateError('Fresh publication verification cannot replace the original CI/image subject')
        self._mission_publication = publication
        self._mission_subject = subject
        return deepcopy(binding)

    def mission_image_binding(self):
        from .runtime_image import RuntimeImageProvenance, require_runtime_image
        from .ci_publication import VerifiedCIPublication
        if type(self._runtime_image) is RuntimeImageProvenance:
            return require_runtime_image(self._runtime_image, run_scope=self._identities,
                                         source_commit=self._mission_subject['source_commit'])
        if type(self._mission_publication) is not VerifiedCIPublication:
            raise StateError('An original fresh verified CI publication is required')
        binding = self._mission_publication.describe()
        if binding['run_scope'] != self._identities:
            raise StateError('Mission image publication scope changed')
        return binding

    def bind_runtime_image(self, context, origin):
        from .runtime_image import RuntimeImageProvenance, require_runtime_image
        self._admit(context)
        if type(origin) is not RuntimeImageProvenance or self._mission_subject is None:
            raise StateError('Fresh runtime image evidence requires the original selected CI publication')
        binding = require_runtime_image(origin, run_scope=self._identities,
                                        source_commit=self._mission_subject['source_commit'])
        subject = {key: deepcopy(binding[key]) for key in self._mission_subject}
        subject['ci'].pop('observed_at', None)
        if subject != self._mission_subject:
            raise StateError('Runtime image observation changed the original CI/source/content identities')
        self._runtime_image = origin
        return binding

    def bind_locked_images(self, context, origin):
        from .locked_images import require_locked_images
        self._admit(context)
        binding = require_locked_images(origin, journal=self._journal, context=context)
        subjects = {role: {key: deepcopy(binding[role][key]) for key in ('repository', 'requested_digest',
            'platform_manifest_digest', 'config_digest', 'architecture', 'required_predicates', 'mapping_receipt_sha256')}
            for role in ('reference', 'mission_base')}
        if self._locked_subjects is not None and self._locked_subjects != subjects:
            raise StateError('Fresh locked image verification cannot change the selected subjects or mappings')
        self._locked_images, self._locked_subjects = origin, subjects
        return binding

    def locked_image_binding(self, context):
        from .locked_images import require_locked_images
        self._admit(context)
        return require_locked_images(self._locked_images, journal=self._journal, context=context)

    def evidence_gap_inputs(self, context):
        """Exact retained D5 inputs, with no mutation or restored origin claim."""
        self._admit(context)
        if context.phase['id'] != 'evidence-gaps' or context.phase['operation'] != 'case':
            raise StateError('Independent evidence-gap inputs are limited to their fixed case')
        baseline = self._selection(self._retained('primary', 'baseline'))
        fault = self._selection(self._retained('primary', 'fault'))
        current_record = self._retained('primary', 'recovery')
        current = self._selection(current_record)
        if baseline.collection is None or fault.collection is None or current.collection is None:
            raise StateError('D5 needs the original completed baseline and recovery collections')
        inputs = {name: (path, _sha(_pin(path))) for name, path in current_record['authority'].items()}
        return MappingProxyType({'baseline': (baseline.collection, baseline.collection_sha256),
            'primary_fault': (fault.collection, fault.collection_sha256),
            'current': (current.collection, current.collection_sha256), 'inputs': MappingProxyType(inputs)})

    def qualified_fault(self, context):
        from .network_policy_qualification import require_qualified_network_policy_path
        self._admit(context)
        if context.phase['operation'] not in {'inject', 'reset', 'build-promote'}:
            raise StateError('Qualified fault selection is outside its mutation phase')
        return require_qualified_network_policy_path(self._network_policy_path, runtime=self,
                                                     run_id=self._run, identities=self._identities)

    def bind_input_producer(self, context, producer):
        from .owned_runtime_inputs import OwnedRuntimeInputCoordinator
        self._admit(context)
        if (type(producer) is not OwnedRuntimeInputCoordinator or producer.runtime is not self
                or producer.journal is not self._journal
                or self._input_producer is not None and self._input_producer is not producer):
            raise StateError('Runtime inputs require the original fixed production composer')
        producer._admit(context)
        self._input_producer = producer

    def qualified_policy_plan(self, context):
        from .owned_runtime_inputs import OwnedRuntimeInputCoordinator
        self._admit(context)
        if type(self._input_producer) is not OwnedRuntimeInputCoordinator:
            raise StateError('Original qualified policy source producer is unavailable')
        return self._input_producer.qualified_policy_plan(context)

    def cni_selection(self, context):
        from .owned_runtime_inputs import OwnedRuntimeInputCoordinator
        self._admit(context)
        if type(self._input_producer) is not OwnedRuntimeInputCoordinator:
            raise StateError('Original selected CNI descriptor producer is unavailable')
        return self._input_producer.require_cni_selection(context)

    def bind_recovery_inputs(self, context, *, contract: Path, contract_sha256: str, evidence: dict):
        """Freeze data for native/offline validation; does not grant its origins.

        Missing live image/F0/delivery/correction capabilities remain missing in
        the fixed provenance adapter, even if every supplied report says pass.
        """
        self._admit(context)
        attempt = context.phase['attempt']
        if context.phase['operation'] not in {'probe', 'native', 'verify'} or attempt in self._recovery:
            raise StateError('Recovery inputs are already bound or outside their phase')
        approved_raw = _pin(contract, contract_sha256)
        approved = _json(approved_raw)
        validate(approved, RECOVERY_CONTRACT_SCHEMA)
        if (approved['run_id'] != self._run or approved['attempt_id'] != attempt
                or approved['identities'] != self._identities
                or approved['environment'] != self._journal.state['environment_id']):
            raise StateError('Recovery contract scope differs from this runtime session')
        expected = set(BASE_ARTIFACTS) | {'http:'+case['id'] for case in approved['probe_cases']}
        if type(evidence) is not dict or set(evidence) != expected:
            raise StateError('Recovery requires the exact complete explicit evidence selection')
        artifacts = {'contract': approved_raw}
        pins = {contract: approved_raw}
        for phase in ('baseline', 'fault', 'recovery'):
            record = self._retained(attempt, phase)
            if record.get('bundle', {}).get('stage') != 'measured':
                raise StateError('Recovery requires original measured collections')
            if approved['intents'][phase] != record['context']['intent']:
                raise StateError('Recovery contract intent differs from the collected intent')
            artifacts[phase] = record['pins'][record['paths']['collection']]
        for name, path in evidence.items():
            raw = _pin(path)
            artifacts[name] = raw
            pins[path] = raw
        origin = RuntimeObservationOrigin(_ISSUER)
        _ORIGINS[origin] = {'session': self, 'attempt': attempt, 'artifacts': artifacts, 'pid': os.getpid()}
        self._recovery[attempt] = {'contract': contract, 'evidence': dict(evidence), 'pins': pins, 'origin': origin}
        return self.recovery_inputs(attempt)

    def recovery_inputs(self, attempt):
        from .native_phase import NativeRecoverySelection
        diagnosis = self.diagnosis_inputs(attempt)
        recovered = self._selection(self._retained(attempt, 'recovery'))
        binding = self._recovery.get(attempt)
        if binding is None or recovered.collection is None:
            raise StateError('Recovery selection has not been bound in this process')
        for path, raw in binding['pins'].items():
            if _pin(path) != raw:
                raise StateError('Recovery selection changed after binding')
        return NativeRecoverySelection(diagnosis.baseline, diagnosis.baseline_sha256,
            diagnosis.fault, diagnosis.fault_sha256, recovered.collection, recovered.collection_sha256,
            binding['contract'], _sha(binding['pins'][binding['contract']]),
            {name: (path, _sha(binding['pins'][path])) for name, path in binding['evidence'].items()})

    def verify(self, context, *, native_execution_origin=None):
        self._admit(context)
        if context.phase['operation'] != 'verify':
            raise StateError('Recovery verification requires the fixed verify phase')
        attempt = context.phase['attempt']
        selection = self.recovery_inputs(attempt)
        if context.execution_mode != 'controlled-test':
            from .correction_phase import require_validated_correction
            correction = require_validated_correction(self._driver, context, attempt=attempt)
            if native_execution_origin is not correction['origin']:
                raise StateError('Host recovery must retain the original native correction origin')
            issued = _ORIGINS[self._recovery[attempt]['origin']]
            issued['correction'], issued['native_execution_origin'] = correction, native_execution_origin
            issued['phase_context'] = context
        result = verify_recovery(selection.baseline, selection.fault, selection.recovered,
            contract=selection.contract, evidence={name: value[0] for name, value in selection.evidence.items()},
            provenance='canonical-runtime-v1', runtime_origin=self._recovery[attempt]['origin'],
            native_execution_origin=native_execution_origin)
        if context.execution_mode == 'operator':
            issued['verified_report'] = (result, canonical_bytes(result), context)
        return result

    def dispatch(self, context):
        """Fixed canonical phase mapping; only measured results close phases."""
        from .lifecycle import AssertionResult, PhaseOutcome
        self._admit(context)
        operation, attempt = context.phase['operation'], context.phase['attempt']
        if operation in _PHASE:
            phase = _PHASE[operation]
            if (attempt, phase) not in self._records:
                return PhaseOutcome('incomplete', 'runtime-inputs-not-prepared-by-mutation-phase',
                                    mutation_effect='not_applied')
            selection = self.finish(context)
            record = self._retained(attempt, phase)
            artifacts = [selection.context, selection.pending, selection.collection]
            if not record.get('probes'):
                return PhaseOutcome('incomplete', 'authorized-runtime-probe-actors-unavailable',
                                    tuple(p for p in artifacts if p), mutation_effect='not_applied')
            artifacts.extend(self.finish_probes(context).values())
            bundle = record['bundle']
            complete = bundle['stage'] == 'measured'
            if complete and phase == 'recovery' and context.execution_mode == 'operator':
                from .owned_runtime_inputs import OwnedRuntimeInputCoordinator
                if type(self._input_producer) is not OwnedRuntimeInputCoordinator:
                    raise StateError('Original recovery input composer is unavailable')
                selected = self._input_producer.complete_recovery_inputs(context)
                artifacts.append(selected.contract)
                artifacts.extend(path for path, _ in selected.evidence.values())
            # Collection completion enables native inspection. It is not D1/D2
            # success: the host verifier checks their full chain independently.
            return PhaseOutcome('completed' if complete else 'incomplete',
                'runtime-phase-measured' if complete else 'runtime-phase-incomplete',
                tuple(dict.fromkeys(p for p in artifacts if p)), mutation_effect='not_applied')
        if operation != 'verify':
            raise StateError('Unsupported canonical runtime operation')
        if context.execution_mode == 'operator':
            from .owned_runtime_inputs import OwnedRuntimeInputCoordinator
            if type(self._input_producer) is not OwnedRuntimeInputCoordinator:
                raise StateError('Original runtime input producer is unavailable')
            self._input_producer.refresh_images(context)
        from .native_factory_session import NativeFactorySession
        native = getattr(self._driver, '_native_session', None)
        if type(native) is not NativeFactorySession:
            return PhaseOutcome('incomplete', 'original-native-factory-session-unavailable', mutation_effect='not_applied')
        result = self.verify(context, native_execution_origin=native.origin(attempt=attempt))
        from .runtime_acceptance import assertions_for_verification
        additional, acceptance, original_paths = assertions_for_verification(self, context, result)
        directory = context.run_dir/'runtime'/(attempt+'-verification-'+str(uuid4()))
        with _new_directory(directory) as fd:
            _write(fd, 'recovery.json', result)
            _write(fd, 'acceptance.json', acceptance)
        complete = result['execution_status'] == 'complete' and result['recovery'] == 'verified'
        assertions = ()
        if complete:
            # These are exactly the runtime/validated-chain conditions checked
            # by the direct verifier. CI trust, installed model reports and the
            # separate causal experiment remain their own producer assertions.
            selected = {
                'D1': ('reference-web-http', 'controller-expected-revision', 'mission-ready-and-healthy',
                       'fresh-target-up-and-mission-metrics', 'unauthorized-probes-denied-with-listening-controls'),
                'D2': ('baseline-proven-before-fault', 'mission-and-reference-health-retained',
                       'minimal-default-deny-preserving-patch', 'patched-tree-equals-delivered-tree',
                       'expected-deployment-and-controller-revision', 'both-image-identities-unchanged',
                       'fresh-scrapes-after-repair', 'unauthorized-probes-still-denied', 'independent-recovery-assertions'),
            }
            assertions = tuple(AssertionResult(case, assertion, 'pass') for case, names in selected.items() for assertion in names)
        return PhaseOutcome('completed' if complete else 'incomplete',
            'runtime-recovery-verified' if complete else 'runtime-recovery-unverified',
            (directory/'recovery.json', directory/'acceptance.json', *original_paths),
            (*assertions, *additional), mutation_effect='not_applied')


def verify_runtime_observation_origin(origin, *, artifacts):
    """Authenticate collection bytes only; never infer the other eight claims."""
    if type(origin) is not RuntimeObservationOrigin or origin not in _ORIGINS:
        raise StateError('An original directly issued runtime origin is required')
    issued = _ORIGINS[origin]
    if issued['pid'] != os.getpid() or dict(artifacts) != issued['artifacts']:
        raise StateError('Runtime origin process or frozen artifacts differ')
    session, attempt = issued['session'], issued['attempt']
    session.recovery_inputs(attempt)
    records = [session._retained(attempt, phase) for phase in ('baseline', 'fault', 'recovery')]
    direct = all(r['mode'] == 'operator' and r['bundle']['execution_mode'] == 'direct' for r in records)
    assurances = ['collection-origin'] if direct else []
    if direct:
        from .canonical_bootstrap import require_bootstrap_authority
        facts = require_bootstrap_authority(records[0]['bootstrap'], session._journal)
        if any(record['bootstrap'] is not records[0]['bootstrap'] for record in records):
            raise StateError('Runtime collections have different bootstrap origins')
        inventory, approved = _json(artifacts['inventory']), _json(artifacts['contract'])
        if getattr(session, '_input_producer', None) is not None and issued.get('phase_context') is not None:
            from .owned_runtime_inputs import OwnedRuntimeInputCoordinator
            if type(session._input_producer) is not OwnedRuntimeInputCoordinator:
                raise StateError('Recovery input producer type changed')
            assurances.extend(session._input_producer.verify_recovery_origins(issued['phase_context'],
                attempt=attempt, artifacts=artifacts, intents=approved['intents']))
        kube = facts['kubernetes']
        from .owned_runtime_transport import require_runtime_read_history
        current_context = issued.get('phase_context')
        if current_context is None:
            raise StateError('Current verification context is required for original acquisition history')
        for record in records:
            if len(record.get('transports', [])) != 2:
                raise StateError('Original before/after owned runtime read transports are unavailable')
            for transport in record['transports']:
                observed = require_runtime_read_history(transport, record['context'], current_context=current_context)
                if observed['actual_origin'] != kube['origin'] or observed['cluster_uid'] != kube['cluster_uid']:
                    raise StateError('Original runtime transport differs from its actual bootstrap cluster')
        argo = kube.get('argo_destination')
        if argo is not None:
            for intent in approved['intents'].values():
                for target in intent['targets']:
                    if (target['argocd']['destination']['server'] != argo['server']
                            or target['argocd']['destination']['namespace'] not in kube['owned_namespaces']):
                        raise StateError('Runtime Argo destination differs from the owned observed cluster')
            if (inventory['cluster_uid'] != kube['cluster_uid']
                    or inventory['kubernetes_origin_sha256'] != _sha(canonical_bytes(kube['origin']))):
                raise StateError('Runtime inventory differs from the actual bootstrap cluster')
            assurances.append('cluster-destination')
        native_origin = issued.get('native_execution_origin')
        if native_origin is not None:
            from .native_provenance import verify_native_execution_origin
            native = verify_native_execution_origin(native_origin, artifacts=artifacts)
            if native['verified'] and native['execution_mode'] == 'direct':
                assurances.append('native-execution')
        correction = issued.get('correction')
        if correction is not None and correction['execution_mode'] == 'operator':
            for name, (path, sha) in correction['artifacts'].items():
                if artifacts.get(name) != _pin(path, sha):
                    raise StateError('Recovery correction artifacts differ from original successful validation')
            assurances.append('correction-execution')
        if getattr(session, '_mission_publication', None) is not None:
            from devsecops.runtime.probe_observations import probe_observation_artifacts, verify_probe_read_origins
            mission = session.mission_image_binding()
            all_receipts = {}
            for record in records:
                for probe_origin in record.get('probes', []):
                    verify_probe_read_origins(probe_origin, current_context=current_context)
                    source, receipts = probe_observation_artifacts(probe_origin)
                    image_digest = source['image_id'].split('://')[-1].split('@')[-1]
                    if image_digest not in {mission['manifest_digest'], mission['config_digest']}:
                        raise StateError('Observed probe executable image differs from the verified CI image')
                    if set(all_receipts) & set(receipts):
                        raise StateError('Duplicate probe receipt origin')
                    all_receipts.update(receipts)
            wanted = {name for name in artifacts if name.startswith('http:')}
            if wanted == set(all_receipts) and all(canonical_bytes(value) == artifacts[name] for name, value in all_receipts.items()):
                assurances.append('probe-origin-and-source')
            if getattr(session, '_locked_images', None) is not None and issued.get('phase_context') is not None:
                locked = session.locked_image_binding(issued['phase_context'])
                reference = locked['reference']
                lock = approved['factory_lock']['images']['reference_web']
                if (reference['repository'] != lock['repository']
                        or reference['requested_digest'] != 'sha256:'+lock['manifest_sha256']
                        or reference['platform_manifest_digest'] != 'sha256:'+lock['platform_manifest_sha256']
                        or reference['config_digest'] != 'sha256:'+lock['config_sha256']
                        or reference['architecture'] != lock['architecture']
                        or set(reference['required_predicates']) != set(lock['required_predicates'])):
                    raise StateError('Reference runtime mapping differs from the independently verified locked image')
                expected = {approved['mission_target']: (mission['manifest_digest'], mission['config_digest']),
                            approved['reference_target']: (reference['platform_manifest_digest'], reference['config_digest'])}
                for row in inventory['images']:
                    manifest, config = expected[row['target_id']]
                    if (row['manifest_digest'] != manifest or row['config_digest'] != config
                            or row['runtime_image_id'].split('://')[-1].split('@')[-1] not in {manifest, config}):
                        raise StateError('Actual container image does not map to the verified OCI content')
                    if row['target_id'] == approved['reference_target'] and row['mapping_receipt_sha256'] != reference['mapping_receipt_sha256']:
                        raise StateError('Reference mapping artifact differs from its original verified content')
                    mission_mapping = mission.get('mapping_receipt_sha256') or mission.get('publication_readback', {}).get('subject-manifest.json', {}).get('sha256')
                    if row['target_id'] == approved['mission_target'] and row['mapping_receipt_sha256'] != mission_mapping:
                        raise StateError('Mission mapping artifact differs from its original verified content')
                assurances.append('image-content-mapping')
    path_origin = getattr(session, '_network_policy_path', None)
    if direct and path_origin is not None:
        from .network_policy_qualification import require_qualified_network_policy_path
        qualified = require_qualified_network_policy_path(path_origin, runtime=session,
            run_id=session._run, identities=session._identities)
        inventory = _json(artifacts['inventory'])
        expected = {k: qualified[k] for k in ('cni', 'mesh_mode', 'probe_transport', 'qualification_sha256')}
        if (inventory.get('topology') != expected or inventory.get('cluster_uid') != qualified['cluster_uid']):
            raise StateError('Recovery topology differs from the original independent F0 experiment')
        for name, key in (('rendered_before', 'fault_spec_sha256'), ('rendered_after', 'baseline_spec_sha256')):
            selected = [r for r in _json(artifacts[name]).get('resources', []) if r['id'] == qualified['resource_id']]
            if len(selected) != 1 or _sha(canonical_bytes(selected[0]['body']['spec'])) != qualified[key]:
                raise StateError('Recovery policy differs from the independent qualified selector fault')
        assurances.append('network-policy-path')
    hashes = {name: _sha(raw) for name, raw in artifacts.items()}
    return OriginVerification(_sha(canonical_bytes(hashes)), MappingProxyType(hashes),
                              tuple(assurances))
