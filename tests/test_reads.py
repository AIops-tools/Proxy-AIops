"""Read-path ops tests (status / routes / services / traffic / config / certs).

Uses a fake connection that returns canned JSON (or metrics text) per path,
so the cross-platform normalisation is exercised without a live proxy. The
fake carries a real Platform descriptor so ops resolve the same paths they
would in production.
"""

import pytest

from proxy_aiops.config import TargetConfig
from proxy_aiops.ops import certs, configread, overview, routes, services, status, traffic
from proxy_aiops.platform import CADDY, HAPROXY, TRAEFIK, UnsupportedOperation, get_platform


class _Conn:
    """Fake connection: get() looks up canned responses by path."""

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
                            "match": [{"host": ["app.example.com"], "path": ["/api*"]}],
                            "handle": [{
                                "handler": "reverse_proxy",
                                "upstreams": [{"dial": "10.0.0.5:8080"},
                                              {"dial": "10.0.0.6:8080"}],
                            }],
                        },
                        {
                            "match": [{"host": ["old.example.com"]}],
                            "handle": [{
                                "handler": "static_response",
                                "status_code": 302,
                                "headers": {"Location": ["https://app.example.com/"]},
                            }],
                        },
                    ],
                }
            }
        },
        "tls": {
            "automation": {"policies": [{"subjects": ["*.example.com"]}]},
        },
    }
}

_HAPROXY_STATS = [
    {
        "runtime_api": "unix",
        "stats": [
            {"name": "web", "type": "frontend",
             "stats": {"status": "OPEN", "req_tot": 100}},
            {"name": "app", "type": "backend",
             "stats": {"status": "UP", "req_tot": 1000, "hrsp_2xx": 900,
                       "hrsp_4xx": 40, "hrsp_5xx": 60, "rate": 12, "scur": 3}},
            {"name": "web1", "backend_name": "app", "type": "server",
             "stats": {"status": "UP", "check_status": "L7OK", "addr": "10.0.0.5:80",
                       "weight": 100, "req_tot": 500}},
            {"name": "web2", "backend_name": "app", "type": "server",
             "stats": {"status": "DOWN", "check_status": "L4CON", "addr": "10.0.0.6:80",
                       "weight": 100, "req_tot": 500, "lastchg": 120}},
            {"name": "web3", "backend_name": "app", "type": "server",
             "stats": {"status": "MAINT", "check_status": "", "addr": "10.0.0.7:80",
                       "weight": 100, "req_tot": 0}},
        ],
    }
]

_TRAEFIK_METRICS = """\
# HELP traefik_service_requests_total How many HTTP requests processed.
traefik_service_requests_total{code="200",method="GET",protocol="http",service="app@file"} 900
traefik_service_requests_total{code="502",method="GET",protocol="http",service="app@file"} 100
traefik_service_requests_total{code="200",method="GET",protocol="http",service="quiet@file"} 10
traefik_service_request_duration_seconds_sum{code="200",service="app@file"} 12.5
traefik_service_request_duration_seconds_count{code="200",service="app@file"} 1000
"""


# ── status ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_version_info_traefik():
    conn = _Conn({_p(TRAEFIK, "version"): {"Version": "3.1.2", "Codename": "x"}})
    out = status.version_info(conn)
    assert out["version"] == "3.1.2" and out["platform"] == "traefik"


@pytest.mark.unit
def test_version_info_haproxy_unwraps_api():
    conn = _Conn(
        {_p(HAPROXY, "version"): {"api": {"version": "2.9.1"}, "system": {"version": "2.9"}}},
        platform=HAPROXY,
    )
    out = status.version_info(conn)
    assert out["version"] == "2.9.1"


@pytest.mark.unit
def test_version_info_caddy_returns_teaching_note():
    conn = _Conn({}, platform=CADDY)
    out = status.version_info(conn)
    assert "caddy version" in out["note"]
    assert "error" not in out


