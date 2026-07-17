"""Governed proxy writes — the only state-changing operations in the tool.

Platform dispatch is enforced by the support matrix: the first thing every
write does is resolve its resource path, and on the wrong platform that raises
the registry's teaching error (Traefik writes → "edit the provider"; HAProxy
config writes → transactions out of scope), never a silent no-op.

Every reversible write reads the current state **before** it changes anything,
so the harness records a faithful undo / audit trail (the before-state is
fetched via a real GET, never guessed):

  * ``set_config_value`` / ``delete_config_path`` (caddy) — read the config
    subtree at the path first; undo restores the prior subtree.
  * ``load_config`` (caddy) — snapshot the FULL config first; undo re-loads it.
  * ``set_server_state`` / ``set_server_weight`` (haproxy) — read the runtime
    server's admin state / weight first; undo sets it back.

Each function returns a plain descriptor; the MCP layer adds dry-run + the
governance harness (risk tier + audit + undo).
"""

from __future__ import annotations

import json
from typing import Any

from proxy_aiops.connection import ProxyApiError
from proxy_aiops.ops._util import s
from proxy_aiops.platform import encode_config_path

SERVER_STATES = ("ready", "drain", "maint")
_MIN_WEIGHT, _MAX_WEIGHT = 0, 256
_MAX_VALUE_BYTES = 200_000  # bound on a config payload we will write


def _validate_value_size(value: Any) -> None:
    if len(json.dumps(value, default=str)) > _MAX_VALUE_BYTES:
        raise ValueError(
            f"Config value too large (> {_MAX_VALUE_BYTES} bytes) — write "
            f"smaller subtrees, or use load_config for a full replace."
        )


# ── caddy config writes (reversible via prior-config capture) ───────────────


def _get_config_subtree(conn: Any, enc_path: str) -> tuple[Any, bool]:
    """Fetch the current subtree at a config path; (value, existed)."""
    base = conn.platform.path("config_get")
    try:
        return conn.get(base + enc_path), True
    except ProxyApiError as exc:
        if exc.status_code == 404:
            return None, False
        raise


def set_config_value(conn: Any, path: str, value: Any) -> dict:
    """[WRITE][med] Set one config subtree (e.g. a route's upstreams),
    capturing the prior subtree for a replayable undo.

    PATCH replaces an existing subtree; a path that does not exist yet is
    created with POST. The prior value is fetched via a real GET first.
    """
    _validate_value_size(value)
    base = conn.platform.path("config_set")  # teaching error off-platform
    enc = encode_config_path(path)
    prior, existed = _get_config_subtree(conn, enc)
    if existed:
        conn.patch(base + enc, json=value)
    else:
        conn.post(base + enc, json=value)
    return {
        "action": "set_config_value",
        "path": s(path, 300),
        "priorState": {"value": prior, "existed": existed},
        "note": "Caddy applies config changes immediately (no separate apply step).",
    }


def delete_config_path(conn: Any, path: str) -> dict:
    """[WRITE][high] Delete a config subtree, capturing it first for undo."""
    base = conn.platform.path("config_delete")  # teaching error off-platform
    enc = encode_config_path(path)
    prior, existed = _get_config_subtree(conn, enc)
    if not existed:
        raise KeyError(
            f"Config path '{path}' does not exist — nothing to delete. "
            f"Use search_config / config_snapshot to find the right path."
        )
    conn.delete(base + enc)
    return {
        "action": "delete_config_path",
        "path": s(path, 300),
        "priorState": {"value": prior, "existed": True},
    }


def load_config(conn: Any, config: dict) -> dict:
    """[WRITE][high] Replace the FULL running config, snapshotting the prior
    config first (undo re-loads the snapshot)."""
    if not isinstance(config, dict) or not config:
        raise ValueError("config must be a non-empty JSON object (the full config tree).")
    _validate_value_size(config)
    load_path = conn.platform.path("config_load")  # teaching error off-platform
    prior = conn.get(conn.platform.path("config_root"))
    conn.post(load_path, json=config)
    return {
        "action": "load_config",
        "priorState": {"config": prior},
        "note": "Full config replace — the prior config was snapshotted for undo.",
    }


# ── haproxy runtime-server writes (reversible via prior state) ──────────────


def _runtime_server(conn: Any, backend: str, server: str) -> dict:
    path = conn.platform.path("runtime_server", name=server, backend=backend)
    raw = conn.get(path)
    return raw if isinstance(raw, dict) else {}


def set_server_state(conn: Any, backend: str, server: str, state: str) -> dict:
    """[WRITE][med] Set a server's admin state (ready / drain / maint),
    capturing its prior admin state. Undo restores it.

    ``drain`` finishes in-flight sessions but takes no new ones; ``maint``
    removes the server immediately; ``ready`` returns it to rotation.
    """
    state = str(state).strip().lower()
    if state not in SERVER_STATES:
        raise ValueError(
            f"state must be one of {SERVER_STATES}, got '{state}'."
        )
    # Resolving the path first raises the teaching error on traefik/caddy.
    path = conn.platform.path("runtime_server", name=server, backend=backend)
    prior = _runtime_server(conn, backend, server)
    prior_state = str(prior.get("admin_state") or "").lower() or None
    conn.put(path, json={"admin_state": state})
    return {
        "action": "set_server_state",
        "backend": s(backend, 128),
        "server": s(server, 128),
        "state": state,
        "priorState": {"adminState": prior_state},
    }


def set_server_weight(conn: Any, backend: str, server: str, weight: int) -> dict:
    """[WRITE][med] Set a server's load-balancing weight, capturing the prior
    weight. Undo restores it. Weight 0 stops new traffic without a state change.
    """
    try:
        weight = int(weight)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"weight must be an integer, got '{weight}'.") from exc
    if not (_MIN_WEIGHT <= weight <= _MAX_WEIGHT):
        raise ValueError(
            f"weight must be between {_MIN_WEIGHT} and {_MAX_WEIGHT}, got {weight}."
        )
    path = conn.platform.path("runtime_server", name=server, backend=backend)
    prior = _runtime_server(conn, backend, server)
    prior_weight = prior.get("weight")
    conn.put(path, json={"weight": weight})
    return {
        "action": "set_server_weight",
        "backend": s(backend, 128),
        "server": s(server, 128),
        "weight": weight,
        "priorState": {
            "weight": int(prior_weight) if prior_weight is not None else None
        },
    }
