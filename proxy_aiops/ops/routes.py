"""Route reads — the host/path → service wiring, normalised across platforms.

A *route* is the platform-neutral row this tool reasons about:

  * **traefik** — an HTTP router (``rule`` string parsed into hosts/paths).
  * **caddy** — a route inside ``apps.http.servers.<srv>.routes`` (its ``name``
    is the config path, e.g. ``apps/http/servers/srv0/routes/0`` — directly
    usable by the config writes).
  * **haproxy** — a frontend (host/path matching lives in ACLs inside
    haproxy.cfg, so hosts/paths stay empty; the default backend is the service).

Normalised row: ``{name, platform, hosts, paths, priority, service, tls,
enabled, redirectTo, raw}``.
"""

from __future__ import annotations

from typing import Any

from proxy_aiops.ops._util import (
    as_int,
    as_obj,
    opt,
    parse_rule_hosts,
    parse_rule_paths,
    pick,
    s,
)
from proxy_aiops.platform import CADDY, TRAEFIK

MAX_ROUTES = 500


def _traefik_routes(conn: Any) -> list[dict]:
    rows = conn.platform.rows(conn.get(conn.platform.path("routers")))
    out = []
    for r in rows:
        rule = str(pick(r, "rule", default=""))
        out.append({
            "name": opt(pick(r, "name")),
            "platform": TRAEFIK,
            "hosts": [s(h, 128) for h in parse_rule_hosts(rule)],
            "paths": [s(p, 128) for p in parse_rule_paths(rule)],
            "priority": as_int(pick(r, "priority", default=0)),
            "service": opt(pick(r, "service")),
            # Traefik marks a TLS router with a "tls" object that is often
            # EMPTY ({}) — presence, not truthiness, is the signal.
            "tls": r.get("tls") is not None,
            "enabled": str(pick(r, "status", default="enabled")).lower() != "disabled",
            "entryPoints": [s(e, 64) for e in (pick(r, "entryPoints", default=[]) or [])],
            # Traefik expresses redirects as middleware, not on the router, so
            # this normaliser has nothing to report — null, not "no redirect".
            "redirectTo": None,
            "raw": opt(pick(r, "rule"), 300),
        })
    return out


def _caddy_redirect_target(handlers: list) -> str | None:
    """The redirect target, or None when this route is not a redirect at all."""
    for h in handlers or []:
        h = as_obj(h)
        if h.get("handler") == "static_response":
            locs = as_obj(h.get("headers")).get("Location") or []
            if locs:
                return str(locs[0])
    return None


def _caddy_service(handlers: list) -> str:
    """A reverse_proxy handler's upstream dials name the 'service'."""
    for h in handlers or []:
        h = as_obj(h)
        if h.get("handler") == "reverse_proxy":
            dials = [str(as_obj(u).get("dial", "")) for u in h.get("upstreams") or []]
            return ",".join(d for d in dials if d)
    for h in handlers or []:
        handler = as_obj(h).get("handler")
        if handler:
            return f"({handler})"
    return ""


def caddy_http_servers(cfg: Any) -> dict:
    """The ``apps.http.servers`` map out of a Caddy config tree."""
    return as_obj(as_obj(as_obj(as_obj(cfg).get("apps")).get("http")).get("servers"))


def _caddy_routes(conn: Any) -> list[dict]:
    cfg = conn.platform.normalise(conn.get(conn.platform.path("config_root")))
    out = []
    for srv_name, srv in caddy_http_servers(cfg).items():
        srv = as_obj(srv)
        listen = [str(a) for a in srv.get("listen") or []]
        tls_ish = any(a.endswith(":443") or a.endswith(":8443") for a in listen)
        for idx, route in enumerate(srv.get("routes") or []):
            route = as_obj(route)
            hosts: list[str] = []
            paths: list[str] = []
            for m in route.get("match") or []:
                m = as_obj(m)
                hosts.extend(str(h).lower() for h in m.get("host") or [])
                paths.extend(str(p) for p in m.get("path") or [])
            handlers = route.get("handle") or []
            out.append({
                "name": f"apps/http/servers/{s(srv_name, 64)}/routes/{idx}",
                "platform": CADDY,
                "hosts": [s(h, 128) for h in hosts],
                "paths": [s(p, 128) for p in paths],
                "priority": 0,
                "service": s(_caddy_service(handlers)),
                "tls": tls_ish,
                "enabled": True,
                "entryPoints": [s(a, 64) for a in listen],
                "redirectTo": opt(_caddy_redirect_target(handlers)),
                "raw": None,
            })
    return out


