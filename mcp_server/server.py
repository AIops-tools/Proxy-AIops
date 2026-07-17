"""MCP server wrapping proxy-aiops operations (stdio transport).

Thin adapter layer: each ``@mcp.tool()`` function (in ``mcp_server/tools/``)
delegates to the ``proxy_aiops`` ops package and is wrapped with the
proxy-aiops ``@governed_tool`` harness (audit / budget / undo / risk-tier).

Standalone, self-governed reverse-proxy operations (preview) over Traefik,
Caddy and HAProxy: status/routes/services/upstreams/certs/traffic/config
reads, four flagship analyses, and governed writes (caddy config set/delete/
load, haproxy server state/weight).

Source: https://github.com/AIops-tools/Proxy-AIops
License: MIT
"""

import logging

from mcp_server._shared import _safe_error, mcp, tool_errors

# Importing the tool modules registers every @mcp.tool() onto the shared
# `mcp` instance. Order does not matter; each module is self-contained.
from mcp_server.tools import (  # noqa: F401 — side effects
    analysis,
    certs,
    configread,
    routes,
    services,
    status,
    traffic,
    undo,
    writes,
)

__all__ = ["mcp", "main", "_safe_error", "tool_errors"]


def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")
