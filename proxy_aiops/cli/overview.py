"""``proxy-aiops overview`` — one-shot proxy health."""

from __future__ import annotations

import json

from proxy_aiops.cli._common import TargetOption, cli_errors, console, get_connection


@cli_errors
def overview_cmd(target: TargetOption = None) -> None:
    """One-shot summary: platform/version + route/service counts + upstream health."""
    from proxy_aiops.ops import overview as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.proxy_overview(conn)))
