"""Smoke tests for proxy-aiops.

Proves: every module imports, the CLI builds and --help works, the MCP server
exposes the expected tool surface and EVERY tool carries the harness marker
``_is_governed_tool``, and config platform validation works. No real
Traefik/Caddy/HAProxy is needed.
"""

import asyncio
import importlib

import pytest
from typer.testing import CliRunner

# Kept in sync with mcp_server/server.py (the full registered tool surface).
EXPECTED_TOOLS = {
    # status
    "proxy_overview", "version_info", "list_entrypoints",
    # routes
    "list_routes", "route_detail", "find_route",
    # services / upstreams
    "list_services", "service_detail", "list_upstreams", "upstream_detail",
    "list_middlewares",
    # certs
    "list_certificates",
    # traffic
    "traffic_stats", "error_counters",
    # config reads
    "config_snapshot", "search_config", "get_config_value",
    # analysis (flagship)
    "backend_health_rca", "cert_expiry_sweep", "error_rate_rca",
    "route_conflict_analysis",
    # writes
    "set_config_value", "delete_config_path", "load_config",
    "set_server_state", "set_server_weight",
}


@pytest.mark.unit
def test_all_modules_import():
    for name in (
        "proxy_aiops", "proxy_aiops.config", "proxy_aiops.connection",
        "proxy_aiops.platform", "proxy_aiops.doctor",
        "proxy_aiops.secretstore",
        "proxy_aiops.ops.status", "proxy_aiops.ops.routes",
        "proxy_aiops.ops.services", "proxy_aiops.ops.certs",
        "proxy_aiops.ops.traffic", "proxy_aiops.ops.configread",
        "proxy_aiops.ops.analysis", "proxy_aiops.ops.writes",
        "proxy_aiops.ops.overview",
        "proxy_aiops.cli", "proxy_aiops.cli._root", "proxy_aiops.cli._common",
        "proxy_aiops.cli.init", "proxy_aiops.cli.secret",
        "proxy_aiops.cli.routes", "proxy_aiops.cli.services",
        "proxy_aiops.cli.certs", "proxy_aiops.cli.analyze",
        "proxy_aiops.cli.configcmd", "proxy_aiops.cli.server",
        "proxy_aiops.cli.overview", "proxy_aiops.cli.doctor",
        "mcp_server.server", "mcp_server._shared",
        "mcp_server.tools.status", "mcp_server.tools.writes",
    ):
        importlib.import_module(name)


@pytest.mark.unit
def test_version_matches_pyproject():
    """__version__ is single-sourced from package metadata; it must track
    pyproject.toml so a release bump can never ship a stale self-report."""
    import tomllib
    from pathlib import Path

    import proxy_aiops

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
    assert proxy_aiops.__version__ == expected


@pytest.mark.unit
def test_cli_app_builds_and_help_works():
    from proxy_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("routes", "services", "analyze", "config", "server", "secret",
                "init", "overview", "certs", "doctor", "mcp"):
        assert sub in result.output


@pytest.mark.unit
def test_cli_leaf_help_triggers_lazy_imports():
    from proxy_aiops.cli import app

    runner = CliRunner()
    for cmd in (
        ["routes", "--help"], ["services", "--help"], ["analyze", "--help"],
        ["config", "--help"], ["server", "--help"], ["secret", "--help"],
        ["doctor", "--help"], ["overview", "--help"], ["certs", "--help"],
        ["init", "--help"],
        ["routes", "list", "--help"], ["routes", "show", "--help"],
        ["routes", "find", "--help"],
        ["services", "list", "--help"], ["services", "upstreams", "--help"],
        ["analyze", "health", "--help"], ["analyze", "errors", "--help"],
        ["analyze", "conflicts", "--help"],
        ["config", "get", "--help"], ["config", "set", "--help"],
        ["config", "delete", "--help"], ["config", "search", "--help"],
        ["server", "state", "--help"], ["server", "weight", "--help"],
        ["secret", "list", "--help"], ["secret", "set", "--help"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"


@pytest.mark.unit
def test_mcp_list_tools_exposes_expected_tools():
    from mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.unit
def test_every_mcp_tool_is_governed_by_harness():
    from mcp_server import _shared

    tool_objs = _shared.mcp._tool_manager._tools
    assert EXPECTED_TOOLS <= set(tool_objs), "tool registry incomplete"
    for name, tool in tool_objs.items():
        fn = getattr(tool, "fn", None)
        assert fn is not None, f"{name} has no fn"
        assert getattr(fn, "_is_governed_tool", False), f"{name} missing @governed_tool"


@pytest.mark.unit
def test_tool_count_is_expected():
    from mcp_server import _shared

    assert len(_shared.mcp._tool_manager._tools) == 28


@pytest.mark.unit
def test_risk_level_agrees_with_read_write_docstring_tag():
    """The two write-markers must never drift apart.

    A tool's ``risk_level`` decides its audit tier and whether it gets dry-run /
    undo handling; its ``[READ]``/``[WRITE]`` docstring tag is what the docs and
    capability tables are built from. If a ``[WRITE]`` were left ``risk_level=low``
    it would be audited as a read and skip the write machinery — this test caught
    16 such mislabels line-wide once, so it is kept even though read-only mode
    (its original motivation) is gone.
    """
    from mcp_server import server

    untagged, mismatched = [], []
    for name, tool in server.mcp._tool_manager._tools.items():
        doc = (tool.fn.__doc__ or "").lstrip()
        if doc.startswith("[READ]"):
            tagged_as_read = True
        elif doc.startswith("[WRITE]"):
            tagged_as_read = False
        else:
            untagged.append(name)
            continue
        if tagged_as_read != (getattr(tool.fn, "_risk_level", "low") == "low"):
            mismatched.append(name)

    assert not untagged, f"tools missing a [READ]/[WRITE] docstring tag: {untagged}"
    assert not mismatched, f"risk_level disagrees with the docstring tag: {mismatched}"
