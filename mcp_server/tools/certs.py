"""TLS certificate MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from proxy_aiops.governance import governed_tool
from proxy_aiops.ops import certs as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_certificates(
    probe: bool = False,
    port: int = 443,
    target: Optional[str] = None,
) -> dict:
    """[READ] TLS domain inventory for a traefik/caddy target; optionally
    handshake-probe each domain for its live expiry.

    On haproxy this returns the support matrix's teaching note (certs are .pem
    files in haproxy.cfg).

    Args:
        probe: If True, TLS-handshake each domain (bounded) to read expiry.
        port: TLS port to probe (default 443).
        target: Proxy target name from config; omit for the default.
    """
    return ops.list_certificates(_get_connection(target), probe=probe, port=port)
