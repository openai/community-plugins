"""Owned loopback TLS + real native Git only. No live GitLab qualification."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import os
import ssl
import subprocess
import threading
import time

import pytest

from devsecops.factory.git_receive_http import Budget, GitReceiveClient, GitReceiveError, Limits, validate_status
from devsecops.factory.git_request_framing import receive_advertisement
from tests.test_argocd import ca  # noqa: F401
from tests.native_git import git_exec_path, selected_git

GIT_TOOL = selected_git()
GIT = GIT_TOOL.path
USER = 'LOCAL-EXPERIMENT-USERNAME-CANARY'
PASSWORD = 'LOCAL-EXPERIMENT-PASSWORD-CANARY'


def native(root, *args, stdin=None):
    GIT_TOOL.verify()
    env = {'PATH': '', 'HOME': str(root), 'LANG': 'C', 'LC_ALL': 'C',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_EXEC_PATH': str(git_exec_path()),
        'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1'}
    result = subprocess.run([str(GIT), '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
        *args], cwd=root, env=env, input=stdin, capture_output=True, timeout=10)
    assert result.returncode == 0, 'Controlled native fixture setup/read failed'
    GIT_TOOL.verify()
    return result.stdout


@pytest.fixture
def local(tmp_path, ca):  # noqa: F811
    root = tmp_path.resolve()
    repo, hosted = root / 'source', root / 'hosted'
    repo.mkdir()
    hosted.mkdir()
    native(repo, 'init', '-q', '--initial-branch=main')
    native(repo, 'config', 'user.name', 'Local Test')
    native(repo, 'config', 'user.email', 'test@example.invalid')
    (repo / 'values.yaml').write_text('image: before\n')
    (repo / 'sibling.txt').write_text('unchanged sibling\n')
    (repo / 'untouched/deep').mkdir(parents=True)
    (repo / 'untouched/deep/values.txt').write_text('unselected nested tree\n')
    native(repo, 'add', '.')
    native(repo, 'commit', '-qm', 'base')
    base = native(repo, 'rev-parse', 'HEAD').decode().strip()
    native(hosted, 'clone', '--bare', '--no-hardlinks', str(repo), 'repo.git')
    (repo / 'values.yaml').write_text('image: after\n')
    native(repo, 'add', 'values.yaml')
    native(repo, 'commit', '-qm', 'one image change')
    new = native(repo, 'rev-parse', 'HEAD').decode().strip()
    expected = {line.split()[0].decode() for line in native(repo, 'rev-list', '--objects', new, '^' + base).splitlines()}
    state = {'calls': [], 'fault': None, 'backend_calls': 0, 'workers': [],
        'base': base, 'new': new, 'hosted': hosted, 'repo': repo, 'branch': 'refs/heads/experiment/one'}
    wanted_auth = 'Basic ' + base64.b64encode((USER + ':' + PASSWORD).encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def do_GET(self):
            self.respond('GET')

        def do_POST(self):
            self.respond('POST')

        def respond(self, method):
            state['calls'].append({'method': method, 'path': self.path,
                'authorized': self.headers.get('Authorization') == wanted_auth})
            denied = self.headers.get('Authorization') != wanted_auth or state['fault'] == 'unauthorized-' + method.lower()
            if denied:
                self.send_response(401)
                self.send_header('Content-Length', '0')
                self.send_header('Connection', 'close')
                self.end_headers()
                return
            if method == 'POST' and state['fault'] == 'race':
                native(hosted, '--git-dir=repo.git', 'update-ref', state['branch'], base)
            if state['fault'] == 'delay-discovery' and method == 'GET':
                time.sleep(.25)
            if state['fault'] == 'disconnect-upload' and method == 'POST':
                self.connection.close()
                return
            length = int(self.headers.get('Content-Length', '0'))
            self.connection.settimeout(3)
            body = self.rfile.read(length) if length else b''
            if method == 'POST':
                state['request_body'] = body
            path, _, query = self.path.partition('?')
            env = {'PATH': '', 'HOME': str(root), 'LANG': 'C', 'LC_ALL': 'C',
                'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                'GIT_EXEC_PATH': str(git_exec_path()), 'GIT_PROJECT_ROOT': str(hosted),
                'GIT_HTTP_EXPORT_ALL': '1', 'REMOTE_USER': 'local-fixture', 'REQUEST_METHOD': method,
                'PATH_INFO': path, 'QUERY_STRING': query, 'CONTENT_LENGTH': str(len(body)),
                'CONTENT_TYPE': self.headers.get('Content-Type', ''), 'SERVER_PROTOCOL': 'HTTP/1.1'}
            GIT_TOOL.verify()
            response = subprocess.run([str(GIT), '-c', 'core.hooksPath=/dev/null', 'http-backend'],
                cwd=hosted, env=env, input=body, capture_output=True, timeout=10)
            GIT_TOOL.verify()
            state['backend_calls'] += 1
            header, sep, payload = response.stdout.partition(b'\r\n\r\n')
            if response.returncode or not sep:
                state['backend_error'] = True
                self.send_error(500)
                return
            content_type = next(line.split(b':', 1)[1].strip().decode() for line in header.split(b'\r\n')
                if line.lower().startswith(b'content-type:'))
            if method == 'POST':
                state['status_raw'] = payload
            if state['fault'] == 'disconnect-status' and method == 'POST':
                self.connection.close()
                return
            if state['fault'] == 'delay-status' and method == 'POST':
                time.sleep(1.5)
            if state['fault'] == 'duplicate-status' and method == 'POST':
                payload += payload
            if state['fault'] == 'truncate-status' and method == 'POST':
                payload = payload[:-5]
            if state['fault'] == 'wrong-ref-status' and method == 'POST':
                payload = payload.replace(b'experiment/one', b'experiment/two')
            redirect = state['fault'] == 'redirect-' + method.lower()
            unauthorized = state['fault'] == 'unauthorized-' + method.lower()
            self.send_response(302 if redirect else 401 if unauthorized else 200)
            if redirect:
                self.send_header('Location', 'https://127.0.0.1:1/credential-sink')
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Connection', 'close')
            self.send_header('X-Remote-Canary', PASSWORD)
            self.end_headers()
            try:
                self.wfile.write(payload)
                self.wfile.flush()
            except (OSError, ssl.SSLError):
                pass

    class Server(ThreadingHTTPServer):
        daemon_threads = False
        def handle_error(self, *_):
            pass

    httpd = Server(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ca[0]), str(ca[1]))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=.01))
    thread.start()
    state['args'] = {'git': GIT, 'git_sha256': GIT_TOOL.sha256,
        'repository': repo, 'new': new, 'branch': state['branch'], 'expected_objects': expected,
        'port': httpd.server_port, 'ca': ca[0], 'username': USER, 'password': PASSWORD,
        'output': root / 'experiment'}
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def tip(local):
    refs = native(local['hosted'], '--git-dir=repo.git', 'for-each-ref', '--format=%(refname) %(objectname)').decode()
    return dict(line.split() for line in refs.splitlines()).get(local['branch'])


def body_from_native(local, advertisement):
    GIT_TOOL.verify()
    refs = receive_advertisement(advertisement,base_ref='refs/heads/main',
                                base_commit=local['base'],target_ref=local['branch'])
    env = {'PATH':'','HOME':str(local['repo']),'LANG':'C','LC_ALL':'C',
        'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,'GIT_EXEC_PATH':str(git_exec_path()),
        'GIT_TERMINAL_PROMPT':'0','GIT_NO_REPLACE_OBJECTS':'1','GIT_NO_LAZY_FETCH':'1'}
    # Actual fixed native send-pack; only its emitted request is consumed here.
    process = subprocess.Popen([str(GIT),'-c','core.hooksPath=/dev/null','send-pack',
        '--stateless-rpc','--no-thin','--force-with-lease='+local['branch']+':',
        'fixture-only',local['new']+':'+local['branch']],cwd=local['repo'],env=env,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        process.stdin.write(refs)
        process.stdin.flush()
        chunks=[]
        while True:
            header=process.stdout.read(4)
            assert len(header)==4
            size=int(header,16)
            if not size:
                break
            chunks.append(process.stdout.read(size-4))
        return b''.join(chunks)
    finally:
        process.terminate()
        process.communicate(timeout=3)
        GIT_TOOL.verify()


def client_for(local, *, limits=Limits()):
    tls=ssl.create_default_context(cafile=str(local['args']['ca']))
    client=GitReceiveClient(origin='https://127.0.0.1:'+str(local['args']['port']),
        repository_path='/repo.git',context=tls,token=PASSWORD,budget=Budget(limits))
    client.set_username(USER)
    return client


def test_bridge_delivers_real_native_pack_and_complete_tree(local):
    client=client_for(local)
    body=body_from_native(local,client.advertisement())
    status=client.send_once(body)
    validate_status(status,target_ref=local['branch'],request_body=body)
    assert tip(local)==local['new']
    assert native(local['hosted'],'--git-dir=repo.git','rev-parse',local['new']+'^').decode().strip()==local['base']
    actual=native(local['hosted'],'--git-dir=repo.git','ls-tree','-r',local['new'])
    assert actual==native(local['repo'],'ls-tree','-r',local['new'])
    assert [row['method'] for row in local['calls']]==['GET','POST']
    assert client.budget.sent+client.budget.received <= client.budget.limits.wire
    with pytest.raises(GitReceiveError,match='write-already-consumed'):
        client.send_once(body)


@pytest.mark.parametrize('fault',['race','disconnect-upload','disconnect-status','duplicate-status',
    'truncate-status','wrong-ref-status','redirect-post','unauthorized-post'])
def test_ambiguous_or_failed_write_never_retries(local,fault):
    client=client_for(local)
    body=body_from_native(local,client.advertisement())
    local['fault']=fault
    with pytest.raises((ValueError,OSError)):
        raw=client.send_once(body)
        validate_status(raw,target_ref=local['branch'],request_body=body)
    with pytest.raises(GitReceiveError,match='write-already-consumed'):
        client.send_once(body)
    assert sum(row['method']=='POST' for row in local['calls'])<=1


def test_tls_identity_failure_sends_no_credentials(local):
    client=client_for(local)
    client._context=ssl.create_default_context()
    with pytest.raises(ssl.SSLError):
        client.advertisement()
    assert not local['calls']


def test_narrow_git_token_never_dispatches_rest_mutation(local):
    client=client_for(local)
    with pytest.raises(GitReceiveError,match='unselected-write'):
        client._exchange('POST','/api/v4/projects/37/merge_requests',body=b'{}',
                         cap=64000,media='application/json')
    assert local['calls']==[]