def _haproxy_routes(conn: Any) -> list[dict]:
    rows = conn.platform.rows(conn.get(conn.platform.path("frontends")))
    return [
        {
            "name": opt(pick(r, "name")),
            "platform": "haproxy",
            "hosts": [],
            "paths": [],
            "priority": 0,
            "service": opt(pick(r, "default_backend", default="")),
            "tls": False,
            "enabled": str(pick(r, "enabled", default="enabled")).lower() != "disabled",
            "entryPoints": [],
            "redirectTo": None,
            "raw": opt(pick(r, "mode"), 64),
            "note": "Host/path matching lives in haproxy.cfg ACLs "
                    "(not statically analysable here).",
        }
        for r in rows
    ]


def list_routes(conn: Any, host: str | None = None) -> dict:
    """[READ] Routes (routers / caddy routes / frontends), optionally filtered
    to those matching a hostname."""
    try:
        if conn.target.platform == TRAEFIK:
            routes = _traefik_routes(conn)
        elif conn.target.platform == CADDY:
            routes = _caddy_routes(conn)
        else:
            routes = _haproxy_routes(conn)
        if host:
            needle = host.strip().lower()
            routes = [r for r in routes if not r["hosts"] or needle in r["hosts"]]
        return {
            "platform": conn.target.platform,
            "total": len(routes),
            "routes": routes[:MAX_ROUTES],
        }
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}


def route_detail(conn: Any, name: str) -> dict:
    """[READ] One route's full (sanitised) detail by name."""
    try:
        if conn.target.platform == TRAEFIK:
            raw = conn.get(conn.platform.path("router_detail", name=name))
            return {"platform": TRAEFIK, "route": conn.platform.normalise(as_obj(raw))}
        # caddy/haproxy: resolve from the normalised list (names are stable).
        listed = list_routes(conn)
        if "error" in listed:
            return listed
        for r in listed.get("routes", []):
            if r.get("name") == name:
                return {"platform": conn.target.platform, "route": r}
        raise KeyError(
            f"Route '{name}' not found. Use list_routes to get current names."
        )
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}


def find_route(conn: Any, host: str, path: str = "/") -> dict:
    """[READ] Which routes would serve a given host/path (static match).

    Matches the normalised host/path rows: a route matches when its host list
    is empty or contains the host, and its path list is empty or has a prefix
    of ``path``. Ordered best-match first (more specific paths, higher
    priority).
    """
    listed = list_routes(conn)
    if "error" in listed:
        return listed
    needle_host = host.strip().lower()
    matches = []
    for r in listed.get("routes", []):
        if r.get("hosts") and needle_host not in r["hosts"]:
            continue
        prefixes = [p for p in r.get("paths", []) if path.startswith(p.rstrip("*"))]
        if r.get("paths") and not prefixes:
            continue
        specificity = max((len(p) for p in prefixes), default=0)
        matches.append({**r, "_spec": (specificity, r.get("priority", 0))})
    matches.sort(key=lambda m: m["_spec"], reverse=True)
    for m in matches:
        m.pop("_spec", None)
    return {
        "platform": conn.target.platform,
        "host": s(host, 128),
        "path": s(path, 128),
        "matched": len(matches),
        "routes": matches[:20],
        "note": (
            "Static analysis of the fetched route table — header/method/regex "
            "matchers and haproxy ACLs are not evaluated."
        ),
    }
