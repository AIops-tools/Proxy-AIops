"""MCP read-tool wrappers: the live-pull branch (no injected rows).

The flagship RCA tools and the plain read tools accept injected data OR pull
live via ``_get_connection``. The pure-analysis path is covered elsewhere; here
we monkeypatch ``_get_connection`` to a fake proxy connection so the live-pull
branch (pull → classify) runs, and assert the error/teaching short-circuits
propagate through unchanged.
"""

from __future__ import annotations

import pytest

from proxy_aiops.config import TargetConfig
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


def _patch(monkeypatch, module, conn):
    monkeypatch.setattr(module, "_get_connection", lambda target=None: conn)


_HAPROXY_STATS = [{"stats": [
    {"name": "app", "type": "backend", "stats": {"status": "UP", "req_tot": 1000,
     "hrsp_2xx": 900, "hrsp_5xx": 100}},
    {"name": "web1", "backend_name": "app", "type": "server",
     "stats": {"status": "DOWN", "check_status": "L4CON", "addr": "10.0.0.5:80"}},
]}]


# ── flagship analysis tools: live pull ──────────────────────────────────────


@pytest.mark.unit
def test_backend_health_rca_live_pull_classifies(monkeypatch):
    from mcp_server.tools import analysis as t

    _patch(monkeypatch, t, _Conn({_p(HAPROXY, "stats"): _HAPROXY_STATS}, platform=HAPROXY))
    out = t.backend_health_rca()
    assert out["outages"] == 1
    assert "connection refused" in out["findings"][0]["cause"]


@pytest.mark.unit
def test_backend_health_rca_live_pull_propagates_error(monkeypatch):
    from mcp_server.tools import analysis as t

    class _Boom(_Conn):
        def get(self, path, **_kw):
            raise RuntimeError("stats unreachable")

    _patch(monkeypatch, t, _Boom({}, platform=HAPROXY))
    out = t.backend_health_rca()
    assert "error" in out


@pytest.mark.unit
def test_error_rate_rca_live_pull(monkeypatch):
    from mcp_server.tools import analysis as t

    _patch(monkeypatch, t, _Conn({_p(HAPROXY, "stats"): _HAPROXY_STATS}, platform=HAPROXY))
    out = t.error_rate_rca(error_rate_pct=5.0)
    assert out["flaggedCount"] == 1
    assert out["flagged"][0]["service"] == "app"


@pytest.mark.unit
def test_error_rate_rca_live_pull_caddy_teaches(monkeypatch):
    from mcp_server.tools import analysis as t

    _patch(monkeypatch, t, _Conn({}, platform=CADDY))
    out = t.error_rate_rca()
    assert "error" in out and "access logs" in out["error"]


@pytest.mark.unit
def test_route_conflict_analysis_live_pull(monkeypatch):
    from mcp_server.tools import analysis as t

    routers = [
        {"name": "specific", "rule": "Host(`a.example.com`) && PathPrefix(`/api`)",
         "service": "svc", "priority": 1},
        {"name": "broad", "rule": "Host(`a.example.com`)", "service": "svc",
         "priority": 10},
    ]
    services = [
        {"name": "app@file", "loadBalancer": {"servers": [{"url": "http://x"}]},
         "serverStatus": {"http://x": "UP"}},
    ]
    _patch(monkeypatch, t, _Conn({
        _p(TRAEFIK, "routers"): routers,
        _p(TRAEFIK, "services"): services,
    }))
    out = t.route_conflict_analysis()
    assert out["shadowedCount"] == 1


@pytest.mark.unit
def test_cert_expiry_sweep_live_pull_haproxy_teaches(monkeypatch):
    from mcp_server.tools import analysis as t

    _patch(monkeypatch, t, _Conn({}, platform=HAPROXY))
    out = t.cert_expiry_sweep()
    assert "unsupported" in out  # haproxy has no cert inventory


@pytest.mark.unit
def test_cert_expiry_sweep_live_pull_caddy_buckets(monkeypatch):
    from mcp_server.tools import analysis as t
    from proxy_aiops.ops import certs as cert_ops

    cfg = {"apps": {"http": {"servers": {
        "srv0": {"listen": [":443"], "routes": [
            {"match": [{"host": ["app.example.com"]}]}]}}}}}
    _patch(monkeypatch, t, _Conn({_p(CADDY, "config_root"): cfg}, platform=CADDY))
    monkeypatch.setattr(
        cert_ops, "probe_certificate",
        lambda domain, port=443: {"domain": domain, "daysToExpiry": 2.0},
    )
    out = t.cert_expiry_sweep(critical_days=7.0)
    assert out["critical"] == 1
    assert "ACME" in out["renewalHint"]


# ── plain read tools: the one-line delegate bodies ──────────────────────────


@pytest.mark.unit
def test_read_tool_delegates_reach_ops(monkeypatch):
    from mcp_server.tools import certs as ct
    from mcp_server.tools import configread as cr
    from mcp_server.tools import services as sv

    conn = _Conn({_p(HAPROXY, "stats"): _HAPROXY_STATS}, platform=HAPROXY)
    _patch(monkeypatch, sv, conn)
    assert sv.list_services()["platform"] == HAPROXY
    assert sv.list_upstreams()["total"] >= 1
    assert sv.upstream_detail(service="app", server="web1")["upstream"]["status"] == "down"
    assert "unsupported" in sv.list_middlewares()  # haproxy teaches

    cfg = {"apps": {"http": {"servers": {"srv0": {"listen": [":443"]}}}}}
    caddy = _Conn({_p(CADDY, "config_snapshot"): cfg, _p(CADDY, "config_root"): cfg},
                  platform=CADDY)
    _patch(monkeypatch, cr, caddy)
    assert "apps" in cr.config_snapshot()["config"]
    assert "matches" in cr.search_config(query="srv0")

    _patch(monkeypatch, ct, caddy)
    assert ct.list_certificates(probe=False)["probed"] == 0
