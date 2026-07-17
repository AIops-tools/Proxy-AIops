"""CLI read commands drive real ops over a fake connection.

Each command's ``get_connection`` is monkeypatched to hand back a fake proxy
connection returning canned JSON (same shape as ``test_reads.py``), so the
Typer command wiring, JSON rendering, and the ``--error → exit 1`` branches are
exercised end-to-end without a live Traefik/Caddy/HAProxy.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from proxy_aiops.config import TargetConfig
from proxy_aiops.platform import CADDY, HAPROXY, TRAEFIK, get_platform

runner = CliRunner()


class _Conn:
    def __init__(self, responses, platform=TRAEFIK):
        self.target = TargetConfig(name="t", platform=platform)
        self.platform = self.target.platform_obj
        self._responses = responses

    def get(self, path, **_kw):
        if path in self._responses:
            return self._responses[path]
        return {}


def _p(platform, resource, **fmt):
    return get_platform(platform).path(resource, **fmt)


def _patch_conn(monkeypatch, module, conn):
    """Wire a fake (conn, config) into a CLI leaf module's get_connection."""
    monkeypatch.setattr(module, "get_connection", lambda target, *a, **k: (conn, None))


_TRAEFIK_ROUTERS = [
    {"name": "api@file", "rule": "Host(`app.example.com`) && PathPrefix(`/api`)",
     "service": "app", "status": "enabled", "tls": {}},
]
_TRAEFIK_SERVICES = [
    {"name": "app@file",
     "loadBalancer": {"servers": [{"url": "http://10.0.0.5"}]},
     "serverStatus": {"http://10.0.0.5": "DOWN"}},
]
_TRAEFIK_METRICS = (
    'traefik_service_requests_total{code="200",service="app@file"} 900\n'
    'traefik_service_requests_total{code="502",service="app@file"} 100\n'
)


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_routes_list_show_find(monkeypatch):
    from proxy_aiops.cli import app
    from proxy_aiops.cli import routes as mod

    _patch_conn(monkeypatch, mod, _Conn({
        _p(TRAEFIK, "routers"): _TRAEFIK_ROUTERS,
        _p(TRAEFIK, "router_detail", name="api@file"): _TRAEFIK_ROUTERS[0],
    }))
    out = runner.invoke(app, ["routes", "list"])
    assert out.exit_code == 0
    assert json.loads(out.output)["total"] == 1

    out = runner.invoke(app, ["routes", "show", "api@file"])
    assert out.exit_code == 0 and "app.example.com" in out.output

    out = runner.invoke(app, ["routes", "find", "app.example.com", "--path", "/api/v1"])
    assert out.exit_code == 0 and json.loads(out.output)["matched"] == 1


# ── services ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_services_list_show_upstreams(monkeypatch):
    from proxy_aiops.cli import app
    from proxy_aiops.cli import services as mod

    _patch_conn(monkeypatch, mod, _Conn({_p(TRAEFIK, "services"): _TRAEFIK_SERVICES}))
    out = runner.invoke(app, ["services", "list"])
    assert out.exit_code == 0 and json.loads(out.output)["total"] == 1

    out = runner.invoke(app, ["services", "show", "app@file"])
    assert out.exit_code == 0

    out = runner.invoke(app, ["services", "upstreams"])
    assert out.exit_code == 0 and json.loads(out.output)["down"] == 1


# ── analyze (flagship RCAs from the CLI) ────────────────────────────────────


@pytest.mark.unit
def test_cli_analyze_health(monkeypatch):
    from proxy_aiops.cli import analyze as mod
    from proxy_aiops.cli import app

    _patch_conn(monkeypatch, mod, _Conn({_p(TRAEFIK, "services"): _TRAEFIK_SERVICES}))
    out = runner.invoke(app, ["analyze", "health"])
    assert out.exit_code == 0
    assert json.loads(out.output)["servicesEvaluated"] >= 1


@pytest.mark.unit
def test_cli_analyze_errors(monkeypatch):
    from proxy_aiops.cli import analyze as mod
    from proxy_aiops.cli import app

    _patch_conn(monkeypatch, mod, _Conn({_p(TRAEFIK, "metrics"): _TRAEFIK_METRICS}))
    out = runner.invoke(app, ["analyze", "errors", "--rate", "5"])
    assert out.exit_code == 0
    data = json.loads(out.output)
    assert data["flaggedCount"] == 1 and data["flagged"][0]["service"] == "app@file"


