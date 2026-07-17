"""One-shot proxy overview (read-only).

A single call an operator can lead with: platform + version, route count,
service count, and upstream up/down counts. Resilient — a failing sub-call
degrades to a partial summary with an ``errors`` list.
"""

from __future__ import annotations

from typing import Any

from proxy_aiops.ops import routes as route_ops
from proxy_aiops.ops import services as service_ops
from proxy_aiops.ops import status as status_ops


def proxy_overview(conn: Any) -> dict:
    """[READ] Summary: platform/version + route/service counts + upstream health."""
    errors: list[str] = []

    ver = status_ops.version_info(conn)
    if isinstance(ver, dict) and "error" in ver:
        errors.append(f"version: {ver['error']}")
        ver = {}

    rl = route_ops.list_routes(conn)
    route_total = rl.get("total") if isinstance(rl, dict) and "error" not in rl else None
    if isinstance(rl, dict) and "error" in rl:
        errors.append(f"routes: {rl['error']}")

    sl = service_ops.list_services(conn)
    svc_list = sl.get("services", []) if isinstance(sl, dict) and "error" not in sl else []
    if isinstance(sl, dict) and "error" in sl:
        errors.append(f"services: {sl['error']}")

    ul = service_ops.list_upstreams(conn)
    up_ok = isinstance(ul, dict) and "error" not in ul
    upstreams = ul.get("upstreams", []) if up_ok else []
    if isinstance(ul, dict) and "error" in ul:
        errors.append(f"upstreams: {ul['error']}")

    return {
        "platform": conn.target.platform,
        "target": conn.target.name,
        "version": ver.get("version"),
        "routesTotal": route_total,
        "servicesTotal": len(svc_list),
        "servicesWithNoServerUp": sum(
            1 for svc in svc_list
            if svc.get("serversTotal") and not svc.get("serversUp")
        ),
        "upstreamsTotal": len(upstreams),
        "upstreamsDown": sum(1 for u in upstreams if u.get("status") == "down"),
        "errors": errors,
    }
