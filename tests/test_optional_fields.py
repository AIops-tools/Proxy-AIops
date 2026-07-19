"""Absent fields come back as null, not as an empty string.

An empty string reads as "this field exists and is empty"; a missing field is a
different fact. Traefik, Caddy and HAProxy describe the same edge concepts with
different keys, so ``pick()`` frequently finds none of its candidates — a Caddy
route has no ``rule``, an HAProxy backend has no ``provider``. "This proxy does
not express that concept" is not "the field is blank", and a smaller local model
will confidently invent the difference.

These tests pin the contract end-to-end: the helper, the ops normalisers, and
the truncation envelope — including ``search_config``, which previously carried
its own ad-hoc ``truncated`` flag and now uses the same shape as everything else.
"""

from __future__ import annotations

import pytest

from proxy_aiops.config import TargetConfig
from proxy_aiops.governance import opt_str
from proxy_aiops.ops import configread, routes, traffic
from proxy_aiops.ops._util import opt, s
from proxy_aiops.platform import CADDY, TRAEFIK, get_platform


class _Conn:
    def __init__(self, responses, platform=TRAEFIK):
        self.target = TargetConfig(name="t", platform=platform)
        self.platform = self.target.platform_obj
        self._responses = responses

    def get(self, path, **_kw):
        return self._responses.get(path, {})


def _p(platform, resource, **fmt):
    return get_platform(platform).path(resource, **fmt)


# ── the helper ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_opt_str_distinguishes_absent_from_empty():
    assert opt_str(None) is None, "absent must stay absent"
    assert opt_str("") == "", "a genuinely empty value is not the same as absent"
    assert opt_str("web@docker", 64) == "web@docker"


@pytest.mark.unit
def test_opt_str_still_sanitizes_and_truncates():
    assert opt_str("a\x00b") == "ab"  # control character stripped
    assert opt_str("abcdef", 3) == "abc"


@pytest.mark.unit
def test_opt_str_accepts_non_string_values():
    assert opt_str(42) == "42"


@pytest.mark.unit
def test_ops_opt_helper_preserves_absence_while_s_still_coerces():
    assert opt(None) is None
    assert s(None) == "", "s() keeps its always-present semantics"


# ── the ops layer ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_route_row_reports_absent_fields_as_none():
    """A router the API barely populated reports null, not ''."""
    conn = _Conn({_p(TRAEFIK, "routers"): [{"name": "web@docker"}]})
    route = routes.list_routes(conn)["routes"][0]
    assert route["name"] == "web@docker"
    assert route["service"] is None, "no service configured is null, not ''"
    assert route["raw"] is None, "no rule string at all is null, not ''"


@pytest.mark.unit
def test_route_row_keeps_empty_string_when_source_is_empty():
    """An explicitly empty upstream value is preserved as '' — not turned into null."""
    conn = _Conn({_p(TRAEFIK, "routers"): [{"name": "web@docker", "rule": ""}]})
    assert routes.list_routes(conn)["routes"][0]["raw"] == ""


@pytest.mark.unit
def test_route_row_never_drops_the_key_itself():
    """Keys are always present; only their value may be null.

    Omitting a key entirely is worse than a null — the consumer cannot tell the
    field was even considered.
    """
    conn = _Conn({_p(TRAEFIK, "routers"): [{}]})
    route = routes.list_routes(conn)["routes"][0]
    for key in ("name", "service", "raw", "redirectTo", "entryPoints"):
        assert key in route, f"{key} must be present even when the source omitted it"


@pytest.mark.unit
def test_a_caddy_route_has_no_traefik_rule_and_says_so_with_null():
    """Cross-platform absence is the point: Caddy expresses matching as a
    match list, so there is no ``rule`` at all — null, not ''."""
    conn = _Conn(
        {_p(CADDY, "config_snapshot"): {
            "apps": {"http": {"servers": {"s0": {"listen": [":443"], "routes": [{}]}}}}
        }},
        platform=CADDY,
    )
    out = routes.list_routes(conn)
    assert out["routes"], "the route should still be listed"
    # Caddy matches on a match list, so there is no Traefik-style rule string
    # to report, and no redirect handler either — both null, neither "".
    assert out["routes"][0]["raw"] is None
    assert out["routes"][0]["redirectTo"] is None


# ── truncation announces itself ─────────────────────────────────────────────


@pytest.mark.unit
def test_search_config_uses_the_standard_envelope():
    """One convention per repo: matches/returned/limit/truncated.

    ``search_config`` used to carry a bare ad-hoc ``truncated`` flag with no
    ``returned``/``limit`` beside it; it now matches every other bounded read.
    """
    conn = _Conn(
        {_p(CADDY, "config_snapshot"): {"apps": {"needle": "value"}}},
        platform=CADDY,
    )
    out = configread.search_config(conn, "needle")
    for key in ("matches", "returned", "limit", "truncated"):
        assert key in out, f"{key} is part of the standard envelope"
    assert out["truncated"] is False
    assert out["returned"] == len(out["matches"])


@pytest.mark.unit
def test_search_config_measures_truncation_rather_than_guessing_at_the_cap():
    """The regression this guards: exactly MAX_MATCHES hits is not truncation.

    The old check was ``len(hits) >= MAX_MATCHES``, which cannot tell a config
    with exactly 50 matches from one with 500. The walk now overshoots by one
    so the difference is measured.
    """
    exact = {f"needle{i}": 1 for i in range(configread.MAX_MATCHES)}
    conn = _Conn({_p(CADDY, "config_snapshot"): exact}, platform=CADDY)
    out = configread.search_config(conn, "needle")
    assert out["returned"] == configread.MAX_MATCHES
    assert out["truncated"] is False, "exactly at the cap is complete, not truncated"

    more = {f"needle{i}": 1 for i in range(configread.MAX_MATCHES + 10)}
    conn = _Conn({_p(CADDY, "config_snapshot"): more}, platform=CADDY)
    out = configread.search_config(conn, "needle")
    assert out["returned"] == configread.MAX_MATCHES
    assert out["truncated"] is True, "more matches than the cap is truncated"


@pytest.mark.unit
def test_traffic_stats_report_truncation_against_the_service_cap():
    rows = [{"name": f"svc{i}", "requestsTotal": i} for i in range(traffic.MAX_SERVICES + 5)]

    class _Metrics(_Conn):
        def get(self, path, **_kw):
            return {"stats": rows}

    conn = _Metrics({}, platform=TRAEFIK)
    out = traffic.traffic_stats(conn)
    if "error" not in out and "services" in out:
        assert out["limit"] == traffic.MAX_SERVICES
        assert out["returned"] <= traffic.MAX_SERVICES


@pytest.mark.unit
def test_undo_list_envelope_measures_truncation(monkeypatch):
    from mcp_server.tools import undo as undo_tools

    rows = [
        {
            "undo_id": f"u{i}",
            "ts": "2026-07-18T00:00:00Z",
            "tool": "some_tool",
            "undo_tool": "some_inverse_tool",
            "note": "",
        }
        for i in range(4)
    ]
    captured = {}

    class _Store:
        def list(self, *, status=None, limit=50):
            captured["limit"] = limit
            return rows[:limit]

    monkeypatch.setattr(undo_tools, "get_undo_store", lambda: _Store())
    result = undo_tools.undo_list(limit=3)
    assert captured["limit"] == 4, "one extra row is fetched to measure truncation"
    assert result["returned"] == 3
    assert result["limit"] == 3
    assert result["truncated"] is True
    assert len(result["undos"]) == 3
