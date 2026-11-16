"""Real local handshake close and certificate rejection; no hosted claims."""
import json
import socket
import ssl
import threading

import pytest
from test_argocd import ca
from test_http_mtls import mtls_server

from devsecops.adapters import http

__all__ = ['ca', 'mtls_server']


def test_successful_http_receipt_keeps_existing_shape(mtls_server, ca):
    client = http.HTTPSReadClient(mtls_server['origin'], ca_bundle=ca[0], client_certificate=ca)
    assert client.get_json('/api') == {'authenticated': True}
    assert set(client.receipts[-1]) == {'request_index', 'method', 'started_at', 'request_sha256',
                                      'status', 'body_bytes', 'body_sha256', 'outcome',
                                      'server_date', 'finished_at', 'duration_ms'}


def test_real_handshake_eof_has_safe_precise_receipt(ca):
    observed = []
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        server.settimeout(2)

        def close_before_tls():
            connection, _ = server.accept()
            with connection:
                connection.settimeout(2)
                observed.append(bool(connection.recv(65536)))
                connection.shutdown(socket.SHUT_WR)
                try:
                    while connection.recv(65536):
                        pass
                except OSError:
                    pass

        thread = threading.Thread(target=close_before_tls, daemon=True)
        thread.start()
        client = http.HTTPSReadClient(f'https://127.0.0.1:{server.getsockname()[1]}',
                                     ca_bundle=ca[0], timeout=1, total_timeout=2)
        with pytest.raises(http.APIError) as failure:
            client.get_json('/readyz')
        thread.join(3)
    assert observed == [True] and not thread.is_alive()
    assert failure.value.code == 'tls'
    receipt = client.receipts[-1]
    assert receipt['tls_failure'] == {'category': 'handshake-eof', 'exception': 'SSLEOFError',
                                      'phase': 'tls-handshake', 'reason': 'UNEXPECTED_EOF_WHILE_READING',
                                      'verify_code': None, 'peer_authenticated': False}
    assert receipt['body_bytes'] == 0 and receipt['status'] is None


def test_real_untrusted_certificate_remains_fatal_and_identified(mtls_server, ca):
    client = http.HTTPSReadClient(mtls_server['origin'], client_certificate=ca, timeout=1)
    with pytest.raises(http.APIError) as failure:
        client.get_json('/api')
    assert failure.value.code == 'tls'
    record = client.receipts[-1]
    assert record['tls_failure']['peer_authenticated'] is False
    assert record['tls_failure']['category'] == 'certificate-verification'
    assert record['tls_failure']['exception'] == 'SSLCertVerificationError'
    assert record['tls_failure']['reason'] == 'CERTIFICATE_VERIFY_FAILED'
    assert isinstance(record['tls_failure']['verify_code'], int)


@pytest.mark.parametrize('category', ['certificate', 'protocol'])
def test_tls_authentication_failures_win_a_deadline_race_and_do_not_leak(monkeypatch, category):
    error = (ssl.SSLCertVerificationError(1, 'DO-NOT-EXPORT-PEER-DIAGNOSTIC')
             if category == 'certificate' else ssl.SSLError(1, 'DO-NOT-EXPORT-PEER-DIAGNOSTIC'))
    error.reason = 'CERTIFICATE_VERIFY_FAILED' if category == 'certificate' else 'TLSV1_ALERT_UNKNOWN_CA'
    if category == 'certificate':
        error.verify_code = 62

    def reject(*args, **kwargs):
        raise error

    class ExpiredTimer:
        def __init__(self, budget, abort):
            self.abort = abort

        def start(self):
            self.abort()

        def cancel(self):
            pass

    monkeypatch.setattr(http.threading, 'Timer', ExpiredTimer)
    monkeypatch.setattr(http.http.client.HTTPSConnection, 'request', reject)
    client = http.HTTPSReadClient('https://example.test')
    with pytest.raises(http.APIError) as failure:
        client.get_json('/api')
    assert failure.value.code == 'tls'
    assert client.receipts[-1]['tls_failure']['category'] == (
        'certificate-verification' if category == 'certificate' else 'tls-protocol')
    assert 'DO-NOT-EXPORT' not in str(failure.value) + json.dumps(client.receipts)


def test_unknown_tls_reason_is_redacted_and_post_handshake_eof_is_not_startup():
    error = ssl.SSLEOFError(1, 'DO-NOT-EXPORT-SECRET')
    error.reason = 'DO_NOT_EXPORT_SECRET'
    result = http._tls_failure(error, 'response-body')
    assert result['category'] == 'tls-protocol' and result['reason'] == 'OTHER'
    assert 'DO_NOT_EXPORT' not in json.dumps(result)


def test_tls_diagnostic_receipts_are_defensive_copies(mtls_server, ca):
    client = http.HTTPSReadClient(mtls_server['origin'], timeout=1)
    with pytest.raises(http.APIError):
        client.get_json('/api')
    copies = client.receipts
    copies[0]['tls_failure']['category'] = 'handshake-eof'
    assert client.receipts[0]['tls_failure']['category'] == 'certificate-verification'
    assert client.receipts[0]['duration_ms'] < 3000
