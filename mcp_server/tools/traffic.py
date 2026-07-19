"""Traffic / error-counter MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from proxy_aiops.governance import governed_tool
from proxy_aiops.ops import traffic as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def traffic_stats(target: Optional[str] = None) -> dict:
    """[READ] Per-service traffic snapshot: requests, latency/rate/sessions
    where the platform exposes it (traefik /metrics, haproxy stats; caddy
    returns the support matrix's teaching note).

    Args:
        target: Proxy target name from config; omit for the default.

    Returns an envelope: {"services": [...], "returned": N, "limit": L,
    "truncated": bool, "total": T}. When "truncated" is true this proxy serves
    more services than were returned.
    """

    return ops.traffic_stats(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def error_counters(target: Optional[str] = None) -> dict:
    """[READ] Per-service request/status-code counters (feeds error_rate_rca).

    traefik: parsed from the /metrics text endpoint (per-code); haproxy: Data Plane
    stats (per-class hrsp_*); caddy: teaching note (no per-route counters).

    Args:
        target: Proxy target name from config; omit for the default.

    Returns an envelope: {"services": [...], "returned": N, "limit": L,
    "truncated": bool, "total": T}. When "truncated" is true this proxy serves
    more services than were returned (only the busiest services are returned).
    """

    return ops.error_counters(_get_connection(target))
