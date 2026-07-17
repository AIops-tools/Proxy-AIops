"""Status MCP tools — overview, version, entrypoints (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from proxy_aiops.governance import governed_tool
from proxy_aiops.ops import overview as overview_ops
from proxy_aiops.ops import status as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def proxy_overview(target: Optional[str] = None) -> dict:
    """[READ] One-shot summary: platform/version + route/service counts +
    upstream up/down health.

    Args:
        target: Proxy target name from config; omit for the default.
    """
    return overview_ops.proxy_overview(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def version_info(target: Optional[str] = None) -> dict:
    """[READ] Version / build info (traefik /api/version, haproxy /v2/info;
    caddy returns a teaching note — its admin API has no version endpoint).

    Args:
        target: Proxy target name from config; omit for the default.
    """
    return ops.version_info(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_entrypoints(target: Optional[str] = None) -> dict:
    """[READ] Listeners: traefik entrypoints / caddy server listen addresses /
    haproxy frontends — where traffic enters this proxy.

    Args:
        target: Proxy target name from config; omit for the default.
    """
    return ops.list_entrypoints(_get_connection(target))
