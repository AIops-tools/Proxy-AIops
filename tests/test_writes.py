"""Unit tests for the governed proxy writes (ops + MCP tools).

Proves: every write captures REAL prior state BEFORE mutating (caddy config
subtree via GET, haproxy runtime server via GET); the support matrix raises
teaching errors on the wrong platform BEFORE anything mutates; risk tiers are
correct (delete/load = high, the rest = medium); dry_run previews never
mutate; the undo descriptors invert correctly AND replay against the target
tool's real signature. No real proxy — the connection is a fake/MagicMock.
"""

import inspect
import json
from unittest.mock import MagicMock

import pytest

from proxy_aiops.connection import ProxyApiError
from proxy_aiops.platform import CADDY, HAPROXY, TRAEFIK, UnsupportedOperation, get_platform


def _conn(platform=CADDY, responses=None):
    conn = MagicMock(name="conn")
    conn.target.platform = platform
    conn.platform = get_platform(platform)
    responses = responses or {}

    def _get(path, **kw):
        if path in responses:
            value = responses[path]
            if isinstance(value, Exception):
                raise value
            return value
        raise ProxyApiError("not found", status_code=404, path=path)

    conn.get.side_effect = _get
    return conn


# ── caddy config writes capture prior state ─────────────────────────────────


@pytest.mark.unit
def test_set_config_value_captures_prior_subtree_before_patch():
    from proxy_aiops.ops import writes as ops

    prior = [{"dial": "10.0.0.5:8080"}]
    path = "apps/http/servers/srv0/routes/0/handle/0/upstreams"
    conn = _conn(CADDY, {f"/config/{path}": prior})

    out = ops.set_config_value(conn, path, [{"dial": "10.0.0.9:8080"}])

    assert out["priorState"] == {"value": prior, "existed": True}
    conn.patch.assert_called_once()
    called_path, kwargs = conn.patch.call_args
    assert called_path[0] == f"/config/{path}"
    assert kwargs["json"] == [{"dial": "10.0.0.9:8080"}]
    conn.post.assert_not_called()


@pytest.mark.unit
def test_set_config_value_creates_missing_path_with_post():
    from proxy_aiops.ops import writes as ops

    conn = _conn(CADDY)  # GET 404s everywhere
    out = ops.set_config_value(conn, "apps/http/servers/srv1", {"listen": [":80"]})
    assert out["priorState"] == {"value": None, "existed": False}
    conn.post.assert_called_once()
    conn.patch.assert_not_called()


@pytest.mark.unit
def test_delete_config_path_captures_prior_and_requires_existence():
    from proxy_aiops.ops import writes as ops

    path = "apps/http/servers/srv0/routes/1"
    prior = {"match": [{"host": ["old.example.com"]}]}
    conn = _conn(CADDY, {f"/config/{path}": prior})
    out = ops.delete_config_path(conn, path)
    assert out["priorState"] == {"value": prior, "existed": True}
    conn.delete.assert_called_once()

    with pytest.raises(KeyError, match="does not exist"):
        ops.delete_config_path(_conn(CADDY), "apps/missing")


@pytest.mark.unit
def test_load_config_snapshots_full_prior_config():
    from proxy_aiops.ops import writes as ops

    prior = {"apps": {"http": {"servers": {}}}}
    conn = _conn(CADDY, {"/config/": prior})
    new_cfg = {"apps": {"http": {"servers": {"srv0": {"listen": [":80"]}}}}}
    out = ops.load_config(conn, new_cfg)
    assert out["priorState"] == {"config": prior}
    called_path, kwargs = conn.post.call_args
    assert called_path[0] == "/load" and kwargs["json"] == new_cfg

    with pytest.raises(ValueError, match="non-empty"):
        ops.load_config(conn, {})


@pytest.mark.unit
def test_config_write_path_traversal_rejected_before_any_call():
    from proxy_aiops.ops import writes as ops

    conn = _conn(CADDY)
    with pytest.raises(ValueError, match="dot-segments"):
        ops.set_config_value(conn, "apps/../../load", {"x": 1})
    conn.get.assert_not_called()
    conn.patch.assert_not_called()
    conn.post.assert_not_called()


# ── haproxy runtime-server writes capture prior state ───────────────────────


