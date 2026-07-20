"""Refuse caddy config writes that tear down this tool's own transport.

``encode_config_path`` blocks traversal *out* of ``/config/`` — but ``admin`` is
a legitimate top-level key *inside* the caddy config tree, and the caddy admin
API (``http://127.0.0.1:2019`` by default) is the only transport proxy-aiops
has. ``set_config_value("admin/disabled", True)`` therefore succeeds, stops the
listener, and leaves the recorded undo trying to re-POST the prior config over
an API that no longer exists. The tool's own setup guide already lists "caddy
admin listener disabled" as a terminal failure; it must not be able to cause it.

The guard must be EXACT (every other config path stays writable — that is the
tool's whole job) and FAIL OPEN on the full-config comparison, where the live
admin block may not be readable.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from proxy_aiops.connection import ProxyApiError
from proxy_aiops.ops import writes as ops
from proxy_aiops.ops.writes import SelfLockout
from proxy_aiops.platform import CADDY, get_platform

_ROOT = "/config/"


def _conn(responses=None):
    conn = MagicMock(name="conn")
    conn.target.platform = CADDY
    conn.platform = get_platform(CADDY)
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


def _assert_nothing_mutated(conn):
    conn.patch.assert_not_called()
    conn.post.assert_not_called()
    conn.delete.assert_not_called()


# ── set_config_value / delete_config_path: the admin subtree ────────────────


@pytest.mark.unit
@pytest.mark.parametrize("path", ["admin", "admin/disabled", "/admin/listen",
                                  "admin/config/persist"])
def test_setting_any_admin_path_is_refused(path):
    conn = _conn()
    with pytest.raises(SelfLockout, match="admin API"):
        ops.set_config_value(conn, path, True)
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_the_refusal_says_why_and_what_to_do_instead():
    with pytest.raises(SelfLockout) as ei:
        ops.set_config_value(_conn(), "admin/disabled", True)
    msg = str(ei.value)
    assert "reversibility" in msg, "must name the concrete failure: the undo cannot replay"
    assert "config file" in msg, "must offer the route that does work"


@pytest.mark.unit
def test_deleting_the_admin_subtree_is_refused():
    conn = _conn({"/config/admin": {"listen": "127.0.0.1:2019"}})
    with pytest.raises(SelfLockout, match="admin API"):
        ops.delete_config_path(conn, "admin")
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_deleting_the_config_root_is_refused():
    """A root delete removes every block, admin included."""
    conn = _conn({_ROOT: {"admin": {"listen": "127.0.0.1:2019"}}})
    with pytest.raises(SelfLockout, match="config root"):
        ops.delete_config_path(conn, "")
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_ordinary_config_paths_still_work():
    """The guard must be exact — writing routes is the tool's whole job."""
    path = "apps/http/servers/srv0/routes/0/handle/0/upstreams"
    conn = _conn({f"/config/{path}": [{"dial": "10.0.0.5:8080"}]})
    out = ops.set_config_value(conn, path, [{"dial": "10.0.0.9:8080"}])
    assert out["priorState"]["existed"] is True
    conn.patch.assert_called_once()


@pytest.mark.unit
def test_a_nested_key_named_admin_is_not_the_listener():
    """Only the TOP-LEVEL admin key configures the transport."""
    path = "apps/http/servers/srv0/routes/admin"
    conn = _conn({f"/config/{path}": {"match": []}})
    ops.set_config_value(conn, path, {"match": [{"host": ["x.example.com"]}]})
    conn.patch.assert_called_once()


@pytest.mark.unit
def test_a_key_that_merely_encodes_to_look_like_admin_is_not_blocked():
    """'%61dmin' is re-encoded and addresses a literally-named key, not admin."""
    conn = _conn({"/config/%2561dmin": {"x": 1}})
    ops.set_config_value(conn, "%61dmin", {"x": 2})
    conn.patch.assert_called_once()


# ── load_config: compare the incoming admin block against the live one ──────


@pytest.mark.unit
def test_load_config_refuses_a_config_that_disables_admin():
    conn = _conn({_ROOT: {"admin": {"listen": "127.0.0.1:2019"}, "apps": {}}})
    with pytest.raises(SelfLockout, match="admin.disabled"):
        ops.load_config(conn, {"admin": {"disabled": True}, "apps": {}})
    conn.post.assert_not_called()


