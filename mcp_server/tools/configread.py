"""Config-tree MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from proxy_aiops.governance import governed_tool
from proxy_aiops.ops import configread as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def config_snapshot(target: Optional[str] = None) -> dict:
    """[READ] The live config tree (caddy /config/) or merged dynamic state
    (traefik /api/rawdata), sanitised and bounded. haproxy returns the support
    matrix's teaching note.

    Args:
        target: Proxy target name from config; omit for the default.
    """
    return ops.config_snapshot(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def search_config(query: str, target: Optional[str] = None) -> dict:
    """[READ] Search the config tree for a string; returns matching config
    paths (on caddy, directly usable by get/set_config_value).

    Args:
        query: Case-insensitive substring to find in keys and values.
        target: Proxy target name from config; omit for the default.

    Returns an envelope: {"matches": [...], "returned": N, "limit": L,
    "truncated": bool}. When "truncated" is true there is more than was
    returned — narrow the query rather than treating these as every match.
    """
    return ops.search_config(_get_connection(target), query)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def get_config_value(path: str, target: Optional[str] = None) -> dict:
    """[READ] One value out of the caddy config tree by config path
    (e.g. apps/http/servers/srv0/routes/0). Off-caddy platforms return the
    support matrix's teaching note.

    Args:
        path: Slash-separated config path (dot-segments rejected).
        target: Proxy target name from config; omit for the default.
    """
    return ops.get_config_value(_get_connection(target), path)
