"""Flagship analyses — pure-function tests (no I/O, no live proxy).

Each RCA is fed injected telemetry rows and must return transparent findings
that carry their numbers (cause + action, never a bare verdict).
"""

import pytest

from proxy_aiops.ops.analysis import (
    backend_health_rca,
    cert_expiry_sweep,
    error_rate_rca,
    route_conflict_analysis,
)

# ── 1. backend health RCA ────────────────────────────────────────────────────


def _u(service, server, status, check=""):
    return {"service": service, "server": server, "address": f"{server}:80",
            "status": status, "checkInfo": check}


@pytest.mark.unit
def test_backend_health_rca_full_outage_connection_refused():
    out = backend_health_rca([
        _u("app", "web1", "down", "L4CON"),
        _u("app", "web2", "down", "L4CON"),
        _u("ok", "web3", "up"),
    ])
    assert out["servicesEvaluated"] == 2
    assert out["outages"] == 1
    worst = out["findings"][0]
    assert worst["service"] == "app"
    assert "OUTAGE" in worst["cause"]
    assert "connection refused" in worst["cause"]
    assert len(worst["failingServers"]) == 2


@pytest.mark.unit
def test_backend_health_rca_l7_check_failure_degraded():
    out = backend_health_rca([
        _u("app", "web1", "up"),
        _u("app", "web2", "down", "L7STS"),
    ])
    finding = out["findings"][0]
    assert out["degraded"] == 1
    assert "Degraded" in finding["cause"]
    assert "failing HTTP status" in finding["cause"]


@pytest.mark.unit
def test_backend_health_rca_dns_and_maint_causes():
    dns = backend_health_rca([_u("a", "s1", "down", "RESOLV")])["findings"][0]
    assert "DNS" in dns["cause"]
    maint = backend_health_rca([_u("b", "s1", "maint")])["findings"][0]
    assert "maint" in maint["cause"].lower()
    assert "set_server_state" in maint["action"]


@pytest.mark.unit
def test_backend_health_rca_all_healthy_and_no_servers():
    healthy = backend_health_rca([_u("a", "s1", "up")])["findings"][0]
    assert healthy["cause"].startswith("Healthy")
    empty = backend_health_rca([])
    assert empty["servicesEvaluated"] == 0


# ── 2. cert expiry sweep ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_cert_expiry_sweep_buckets_and_ordering():
    out = cert_expiry_sweep([
        {"domain": "ok.example.com", "daysToExpiry": 200},
        {"domain": "warn.example.com", "daysToExpiry": 20},
        {"domain": "crit.example.com", "daysToExpiry": 3},
        {"domain": "dead.example.com", "daysToExpiry": -2},
        {"domain": "unknown.example.com", "error": "handshake timeout"},
    ], platform="caddy")
    assert out["certsEvaluated"] == 5
    assert (out["expired"], out["critical"], out["warning"], out["ok"]) == (1, 1, 1, 1)
    assert out["unknown"] == 1
    # soonest expiry first
    assert [c["domain"] for c in out["certificates"]][:2] == [
        "dead.example.com", "crit.example.com"]
    assert "ACME" in out["renewalHint"]


@pytest.mark.unit
def test_cert_expiry_sweep_platform_hints_differ():
    tr = cert_expiry_sweep([], platform="traefik")
    assert "acme.json" in tr["renewalHint"]
    generic = cert_expiry_sweep([], platform="")
    assert "issuer" in generic["renewalHint"]


@pytest.mark.unit
def test_cert_expiry_sweep_custom_thresholds():
    out = cert_expiry_sweep(
        [{"domain": "d", "daysToExpiry": 50}], warn_days=60, critical_days=45,
    )
    assert out["certificates"][0]["bucket"] == "warning"
    assert out["thresholds"] == {"warnDays": 60, "criticalDays": 45}