@pytest.mark.unit
def test_load_config_refuses_a_config_that_moves_the_listener():
    conn = _conn({_ROOT: {"admin": {"listen": "127.0.0.1:2019"}, "apps": {}}})
    with pytest.raises(SelfLockout, match="admin.listen"):
        ops.load_config(conn, {"admin": {"listen": "127.0.0.1:9999"}, "apps": {}})
    conn.post.assert_not_called()


@pytest.mark.unit
def test_load_config_allows_an_unchanged_admin_block():
    """Sending the live admin block back verbatim is the documented safe path."""
    admin = {"listen": "127.0.0.1:2019"}
    conn = _conn({_ROOT: {"admin": admin, "apps": {}}})
    out = ops.load_config(conn, {"admin": dict(admin), "apps": {"http": {}}})
    assert out["priorState"]["config"]["admin"] == admin
    conn.post.assert_called_once()


@pytest.mark.unit
def test_load_config_allows_a_config_with_no_admin_block():
    conn = _conn({_ROOT: {"admin": {"listen": "127.0.0.1:2019"}, "apps": {}}})
    ops.load_config(conn, {"apps": {"http": {"servers": {}}}})
    conn.post.assert_called_once()


@pytest.mark.unit
def test_unknown_live_admin_block_does_not_block_a_listen_change():
    """Fail open: with no readable prior admin block there is nothing to compare.

    An undeterminable prior must never be reported as a change — that would be a
    false positive blocking legitimate work.
    """
    conn = _conn({_ROOT: {"apps": {}}})  # live config carries no admin key
    ops.load_config(conn, {"admin": {"listen": "127.0.0.1:9999"}, "apps": {}})
    conn.post.assert_called_once()


@pytest.mark.unit
def test_disabled_is_refused_even_without_a_readable_prior():
    """admin.disabled is absolute — it needs no comparison to be wrong."""
    conn = _conn({_ROOT: "not-a-dict"})
    with pytest.raises(SelfLockout, match="admin.disabled"):
        ops.load_config(conn, {"admin": {"disabled": True}, "apps": {}})
    conn.post.assert_not_called()


@pytest.mark.unit
def test_root_set_config_value_is_checked_like_load_config():
    """A root path writes the whole tree, so the admin block rides along."""
    conn = _conn({_ROOT: {"admin": {"listen": "127.0.0.1:2019"}}})
    with pytest.raises(SelfLockout, match="admin.disabled"):
        ops.set_config_value(conn, "", {"admin": {"disabled": True}})
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_self_lockout_is_a_valueerror():
    """Existing 'except ValueError' handling (CLI, tool_errors) keeps working."""
    assert issubclass(SelfLockout, ValueError)


# ── dry_run must report the refusal, not preview a call that will be refused ─
#
# A green preview followed by a refusal is the weak-model trap this line designs
# against: the model reads the refusal as transient and retries. dry_run's whole
# job is to say what would happen, so "it would be refused" IS the right answer.


@pytest.mark.unit
def test_dry_run_of_an_admin_write_is_refused(monkeypatch):
    from mcp_server.tools import writes as t

    conn = _conn()
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    result = t.set_config_value(path="admin/disabled", value=True, dry_run=True)

    assert "error" in result, "the preview must report the refusal"
    assert "wouldSet" not in result, "must not also hand back a green preview"
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_dry_run_of_an_ordinary_config_write_still_previews(monkeypatch):
    """The dry-run guard must be exact, not a blanket refusal of every preview."""
    from mcp_server.tools import writes as t

    conn = _conn()
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)
    path = "apps/http/servers/srv0/routes/0/handle/0/upstreams"

    result = t.set_config_value(path=path, value=[{"dial": "10.0.0.9:8080"}],
                                dry_run=True)

    assert result["dryRun"] is True
    assert result["wouldSet"]["path"] == path
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_dry_run_of_an_admin_delete_is_refused(monkeypatch):
    from mcp_server.tools import writes as t

    conn = _conn()
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    result = t.delete_config_path(path="admin", dry_run=True)

    assert "error" in result
    assert "wouldDelete" not in result
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_dry_run_of_an_ordinary_delete_still_previews(monkeypatch):
    from mcp_server.tools import writes as t

    conn = _conn()
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    result = t.delete_config_path(path="apps/http/servers/srv0/routes/1", dry_run=True)

    assert result["dryRun"] is True
    assert result["wouldDelete"] == {"path": "apps/http/servers/srv0/routes/1"}
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_dry_run_of_a_load_that_disables_admin_is_refused(monkeypatch):
    from mcp_server.tools import writes as t

    conn = _conn({_ROOT: {"admin": {"listen": "127.0.0.1:2019"}, "apps": {}}})
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    result = t.load_config(config={"admin": {"disabled": True}, "apps": {}},
                           dry_run=True)

    assert "error" in result
    assert "wouldLoad" not in result
    conn.post.assert_not_called()


