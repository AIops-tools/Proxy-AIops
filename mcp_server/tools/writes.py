"""Governed proxy-write MCP tools (the only state-changing tools).

Every tool is wrapped with the governance harness (audit + descriptive risk
tier) and takes a ``dry_run`` preview. Reversible writes pass an ``undo=``
callback that turns the fetched before-state into an inverse descriptor the
harness records; the undo params match the target tool's own signature, so the
descriptor is replayable as-is.

Risk tiers: delete_config_path / load_config = high (delete / full replace);
set_config_value / set_server_state / set_server_weight = medium.

Platform dispatch is the support matrix's job: a write against the wrong
platform raises its teaching error (traefik writes → edit the provider;
haproxy config writes → transactions out of scope) before anything mutates.
"""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from proxy_aiops.governance import governed_tool
from proxy_aiops.ops import writes as ops

# ── undo descriptors (built from the fetched before-state) ──────────────────


def _set_config_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of set_config_value: restore the prior subtree (or delete a
    path that did not exist before)."""
    if not isinstance(result, dict):
        return None
    prior = result.get("priorState") or {}
    if prior.get("existed"):
        return {
            "tool": "set_config_value",
            "params": {"path": params.get("path"), "value": prior.get("value")},
            "skill": "proxy-aiops",
            "note": "Inverse of set_config_value: restore the prior config subtree.",
        }
    return {
        "tool": "delete_config_path",
        "params": {"path": params.get("path")},
        "skill": "proxy-aiops",
        "note": "Inverse of set_config_value: the path did not exist before — delete it.",
    }


def _delete_config_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of delete_config_path: re-create the deleted subtree."""
    if not isinstance(result, dict):
        return None
    prior = result.get("priorState") or {}
    if not prior.get("existed"):
        return None
    return {
        "tool": "set_config_value",
        "params": {"path": params.get("path"), "value": prior.get("value")},
        "skill": "proxy-aiops",
        "note": "Inverse of delete_config_path: re-create the deleted subtree.",
    }


def _load_config_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of load_config: re-load the snapshotted prior config."""
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("config")
    if not isinstance(prior, dict) or not prior:
        return None
    return {
        "tool": "load_config",
        "params": {"config": prior},
        "skill": "proxy-aiops",
        "note": "Inverse of load_config: restore the snapshotted prior config.",
    }


def _server_state_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of set_server_state: restore the prior admin state."""
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("adminState")
    if prior not in ops.SERVER_STATES:
        return None
    return {
        "tool": "set_server_state",
        "params": {
            "backend": params.get("backend"),
            "server": params.get("server"),
            "state": prior,
        },
        "skill": "proxy-aiops",
        "note": "Inverse of set_server_state: restore the prior admin state.",
    }


def _server_weight_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of set_server_weight: restore the prior weight."""
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("weight")
    if prior is None:
        return None
    return {
        "tool": "set_server_weight",
        "params": {
            "backend": params.get("backend"),
            "server": params.get("server"),
            "weight": prior,
        },
        "skill": "proxy-aiops",
        "note": "Inverse of set_server_weight: restore the prior weight.",
    }


# ── caddy config writes ──────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_set_config_undo)
@tool_errors("dict")
def set_config_value(
    path: str,
    value: Any,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Set a caddy config subtree (e.g. a route's
    upstreams); reversible — the prior subtree is fetched first and the undo
    restores it.

    Caddy applies the change immediately. On traefik/haproxy this raises the
    support matrix's teaching error. Pass dry_run=True to preview.

    Refuses the 'admin' subtree: that configures the admin API this tool speaks
    to, so disabling or moving it would end the connection the undo needs.
    Change the admin block in caddy's own config file and reload locally. The
    refusal applies under dry_run too — a preview whose real call would be
    refused must report that, not a green 'wouldSet'.

    Args:
        path: Slash-separated config path (from search_config / list_routes,
            e.g. apps/http/servers/srv0/routes/0/handle/0/upstreams).
        value: The JSON value to write at that path.
        dry_run: If True, preview without changing.
        target: Proxy target name from config; omit for the default.
    """
    conn = _get_connection(target)
    # Ahead of the dry_run return: a preview whose real call would be refused
    # must say so, or the caller reads the refusal as transient and retries.
    ops.guard_set_config_value(conn, path, value)
    if dry_run:
        return {"dryRun": True, "wouldSet": {"path": path, "value": value}}
    return ops.set_config_value(conn, path, value)


