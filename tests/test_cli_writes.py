"""CLI confirmed-write path — past dry-run, through governance, onto disk.

The CLI write commands delegate real execution to the ``@governed_tool``
functions in ``mcp_server.tools``. These tests drive ``server state`` (haproxy)
and ``config set`` (caddy) PAST the dry-run branch and the double-confirm
prompts and assert the call really went through the governed path (audit row on
disk) — the regression test for the "CLI writes were unaudited" line-wide fix.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import proxy_aiops.governance.audit as audit_mod
import proxy_aiops.governance.policy as policy_mod
import proxy_aiops.governance.undo as undo_mod
from proxy_aiops.connection import ProxyApiError
from proxy_aiops.platform import CADDY, HAPROXY, get_platform


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _fake_conn(platform, responses):
    conn = MagicMock(name="conn")
    conn.target.platform = platform
    conn.platform = get_platform(platform)

    def _get(path, **kw):
        if path in responses:
            return responses[path]
        raise ProxyApiError("not found", status_code=404, path=path)

    conn.get.side_effect = _get
    return conn


@pytest.fixture
def ha_conn(monkeypatch):
    """A fake haproxy connection wired into the governed write module."""
    from mcp_server.tools import writes as gov

    path = get_platform(HAPROXY).path("runtime_server", name="web1", backend="app")
    conn = _fake_conn(HAPROXY, {path: {"admin_state": "ready", "weight": 100}})
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    return conn


@pytest.fixture
def caddy_conn(monkeypatch):
    from mcp_server.tools import writes as gov

    conn = _fake_conn(CADDY, {"/config/apps/http/servers/srv0": {"listen": [":80"]}})
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    return conn


def _audit_tools(db_path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


@pytest.mark.unit
def test_cli_server_state_dry_run_makes_no_call_and_no_audit(gov_home, ha_conn):
    from proxy_aiops.cli import app

    result = CliRunner().invoke(
        app, ["server", "state", "app", "web1", "drain", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "DRY-RUN" in result.output
    ha_conn.put.assert_not_called()
    assert not (gov_home / "audit.db").exists()


@pytest.mark.unit
def test_cli_server_state_confirmed_goes_through_governance(gov_home, ha_conn):
    """Confirmed CLI write must execute via the governed twin: the API call
    fires AND an audit row lands in audit.db (this is what the reroute fix
    bought)."""
    from proxy_aiops.cli import app

    result = CliRunner().invoke(
        app, ["server", "state", "app", "web1", "drain"], input="y\ny\n"
    )
    assert result.exit_code == 0, result.output
    ha_conn.put.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["set_server_state"]


@pytest.mark.unit
def test_cli_server_state_aborts_without_double_confirm(gov_home, ha_conn):
    from proxy_aiops.cli import app

    result = CliRunner().invoke(
        app, ["server", "state", "app", "web1", "drain"], input="y\nn\n"
    )
    assert result.exit_code != 0
    ha_conn.put.assert_not_called()
    assert not (gov_home / "audit.db").exists()


@pytest.mark.unit
def test_cli_config_set_confirmed_audits_and_patches(gov_home, caddy_conn):
    from proxy_aiops.cli import app

    result = CliRunner().invoke(
        app,
        ["config", "set", "apps/http/servers/srv0", '{"listen": [":8080"]}'],
        input="y\ny\n",
    )
    assert result.exit_code == 0, result.output
    caddy_conn.patch.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["set_config_value"]


@pytest.mark.unit
def test_cli_config_set_rejects_invalid_json_before_any_call(gov_home, caddy_conn):
    from proxy_aiops.cli import app

    result = CliRunner().invoke(
        app, ["config", "set", "apps/http", "{not json"], input="y\ny\n"
    )
    assert result.exit_code == 1
    assert "valid JSON" in result.output
    caddy_conn.patch.assert_not_called()
    assert not (gov_home / "audit.db").exists()
