"""The public callback listener exposes only exact POST routes and preserves signed bytes."""

import copy
import http.client
import os
import stat
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import meeting_webhook as proxy
import pytest


@contextmanager
def running(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.fixture
def callback():
    captured = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            captured.append((self.path, dict(self.headers), body))
            self.send_response(202)
            self.send_header("Content-Length", "2")
            self.send_header("Retry-After", "30")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_):
            pass

    with running(ThreadingHTTPServer(("127.0.0.1", 0), Upstream)) as upstream:
        with running(proxy.CallbackServer(("127.0.0.1", 0), upstream)) as address:
            yield address, captured


def request(address, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*address, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.headers), response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("path", sorted(proxy.PATHS))
def test_proxy_preserves_exact_body_and_signatures_but_drops_browser_identity(callback, path):
    address, captured = callback
    body = b'{ "customer" : "caf\\u00e9",\n "quote": "literal spaces" }\n'
    status, headers, result = request(
        address,
        "POST",
        path,
        body,
        {
            "Webhook-Id": "event-id",
            "Webhook-Timestamp": "123456789",
            "Webhook-Signature": "v1,test-signature v1,rotated-key",
            "Svix-Id": "legacy-id",
            "Svix-Timestamp": "123456789",
            "Svix-Signature": "v1,legacy-signature",
            "Authorization": "Bearer browser-value",
            "Cookie": "session=browser-session",
            "X-Forwarded-Host": "attacker.invalid",
            "X-Forwarded-Proto": "http",
            "X-User-Email": "someone@example.com",
        },
    )
    assert status == 202 and result == b"{}"
    assert headers["Retry-After"] == "30"
    assert headers["X-Toir-Webhook-Proxy"] == "v1"
    actual_path, actual_headers, actual_body = captured[0]
    assert actual_path == path and actual_body == body
    lowered = {k.lower(): v for k, v in actual_headers.items()}
    assert lowered["webhook-signature"] == "v1,test-signature v1,rotated-key"
    assert lowered["svix-signature"] == "v1,legacy-signature"
    assert (
        not {"authorization", "cookie", "x-forwarded-host", "x-forwarded-proto", "x-user-email"}
        & lowered.keys()
    )


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/api/health",
        "/api/auth/login",
        "/api/meetings/workspace",
        "/v1/meetings/webhooks/recall/realtime",
        "/api/meetings/webhooks/recall/realtime/",
        "/api/meetings/webhooks/recall/realtime?redirect=/api/meetings/workspace",
        "/api/meetings/webhooks/recall/../workspace",
        "/api/meetings/webhooks/recall/%72ealtime",
    ],
)
def test_other_paths_never_reach_application(callback, path):
    address, captured = callback
    assert request(address, "POST", path, b"{}")[0] == 404
    assert captured == []


@pytest.mark.parametrize(
    "method", ["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"]
)
def test_non_post_methods_never_reach_application(callback, method):
    address, captured = callback
    assert request(address, method, "/api/meetings/webhooks/recall/dashboard")[0] == 404
    assert captured == []


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"Content-Length": "1000001"}, 413),
        ({"Content-Length": "-1"}, 411),
        ({"Content-Length": "1e3"}, 411),
        ({"Transfer-Encoding": "chunked"}, 411),
        ({"Content-Encoding": "gzip"}, 415),
    ],
)
def test_body_limits_and_encoding_fail_before_upstream(callback, headers, expected):
    address, captured = callback
    assert (
        request(address, "POST", "/api/meetings/webhooks/recall/dashboard", b"{}", headers)[0]
        == expected
    )
    assert captured == []


def test_missing_upstream_is_retryable_and_does_not_expose_internal_details():
    with running(proxy.CallbackServer(("127.0.0.1", 0), ("127.0.0.1", 1))) as address:
        status, headers, body = request(address, "POST", sorted(proxy.PATHS)[0], b"{}")
    assert status == 503 and headers["Retry-After"] == "30" and body == b""


def existing_node():
    return {
        "BackendState": "Running",
        "CurrentTailnet": {"Name": proxy.TAILNET},
        "Self": {
            "DNSName": proxy.HOSTNAME + ".",
            "CapMap": {
                "funnel": None,
                "https": None,
                "https://tailscale.com/cap/funnel-ports?ports=443,8443,10000": None,
            },
        },
    }


def private_config():
    return {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {f"{proxy.HOSTNAME}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:80"}}}},
    }


def test_existing_node_must_already_have_funnel_permission():
    proxy.validate_node(existing_node())
    node = existing_node()
    del node["Self"]["CapMap"]["funnel"]
    with pytest.raises(RuntimeError, match="no policy was changed"):
        proxy.validate_node(node)


def test_other_service_on_8443_is_never_overwritten():
    value = private_config()
    value["TCP"]["8443"] = {"HTTPS": True}
    value["Web"][proxy.endpoint_key()] = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:80"}}}
    with pytest.raises(RuntimeError, match="already used"):
        proxy.validate_endpoint(value)


def test_setup_only_adds_separate_8443_and_keeps_443_private(monkeypatch):
    before = private_config()
    after = copy.deepcopy(before)
    after["TCP"]["8443"] = {"HTTPS": True}
    after["Web"][proxy.endpoint_key()] = {"Handlers": {"/": {"Proxy": proxy.TARGET}}}
    after["AllowFunnel"] = {proxy.endpoint_key(): True}
    values = iter([before, after])
    calls = []
    monkeypatch.setattr(proxy, "node_status", existing_node)
    monkeypatch.setattr(proxy, "config", lambda: next(values))
    monkeypatch.setattr(proxy, "install_proxy", lambda: calls.append(("install",)))
    monkeypatch.setattr(proxy, "command", lambda *args: calls.append(args))
    monkeypatch.setattr(proxy, "local_ready", lambda: True)
    proxy.setup()
    assert calls == [("install",), ("tailscale", "funnel", "--bg", "--https=8443", proxy.TARGET)]
    assert proxy.other_endpoints(after) == before


def test_disable_removes_only_our_8443(monkeypatch):
    before = private_config()
    before["TCP"]["8443"] = {"HTTPS": True}
    before["Web"][proxy.endpoint_key()] = {"Handlers": {"/": {"Proxy": proxy.TARGET}}}
    before["AllowFunnel"] = {proxy.endpoint_key(): True}
    states = iter([before, private_config()])
    calls = []
    monkeypatch.setattr(proxy, "config", lambda: next(states))
    monkeypatch.setattr(proxy, "command", lambda *args: calls.append(args))
    proxy.disable()
    assert calls == [
        ("tailscale", "funnel", "--https=8443", "off"),
        ("systemctl", "disable", "--now", proxy.SERVICE),
    ]
    assert not any("reset" in call for call in calls)


def test_install_remains_readable_to_dynamic_user_under_private_operator_umask(
    monkeypatch, tmp_path
):
    target = tmp_path / "proxy" / "proxy.py"
    unit = tmp_path / "proxy.service"
    monkeypatch.setattr(proxy, "INSTALL", target)
    monkeypatch.setattr(proxy, "UNIT", unit)
    monkeypatch.setattr(proxy, "local_ready", lambda: True)
    monkeypatch.setattr(proxy, "command", lambda *args: "")
    previous = os.umask(0o077)
    try:
        proxy.install_proxy()
    finally:
        os.umask(previous)
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert stat.S_IMODE(unit.stat().st_mode) == 0o644