# ── 3. error-rate RCA ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_error_rate_rca_flags_by_dominant_code():
    out = error_rate_rca([
        {"service": "app", "total": 1000, "codes": {"200": 900, "502": 100}},
        {"service": "quiet", "total": 10, "codes": {"500": 10}},  # under min_requests
        {"service": "fine", "total": 1000, "codes": {"200": 999, "500": 1}},
    ])
    assert out["servicesEvaluated"] == 3
    assert out["flaggedCount"] == 1
    flagged = out["flagged"][0]
    assert flagged["service"] == "app"
    assert flagged["errorRatePct"] == 10.0
    assert flagged["dominantCode"] == "502"
    assert "Bad Gateway" in flagged["cause"]
    assert flagged["vsBaselineX"] is not None


@pytest.mark.unit
def test_error_rate_rca_code_causes():
    def flag(code):
        out = error_rate_rca([{"service": "s", "total": 100, "codes": {code: 50}}])
        return out["flagged"][0]

    assert "no healthy upstream" in flag("503")["cause"]
    assert "too slowly" in flag("504")["cause"]
    assert "application bug" in flag("500")["action"] or "app" in flag("500")["cause"]
    assert flag("503")["severity"] == "critical"  # 50% >= 25%


@pytest.mark.unit
def test_error_rate_rca_class_only_counters_generic_cause():
    out = error_rate_rca([
        {"service": "app", "total": 1000, "classes": {"5xx": 60}},
    ])
    flagged = out["flagged"][0]
    assert flagged["dominantCode"] is None
    assert "no per-code breakdown" in flagged["cause"]


@pytest.mark.unit
def test_error_rate_rca_fleet_baseline_weighted():
    out = error_rate_rca([
        {"service": "a", "total": 900, "codes": {"200": 900}},
        {"service": "b", "total": 100, "codes": {"500": 100}},
    ])
    assert out["fleetErrorRatePct"] == 10.0  # 100 / 1000


# ── 4. route conflict & shadow analysis ──────────────────────────────────────


def _r(name, hosts=None, paths=None, priority=0, service="", redirect="", enabled=True):
    return {"name": name, "hosts": hosts or [], "paths": paths or [],
            "priority": priority, "service": service, "redirectTo": redirect,
            "enabled": enabled}


@pytest.mark.unit
def test_shadowed_route_detected_by_priority_order():
    out = route_conflict_analysis([
        _r("specific", ["app.example.com"], ["/api"], priority=1, service="svc"),
        _r("broad", ["app.example.com"], [], priority=10, service="svc"),
    ])
    assert out["shadowedCount"] == 1
    finding = out["shadowedRoutes"][0]
    assert finding["route"] == "specific"
    assert finding["shadowedBy"] == "broad"


@pytest.mark.unit
def test_catchall_does_not_shadow_and_disabled_ignored():
    out = route_conflict_analysis([
        _r("default", [], [], priority=100, service="svc"),  # match-all catch-all
        _r("api", ["a.example.com"], ["/api"], service="svc"),
        _r("off", ["a.example.com"], ["/api"], enabled=False),
    ])
    assert out["shadowedCount"] == 0
    assert out["routesEvaluated"] == 2


@pytest.mark.unit
def test_dead_routes_missing_service_and_zero_up():
    out = route_conflict_analysis(
        [
            _r("r1", ["a"], service="ghost"),
            _r("r2", ["b"], service="empty"),
            _r("r3", ["c"], service="ok"),
        ],
        services=[
            {"name": "empty", "serversTotal": 2, "serversUp": 0},
            {"name": "ok", "serversTotal": 2, "serversUp": 2},
        ],
    )
    reasons = {d["route"]: d["reason"] for d in out["deadRoutes"]}
    assert reasons == {"r1": "service not found", "r2": "service has zero servers up"}


@pytest.mark.unit
def test_redirect_loop_detected():
    out = route_conflict_analysis([
        _r("a", ["a.example.com"], redirect="https://b.example.com/"),
        _r("b", ["b.example.com"], redirect="https://a.example.com/"),
    ])
    assert out["redirectLoopCount"] >= 1
    chain = out["redirectLoops"][0]["chain"]
    assert chain[0] in ("a", "b") and chain[0] == chain[-1]


@pytest.mark.unit
def test_redirect_chain_without_loop_not_flagged():
    out = route_conflict_analysis([
        _r("old", ["old.example.com"], redirect="https://new.example.com/"),
        _r("new", ["new.example.com"], service="svc"),
    ])
    assert out["redirectLoopCount"] == 0
