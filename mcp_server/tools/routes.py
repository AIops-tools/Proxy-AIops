"""Route MCP tools — routers / caddy routes / frontends (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from proxy_aiops.governance import governed_tool
from proxy_aiops.ops import routes as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_routes(host: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] Routes normalised across platforms: {name, hosts, paths,
    priority, service, tls, enabled, redirectTo}.

    Traefik router names and caddy config paths (apps/http/servers/...) come
    back exactly as the other tools expect them.

    Args:
        host: Optional hostname filter (keeps host-less catch-alls).
        target: Proxy target name from config; omit for the default.
    """
    return ops.list_routes(_get_connection(target), host)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def route_detail(name: str, target: Optional[str] = None) -> dict:
    """[READ] One route's full detail by name (from list_routes).

    Args:
        name: Route name — traefik router name, caddy route config path, or
            haproxy frontend name.
        target: Proxy target name from config; omit for the default.
    """
    return ops.route_detail(_get_connection(target), name)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def find_route(host: str, path: str = "/", target: Optional[str] = None) -> dict:
    """[READ] Which routes would serve a host/path (static match, best first).

    Args:
        host: Hostname to match (e.g. app.example.com).
        path: Request path to match (default /).
        target: Proxy target name from config; omit for the default.
    """
    return ops.find_route(_get_connection(target), host, path)
