"""``proxy-aiops routes`` — list / show / find routes."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from proxy_aiops.cli._common import TargetOption, cli_errors, console, get_connection

routes_app = typer.Typer(
    name="routes",
    help="Routes (traefik routers / caddy routes / haproxy frontends): "
    "list, show detail, find by host/path.",
    no_args_is_help=True,
)


@routes_app.command("list")
@cli_errors
def routes_list(
    host: Annotated[
        str | None, typer.Option("--host", "-H", help="Filter by hostname")
    ] = None,
    target: TargetOption = None,
) -> None:
    """List routes (optionally filtered to one hostname)."""
    from proxy_aiops.ops import routes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_routes(conn, host)))


@routes_app.command("show")
@cli_errors
def routes_show(
    name: Annotated[str, typer.Argument(help="Route name (from 'routes list')")],
    target: TargetOption = None,
) -> None:
    """Show one route's full detail."""
    from proxy_aiops.ops import routes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.route_detail(conn, name)))


@routes_app.command("find")
@cli_errors
def routes_find(
    host: Annotated[str, typer.Argument(help="Hostname to match")],
    path: Annotated[str, typer.Option("--path", "-p", help="Request path")] = "/",
    target: TargetOption = None,
) -> None:
    """Which routes would serve a host/path (static match, best first)."""
    from proxy_aiops.ops import routes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.find_route(conn, host, path)))
