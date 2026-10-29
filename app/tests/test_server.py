"""The HTTP front on a real 127.0.0.1 socket, with the fake mic and engine from test_service."""

import http.client
import json
import threading

import numpy as np
import pytest

from gpw.mic import MicError
from gpw.server import make_server
from gpw.service import DictationService
from test_service import FakeEngine, FakeMic, make_config

HEADERS = {"X-GPW-Client": "test"}


@pytest.fixture
def running(tmp_path):
    started = []

    def start(engine=None, mic=None):
        service = DictationService(make_config(tmp_path), engine or FakeEngine(), mic or FakeMic())
        server = make_server(service, 0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        started.append((server, service))
        return server, service

    yield start
    for server, service in started:
        server.shutdown()
        server.server_close()
        service.close()


def request(server, method, path, body="", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    conn.request(method, path, body=body, headers={**HEADERS, **(headers or {})})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def test_binds_to_loopback_only(running):
    server, _ = running()
    assert server.server_address[0] == "127.0.0.1"


def test_health(running):
    server, _ = running()
    response, data = request(server, "GET", "/health", headers={"X-GPW-Client": ""})
    assert response.status == 200
    health = json.loads(data)
    assert health["service"] == "gpw" and health["dictate_key"] == "RCtrl"


def test_full_dictation_over_http(running):
    text = "Café at 9 – “quoted” ✓"
    server, _ = running(engine=FakeEngine(text=text))
    response, data = request(server, "POST", "/start")
    assert response.status == 200 and json.loads(data)["id"] == 1

    response, data = request(server, "POST", "/stop", "mode=hold")
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/plain; charset=utf-8"
    assert response.getheader("X-Dictation-Status") == "ok"
    assert response.getheader("X-Dictation-Id") == "1"
    assert data.decode("utf-8") == text

    response, data = request(server, "POST", "/pasted", "id=1&release_to_paste_ms=1420&paste_ms=30&clipboard_restored=1")
    assert json.loads(data) == {"ok": True}


def test_skipped_clip_over_http(running):
    server, _ = running(mic=FakeMic(np.zeros(16_000, dtype=np.float32)))
    request(server, "POST", "/start")
    response, data = request(server, "POST", "/stop")
    assert response.getheader("X-Dictation-Status") == "skipped"
    assert data.decode("utf-8").startswith("Didn't hear anything")


def test_cancel_over_http(running):
    server, service = running()
    request(server, "POST", "/start")
    response, data = request(server, "POST", "/cancel")
    assert json.loads(data) == {"ok": True, "result": "cancelled"}
    assert service.state == "idle"


def test_mic_error_is_503_with_reason(running):
    mic = FakeMic()
    mic.start_errors = [MicError("The mic is gone"), MicError("The mic is gone")]
    server, _ = running(mic=mic)
    response, data = request(server, "POST", "/start")
    assert response.status == 503
    assert json.loads(data) == {"ok": False, "error": "The mic is gone"}


def test_post_without_client_header_is_refused(running):
    server, service = running()
    response, _ = request(server, "POST", "/start", headers={"X-GPW-Client": ""})
    assert response.status == 403
    assert service.state == "idle"


def test_browser_requests_are_refused(running):
    # A web page can POST to localhost, but the browser adds Origin. The mic must stay closed.
    server, service = running()
    response, _ = request(server, "POST", "/start", headers={"Origin": "https://example.com"})
    assert response.status == 403
    response, _ = request(server, "GET", "/health", headers={"Origin": "https://example.com"})
    assert response.status == 403
    assert service.state == "idle"


def test_dns_rebinding_host_is_refused(running):
    server, _ = running()
    response, _ = request(server, "GET", "/health", headers={"Host": f"evil.example:{server.server_port}"})
    assert response.status == 403
    response, _ = request(server, "GET", "/health", headers={"Host": f"localhost:{server.server_port}"})
    assert response.status == 200


def test_unknown_path_is_404(running):
    server, _ = running()
    assert request(server, "POST", "/shutdown")[0].status == 404
    assert request(server, "GET", "/start")[0].status == 404


def test_second_service_on_the_same_port_fails_clearly(running, tmp_path):
    server, _ = running()
    other = DictationService(make_config(tmp_path / "other"), FakeEngine(), FakeMic())
    with pytest.raises(RuntimeError, match=f"Port {server.server_port} on 127.0.0.1 is taken"):
        make_server(other, server.server_port)
    other.close()
