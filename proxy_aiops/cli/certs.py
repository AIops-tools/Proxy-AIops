"""``proxy-aiops certs`` — TLS certificate inventory + expiry sweep."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from proxy_aiops.cli._common import TargetOption, cli_errors, console, get_connection


@cli_errors
def certs_cmd(
    sweep: Annotated[
        bool,
        typer.Option("--sweep/--no-sweep", help="Probe expiry and bucket by days-to-expiry"),
    ] = True,
    warn_days: Annotated[
        float, typer.Option("--warn-days", help="Warning threshold (days)")
    ] = 30.0,
    critical_days: Annotated[
        float, typer.Option("--critical-days", help="Critical threshold (days)")
    ] = 7.0,
    port: Annotated[int, typer.Option("--port", help="TLS port to probe")] = 443,
    target: TargetOption = None,
) -> None:
    """TLS domain inventory; --sweep live-probes each cert's expiry."""
    from proxy_aiops.ops import analysis as analysis_ops
    from proxy_aiops.ops import certs as ops

    conn, _ = get_connection(target)
    inventory = ops.list_certificates(conn, probe=sweep, port=port)
    if not sweep or "unsupported" in inventory or "error" in inventory:
        console.print_json(json.dumps(inventory))
        return
    result = analysis_ops.cert_expiry_sweep(
        inventory.get("certificates", []),
        warn_days=warn_days,
        critical_days=critical_days,
        platform=conn.target.platform,
    )
    console.print_json(json.dumps(result))
