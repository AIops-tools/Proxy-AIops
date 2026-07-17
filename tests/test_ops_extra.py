"""Read-path ops coverage for the branches the flagship read tests skip:
caddy service/upstream joins, per-platform ``service_detail``, the live TLS
probe (with a faked ssl handshake — no socket), config-tree edge cases, and the
neutral cell coercions in ``ops._util``. Same fake-connection style as
``test_reads.py``; no real proxy is contacted.
"""

from __future__ import annotations

import pytest

from proxy_aiops.config import TargetConfig
from proxy_aiops.ops import _util, certs, configread, services
from proxy_aiops.platform import CADDY, HAPROXY, TRAEFIK, get_platform


class _Conn:
    def __init__(self, responses, platform=TRAEFIK):
        self.target = TargetConfig(name="t", platform=platform)
        self.platform = self.target.platform_obj
        self._responses = responses

    def get(self, path, **_kw):
        return self._responses.get(path, {})


def _p(platform, resource, **fmt):
    return get_platform(platform).path(resource, **fmt)


_CADDY_CONFIG = {
    "apps": {
        "http": {
            "servers": {
                "srv0": {
                    "listen": [":443"],
                    "routes": [
                        {
                            "match": [{"host": ["app.example.com"]}],
                            "handle": [{
                                "handler": "reverse_proxy",
                                "upstreams": [{"dial": "10.0.0.5:8080"},
                                              {"dial": "10.0.0.6:8080"}],
                            }],
                        },
                    ],
                }
            }
        }
    }
}

_HAPROXY_STATS = [
    {
        "stats": [
            {"name": "app", "type": "backend", "stats": {"status": "UP"}},
            {"name": "web1", "backend_name": "app", "type": "server",
             "stats": {"status": "UP", "check_status": "L7OK", "addr": "10.0.0.5:80",
                       "weight": 100, "req_tot": 500}},
            {"name": "web2", "backend_name": "app", "type": "server",
             "stats": {"status": "DRAIN", "check_status": "", "addr": "10.0.0.6:80",
                       "weight": 0, "req_tot": 0}},
        ],
    }
]


# ── caddy services join upstream health (services.py 47-71) ─────────────────


@pytest.mark.unit
def test_list_services_caddy_joins_upstream_health():
    conn = _Conn({
        _p(CADDY, "config_root"): _CADDY_CONFIG,
        _p(CADDY, "upstreams"): [
            {"address": "10.0.0.5:8080", "num_requests": 3, "fails": 0},
            {"address": "10.0.0.6:8080", "num_requests": 0, "fails": 2},
        ],
    }, platform=CADDY)
    out = services.list_services(conn)
    svc = out["services"][0]
    assert svc["platform"] == CADDY
    assert svc["type"] == "reverse_proxy"
    assert svc["name"] == "apps/http/servers/srv0/routes/0"
    assert svc["serversTotal"] == 2
    assert svc["serversUp"] == 1  # only the fails=0 upstream is up


# ── service_detail per platform (services.py 121-141) ───────────────────────


@pytest.mark.unit
def test_service_detail_traefik_normalises_raw():
    detail_path = _p(TRAEFIK, "service_detail", name="app@file")
    conn = _Conn({detail_path: {"name": "app@file", "type": "loadbalancer"}})
    out = services.service_detail(conn, "app@file")
    assert out["platform"] == TRAEFIK
    assert out["service"]["name"] == "app@file"


@pytest.mark.unit
def test_service_detail_haproxy_attaches_servers():
    conn = _Conn({
        _p(HAPROXY, "backend_detail", name="app"): {"data": {"name": "app", "mode": "http"}},
        _p(HAPROXY, "stats"): _HAPROXY_STATS,
    }, platform=HAPROXY)
    out = services.service_detail(conn, "app")
    assert out["platform"] == HAPROXY
    assert out["service"]["name"] == "app"
    assert {s_["server"] for s_ in out["servers"]} == {"web1", "web2"}


