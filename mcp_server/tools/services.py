"""Service / upstream MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from proxy_aiops.governance import governed_tool
from proxy_aiops.ops import services as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_services(target: Optional[str] = None) -> dict:
    """[READ] Services / backends with per-service server-up counts.

    Args:
        target: Proxy target name from config; omit for the default.
    """
    return ops.list_services(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def service_detail(name: str, target: Optional[str] = None) -> dict:
    """[READ] One service/backend's full detail by name (from list_services).

    Args:
        name: Service name — traefik service name, caddy route config path, or
            haproxy backend name.
        target: Proxy target name from config; omit for the default.
    """
    return ops.service_detail(_get_connection(target), name)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_upstreams(service: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] Server-level upstream health rows: {service, server, address,
    status(up/down/maint/drain), checkInfo, weight}. Feeds backend_health_rca.

    Args:
        service: Optional service/backend filter.
        target: Proxy target name from config; omit for the default.
    """
    return ops.list_upstreams(_get_connection(target), service)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def upstream_detail(service: str, server: str, target: Optional[str] = None) -> dict:
    """[READ] One upstream server's health/state row.

    Args:
        service: Service/backend name.
        server: Server name or address (from list_upstreams).
        target: Proxy target name from config; omit for the default.
    """
    return ops.upstream_detail(_get_connection(target), service, server)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_middlewares(target: Optional[str] = None) -> dict:
    """[READ] Middlewares (traefik). On caddy/haproxy this returns the support
    matrix's teaching note (their equivalents live inside routes / haproxy.cfg).

    Args:
        target: Proxy target name from config; omit for the default.
    """
    return ops.list_middlewares(_get_connection(target))