def _runtime_path(backend="app", server="web1"):
    return get_platform(HAPROXY).path("runtime_server", name=server, backend=backend)


@pytest.mark.unit
def test_set_server_state_captures_prior_admin_state():
    from proxy_aiops.ops import writes as ops

    conn = _conn(HAPROXY, {_runtime_path(): {"admin_state": "ready", "weight": 100}})
    out = ops.set_server_state(conn, "app", "web1", "drain")
    assert out["priorState"] == {"adminState": "ready"}
    called_path, kwargs = conn.put.call_args
    assert called_path[0] == _runtime_path()
    assert kwargs["json"] == {"admin_state": "drain"}


@pytest.mark.unit
def test_set_server_state_validates_state():
    from proxy_aiops.ops import writes as ops

    conn = _conn(HAPROXY)
    with pytest.raises(ValueError, match="ready"):
        ops.set_server_state(conn, "app", "web1", "offline")
    conn.put.assert_not_called()


@pytest.mark.unit
def test_set_server_weight_captures_prior_weight_and_validates():
    from proxy_aiops.ops import writes as ops

    conn = _conn(HAPROXY, {_runtime_path(): {"admin_state": "ready", "weight": 100}})
    out = ops.set_server_weight(conn, "app", "web1", 0)
    assert out["priorState"] == {"weight": 100}
    _, kwargs = conn.put.call_args
    assert kwargs["json"] == {"weight": 0}

    with pytest.raises(ValueError, match="between"):
        ops.set_server_weight(conn, "app", "web1", 999)


# ── wrong-platform writes raise the support matrix's teaching error ─────────


@pytest.mark.unit
def test_traefik_writes_teach_the_provider_before_mutating():
    from proxy_aiops.ops import writes as ops

    conn = _conn(TRAEFIK)
    with pytest.raises(UnsupportedOperation, match="providers"):
        ops.set_config_value(conn, "apps/http", {"x": 1})
    with pytest.raises(UnsupportedOperation, match="provider"):
        ops.set_server_state(conn, "app", "web1", "drain")
    conn.patch.assert_not_called()
    conn.put.assert_not_called()
    conn.post.assert_not_called()


@pytest.mark.unit
def test_caddy_server_writes_and_haproxy_config_writes_teach():
    from proxy_aiops.ops import writes as ops

    caddy = _conn(CADDY)
    with pytest.raises(UnsupportedOperation, match="config tree"):
        ops.set_server_state(caddy, "app", "web1", "maint")
    ha = _conn(HAPROXY)
    with pytest.raises(UnsupportedOperation, match="set_server_state"):
        ops.set_config_value(ha, "apps/http", {"x": 1})


@pytest.mark.unit
def test_teaching_error_passes_through_mcp_error_layer(monkeypatch):
    """At the MCP layer the teaching text must reach the agent (sanitised),
    not collapse into a generic 'operation failed'."""
    from mcp_server.tools import writes as t

    conn = _conn(TRAEFIK)
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)
    out = t.set_config_value(path="apps/http", value={"x": 1})
    assert "providers" in out["error"]


# ── governed tools record real undo tokens ──────────────────────────────────


@pytest.mark.unit
def test_governed_set_server_state_records_undo_token(monkeypatch):
    from mcp_server.tools import writes as t
    from proxy_aiops.governance.undo import get_undo_store

    conn = _conn(HAPROXY, {_runtime_path(): {"admin_state": "ready", "weight": 100}})
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    result = t.set_server_state(backend="app", server="web1", state="maint")

    assert "_undo_id" in result
    recorded = get_undo_store().list()
    assert any(u.get("tool") == "set_server_state" for u in recorded)


# ── risk tiers ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_write_risk_tiers():
    from mcp_server.tools import writes as t

    assert t.delete_config_path._risk_level == "high"
    assert t.load_config._risk_level == "high"
    for fn in (t.set_config_value, t.set_server_state, t.set_server_weight):
        assert fn._risk_level == "medium"


# ── dry-run previews never mutate ───────────────────────────────────────────