@pytest.mark.unit
def test_service_detail_caddy_looks_up_in_list_and_missing():
    conn = _Conn({
        _p(CADDY, "config_root"): _CADDY_CONFIG,
        _p(CADDY, "upstreams"): [],
    }, platform=CADDY)
    hit = services.service_detail(conn, "apps/http/servers/srv0/routes/0")
    assert hit["platform"] == CADDY
    assert hit["service"]["name"] == "apps/http/servers/srv0/routes/0"
    miss = services.service_detail(conn, "no/such/route")
    assert "not found" in miss["error"]


@pytest.mark.unit
def test_haproxy_upstream_drain_admin_state():
    conn = _Conn({_p(HAPROXY, "stats"): _HAPROXY_STATS}, platform=HAPROXY)
    out = services.list_upstreams(conn, service="app")
    drain = [u for u in out["upstreams"] if u["status"] == "drain"][0]
    assert drain["adminState"] == "maint"
    assert drain["weight"] == 0.0


# ── cert probe: fake the TLS handshake, no socket (certs.py 94-123) ─────────


@pytest.mark.unit
def test_probe_certificate_reads_expiry(monkeypatch):
    from datetime import UTC, datetime, timedelta

    not_after = datetime.now(UTC) + timedelta(days=42)

    class _TLS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getpeercert(self, binary_form=True):
            return b"DER-BYTES"

    class _Ctx:
        check_hostname = True
        verify_mode = None

        def wrap_socket(self, sock, server_hostname=None):
            return _TLS()

    class _Sock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(certs.ssl, "create_default_context", lambda: _Ctx())
    monkeypatch.setattr(certs.socket, "create_connection",
                        lambda addr, timeout=None: _Sock())
    monkeypatch.setattr(certs, "_der_not_after", lambda der: not_after)

    row = certs.probe_certificate("*.example.com", port=443)
    assert row["domain"] == "*.example.com"
    assert 41 <= row["daysToExpiry"] <= 42
    assert "error" not in row


@pytest.mark.unit
def test_probe_certificate_records_failure_never_raises(monkeypatch):
    def _boom(addr, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(certs.socket, "create_connection", _boom)
    row = certs.probe_certificate("dead.example.com")
    assert row["domain"] == "dead.example.com"
    assert "refused" in row["error"]


@pytest.mark.unit
def test_list_certificates_probes_inventory(monkeypatch):
    conn = _Conn({_p(CADDY, "config_root"): _CADDY_CONFIG}, platform=CADDY)
    monkeypatch.setattr(
        certs, "probe_certificate",
        lambda domain, port=443: {"domain": domain, "daysToExpiry": 10.0},
    )
    out = certs.list_certificates(conn, probe=True)
    assert out["probed"] == out["total"] >= 1
    assert all("daysToExpiry" in c for c in out["certificates"])


# ── config reads: get_config_value teaching + snapshot error (configread) ───


@pytest.mark.unit
def test_get_config_value_off_caddy_returns_teaching_note():
    conn = _Conn({}, platform=HAPROXY)
    out = configread.get_config_value(conn, "apps/http")
    assert "unsupported" in out


@pytest.mark.unit
def test_search_config_returns_snapshot_error_when_unsupported():
    conn = _Conn({}, platform=HAPROXY)
    out = configread.search_config(conn, "anything")
    assert "unsupported" in out  # propagated straight from config_snapshot


@pytest.mark.unit
def test_search_config_rejects_blank_query():
    conn = _Conn({_p(CADDY, "config_snapshot"): _CADDY_CONFIG}, platform=CADDY)
    with pytest.raises(ValueError, match="non-empty"):
        configread.search_config(conn, "   ")


# ── _util neutral coercions (to_bool 44-53) ─────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False),
    (1, True), (0, False), (2.5, True),
    ("up", True), ("enabled", True), ("yes", True),
    ("down", False), ("disabled", False), ("", False), ("none", False),
    ("something-else", True),
])
def test_to_bool_coerces_neutral_cells(value, expected):
    assert _util.to_bool(value) is expected
