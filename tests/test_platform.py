"""Platform registry + connection wiring (Traefik + Caddy + HAProxy).

No real proxy is needed — the httpx client is injected. Proves the registry
maps each platform name to its API shape, the support matrix raises teaching
errors for unsupported ops, path templates format with URL-encoding, list
payloads unwrap across response conventions, and the connection sends the
right auth (Basic for haproxy, optional Basic for traefik/caddy) and
translates errors.
"""

import httpx
import pytest

from proxy_aiops.config import TargetConfig
from proxy_aiops.connection import ProxyApiError, ProxyConnection
from proxy_aiops.platform import (
    CADDY,
    HAPROXY,
    TRAEFIK,
    UnsupportedOperation,
    encode_config_path,
    get_platform,
    platform_names,
)


@pytest.mark.unit
def test_all_three_platforms_registered():
    assert set(platform_names()) == {TRAEFIK, CADDY, HAPROXY}
    assert get_platform(HAPROXY).requires_secret
    assert not get_platform(TRAEFIK).requires_secret
    assert not get_platform(CADDY).requires_secret


@pytest.mark.unit
def test_unknown_platform_raises_with_registered_names():
    with pytest.raises(ValueError, match="traefik"):
        get_platform("nginx-unit")


@pytest.mark.unit
def test_path_templates_differ_per_platform():
    tr = get_platform(TRAEFIK)
    ha = get_platform(HAPROXY)
    assert tr.path("routers") == "/api/http/routers"
    assert tr.path("router_detail", name="web@file").endswith("/web%40file")
    assert ha.path("backends") == "/v2/services/haproxy/configuration/backends"
    assert ha.path("runtime_server", name="web1", backend="app") == (
        "/v2/services/haproxy/runtime/servers/web1?backend=app"
    )
    assert get_platform(CADDY).path("config_load") == "/load"


@pytest.mark.unit
def test_support_matrix_raises_teaching_errors():
    """Unsupported ops must teach, never silently no-op or 404."""
    with pytest.raises(UnsupportedOperation, match="providers"):
        get_platform(TRAEFIK).path("config_set")
    with pytest.raises(UnsupportedOperation, match="runtime server-state"):
        get_platform(CADDY).path("runtime_server")
    with pytest.raises(UnsupportedOperation, match="transactions"):
        get_platform(HAPROXY).path("config_set")
    with pytest.raises(UnsupportedOperation, match="version"):
        get_platform(CADDY).path("version")


@pytest.mark.unit
def test_unmapped_resource_raises_teaching_keyerror():
    with pytest.raises(KeyError, match="not mapped"):
        get_platform(TRAEFIK).path("does_not_exist")


@pytest.mark.unit
def test_rows_unwraps_conventions_and_bare_array():
    ha = get_platform(HAPROXY)
    assert ha.rows({"data": [{"a": 1}, {"a": 2}]}) == [{"a": 1}, {"a": 2}]
    assert ha.rows({"rows": [{"b": 3}]}) == [{"b": 3}]
    assert ha.rows([{"c": 4}]) == [{"c": 4}]
    assert ha.rows({"nope": 1}) == []


@pytest.mark.unit
def test_rows_sanitizes_strings():
    out = get_platform(TRAEFIK).rows([{"x": "ok", "n": 5}])
    assert out[0]["x"] == "ok" and out[0]["n"] == 5


