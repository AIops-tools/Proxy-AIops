"""Status reads — version/info and entrypoints/listeners (read-only).

The day-to-day "is this proxy healthy?" surface, platform-neutral: each op asks
the platform for the right path and unwraps the payload through the shared
helpers. Every call is resilient — a transport/parse failure surfaces as
``{"error": ...}`` instead of raising, and all proxy text is sanitised via
``s``. Platform gaps surface as teaching notes (e.g. Caddy has no version
endpoint), never silent empties.
"""

from __future__ import annotations

from typing import Any

from proxy_aiops.ops._util import as_obj, pick, s
from proxy_aiops.platform import CADDY, HAPROXY, TRAEFIK, UnsupportedOperation


def version_info(conn: Any) -> dict:
    """[READ] Version / build info for the proxy behind this target."""
    try:
        obj = as_obj(conn.get(conn.platform.path("version")))
    except UnsupportedOperation as exc:
        return {"platform": conn.target.platform, "note": s(exc, 400)}
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}
    if conn.target.platform == HAPROXY:
        api = as_obj(obj.get("api"))
        system = as_obj(obj.get("system"))
        return {
            "platform": conn.target.platform,
            "version": s(pick(api, "version", default="")),
            "buildDate": s(pick(api, "build_date", default="")),
            "systemVersion": s(pick(system, "version", default="")),
        }
    return {
        "platform": conn.target.platform,
        "version": s(pick(obj, "Version", "version")),
        "codename": s(pick(obj, "Codename", "codename")),
        "startDate": s(pick(obj, "startDate", "start_date")),
    }


def list_entrypoints(conn: Any) -> dict:
    """[READ] Listeners: Traefik entrypoints / Caddy server listen addresses /
    HAProxy frontend binds — where traffic enters this proxy."""
    try:
        if conn.target.platform == TRAEFIK:
            rows = conn.platform.rows(conn.get(conn.platform.path("entrypoints")))
            eps = [
                {"name": s(pick(r, "name")), "address": s(pick(r, "address"))}
                for r in rows
            ]
        elif conn.target.platform == CADDY:
            cfg = conn.platform.normalise(conn.get(conn.platform.path("config_root")))
            servers = as_obj(as_obj(as_obj(as_obj(cfg).get("apps")).get("http")).get("servers"))
            eps = [
                {"name": s(name), "address": ", ".join(s(a) for a in (srv or {}).get("listen", []))}
                for name, srv in servers.items()
            ]
        else:  # haproxy: frontends (+ note that binds need a per-frontend call)
            rows = conn.platform.rows(conn.get(conn.platform.path("frontends")))
            eps = [
                {
                    "name": s(pick(r, "name")),
                    "address": s(pick(r, "default_backend", default="")),
                    "mode": s(pick(r, "mode", default="")),
                }
                for r in rows
            ]
        return {"platform": conn.target.platform, "total": len(eps), "entrypoints": eps}
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}