@pytest.mark.unit
def test_list_entrypoints_per_platform():
    tr = _Conn({_p(TRAEFIK, "entrypoints"): [
        {"name": "web", "address": ":80"}, {"name": "websecure", "address": ":443"},
    ]})
    assert status.list_entrypoints(tr)["total"] == 2

    ca = _Conn({_p(CADDY, "config_root"): _CADDY_CONFIG}, platform=CADDY)
    out = status.list_entrypoints(ca)
    assert out["total"] == 1 and out["entrypoints"][0]["address"] == ":443"

    ha = _Conn({_p(HAPROXY, "frontends"): {"data": [
        {"name": "web", "mode": "http", "default_backend": "app"},
    ]}}, platform=HAPROXY)
    assert status.list_entrypoints(ha)["entrypoints"][0]["name"] == "web"


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_list_routes_traefik_parses_rule_hosts_and_paths():
    conn = _Conn({_p(TRAEFIK, "routers"): [
        {"name": "api@file", "rule": "Host(`app.example.com`) && PathPrefix(`/api`)",
         "service": "app", "priority": 10, "status": "enabled", "tls": {}},
        {"name": "dead@file", "rule": "Host(`x.example.com`)", "service": "gone",
         "status": "disabled"},
    ]})
    out = routes.list_routes(conn)
    assert out["total"] == 2
    r0 = out["routes"][0]
    assert r0["hosts"] == ["app.example.com"]
    assert r0["paths"] == ["/api"]
    assert r0["tls"] is True
    assert out["routes"][1]["enabled"] is False


@pytest.mark.unit
def test_list_routes_caddy_names_are_config_paths():
    conn = _Conn({_p(CADDY, "config_root"): _CADDY_CONFIG}, platform=CADDY)
    out = routes.list_routes(conn)
    assert out["total"] == 2
    assert out["routes"][0]["name"] == "apps/http/servers/srv0/routes/0"
    assert out["routes"][0]["service"] == "10.0.0.5:8080,10.0.0.6:8080"
    assert out["routes"][1]["redirectTo"] == "https://app.example.com/"


@pytest.mark.unit
def test_list_routes_haproxy_frontends_with_acl_note():
    conn = _Conn({_p(HAPROXY, "frontends"): {"data": [
        {"name": "web", "mode": "http", "default_backend": "app"},
    ]}}, platform=HAPROXY)
    out = routes.list_routes(conn)
    assert out["routes"][0]["service"] == "app"
    assert "ACL" in out["routes"][0]["note"]


@pytest.mark.unit
def test_route_detail_and_unknown_name():
    conn = _Conn({_p(CADDY, "config_root"): _CADDY_CONFIG}, platform=CADDY)
    out = routes.route_detail(conn, "apps/http/servers/srv0/routes/0")
    assert out["route"]["hosts"] == ["app.example.com"]
    missing = routes.route_detail(conn, "nope")
    assert "not found" in missing["error"]


@pytest.mark.unit
def test_find_route_static_match_orders_by_specificity():
    conn = _Conn({_p(TRAEFIK, "routers"): [
        {"name": "catchall", "rule": "Host(`app.example.com`)", "service": "a"},
        {"name": "api", "rule": "Host(`app.example.com`) && PathPrefix(`/api`)",
         "service": "b"},
    ]})
    out = routes.find_route(conn, "app.example.com", "/api/v1")
    assert out["matched"] == 2
    assert out["routes"][0]["name"] == "api"  # more specific path first
    none = routes.find_route(conn, "other.example.com", "/")
    assert none["matched"] == 0


# ── services / upstreams ────────────────────────────────────────────────────


@pytest.mark.unit
def test_list_services_traefik_counts_server_status():
    conn = _Conn({_p(TRAEFIK, "services"): [
        {"name": "app@file", "type": "loadbalancer",
         "loadBalancer": {"servers": [{"url": "http://10.0.0.5"}, {"url": "http://10.0.0.6"}]},
         "serverStatus": {"http://10.0.0.5": "UP", "http://10.0.0.6": "DOWN"}},
    ]})
    out = services.list_services(conn)
    svc = out["services"][0]
    assert svc["serversTotal"] == 2 and svc["serversUp"] == 1