class _Resp:
    def __init__(self, status, payload=None, content=b"{}", text="body"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Client:
    def __init__(self, resp):
        self._resp = resp

    def request(self, method, path, **k):
        return self._resp

    def close(self):
        pass


@pytest.mark.unit
def test_haproxy_uses_basic_auth(monkeypatch):
    monkeypatch.setenv("PROXY_LB1_SECRET", "s3cr3t")
    target = TargetConfig(name="lb1", platform=HAPROXY,
                          base_url="http://lb.local:5555", username="dpapi")
    auth = ProxyConnection._build_auth(target)
    assert isinstance(auth, httpx.BasicAuth)


@pytest.mark.unit
def test_traefik_without_secret_sends_no_auth(monkeypatch):
    monkeypatch.delenv("PROXY_EDGE_SECRET", raising=False)
    target = TargetConfig(name="edge", platform=TRAEFIK)
    assert ProxyConnection._build_auth(target) is None


@pytest.mark.unit
def test_caddy_with_stored_secret_sends_optional_basic(monkeypatch):
    monkeypatch.setenv("PROXY_CADDY1_SECRET", "adminpw")
    target = TargetConfig(name="caddy1", platform=CADDY, username="admin")
    auth = ProxyConnection._build_auth(target)
    assert isinstance(auth, httpx.BasicAuth)


@pytest.mark.unit
def test_connection_translates_non_2xx(monkeypatch):
    monkeypatch.delenv("PROXY_EDGE_SECRET", raising=False)
    target = TargetConfig(name="edge", platform=TRAEFIK)
    conn = ProxyConnection(target, client=_Client(_Resp(404, content=b"x")))
    with pytest.raises(ProxyApiError) as ei:
        conn.get("/api/x")
    assert ei.value.status_code == 404
    assert "not found" in str(ei.value).lower()


@pytest.mark.unit
def test_connection_401_teaches_credentials(monkeypatch):
    monkeypatch.setenv("PROXY_LB1_SECRET", "pw")
    target = TargetConfig(name="lb1", platform=HAPROXY, username="u")
    conn = ProxyConnection(target, client=_Client(_Resp(401, content=b"x")))
    with pytest.raises(ProxyApiError, match="credentials"):
        conn.get("/v2/info")


@pytest.mark.unit
def test_connection_returns_text_for_non_json(monkeypatch):
    """the /metrics text endpoint is text, not JSON — must pass through as str."""
    monkeypatch.delenv("PROXY_EDGE_SECRET", raising=False)
    target = TargetConfig(name="edge", platform=TRAEFIK)
    resp = _Resp(200, payload=ValueError("not json"), content=b"m 1", text="m 1")
    conn = ProxyConnection(target, client=_Client(resp))
    assert conn.get("/metrics") == "m 1"


@pytest.mark.unit
def test_config_rejects_bad_platform_and_defaults_base_url():
    with pytest.raises(ValueError):
        TargetConfig(name="x", platform="nginx-unit")
    with pytest.raises(ValueError, match="base_url"):
        TargetConfig(name="x", platform=TRAEFIK, base_url="lb.local:8080")
    tr = TargetConfig(name="t", platform=TRAEFIK)
    assert tr.base_url == "http://localhost:8080"
    ca = TargetConfig(name="c", platform=CADDY)
    assert ca.base_url == "http://localhost:2019"
    ha = TargetConfig(name="h", platform=HAPROXY, base_url="https://lb:5555/")
    assert ha.base_url == "https://lb:5555"  # trailing slash stripped


@pytest.mark.unit
def test_optional_secret_degrades_to_empty(monkeypatch):
    monkeypatch.delenv("PROXY_EDGE_SECRET", raising=False)
    target = TargetConfig(name="edge", platform=TRAEFIK)
    assert target.secret == ""
    assert target.has_secret is False


@pytest.mark.unit
def test_required_secret_missing_raises_teaching_oserror(monkeypatch):
    monkeypatch.delenv("PROXY_LB1_SECRET", raising=False)
    target = TargetConfig(name="lb1", platform=HAPROXY, username="u")
    with pytest.raises(OSError, match="proxy-aiops secret set lb1"):
        _ = target.secret


# ── URL-encoding of agent-supplied path segments ─────────────────────────────


@pytest.mark.unit
def test_path_traversal_ids_are_url_encoded():
    """An id carrying ``../`` must not reach the HTTP client as a raw path
    traversal — every substituted value is URL-encoded in Platform.path()."""
    ha = get_platform(HAPROXY)
    path = ha.path("runtime_server", name="../../stop", backend="a&b=1")
    assert "../" not in path
    assert "&b" not in path.split("?backend=")[1]

    tr = get_platform(TRAEFIK)
    path = tr.path("router_detail", name="../../entrypoints?x=1")
    assert "../" not in path and "?x" not in path


@pytest.mark.unit
def test_caddy_config_path_encoding_rejects_dot_segments():
    """A caddy config path must never traverse out of /config/."""
    with pytest.raises(ValueError, match="dot-segments"):
        encode_config_path("apps/../../load")
    enc = encode_config_path("apps/http/servers/srv 0/routes/0")
    assert "srv%200" in enc
    assert enc == "apps/http/servers/srv%200/routes/0"
