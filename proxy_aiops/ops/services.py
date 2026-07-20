"""Service / backend / upstream reads, normalised across platforms.

*Service* is the platform-neutral grouping (Traefik service / Caddy
reverse_proxy route / HAProxy backend); *upstream* is one server inside it.

Normalised upstream row: ``{service, server, address, status, adminState,
checkInfo, weight, requests}`` — ``status`` is one of ``up`` / ``down`` /
``maint`` / ``drain`` / ``unknown``. This row shape feeds
``backend_health_rca`` directly.
"""

from __future__ import annotations

from typing import Any

from proxy_aiops.ops import routes as route_ops
from proxy_aiops.ops._util import as_int, as_obj, num, opt, pick, s
from proxy_aiops.platform import CADDY, HAPROXY, TRAEFIK, UnsupportedOperation

MAX_ROWS = 500


# ── services ─────────────────────────────────────────────────────────────────


def _traefik_services(conn: Any) -> list[dict]:
    rows = conn.platform.rows(conn.get(conn.platform.path("services")))
    out = []
    for r in rows:
        lb = as_obj(pick(r, "loadBalancer", default={}))
        servers = lb.get("servers") or []
        status_map = as_obj(pick(r, "serverStatus", default={}))
        out.append({
            "name": opt(pick(r, "name")),
            "platform": TRAEFIK,
            "type": opt(pick(r, "type", default="loadBalancer")),
            "serversTotal": len(servers) or len(status_map),
            "serversUp": sum(1 for v in status_map.values() if str(v).upper() == "UP"),
            "usedBy": [s(u, 128) for u in (pick(r, "usedBy", default=[]) or [])],
            "status": opt(pick(r, "status", default="")),
        })
    return out


def _caddy_services(conn: Any) -> list[dict]:
    """Each reverse_proxy route is a 'service'; health joined from upstreams."""
    upstream_health = {u["address"]: u for u in _caddy_upstreams(conn)}
    cfg = conn.platform.normalise(conn.get(conn.platform.path("config_root")))
    out = []
    for srv_name, srv in route_ops.caddy_http_servers(cfg).items():
        for idx, route in enumerate(as_obj(srv).get("routes") or []):
            for h in as_obj(route).get("handle") or []:
                h = as_obj(h)
                if h.get("handler") != "reverse_proxy":
                    continue
                dials = [str(as_obj(u).get("dial", "")) for u in h.get("upstreams") or []]
                dials = [d for d in dials if d]
                up = sum(
                    1 for d in dials
                    if upstream_health.get(d, {}).get("status", "unknown") == "up"
                )
                out.append({
                    "name": f"apps/http/servers/{s(srv_name, 64)}/routes/{idx}",
                    "platform": CADDY,
                    "type": "reverse_proxy",
                    "serversTotal": len(dials),
                    "serversUp": up,
                    "usedBy": [],
                    "status": "",
                })
    return out


def _haproxy_services(conn: Any) -> list[dict]:
    rows = conn.platform.rows(conn.get(conn.platform.path("backends")))
    stat_rows = _haproxy_stat_rows(conn)
    per_backend: dict[str, dict] = {}
    for row in stat_rows:
        if row.get("type") != "server":
            continue
        bucket = per_backend.setdefault(row.get("backend", ""), {"total": 0, "up": 0})
        bucket["total"] += 1
        if row.get("status") == "up":
            bucket["up"] += 1
    out = []
    for r in rows:
        name = opt(pick(r, "name"))
        counts = per_backend.get(name, {"total": 0, "up": 0})
        out.append({
            "name": name,
            "platform": HAPROXY,
            "type": opt(pick(r, "mode", default="http")),
            "serversTotal": counts["total"],
            "serversUp": counts["up"],
            "usedBy": [],
            "status": opt(pick(r, "balance", default=""), 64),
        })
    return out


def list_services(conn: Any) -> dict:
    """[READ] Services / backends with per-service server-up counts."""
    try:
        if conn.target.platform == TRAEFIK:
            services = _traefik_services(conn)
        elif conn.target.platform == CADDY:
            services = _caddy_services(conn)
        else:
            services = _haproxy_services(conn)
        return {
            "platform": conn.target.platform,
            "total": len(services),
            "services": services[:MAX_ROWS],
        }
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}


def service_detail(conn: Any, name: str) -> dict:
    """[READ] One service's full (sanitised) detail by name."""
    try:
        if conn.target.platform == TRAEFIK:
            raw = conn.get(conn.platform.path("service_detail", name=name))
            return {"platform": TRAEFIK, "service": conn.platform.normalise(as_obj(raw))}
        if conn.target.platform == HAPROXY:
            raw = conn.get(conn.platform.path("backend_detail", name=name))
            obj = as_obj(raw)
            detail = conn.platform.normalise(as_obj(obj.get("data")) or obj)
            servers = [u for u in _haproxy_upstreams(conn, backend=name)]
            return {"platform": HAPROXY, "service": detail, "servers": servers}
        listed = list_services(conn)
        if "error" in listed:
            return listed
        for svc in listed.get("services", []):
            if svc.get("name") == name:
                return {"platform": CADDY, "service": svc}
        raise KeyError(
            f"Service '{name}' not found. Use list_services to get current names."
        )
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}


# ── upstreams (server level) ─────────────────────────────────────────────────