@pytest.mark.unit
def test_list_services_haproxy_joins_stats():
    conn = _Conn({
        _p(HAPROXY, "backends"): {"data": [{"name": "app", "mode": "http"}]},
        _p(HAPROXY, "stats"): _HAPROXY_STATS,
    }, platform=HAPROXY)
    out = services.list_services(conn)
    svc = out["services"][0]
    assert svc["name"] == "app" and svc["serversTotal"] == 3 and svc["serversUp"] == 1


@pytest.mark.unit
def test_list_upstreams_haproxy_status_and_check():
    conn = _Conn({_p(HAPROXY, "stats"): _HAPROXY_STATS}, platform=HAPROXY)
    out = services.list_upstreams(conn)
    assert out["total"] == 3 and out["down"] == 1
    down = [u for u in out["upstreams"] if u["status"] == "down"][0]
    assert down["server"] == "web2" and down["checkInfo"] == "L4CON"
    maint = [u for u in out["upstreams"] if u["status"] == "maint"][0]
    assert maint["server"] == "web3"


@pytest.mark.unit
def test_list_upstreams_caddy_infers_health_from_fails():
    conn = _Conn({_p(CADDY, "upstreams"): [
        {"address": "10.0.0.5:8080", "num_requests": 4, "fails": 0},
        {"address": "10.0.0.6:8080", "num_requests": 0, "fails": 3},
    ]}, platform=CADDY)
    out = services.list_upstreams(conn)
    assert out["down"] == 1
    assert out["upstreams"][1]["checkInfo"] == "fails=3"


@pytest.mark.unit
def test_upstream_detail_found_and_missing():
    conn = _Conn({_p(HAPROXY, "stats"): _HAPROXY_STATS}, platform=HAPROXY)
    hit = services.upstream_detail(conn, "app", "web1")
    assert hit["upstream"]["status"] == "up"
    miss = services.upstream_detail(conn, "app", "nope")
    assert "not found" in miss["error"]


@pytest.mark.unit
def test_list_middlewares_traefik_and_teaching_on_haproxy():
    tr = _Conn({_p(TRAEFIK, "middlewares"): [
        {"name": "auth@file", "type": "basicAuth", "usedBy": ["api@file"]},
    ]})
    assert services.list_middlewares(tr)["total"] == 1
    ha = _Conn({}, platform=HAPROXY)
    out = services.list_middlewares(ha)
    assert "middleware" in out["unsupported"].lower()


# ── traffic / error counters ────────────────────────────────────────────────


@pytest.mark.unit
def test_error_counters_traefik_parses_metrics_text_by_code():
    conn = _Conn({_p(TRAEFIK, "metrics"): _TRAEFIK_METRICS})
    out = traffic.error_counters(conn)
    app = next(s_ for s_ in out["services"] if s_["service"] == "app@file")
    assert app["total"] == 1000.0
    assert app["codes"]["502"] == 100.0


@pytest.mark.unit
def test_error_counters_haproxy_uses_hrsp_classes():
    conn = _Conn({_p(HAPROXY, "stats"): _HAPROXY_STATS}, platform=HAPROXY)
    out = traffic.error_counters(conn)
    app = out["services"][0]
    assert app["service"] == "app" and app["classes"]["5xx"] == 60.0


@pytest.mark.unit
def test_error_counters_caddy_teaches():
    conn = _Conn({}, platform=CADDY)
    out = traffic.error_counters(conn)
    assert "error" in out and "access logs" in out["error"]


@pytest.mark.unit
def test_traffic_stats_traefik_latency():
    conn = _Conn({_p(TRAEFIK, "metrics"): _TRAEFIK_METRICS})
    out = traffic.traffic_stats(conn)
    app = next(s_ for s_ in out["services"] if s_["service"] == "app@file")
    assert app["avgLatencyMs"] == 12.5


