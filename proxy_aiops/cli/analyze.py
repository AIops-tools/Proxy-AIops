"""``proxy-aiops analyze`` — the flagship RCA analyses from the CLI."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from proxy_aiops.cli._common import TargetOption, cli_errors, console, get_connection

analyze_app = typer.Typer(
    name="analyze",
    help="Flagship analyses: backend health RCA, error-rate RCA, route conflicts.",
    no_args_is_help=True,
)


@analyze_app.command("health")
@cli_errors
def analyze_health(
    service: Annotated[
        str | None, typer.Option("--service", "-s", help="Filter by service/backend")
    ] = None,
    target: TargetOption = None,
) -> None:
    """Backend/upstream health RCA: down servers grouped per service, cause + action."""
    from proxy_aiops.ops import analysis as analysis_ops
    from proxy_aiops.ops import services as svc_ops

    conn, _ = get_connection(target)
    pulled = svc_ops.list_upstreams(conn, service)
    if "error" in pulled:
        console.print_json(json.dumps(pulled))
        raise typer.Exit(1)
    console.print_json(json.dumps(analysis_ops.backend_health_rca(pulled["upstreams"])))


@analyze_app.command("errors")
@cli_errors
def analyze_errors(
    error_rate_pct: Annotated[
        float, typer.Option("--rate", help="5xx %% at/above which a service is flagged")
    ] = 5.0,
    min_requests: Annotated[
        float, typer.Option("--min-requests", help="Minimum requests to be flagged")
    ] = 30.0,
    target: TargetOption = None,
) -> None:
    """5xx / error-rate RCA vs the fleet baseline, cause + action per service."""
    from proxy_aiops.ops import analysis as analysis_ops
    from proxy_aiops.ops import traffic as traffic_ops

    conn, _ = get_connection(target)
    pulled = traffic_ops.error_counters(conn)
    if "error" in pulled:
        console.print_json(json.dumps(pulled))
        raise typer.Exit(1)
    console.print_json(json.dumps(analysis_ops.error_rate_rca(
        pulled["services"], error_rate_pct=error_rate_pct, min_requests=min_requests
    )))


@analyze_app.command("conflicts")
@cli_errors
def analyze_conflicts(target: TargetOption = None) -> None:
    """Route conflict & shadow analysis: shadowed / dead routes, redirect loops."""
    from proxy_aiops.ops import analysis as analysis_ops
    from proxy_aiops.ops import routes as route_ops
    from proxy_aiops.ops import services as svc_ops

    conn, _ = get_connection(target)
    routes = route_ops.list_routes(conn)
    if "error" in routes:
        console.print_json(json.dumps(routes))
        raise typer.Exit(1)
    services = svc_ops.list_services(conn)
    svc_rows = services.get("services") if "error" not in services else None
    console.print_json(json.dumps(
        analysis_ops.route_conflict_analysis(routes["routes"], svc_rows)
    ))