@mcp.tool()
@governed_tool(risk_level="high", undo=_delete_config_undo)
@tool_errors("dict")
def delete_config_path(
    path: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Delete a caddy config subtree; reversible — the
    subtree is captured first and the undo re-creates it.

    Pass dry_run=True to preview.

    Refuses the 'admin' subtree and the config root: both remove the admin API
    this tool speaks to, leaving the undo with no way to reach the server. The
    refusal applies under dry_run too, which must report it rather than preview
    a call that will be refused.

    Args:
        path: Slash-separated config path to delete.
        dry_run: If True, preview without deleting.
        target: Proxy target name from config; omit for the default.
    """
    conn = _get_connection(target)
    # Ahead of the dry_run return: a preview whose real call would be refused
    # must say so, or the caller reads the refusal as transient and retries.
    ops.guard_delete_config_path(path)
    if dry_run:
        return {"dryRun": True, "wouldDelete": {"path": path}}
    return ops.delete_config_path(conn, path)


@mcp.tool()
@governed_tool(risk_level="high", undo=_load_config_undo)
@tool_errors("dict")
def load_config(
    config: dict,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Replace caddy's FULL running config; reversible —
    the prior config is snapshotted first and the undo re-loads it.

    Pass dry_run=True to preview.

    Refuses a config that disables the admin API or moves admin.listen off the
    configured base_url — the undo re-POSTs the snapshot over that same API.
    Send the admin block unchanged, or omit it. The refusal applies under
    dry_run too, which must report it rather than preview a refused call.

    Args:
        config: The full config tree to load (a JSON object).
        dry_run: If True, preview without loading.
        target: Proxy target name from config; omit for the default.
    """
    conn = _get_connection(target)
    # Ahead of the dry_run return: costs the same config_root GET the real call
    # makes, and in exchange the preview can never contradict it.
    ops.guard_load_config(conn, config)
    if dry_run:
        return {"dryRun": True, "wouldLoad": {"topLevelKeys": sorted(config or {})}}
    return ops.load_config(conn, config)


# ── haproxy runtime-server writes ────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_server_state_undo)
@tool_errors("dict")
def set_server_state(
    backend: str,
    server: str,
    state: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Set an haproxy server's admin state (ready / drain
    / maint); reversible — the prior admin state is fetched first and the undo
    restores it.

    drain finishes in-flight sessions but takes no new ones; maint removes the
    server immediately; ready returns it to rotation. On traefik/caddy this
    raises the support matrix's teaching error. Pass dry_run=True to preview.

    Args:
        backend: Backend name (from list_services).
        server: Server name inside the backend (from list_upstreams).
        state: One of ready, drain, maint.
        dry_run: If True, preview without changing.
        target: Proxy target name from config; omit for the default.
    """
    conn = _get_connection(target)
    # Ahead of the dry_run return: off-platform (traefik/caddy) the real call
    # raises the support matrix's teaching error, so the preview must too — a
    # green 'wouldSetState' for a call that cannot run reads as transient.
    state = ops.guard_set_server_state(conn, backend, server, state)
    if dry_run:
        return {
            "dryRun": True,
            "wouldSetState": {"backend": backend, "server": server, "state": state},
        }
    return ops.set_server_state(conn, backend, server, state)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_server_weight_undo)
@tool_errors("dict")
def set_server_weight(
    backend: str,
    server: str,
    weight: int,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Set an haproxy server's load-balancing weight
    (0-256); reversible — the prior weight is fetched first and the undo
    restores it.

    Weight 0 stops new traffic to the server without a state change. On
    traefik/caddy this raises the support matrix's teaching error. Pass
    dry_run=True to preview.

    Args:
        backend: Backend name (from list_services).
        server: Server name inside the backend (from list_upstreams).
        weight: New weight, 0-256.
        dry_run: If True, preview without changing.
        target: Proxy target name from config; omit for the default.
    """
    conn = _get_connection(target)
    # Ahead of the dry_run return: off-platform (traefik/caddy) the real call
    # raises the support matrix's teaching error, so the preview must too.
    weight = ops.guard_set_server_weight(conn, backend, server, weight)
    if dry_run:
        return {
            "dryRun": True,
            "wouldSetWeight": {"backend": backend, "server": server, "weight": weight},
        }
    return ops.set_server_weight(conn, backend, server, weight)