@pytest.mark.unit
def test_dry_run_previews_do_not_mutate(monkeypatch):
    from mcp_server.tools import writes as t

    conn = _conn(CADDY)
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    assert t.set_config_value(path="a/b", value=1, dry_run=True)["dryRun"] is True
    assert t.delete_config_path(path="a/b", dry_run=True)["dryRun"] is True
    assert t.load_config(config={"apps": {}}, dry_run=True)["dryRun"] is True
    assert t.set_server_state(backend="b", server="s", state="drain",
                              dry_run=True)["dryRun"] is True
    assert t.set_server_weight(backend="b", server="s", weight=1,
                               dry_run=True)["dryRun"] is True
    conn.get.assert_not_called()
    conn.patch.assert_not_called()
    conn.post.assert_not_called()
    conn.put.assert_not_called()
    conn.delete.assert_not_called()


# ── undo descriptors invert correctly and REPLAY against real signatures ────


def _assert_replayable(desc):
    """The undo descriptor's params must bind to the target tool's signature."""
    from mcp_server.tools import writes as t

    tool = getattr(t, desc["tool"])
    sig = inspect.signature(tool)
    sig.bind_partial(**desc["params"])  # raises TypeError on a mismatch
    return tool


@pytest.mark.unit
def test_set_config_undo_restores_prior_subtree_and_replays():
    from mcp_server.tools import writes as t

    desc = t._set_config_undo(
        {"path": "a/b"}, {"priorState": {"value": {"old": 1}, "existed": True}}
    )
    assert desc["tool"] == "set_config_value"
    assert desc["params"] == {"path": "a/b", "value": {"old": 1}}
    _assert_replayable(desc)

    created = t._set_config_undo(
        {"path": "a/b"}, {"priorState": {"value": None, "existed": False}}
    )
    assert created["tool"] == "delete_config_path"
    _assert_replayable(created)


@pytest.mark.unit
def test_delete_and_load_undo_descriptors():
    from mcp_server.tools import writes as t

    d = t._delete_config_undo({"path": "a/b"},
                              {"priorState": {"value": {"x": 1}, "existed": True}})
    assert d["tool"] == "set_config_value" and d["params"]["value"] == {"x": 1}
    _assert_replayable(d)

    ld = t._load_config_undo({}, {"priorState": {"config": {"apps": {}}}})
    assert ld["tool"] == "load_config" and ld["params"] == {"config": {"apps": {}}}
    _assert_replayable(ld)
    assert t._load_config_undo({}, {"priorState": {"config": {}}}) is None


@pytest.mark.unit
def test_server_undo_descriptors():
    from mcp_server.tools import writes as t

    st = t._server_state_undo({"backend": "app", "server": "w1"},
                              {"priorState": {"adminState": "ready"}})
    assert st["tool"] == "set_server_state"
    assert st["params"] == {"backend": "app", "server": "w1", "state": "ready"}
    _assert_replayable(st)
    # unknown prior state → no undo rather than a junk replay
    assert t._server_state_undo({"backend": "a", "server": "s"},
                                {"priorState": {"adminState": None}}) is None

    wt = t._server_weight_undo({"backend": "app", "server": "w1"},
                               {"priorState": {"weight": 100}})
    assert wt["params"]["weight"] == 100
    _assert_replayable(wt)


@pytest.mark.unit
def test_undo_replay_executes_end_to_end(monkeypatch):
    """Full loop: do a write, take the recorded undo descriptor, replay it via
    the named governed tool, and verify the inverse API call fires."""
    from mcp_server.tools import writes as t
    from proxy_aiops.governance.undo import get_undo_store

    conn = _conn(HAPROXY, {_runtime_path(): {"admin_state": "ready", "weight": 100}})
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    t.set_server_state(backend="app", server="web1", state="maint")
    row = next(u for u in get_undo_store().list() if u["undo_tool"] == "set_server_state")
    desc = {"tool": row["undo_tool"], "params": json.loads(row["undo_params"])}
    assert desc["params"] == {"backend": "app", "server": "web1", "state": "ready"}

    # Replay: the runtime server now reports maint; undo must PUT ready back.
    replay_conn = _conn(HAPROXY, {_runtime_path(): {"admin_state": "maint", "weight": 100}})
    monkeypatch.setattr(t, "_get_connection", lambda target=None: replay_conn)
    tool = getattr(t, desc["tool"])
    result = tool(**desc["params"])
    assert result["state"] == "ready"
    _, kwargs = replay_conn.put.call_args
    assert kwargs["json"] == {"admin_state": "ready"}
