"""Closed operator-selected Zot recipe; selection is not live qualification."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()


def _shape(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('Unsupported closed registry recipe fields')


def _match(pattern, value):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError('Unsupported registry recipe identity')


def validate(value):
    v2 = value.get('schema_version') == 2 if isinstance(value, dict) else False
    _shape(value, 'schema_version kind version environment origin repository remote_directory private_state_directory '
           'image tool_image transport materials credentials' + (' cleanup' if v2 else ''))
    if (type(value['schema_version']) is not int or value['schema_version'] not in (1, 2)
            or value['kind'] != ('owned-zot-v2' if v2 else 'owned-zot-v1') or value['version'] != '2.1.22'
            or v2 and value['cleanup'] != 'remove-owned-private-material'):
        raise ValueError('Unsupported registry recipe version')
    _match(r'[a-z][a-z0-9-]{0,62}', value['environment'])
    if value['origin'] != 'https://registry.'+value['environment']+'.test:8443':
        raise ValueError('Registry origin must use the fixed private service name')
    if value['repository'] != 'devsecops/'+value['environment']:
        raise ValueError('Registry repository must be exact and environment-owned')
    _match(r'/home/[A-Za-z0-9_-]+/\.local/share/devsecops/registries/'+value['environment'], value['remote_directory'])
    _match(r'/[^\x00-\x20,]{1,4000}', value['private_state_directory'])
    if '..' in Path(value['private_state_directory']).parts:
        raise ValueError('Registry state path must be canonical')
    _shape(value['image'], 'reference image_id entrypoint')
    _match(r'ghcr\.io/project-zot/zot-minimal@sha256:[a-f0-9]{64}', value['image']['reference'])
    _match(r'sha256:[a-f0-9]{64}', value['image']['image_id'])
    if value['image']['entrypoint'] != '/usr/local/bin/zot-linux-amd64-minimal':
        raise ValueError('Unsupported fixed Zot release entry point')
    _match(r'sha256:[a-f0-9]{64}', value['tool_image'])
    transport = value['transport']
    _shape(transport, 'config known_hosts certificate identity proxy identity_agent host_key_sha256 verification_basis agent_protocol')
    if (transport['verification_basis'] not in ('operator-verified', 'authenticated-control-plane')
            or transport['agent_protocol'] not in ('native', 'certificate-view-v1')):
        raise ValueError('Registry transport needs separately verified destination and issued identity')
    _match(r'[a-f0-9]{64}', transport['host_key_sha256'])
    _match(r'/[^\x00-\x20]{1,4000}', transport['identity_agent'])
    _shape(value['materials'], 'ca certificate private_key htpasswd')
    for item in [transport[name] for name in ('config', 'known_hosts', 'certificate', 'identity', 'proxy')]+list(value['materials'].values()):
        _shape(item, 'path sha256')
        _match(r'/[^\x00-\x20]{1,4000}', item['path'])
        _match(r'[a-f0-9]{64}', item['sha256'])
    _shape(value['credentials'], 'operator reader')
    for actor in value['credentials'].values():
        _shape(actor, 'username password_env')
        _match(r'[a-z][a-z0-9_-]{0,63}', actor['username'])
        _match(r'[A-Z][A-Z0-9_]{0,127}', actor['password_env'])
    if (value['credentials']['operator']['username'] == value['credentials']['reader']['username']
            or value['credentials']['operator']['password_env'] == value['credentials']['reader']['password_env']):
        raise ValueError('Registry writer and reader identities must be distinct')
    return deepcopy(value)


def configuration(recipe):
    """Generate fixed Zot settings; config files cannot select extra extensions."""
    value = validate(recipe)
    policies = [{'users': [value['credentials']['operator']['username']], 'actions': ['read', 'create', 'update']},
                {'users': [value['credentials']['reader']['username']], 'actions': ['read']}]
    return {'distSpecVersion': '1.1.0', 'storage': {'rootDirectory': '/data', 'gc': False},
        'http': {'address': '0.0.0.0', 'port': '8443',
            'tls': {'cert': '/config/certificate.pem', 'key': '/config/private-key.pem'},
            'auth': {'htpasswd': {'path': '/config/htpasswd'}},
            'accessControl': {'repositories': {name: {'policies': policies, 'defaultPolicy': [], 'anonymousPolicy': []}
                for name in (value['repository'], value['repository']+'/qualification')}}},
        'log': {'level': 'error'}}


def binding(recipe, *, run_id, identities, package_modules):
    validate(recipe)
    _match(r'[a-f0-9-]{36}', run_id)
    return hashlib.sha256(canonical({'recipe': recipe, 'run_id': run_id,
        'identities': identities, 'package_modules': package_modules})).hexdigest()


def hostname(recipe):
    return urlsplit(validate(recipe)['origin']).hostname