def _traefik_upstreams(conn: Any) -> list[dict]:
    rows = conn.platform.rows(conn.get(conn.platform.path("services")))
    out = []
    for r in rows:
        name = opt(pick(r, "name"))
        status_map = as_obj(pick(r, "serverStatus", default={}))
        lb_servers = [
            str(as_obj(srv).get("url", ""))
            for srv in as_obj(pick(r, "loadBalancer", default={})).get("servers") or []
        ]
        addresses = list(status_map) or [a for a in lb_servers if a]
        for addr in addresses:
            raw_status = str(status_map.get(addr, "")).upper()
            status = {"UP": "up", "DOWN": "down"}.get(raw_status, "unknown")
            out.append({
                "service": name,
                "server": s(addr, 200),
                "address": s(addr, 200),
                "status": status,
                "adminState": "",
                "checkInfo": "" if raw_status else "no health check configured",
                "weight": None,
                "requests": None,
            })
    return out


def _caddy_upstreams(conn: Any) -> list[dict]:
    rows = conn.platform.rows(conn.get(conn.platform.path("upstreams")))
    out = []
    for r in rows:
        fails = int(num(pick(r, "fails", default=0)))
        out.append({
            "service": "",
            "server": opt(pick(r, "address"), 200),
            "address": opt(pick(r, "address"), 200),
            "status": "down" if fails > 0 else "up",
            "adminState": "",
            "checkInfo": f"fails={fails}" if fails else "",
            "weight": None,
            "requests": int(num(pick(r, "num_requests", default=0))),
        })
    return out


def _haproxy_stat_rows(conn: Any) -> list[dict]:
    """Flatten Data Plane API native stats into neutral per-row dicts."""
    raw = conn.get(conn.platform.path("stats"))
    runs = raw if isinstance(raw, list) else [raw]
    out = []
    for run in runs:
        for row in as_obj(run).get("stats") or []:
            row = as_obj(row)
            stats = as_obj(row.get("stats"))
            raw_status = str(pick(stats, "status", default="")).upper()
            if raw_status.startswith("UP"):
                status = "up"
            elif raw_status.startswith("DOWN"):
                status = "down"
            elif raw_status.startswith("MAINT"):
                status = "maint"
            elif raw_status.startswith("DRAIN"):
                status = "drain"
            else:
                status = "unknown"
            out.append({
                "name": opt(pick(row, "name")),
                "backend": opt(pick(row, "backend_name", default="")),
                "type": opt(pick(row, "type", default="")),
                "status": status,
                "checkStatus": opt(pick(stats, "check_status", default=""), 64),
                "address": opt(pick(stats, "addr", default=""), 200),
                "weight": as_int(pick(stats, "weight", default=0)),
                "requestsTotal": as_int(pick(stats, "req_tot", default=0)),
                "hrsp2xx": num(pick(stats, "hrsp_2xx", default=0)),
                "hrsp4xx": num(pick(stats, "hrsp_4xx", default=0)),
                "hrsp5xx": num(pick(stats, "hrsp_5xx", default=0)),
                "currentSessions": as_int(pick(stats, "scur", default=0)),
                "rate": num(pick(stats, "rate", default=0)),
                "lastStatusChange": as_int(pick(stats, "lastchg", default=0)),
            })
    return out


def _haproxy_upstreams(conn: Any, backend: str | None = None) -> list[dict]:
    rows = [r for r in _haproxy_stat_rows(conn) if r.get("type") == "server"]
    if backend:
        rows = [r for r in rows if r.get("backend") == backend]
    return [
        {
            "service": r["backend"],
            "server": r["name"],
            "address": r["address"],
            "status": r["status"],
            "adminState": "maint" if r["status"] in ("maint", "drain") else "ready",
            "checkInfo": r["checkStatus"],
            "weight": r["weight"],
            "requests": r["requestsTotal"],
        }
        for r in rows
    ]


def list_upstreams(conn: Any, service: str | None = None) -> dict:
    """[READ] Server-level upstream health rows (feeds backend_health_rca)."""
    try:
        if conn.target.platform == TRAEFIK:
            ups = _traefik_upstreams(conn)
        elif conn.target.platform == CADDY:
            ups = _caddy_upstreams(conn)
        else:
            ups = _haproxy_upstreams(conn)
        if service:
            ups = [u for u in ups if u.get("service") == service or not u.get("service")]
        return {
            "platform": conn.target.platform,
            "total": len(ups),
            "down": sum(1 for u in ups if u["status"] == "down"),
            "upstreams": ups[:MAX_ROWS],
        }
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}


def upstream_detail(conn: Any, service: str, server: str) -> dict:
    """[READ] One upstream server's health/state row."""
    listed = list_upstreams(conn, service=service)
    if "error" in listed:
        return listed
    for u in listed.get("upstreams", []):
        if u.get("server") == server or u.get("address") == server:
            return {"platform": conn.target.platform, "upstream": u}
    return {
        "error": (
            f"Server '{server}' not found in service '{service}'. "
            f"Use list_upstreams to get current names."
        )
    }


def list_middlewares(conn: Any) -> dict:
    """[READ] Middlewares (Traefik). Caddy/HAProxy raise a teaching note."""
    try:
        rows = conn.platform.rows(conn.get(conn.platform.path("middlewares")))
        mws = [
            {
                "name": opt(pick(r, "name")),
                "type": opt(pick(r, "type", default="")),
                "status": opt(pick(r, "status", default="")),
                "usedBy": [s(u, 128) for u in (pick(r, "usedBy", default=[]) or [])],
            }
            for r in rows
        ]
        return {"platform": conn.target.platform, "total": len(mws), "middlewares": mws}
    except UnsupportedOperation as exc:
        return {"platform": conn.target.platform, "unsupported": s(exc, 400)}
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}