@pytest.mark.unit
def test_dry_run_of_a_safe_load_still_previews(monkeypatch):
    """Exactness: the extra config_root GET must not become a blanket refusal."""
    from mcp_server.tools import writes as t

    conn = _conn({_ROOT: {"admin": {"listen": "127.0.0.1:2019"}, "apps": {}}})
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    result = t.load_config(config={"apps": {"http": {"servers": {}}}}, dry_run=True)

    assert result["dryRun"] is True
    assert result["wouldLoad"] == {"topLevelKeys": ["apps"]}
    conn.post.assert_not_called()


@pytest.mark.unit
def test_dry_run_fails_open_exactly_like_the_real_call(monkeypatch):
    """A dry_run must never refuse something the real call would allow."""
    from mcp_server.tools import writes as t

    conn = _conn({_ROOT: {"apps": {}}})  # live config carries no admin block
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    result = t.load_config(config={"admin": {"listen": "127.0.0.1:9999"}, "apps": {}},
                           dry_run=True)

    assert result["dryRun"] is True, "unreadable prior admin is unknown, not 'changed'"


# ── the CLI preview path must refuse too, and exit non-zero ─────────────────


def _flat(text: str) -> str:
    """Collapse whitespace: rich wraps console output at the terminal width, so a
    phrase can arrive split across two lines. Assert on meaning, not on layout."""
    return " ".join(text.split())


def _cli_dry_run(monkeypatch, tmp_path, argv, conn):
    """Drive a CLI --dry-run with the governed write module pointed at ``conn``."""
    from typer.testing import CliRunner

    import proxy_aiops.governance.audit as audit_mod
    import proxy_aiops.governance.policy as policy_mod
    import proxy_aiops.governance.undo as undo_mod
    from mcp_server.tools import writes as gov
    from proxy_aiops.cli import app

    monkeypatch.setenv("PROXY_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    try:
        return CliRunner().invoke(app, argv)
    finally:
        audit_mod.reset_engine()
        policy_mod.reset_policy_engine()
        undo_mod.reset_undo_store()


@pytest.mark.unit
def test_cli_dry_run_of_an_admin_write_is_refused(monkeypatch, tmp_path):
    """A refused preview must look like a refusal: teaching message, exit 1."""
    conn = _conn()
    result = _cli_dry_run(monkeypatch, tmp_path,
                          ["config", "set", "admin/disabled", "true", "--dry-run"], conn)

    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in _flat(result.output), "must not print a green banner"
    assert "admin API" in _flat(result.output), "must carry the teaching message"
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_cli_dry_run_of_an_admin_delete_is_refused(monkeypatch, tmp_path):
    conn = _conn()
    result = _cli_dry_run(monkeypatch, tmp_path,
                          ["config", "delete", "admin", "--dry-run"], conn)

    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in _flat(result.output)
    assert "admin API" in _flat(result.output)
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_cli_dry_run_of_an_ordinary_config_write_still_previews(monkeypatch, tmp_path):
    """Exactness: the CLI guard must not turn into a blanket refusal."""
    conn = _conn()
    result = _cli_dry_run(
        monkeypatch, tmp_path,
        ["config", "set", "apps/http/servers/srv0/routes", "[]", "--dry-run"], conn)

    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in _flat(result.output)
    _assert_nothing_mutated(conn)


@pytest.mark.unit
def test_the_refusal_reaches_the_agent_intact_through_the_mcp_layer(monkeypatch):
    """The teaching tail must survive _safe_error's length cap.

    ValueError is on the passthrough list, so the message is forwarded rather
    than replaced — but it is truncated. The route back sits at the END of the
    message, so an over-long refusal loses exactly the part the caller acts on.
    """
    from mcp_server.tools import writes as t

    conn = _conn()
    monkeypatch.setattr(t, "_get_connection", lambda target=None: conn)

    result = t.set_config_value(path="admin/disabled", value=True)

    assert "error" in result, "the refusal must surface as an error, not a success"
    assert "config file" in result["error"], "the route back must not be truncated away"
    _assert_nothing_mutated(conn)
