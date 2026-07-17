"""``proxy-aiops config`` — caddy config tree: get / search / set / delete.

The writes delegate real execution to the ``@governed_tool`` twins in
``mcp_server.tools.writes`` so a CLI write is audited and undo-recorded exactly
like an agent write.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from proxy_aiops.cli._common import (
    DryRunOption,
    TargetOption,
    cli_errors,
    console,
    double_confirm,
    dry_run_print,
    get_connection,
)

config_app = typer.Typer(
    name="config",
    help="Config tree (caddy writes; traefik snapshot is read-only): "
    "get, search, set, delete.",
    no_args_is_help=True,
)


@config_app.command("snapshot")
@cli_errors
def config_snapshot(target: TargetOption = None) -> None:
    """The live config tree / merged dynamic state (sanitised)."""
    from proxy_aiops.ops import configread as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.config_snapshot(conn)))


@config_app.command("search")
@cli_errors
def config_search(
    query: Annotated[str, typer.Argument(help="Substring to find in keys/values")],
    target: TargetOption = None,
) -> None:
    """Search the config tree; returns matching config paths."""
    from proxy_aiops.ops import configread as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.search_config(conn, query)))


@config_app.command("get")
@cli_errors
def config_get(
    path: Annotated[str, typer.Argument(help="Config path (e.g. apps/http/servers)")],
    target: TargetOption = None,
) -> None:
    """Read one value out of the caddy config tree."""
    from proxy_aiops.ops import configread as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.get_config_value(conn, path)))


@config_app.command("set")
@cli_errors
def config_set(
    path: Annotated[str, typer.Argument(help="Config path to write")],
    value: Annotated[str, typer.Argument(help="JSON value to write at the path")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Set a caddy config subtree (governed write; prior subtree captured)."""
    from mcp_server.tools import writes as gov

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"value must be valid JSON: {exc}") from exc
    if dry_run:
        dry_run_print(operation="set_config_value", api_call=f"PATCH /config/{path}",
                      parameters={"path": path, "value": value})
        return
    double_confirm("set config value at", path)
    console.print_json(json.dumps(gov.set_config_value(path=path, value=parsed, target=target)))


@config_app.command("delete")
@cli_errors
def config_delete(
    path: Annotated[str, typer.Argument(help="Config path to delete")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Delete a caddy config subtree (governed write, risk=high; captured for undo)."""
    from mcp_server.tools import writes as gov

    if dry_run:
        dry_run_print(operation="delete_config_path", api_call=f"DELETE /config/{path}",
                      parameters={"path": path})
        return
    double_confirm("delete config path", path)
    console.print_json(json.dumps(gov.delete_config_path(path=path, target=target)))
