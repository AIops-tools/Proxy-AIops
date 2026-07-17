"""``proxy-aiops services`` — services/backends and upstream health."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from proxy_aiops.cli._common import TargetOption, cli_errors, console, get_connection

services_app = typer.Typer(
    name="services",
    help="Services / backends and server-level upstream health.",
    no_args_is_help=True,
)


@services_app.command("list")
@cli_errors
def services_list(target: TargetOption = None) -> None:
    """List services/backends with per-service server-up counts."""
    from proxy_aiops.ops import services as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_services(conn)))


@services_app.command("show")
@cli_errors
def services_show(
    name: Annotated[str, typer.Argument(help="Service name (from 'services list')")],
    target: TargetOption = None,
) -> None:
    """Show one service/backend's full detail."""
    from proxy_aiops.ops import services as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.service_detail(conn, name)))


@services_app.command("upstreams")
@cli_errors
def services_upstreams(
    service: Annotated[
        str | None, typer.Option("--service", "-s", help="Filter by service/backend")
    ] = None,
    target: TargetOption = None,
) -> None:
    """Server-level upstream health rows (up/down/maint/drain + check info)."""
    from proxy_aiops.ops import services as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_upstreams(conn, service)))
