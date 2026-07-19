"""Config-tree reads (read-only): snapshot + search.

* **caddy** — ``GET /config/`` (the live config tree, exactly what the config
  writes patch).
* **traefik** — ``GET /api/rawdata`` (the merged dynamic state from all
  providers — read-only; edits belong to the provider).
* **haproxy** — unsupported with a teaching error (raw haproxy.cfg is not
  fetched in this preview; use the structured reads).
"""

from __future__ import annotations

from typing import Any

from proxy_aiops.ops._util import s
from proxy_aiops.platform import UnsupportedOperation

MAX_MATCHES = 50


def config_snapshot(conn: Any) -> dict:
    """[READ] The live config tree / merged dynamic state (sanitised, bounded)."""
    try:
        raw = conn.get(conn.platform.path("config_snapshot"))
        return {
            "platform": conn.target.platform,
            "config": conn.platform.normalise(raw),
            "note": (
                "Sanitised snapshot (strings bounded, depth capped). For caddy this "
                "is the exact tree the config writes patch."
            ),
        }
    except UnsupportedOperation as exc:
        return {"platform": conn.target.platform, "unsupported": s(exc, 400)}
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}


def _walk(node: Any, needle: str, path: str, hits: list[dict]) -> None:
    # Stop one past the cap, not at it: the extra hit is what lets the caller
    # distinguish "exactly MAX_MATCHES matches" from "more than we will return".
    if len(hits) > MAX_MATCHES:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            key_path = f"{path}/{key}" if path else str(key)
            if needle in str(key).lower():
                hits.append({"path": s(key_path, 300), "match": "key"})
                if len(hits) > MAX_MATCHES:
                    return
            _walk(value, needle, key_path, hits)
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            _walk(value, needle, f"{path}/{idx}", hits)
    else:
        if needle in str(node).lower():
            hits.append({"path": s(path, 300), "match": "value", "value": s(node, 160)})


def search_config(conn: Any, query: str) -> dict:
    """[READ] Search the config tree for a string; returns matching paths.

    The returned paths are config paths — on caddy they can be passed straight
    to get_config_value / set_config_value.
    """
    if not query or not str(query).strip():
        raise ValueError("query must be a non-empty string.")
    snap = config_snapshot(conn)
    if "config" not in snap:
        return snap
    hits: list[dict] = []
    # The walk collects one more hit than we return, so truncation is *measured*
    # rather than inferred from the count happening to reach the cap — at
    # exactly MAX_MATCHES hits the old test could not tell "full" from "more".
    _walk(snap["config"], str(query).strip().lower(), "", hits)
    truncated = len(hits) > MAX_MATCHES
    matches = hits[:MAX_MATCHES]
    return {
        "platform": conn.target.platform,
        "query": s(query, 128),
        "matches": matches,
        "returned": len(matches),
        "limit": MAX_MATCHES,
        "truncated": truncated,
    }


def get_config_value(conn: Any, path: str) -> dict:
    """[READ] One value out of the caddy config tree by config path."""
    from proxy_aiops.platform import encode_config_path

    try:
        base = conn.platform.path("config_get")  # raises teaching error off-platform
        raw = conn.get(base + encode_config_path(path))
        return {
            "platform": conn.target.platform,
            "path": s(path, 300),
            "value": conn.platform.normalise(raw),
        }
    except UnsupportedOperation as exc:
        return {"platform": conn.target.platform, "unsupported": s(exc, 400)}
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}
