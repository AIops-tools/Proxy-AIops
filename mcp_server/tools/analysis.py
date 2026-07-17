"""Flagship proxy-analysis MCP tools (read-only)."""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from proxy_aiops.governance import governed_tool
from proxy_aiops.ops import analysis as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def backend_health_rca(
    service: Optional[str] = None,
    upstreams: Optional[list[dict[str, Any]]] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] Down upstreams grouped per service, each mapped to cause + action.

    The flagship availability RCA: pulls server-level upstream health, groups
    per service, and classifies each outage/degradation (connection refused,
    L4/L7 health-check failure class, DNS, admin maint/drain, all-servers-down)
    with a recommended action. Every finding carries its numbers. Pass
    'upstreams' for pure analysis, or a target to pull live.

    Args:
        service: Optional service/backend filter when pulling live.
        upstreams: Injected rows {service, server, address, status, checkInfo};
            skips the live pull.
        target: Proxy target name from config; omit for the default.

    Returns dict: {servicesEvaluated, outages, degraded, findings:[{service,
        serversTotal, up, down, maint, failingServers, cause, action}], note}.
    """
    if upstreams is None:
        from proxy_aiops.ops import services as svc_ops

        pulled = svc_ops.list_upstreams(_get_connection(target), service)
        if "error" in pulled:
            return pulled
        upstreams = pulled.get("upstreams", [])
    return ops.backend_health_rca(upstreams)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def cert_expiry_sweep(
    warn_days: float = 30.0,
    critical_days: float = 7.0,
    port: int = 443,
    certs: Optional[list[dict[str, Any]]] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] TLS cert inventory bucketed by days-to-expiry + renewal hints.

    The flagship cert sweep for traefik/caddy targets: collects the TLS domain
    inventory, live-probes each domain's served leaf cert (bounded handshake),
    and buckets by expiry: expired / critical / warning / ok — with a
    platform-specific renewal hint (ACME resolver / storage checks). Pass
    'certs' for pure analysis over {domain, daysToExpiry} rows. haproxy targets
    return the support matrix's teaching note.

    Args:
        warn_days: Days-to-expiry at/below which a cert is a warning (default 30).
        critical_days: Days at/below which a cert is critical (default 7).
        port: TLS port to probe (default 443).
        certs: Injected rows {domain, daysToExpiry, notAfter?}; skips pull+probe.
        target: Proxy target name from config; omit for the default.

    Returns dict: {certsEvaluated, expired, critical, warning, ok, unknown,
        certificates (soonest first), thresholds, renewalHint, note}.
    """
    platform = ""
    if certs is None:
        from proxy_aiops.ops import certs as cert_ops

        conn = _get_connection(target)
        platform = conn.target.platform
        pulled = cert_ops.list_certificates(conn, probe=True, port=port)
        if "unsupported" in pulled or "error" in pulled:
            return pulled
        certs = pulled.get("certificates", [])
    return ops.cert_expiry_sweep(
        certs, warn_days=warn_days, critical_days=critical_days, platform=platform
    )


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def error_rate_rca(
    error_rate_pct: float = 5.0,
    min_requests: float = 30.0,
    counters: Optional[list[dict[str, Any]]] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] Rank services by 5xx share vs the fleet baseline, cause + action.

    The flagship error RCA: reads per-service status-code counters (traefik
    /metrics, haproxy stats), flags services whose 5xx rate crosses the
    threshold with enough traffic, and maps the dominant code to a cause
    (503 no-upstream / 502 conn-fail / 504 timeout / 500 app error). Every
    entry carries its numbers and its multiple vs the fleet baseline. Pass
    'counters' for pure analysis. caddy targets return the support matrix's
    teaching note (no per-route counters).

    Args:
        error_rate_pct: 5xx %% at/above which a service is flagged (default 5.0).
        min_requests: Minimum requests before a service can be flagged (default 30).
        counters: Injected rows {service, total, codes:{...}} and/or
            classes:{"5xx": n}; skips the live pull.
        target: Proxy target name from config; omit for the default.

    Returns dict: {servicesEvaluated, flaggedCount, fleetErrorRatePct,
        thresholds, flagged:[{service, requestsTotal, errors5xx, errorRatePct,
        dominantCode, vsBaselineX, severity, cause, action}], note}.
    """
    if counters is None:
        from proxy_aiops.ops import traffic as traffic_ops

        pulled = traffic_ops.error_counters(_get_connection(target))
        if "error" in pulled:
            return pulled
        counters = pulled.get("services", [])
    return ops.error_rate_rca(
        counters, error_rate_pct=error_rate_pct, min_requests=min_requests
    )


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def route_conflict_analysis(
    routes: Optional[list[dict[str, Any]]] = None,
    services: Optional[list[dict[str, Any]]] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] Shadowed routes, dead routes, and redirect loops (static).

    The flagship routing hygiene analysis: fetches the route table and service
    list and statically finds (1) routes fully covered by an earlier/higher-
    priority route (they can never match), (2) routes pointing at a missing
    service or one with zero servers up, and (3) redirect chains that loop.
    Every finding names the covering route / missing service. Pass 'routes'
    (and optionally 'services') for pure analysis.

    Args:
        routes: Injected rows {name, hosts, paths, priority, service, enabled,
            redirectTo}; skips the live pull.
        services: Injected rows {name, serversTotal, serversUp} for dead-route
            detection.
        target: Proxy target name from config; omit for the default.

    Returns dict: {routesEvaluated, shadowedCount, deadCount,
        redirectLoopCount, shadowedRoutes, deadRoutes, redirectLoops, note}.
    """
    if routes is None:
        from proxy_aiops.ops import routes as route_ops
        from proxy_aiops.ops import services as svc_ops

        conn = _get_connection(target)
        pulled = route_ops.list_routes(conn)
        if "error" in pulled:
            return pulled
        routes = pulled.get("routes", [])
        if services is None:
            pulled_svcs = svc_ops.list_services(conn)
            services = pulled_svcs.get("services", []) if "error" not in pulled_svcs else None
    return ops.route_conflict_analysis(routes, services)