@pytest.mark.unit
def test_parse_metrics_text_skips_comments_and_bad_lines():
    rows = traffic.parse_metrics_text("# c\nbad line\nm{a=\"b\"} 5\n")
    assert rows == [{"metric": "m", "labels": {"a": "b"}, "value": 5.0}]


# ── config reads ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_config_snapshot_and_search():
    conn = _Conn({_p(CADDY, "config_snapshot"): _CADDY_CONFIG}, platform=CADDY)
    snap = configread.config_snapshot(conn)
    assert "apps" in snap["config"]
    hits = configread.search_config(conn, "10.0.0.6")
    assert any("upstreams" in h["path"] for h in hits["matches"])
    with pytest.raises(ValueError):
        configread.search_config(conn, "  ")


@pytest.mark.unit
def test_config_snapshot_haproxy_teaches():
    conn = _Conn({}, platform=HAPROXY)
    out = configread.config_snapshot(conn)
    assert "unsupported" in out


@pytest.mark.unit
def test_get_config_value_encodes_path():
    seen = {}

    class _C(_Conn):
        def get(self, path, **kw):
            seen["path"] = path
            return {"ok": 1}

    conn = _C({}, platform=CADDY)
    out = configread.get_config_value(conn, "apps/http/servers/srv 0")
    assert out["value"] == {"ok": 1}
    assert seen["path"] == "/config/apps/http/servers/srv%200"


# ── certs ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_pull_tls_inventory_traefik_tls_routers_only():
    conn = _Conn({_p(TRAEFIK, "routers"): [
        {"name": "api@file", "rule": "Host(`app.example.com`)", "tls": {}},
        {"name": "plain@file", "rule": "Host(`plain.example.com`)"},
        {"name": "san@file", "rule": "HostSNI(`*`)",
         "tls": {"domains": [{"main": "example.com", "sans": ["www.example.com"]}]}},
    ]})
    inv = {c["domain"] for c in certs.pull_tls_inventory(conn)}
    assert inv == {"app.example.com", "example.com", "www.example.com"}


@pytest.mark.unit
def test_pull_tls_inventory_caddy_hosts_and_policies():
    conn = _Conn({_p(CADDY, "config_root"): _CADDY_CONFIG}, platform=CADDY)
    inv = {c["domain"] for c in certs.pull_tls_inventory(conn)}
    assert {"app.example.com", "old.example.com", "*.example.com"} == inv


@pytest.mark.unit
def test_tls_inventory_haproxy_raises_teaching():
    conn = _Conn({}, platform=HAPROXY)
    with pytest.raises(UnsupportedOperation, match="pem"):
        certs.pull_tls_inventory(conn)
    out = certs.list_certificates(conn)
    assert "unsupported" in out


@pytest.mark.unit
def test_list_certificates_no_probe_returns_inventory():
    conn = _Conn({_p(CADDY, "config_root"): _CADDY_CONFIG}, platform=CADDY)
    out = certs.list_certificates(conn, probe=False)
    assert out["total"] == 3 and out["probed"] == 0
    assert all("daysToExpiry" not in c for c in out["certificates"])


# ── overview ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_proxy_overview_resilient_shapes():
    conn = _Conn({
        _p(TRAEFIK, "version"): {"Version": "3.1"},
        _p(TRAEFIK, "routers"): [{"name": "r", "rule": "Host(`a`)", "service": "app"}],
        _p(TRAEFIK, "services"): [
            {"name": "app@file",
             "loadBalancer": {"servers": [{"url": "http://x"}]},
             "serverStatus": {"http://x": "DOWN"}},
        ],
    })
    out = overview.proxy_overview(conn)
    assert out["platform"] == "traefik"
    assert out["version"] == "3.1"
    assert out["routesTotal"] == 1
    assert out["servicesWithNoServerUp"] == 1
    assert out["upstreamsDown"] == 1
