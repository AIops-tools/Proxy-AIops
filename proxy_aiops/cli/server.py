"""``proxy-aiops server`` — haproxy runtime server state / weight (governed).

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
    dry_run_preview,
)

server_app = typer.Typer(
    name="server",
    help="haproxy runtime servers: set admin state (ready/drain/maint) and weight.",
    no_args_is_help=True,
)


@server_app.command("state")
@cli_errors
def server_state(
    backend: Annotated[str, typer.Argument(help="Backend name (from 'services list')")],
    server: Annotated[str, typer.Argument(help="Server name (from 'services upstreams')")],
    state: Annotated[str, typer.Argument(help="ready | drain | maint")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Set a server's admin state (governed write; prior state captured for undo)."""
    from mcp_server.tools import writes as gov

    if dry_run:
        # Through the governed call: set_server_state is haproxy-only and
        # validates the state, so a preview must report either refusal.
        dry_run_preview(
            gov.set_server_state(
                backend=backend, server=server, state=state, dry_run=True, target=target
            ),
            operation="set_server_state",
            api_call=f"PUT runtime server {backend}/{server}",
            parameters={"backend": backend, "server": server, "state": state})
        return
    double_confirm(f"set state={state} on", f"{backend}/{server}")
    console.print_json(json.dumps(
        gov.set_server_state(backend=backend, server=server, state=state, target=target)
    ))


@server_app.command("weight")
@cli_errors
def server_weight(
    backend: Annotated[str, typer.Argument(help="Backend name (from 'services list')")],
    server: Annotated[str, typer.Argument(help="Server name (from 'services upstreams')")],
    weight: Annotated[int, typer.Argument(help="New weight, 0-256")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Set a server's LB weight (governed write; prior weight captured for undo)."""
    from mcp_server.tools import writes as gov

    if dry_run:
        # Through the governed call: set_server_weight is haproxy-only and
        # range-checks the weight, so a preview must report either refusal.
        dry_run_preview(
            gov.set_server_weight(
                backend=backend, server=server, weight=weight, dry_run=True, target=target
            ),
            operation="set_server_weight",
            api_call=f"PUT runtime server {backend}/{server}",
            parameters={"backend": backend, "server": server, "weight": weight})
        return
    double_confirm(f"set weight={weight} on", f"{backend}/{server}")
    console.print_json(json.dumps(
        gov.set_server_weight(backend=backend, server=server, weight=weight, target=target)
    ))
