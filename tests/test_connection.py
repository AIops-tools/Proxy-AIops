"""Connection-layer tests: HTTP error translation + session management.

No real proxy is touched — the httpx client is faked (``request`` / ``close``)
and injected, and the ``ConnectionManager`` is exercised over a stub
``ProxyConnection`` so no socket is ever opened. Proves the teaching-message
mapping per status, JSON-vs-text-vs-empty body handling, transport-error
translation, and the manager's per-target caching / disconnect lifecycle.
"""

from __future__ import annotations

import httpx
import pytest

import proxy_aiops.connection as connection_mod
from proxy_aiops.config import AppConfig, TargetConfig
from proxy_aiops.connection import (
    ConnectionManager,
    ProxyApiError,
    ProxyConnection,
    _teaching_message,
)
from proxy_aiops.platform import HAPROXY, TRAEFIK


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text="", content=b"{}"):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class _FakeClient:
    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp
        self._raise = raise_exc
        self.calls: list[tuple] = []
        self.closed = False

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self._raise is not None:
            raise self._raise
        return self._resp

    def close(self):
        self.closed = True


def _conn(resp=None, raise_exc=None, platform=TRAEFIK):
    target = TargetConfig(name="t", platform=platform, base_url="http://x:8080")
    return ProxyConnection(target, client=_FakeClient(resp, raise_exc))


# ── _teaching_message: one branch per status class ──────────────────────────


@pytest.mark.unit
def test_teaching_message_auth_401_403():
    for status in (401, 403):
        msg = _teaching_message(status, "/api", "denied", "Traefik")
        assert f"({status})" in msg
        assert "Authentication/authorization failed" in msg


@pytest.mark.unit
def test_teaching_message_404_lists_parent_hint():
    msg = _teaching_message(404, "/api/http/routers/x", "gone", "Caddy")
    assert "Resource not found (404)" in msg
    assert "list the parent collection" in msg


@pytest.mark.unit
def test_teaching_message_400_and_5xx_and_generic():
    assert "Bad request (400)" in _teaching_message(400, "/p", "bad", "HAProxy")
    for status in (500, 502, 503, 504):
        m = _teaching_message(status, "/p", "boom", "Traefik")
        assert f"server error ({status})" in m
        assert "mid-reload" in m
    generic = _teaching_message(418, "/p", "teapot", "Caddy")
    assert "API error (418)" in generic


@pytest.mark.unit
def test_teaching_message_truncates_body():
    msg = _teaching_message(500, "/p", "Z" * 500, "Traefik")
    assert "Z" * 200 in msg
    assert "Z" * 201 not in msg


# ── ProxyConnection request handling ────────────────────────────────────────


@pytest.mark.unit
def test_get_returns_parsed_json():
    conn = _conn(_FakeResp(200, json_data={"Version": "3.1"}))
    assert conn.get(conn.platform.path("version")) == {"Version": "3.1"}


@pytest.mark.unit
def test_non_json_body_falls_back_to_text():
    conn = _conn(_FakeResp(200, json_data=None, text="metric 5\n", content=b"metric 5\n"))
    assert conn.get("/metrics") == "metric 5\n"


@pytest.mark.unit
def test_empty_body_returns_empty_dict():
    conn = _conn(_FakeResp(204, content=b""))
    assert conn.get("/x") == {}


@pytest.mark.unit
def test_non_2xx_raises_proxy_api_error_with_status():
    conn = _conn(_FakeResp(404, text="missing", content=b"missing"))
    with pytest.raises(ProxyApiError) as ei:
        conn.get("/api/http/routers/x")
    assert ei.value.status_code == 404
    assert "Resource not found (404)" in str(ei.value)


@pytest.mark.unit
def test_transport_error_is_translated():
    conn = _conn(raise_exc=httpx.ConnectError("refused"))
    with pytest.raises(ProxyApiError, match="Could not reach"):
        conn.get("/x")


@pytest.mark.unit
def test_all_verbs_dispatch_the_right_method():
    client = _FakeClient(_FakeResp(200, json_data={"ok": 1}))
    conn = ProxyConnection(
        TargetConfig(name="t", platform=TRAEFIK, base_url="http://x:8080"), client=client
    )
    conn.get("/g")
    conn.post("/p", json={"a": 1})
    conn.put("/u")
    conn.patch("/pa")
    conn.delete("/d")
    methods = [c[0] for c in client.calls]
    assert methods == ["GET", "POST", "PUT", "PATCH", "DELETE"]
    assert client.calls[1][2]["json"] == {"a": 1}
    conn.close()
    assert client.closed is True


@pytest.mark.unit
def test_platform_and_target_properties():
    conn = _conn(_FakeResp(200, json_data={}), platform=HAPROXY)
    assert conn.target.name == "t"
    assert conn.platform.name == HAPROXY


# ── ConnectionManager lifecycle (stubbed ProxyConnection) ───────────────────


class _StubConn:
    def __init__(self, target, client=None):
        self.target = target
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def _mgr(monkeypatch):
    monkeypatch.setattr(connection_mod, "ProxyConnection", _StubConn)
    cfg = AppConfig(
        targets=(
            TargetConfig(name="edge1", platform=TRAEFIK, base_url="http://a:8080"),
            TargetConfig(name="edge2", platform=TRAEFIK, base_url="http://b:8080"),
        )
    )
    return ConnectionManager(cfg)


@pytest.mark.unit
def test_connect_caches_per_target(_mgr):
    first = _mgr.connect("edge1")
    again = _mgr.connect("edge1")
    assert first is again  # cached, not rebuilt
    assert _mgr.connect("edge2") is not first


@pytest.mark.unit
def test_connect_default_target_when_name_omitted(_mgr):
    conn = _mgr.connect()
    assert conn.target.name == "edge1"  # first target is the default


@pytest.mark.unit
def test_disconnect_closes_and_forgets(_mgr):
    conn = _mgr.connect("edge1")
    assert _mgr.list_connected() == ["edge1"]
    _mgr.disconnect("edge1")
    assert conn.closed is True
    assert _mgr.list_connected() == []
    _mgr.disconnect("edge1")  # idempotent — no error on a missing target


@pytest.mark.unit
def test_disconnect_all_and_listings(_mgr):
    _mgr.connect("edge1")
    _mgr.connect("edge2")
    assert set(_mgr.list_targets()) == {"edge1", "edge2"}
    _mgr.disconnect_all()
    assert _mgr.list_connected() == []


@pytest.mark.unit
def test_from_config_uses_supplied_config(_mgr, monkeypatch):
    monkeypatch.setattr(connection_mod, "ProxyConnection", _StubConn)
    cfg = AppConfig(targets=(TargetConfig(name="solo", platform=TRAEFIK,
                                          base_url="http://s:8080"),))
    mgr = ConnectionManager.from_config(cfg)
    assert mgr.list_targets() == ["solo"]
