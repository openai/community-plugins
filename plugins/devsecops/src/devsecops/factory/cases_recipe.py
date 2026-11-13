"""Explicit immutable selections for actual canonical case verification."""
from pathlib import Path
from jsonschema import Draft202012Validator
from .contracts import DIGEST, PINNED_FILE, ENV_REF, ABS_PATH, NAME, array, document, object_schema
from .state import IDENTITY_KEYS, _json

REGISTRY_AUTH = object_schema({'token_env': ENV_REF, 'basic_username_env': ENV_REF,
                              'basic_password_env': ENV_REF, 'token_path': ABS_PATH,
                              'token_service': {'type': 'string', 'minLength': 1, 'maxLength': 256},
                              'ca_bundle': ABS_PATH}, [])
IMAGE_CONTROL = object_schema({
    'trust_identity_basis': {'const': 'publisher-policy-sha256-v1'},
    'publisher_policy': PINNED_FILE, 'public_key': PINNED_FILE,
    'publisher_package': {'oneOf': [PINNED_FILE, {'type': 'null'}]},
    'image_policy': PINNED_FILE, 'registry_auth': REGISTRY_AUTH, 'blob_download_policy': PINNED_FILE,
    'attestation_size_policy': PINNED_FILE,
}, ['trust_identity_basis', 'publisher_policy', 'public_key', 'publisher_package', 'image_policy', 'registry_auth'])
PLATFORM = object_schema({
    'namespace': NAME, 'source_name': NAME, 'root_name': NAME, 'dependency_name': NAME, 'extension_name': NAME,
    'repository_path': {'type': 'string', 'pattern': '^platform/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,5}\\.ya?ml$'},
    'baseline_file': PINNED_FILE,
    'core_resources': array(object_schema({'apiVersion': {'enum': ['apps/v1', 'helm.toolkit.fluxcd.io/v2', 'argoproj.io/v1alpha1']},
                                          'kind': {'enum': ['Deployment', 'HelmRelease', 'Application']},
                                          'namespace': NAME, 'name': NAME}), minimum=3, maximum=129),
    'service': object_schema({'name': NAME, 'port': {'type': 'integer', 'minimum': 1, 'maximum': 65535},
                             'health_path': {'enum': ['/health', '/healthz']},
                             'expected': object_schema({'status': {'const': 'ok'}})}),
    'poll_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 10},
    'max_observations': {'type': 'integer', 'minimum': 1, 'maximum': 120},
})
CASES_SCHEMA = document('owned-cases-recipe', {
    'schema_version': {'type': 'integer', 'const': 1}, 'kind': {'const': 'owned-cases-recipe'},
    'environment_id': {'type': 'string', 'pattern': '^[a-z][a-z0-9-]{0,31}$'},
    'run_scope': object_schema({name: DIGEST for name in sorted(IDENTITY_KEYS)}),
    'plugin_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
    'plugin_version': {'type': 'string', 'minLength': 1, 'maxLength': 128},
    'supply_chain': object_schema({'reference_web': IMAGE_CONTROL, 'mission_base': IMAGE_CONTROL,
                                    'cosign': PINNED_FILE, 'untrusted_public_key': PINNED_FILE,
                                    'max_verification_age_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 3600}}),
    'platform_dependency': PLATFORM,
    'evidence_gaps': object_schema({'max_native_seconds': {'type': 'integer', 'minimum': 10, 'maximum': 240}}),
})


def cases_configuration(raw):
    if type(raw) is not bytes or len(raw) > 64000:
        raise ValueError('Bounded canonical case selection required')
    value = _json(raw)
    if not Draft202012Validator(CASES_SCHEMA).is_valid(value):
        raise ValueError('Invalid closed canonical case recipe')
    def paths(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ('path', 'ca_bundle', 'token_path') and '..' in Path(child).parts:
                    raise ValueError('Case selection paths cannot traverse parents')
                paths(child)
        elif isinstance(item, list):
            for child in item:
                paths(child)
    paths(value)
    for name in ('reference_web', 'mission_base'):
        auth = value['supply_chain'][name]['registry_auth']
        if ('token_env' in auth and any(key.startswith('basic_') for key in auth)
                or ('basic_username_env' in auth) != ('basic_password_env' in auth)):
            raise ValueError('Choose one complete explicit registry credential-reference mode')
    platform = value['platform_dependency']
    if (len({platform[key] for key in ('root_name', 'dependency_name', 'extension_name')}) != 3
            or len(platform['dependency_name']) > 51
            or any(part in ('.', '..', '.git') for part in platform['repository_path'].split('/'))
            or {item['kind'] for item in platform['core_resources']} != {'Deployment', 'HelmRelease', 'Application'}):
        raise ValueError('D3 requires distinct optional resources and explicit Flux/BigBang/Argo core observations')
    kinds = {'Deployment': 'apps/v1', 'HelmRelease': 'helm.toolkit.fluxcd.io/v2', 'Application': 'argoproj.io/v1alpha1'}
    if any(item['apiVersion'] != kinds[item['kind']] for item in platform['core_resources']):
        raise ValueError('D3 core resource API version differs')
    return value


def bind_cases_recipe(raw, *, identities, environment_id, plugin_id, plugin_version):
    value = cases_configuration(raw)
    if (value['run_scope'] != dict(identities) or value['environment_id'] != environment_id
            or value['plugin_id'] != plugin_id or value['plugin_version'] != plugin_version):
        raise ValueError('Canonical case recipe differs from the active run')
    return value