@pytest.mark.unit
def test_cli_analyze_conflicts(monkeypatch):
    from proxy_aiops.cli import analyze as mod
    from proxy_aiops.cli import app

    _patch_conn(monkeypatch, mod, _Conn({
        _p(TRAEFIK, "routers"): _TRAEFIK_ROUTERS,
        _p(TRAEFIK, "services"): _TRAEFIK_SERVICES,
    }))
    out = runner.invoke(app, ["analyze", "conflicts"])
    assert out.exit_code == 0 and "routesEvaluated" in out.output


@pytest.mark.unit
def test_cli_analyze_errors_caddy_reports_and_exits_one(monkeypatch):
    """error_counters teaches on caddy → the CLI surfaces it and exits 1."""
    from proxy_aiops.cli import analyze as mod
    from proxy_aiops.cli import app

    _patch_conn(monkeypatch, mod, _Conn({}, platform=CADDY))
    out = runner.invoke(app, ["analyze", "errors"])
    assert out.exit_code == 1
    assert "access logs" in out.output


@pytest.mark.unit
def test_cli_analyze_health_error_exits_one(monkeypatch):
    """A pull error from list_upstreams is rendered and exits 1."""
    from proxy_aiops.cli import analyze as mod
    from proxy_aiops.cli import app

    class _Boom(_Conn):
        def get(self, path, **_kw):
            raise RuntimeError("upstream boom")

    _patch_conn(monkeypatch, mod, _Boom({}, platform=HAPROXY))
    out = runner.invoke(app, ["analyze", "health"])
    assert out.exit_code == 1
    assert "error" in out.output.lower()


# ── config reads ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_config_snapshot_search_get(monkeypatch):
    from proxy_aiops.cli import app
    from proxy_aiops.cli import configcmd as mod

    cfg = {"apps": {"http": {"servers": {"srv0": {"listen": [":443"]}}}}}
    _patch_conn(monkeypatch, mod, _Conn({
        _p(CADDY, "config_snapshot"): cfg,
        _p(CADDY, "config_get") + "apps/http": {"servers": {}},
    }, platform=CADDY))

    out = runner.invoke(app, ["config", "snapshot"])
    assert out.exit_code == 0 and "apps" in out.output

    out = runner.invoke(app, ["config", "search", "srv0"])
    assert out.exit_code == 0 and "matches" in out.output

    out = runner.invoke(app, ["config", "get", "apps/http"])
    assert out.exit_code == 0 and json.loads(out.output)["path"] == "apps/http"


# ── certs + overview ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_certs_no_sweep_lists_inventory(monkeypatch):
    from proxy_aiops.cli import app
    from proxy_aiops.cli import certs as mod

    cfg = {"apps": {"http": {"servers": {
        "srv0": {"listen": [":443"], "routes": [
            {"match": [{"host": ["app.example.com"]}]}]}}}}}
    _patch_conn(monkeypatch, mod, _Conn({_p(CADDY, "config_root"): cfg}, platform=CADDY))
    out = runner.invoke(app, ["certs", "--no-sweep"])
    assert out.exit_code == 0
    assert json.loads(out.output)["probed"] == 0


@pytest.mark.unit
def test_cli_certs_sweep_buckets(monkeypatch):
    from proxy_aiops.cli import app
    from proxy_aiops.cli import certs as mod
    from proxy_aiops.ops import certs as cert_ops

    cfg = {"apps": {"http": {"servers": {
        "srv0": {"listen": [":443"], "routes": [
            {"match": [{"host": ["app.example.com"]}]}]}}}}}
    _patch_conn(monkeypatch, mod, _Conn({_p(CADDY, "config_root"): cfg}, platform=CADDY))
    monkeypatch.setattr(
        cert_ops, "probe_certificate",
        lambda domain, port=443: {"domain": domain, "daysToExpiry": 3.0},
    )
    out = runner.invoke(app, ["certs", "--sweep"])
    assert out.exit_code == 0
    assert json.loads(out.output)["critical"] == 1


@pytest.mark.unit
def test_cli_overview(monkeypatch):
    from proxy_aiops.cli import app
    from proxy_aiops.cli import overview as mod

    _patch_conn(monkeypatch, mod, _Conn({
        _p(TRAEFIK, "version"): {"Version": "3.1"},
        _p(TRAEFIK, "routers"): _TRAEFIK_ROUTERS,
        _p(TRAEFIK, "services"): _TRAEFIK_SERVICES,
    }))
    out = runner.invoke(app, ["overview"])
    assert out.exit_code == 0
    data = json.loads(out.output)
    assert data["platform"] == "traefik" and data["version"] == "3.1"
