"""Flagship signature analyses over proxy telemetry (pure analysis).

These are the differentiators — transparent heuristics, every flag reported
with its numbers so an operator can see *why* something was ranked, never a
black-box verdict:

  1. ``backend_health_rca`` — down upstreams/servers grouped per service with a
     likely cause (health-check failure class, connection refused, DNS,
     all-servers-down, admin maint/drain) and a recommended action.
  2. ``cert_expiry_sweep`` — TLS cert inventory bucketed by days-to-expiry
     (expired / critical / warning / ok) with per-platform renewal hints.
  3. ``error_rate_rca`` — per-service 5xx share vs the fleet baseline, with the
     dominant status code mapped to a cause (503 no-upstream / 502 conn-fail /
     504 timeout / 500 app error).
  4. ``route_conflict_analysis`` — static analysis of the fetched route table:
     shadowed (an earlier/higher-priority route covers a later one), dead
     routes (service missing or zero servers up), and redirect loops.

All four are pure functions (no I/O): pass them the telemetry (from the reads
in the other ops modules, or injected) and they return the analysis.
"""

from __future__ import annotations

from proxy_aiops.ops._util import num, opt, s

MAX_ROWS = 100

# ── 1. backend / upstream health RCA ─────────────────────────────────────────

# HAProxy check_status prefixes → failure class.
_CHECK_CLASSES = (
    ("L4CON", "connection refused — nothing is listening on the address:port"),
    ("L4TOUT", "layer-4 timeout — host unreachable or packets dropped on the path"),
    ("L6", "TLS handshake failure between proxy and server"),
    ("L7TOUT", "health-check timeout — the app answers too slowly"),
    ("L7RSP", "malformed health-check response from the app"),
    ("L7STS", "health check got a failing HTTP status — app is up but unhealthy"),
    ("SOCKERR", "socket/DNS error — the server address may not resolve"),
    ("RESOLV", "DNS resolution failure for the server address"),
)


def _classify_check(check_info: str) -> str | None:
    check = str(check_info or "").upper()
    for prefix, meaning in _CHECK_CLASSES:
        if check.startswith(prefix):
            return meaning
    return None


def _classify_service(total: int, down: int, maint: int, checks: list[str]) -> dict:
    if total == 0:
        return {
            "cause": "No servers configured for this service",
            "action": "Add at least one upstream server, or remove the dead service/route.",
        }
    check_cause = next((c for c in (_classify_check(ci) for ci in checks) if c), None)
    if down + maint >= total:
        if maint == total:
            return {
                "cause": "All servers administratively in maint/drain",
                "action": "If maintenance is done, return them with set_server_state "
                "(state=ready); users currently get 503s.",
            }
        cause = check_cause or "All servers down — upstream app or its host is failing"
        return {
            "cause": f"OUTAGE: {cause}",
            "action": "Check the upstream app (recent deploy? process up? port "
            "listening?) and its DNS/address; users receive 502/503 until one "
            "server recovers.",
        }
    if down > 0:
        cause = check_cause or "Some servers failing health checks"
        return {
            "cause": f"Degraded capacity: {cause}",
            "action": "Investigate the failing server(s); remaining servers carry "
            "the load — watch for saturation.",
        }
    if maint > 0:
        return {
            "cause": "Healthy, but some servers are in maint/drain",
            "action": "Return drained servers with set_server_state (state=ready) "
            "when their maintenance is done.",
        }
    return {"cause": "Healthy — all servers up", "action": "No action needed."}


def backend_health_rca(upstreams: list[dict]) -> dict:
    """[READ] Group upstream rows per service and map each to cause + action.

    Pure analysis over rows (from ``list_upstreams`` or injected):
    ``{service, server, address, status(up|down|maint|drain|unknown),
    checkInfo}``. Ranks worst-first (full outage, then degraded); every finding
    carries its numbers and the failing servers' check info.
    """
    per_service: dict[str, dict] = {}
    for u in upstreams or []:
        service = opt(u.get("service") or "(default)", 200)
        bucket = per_service.setdefault(
            service,
            {"service": service, "serversTotal": 0, "up": 0, "down": 0,
             "maint": 0, "checks": [], "failingServers": []},
        )
        bucket["serversTotal"] += 1
        status = str(u.get("status") or "unknown").lower()
        if status == "up":
            bucket["up"] += 1
        elif status in ("maint", "drain"):
            bucket["maint"] += 1
        elif status == "down":
            bucket["down"] += 1
            bucket["checks"].append(str(u.get("checkInfo") or ""))
            bucket["failingServers"].append({
                "server": opt(u.get("server"), 200),
                "address": opt(u.get("address"), 200),
                "checkInfo": opt(u.get("checkInfo"), 64),
            })

    findings = []
    for bucket in per_service.values():
        checks = bucket.pop("checks")
        entry = dict(bucket)
        entry.update(_classify_service(
            bucket["serversTotal"], bucket["down"], bucket["maint"], checks
        ))
        entry["_score"] = (
            (1000 if bucket["down"] + bucket["maint"] >= bucket["serversTotal"]
             and bucket["serversTotal"] else 0)
            + bucket["down"] * 10 + bucket["maint"]
        )
        findings.append(entry)
    findings.sort(key=lambda e: e["_score"], reverse=True)
    for e in findings:
        e.pop("_score", None)

    return {
        "servicesEvaluated": len(findings),
        "outages": sum(
            1 for f in findings
            if f["serversTotal"] and f["up"] == 0
        ),
        "degraded": sum(1 for f in findings if f["up"] and f["down"]),
        "findings": findings[:MAX_ROWS],
        "note": (
            "Advisory read-only heuristic: services ranked by down-server count; "
            "cause derived from health-check failure class (L4/L6/L7/DNS) and "
            "admin state. Correlate with error_rate_rca before acting."
        ),
    }


# ── 2. cert expiry sweep ─────────────────────────────────────────────────────
DEFAULT_WARN_DAYS = 30.0
DEFAULT_CRITICAL_DAYS = 7.0

_RENEWAL_HINTS = {
    "caddy": (
        "Caddy auto-renews ACME certs — a short expiry usually means renewal is "
        "failing: check the caddy logs for ACME errors, the storage directory's "
        "permissions, and that port 80/443 (or the DNS provider creds) are "
        "reachable for the challenge."
    ),
    "traefik": (
        "Check the certificatesResolvers ACME config and acme.json (must be "
        "chmod 600); a stuck renewal usually logs an ACME challenge failure. "
        "Static certs in the file provider must be replaced and the file "
        "provider reloaded."
    ),
}
_GENERIC_HINT = "Renew/replace the certificate at its issuer and reload the proxy."


def cert_expiry_sweep(
    certs: list[dict],
    warn_days: float = DEFAULT_WARN_DAYS,
    critical_days: float = DEFAULT_CRITICAL_DAYS,
    platform: str = "",
) -> dict:
    """[READ] Bucket cert rows by days-to-expiry with renewal hints.

    Pure analysis over rows (from ``list_certificates(probe=True)`` or
    injected): ``{domain, daysToExpiry, notAfter?, error?}``. Buckets:
    expired (<0), critical (<= critical_days), warning (<= warn_days), ok;
    probe failures land in ``unknown``. Sorted soonest-expiry first.
    """
    ranked, unknown = [], []
    for c in certs or []:
        if "daysToExpiry" not in c or c.get("daysToExpiry") is None:
            unknown.append({
                "domain": opt(c.get("domain"), 128),
                "error": opt(c.get("error") or "no expiry data (probe skipped/failed)", 160),
            })
            continue
        days = num(c.get("daysToExpiry"))
        if days < 0:
            bucket = "expired"
        elif days <= critical_days:
            bucket = "critical"
        elif days <= warn_days:
            bucket = "warning"
        else:
            bucket = "ok"
        ranked.append({
            "domain": opt(c.get("domain"), 128),
            "daysToExpiry": days,
            "notAfter": opt(c.get("notAfter"), 64),
            "bucket": bucket,
        })
    ranked.sort(key=lambda c: c["daysToExpiry"])
    counts = {b: sum(1 for c in ranked if c["bucket"] == b)
              for b in ("expired", "critical", "warning", "ok")}
    return {
        "certsEvaluated": len(ranked) + len(unknown),
        **counts,
        "unknown": len(unknown),
        "certificates": ranked[:MAX_ROWS],
        "unknownDomains": unknown[:MAX_ROWS],
        "thresholds": {"warnDays": warn_days, "criticalDays": critical_days},
        "renewalHint": _RENEWAL_HINTS.get(platform, _GENERIC_HINT),
        "note": (
            "Advisory read-only heuristic: expired < 0 days, critical <= "
            f"{critical_days:g}, warning <= {warn_days:g}. Expiry read from a "
            "live handshake of the served leaf cert."
        ),
    }


# ── 3. 5xx / error-rate RCA ──────────────────────────────────────────────────
DEFAULT_ERROR_RATE_PCT = 5.0
DEFAULT_MIN_REQUESTS = 30.0
_CRITICAL_RATE_PCT = 25.0

_CODE_CAUSES = {
    "503": ("503 Service Unavailable dominates — no healthy upstream (all "
            "servers down, or maint/drain took the last one out)",
            "Run backend_health_rca; return drained servers or fix the failing "
            "health checks."),
    "502": ("502 Bad Gateway dominates — the proxy cannot complete connections "
            "to the upstream (refused/reset mid-request)",
            "Check the upstream process and its port; look for crash-restart "
            "loops or connection limits."),
    "504": ("504 Gateway Timeout dominates — the upstream accepts but answers "
            "too slowly",
            "Check upstream latency/saturation and the proxy's timeout budget; "
            "consider more replicas."),
    "500": ("500 Internal Server Error dominates — the app itself is erroring",
            "This is an application bug/overload, not the proxy: check the "
            "app's logs around the spike."),
}
_GENERIC_5XX = (
    "Elevated 5xx share (no per-code breakdown on this platform)",
    "Correlate with backend_health_rca and the upstream app's logs.",
)


def _five_xx(codes: dict, classes: dict) -> tuple[float, str | None]:
    """Total 5xx count + the dominant concrete code (if per-code data exists)."""
    five = {c: num(v) for c, v in (codes or {}).items() if str(c).startswith("5")}
    if five:
        dominant = max(five, key=lambda c: five[c])
        return sum(five.values()), dominant
    return num((classes or {}).get("5xx")), None


def error_rate_rca(
    counters: list[dict],
    error_rate_pct: float = DEFAULT_ERROR_RATE_PCT,
    min_requests: float = DEFAULT_MIN_REQUESTS,
) -> dict:
    """[READ] Rank services by 5xx share vs the fleet baseline, cause + action.

    Pure analysis over counter rows (from ``error_counters`` or injected):
    ``{service, total, codes: {"502": n, ...}}`` and/or ``classes:
    {"5xx": n}``. A service is flagged when its 5xx rate >= error_rate_pct AND
    it saw >= min_requests requests; the dominant concrete code maps to a
    cause + action. The fleet baseline (weighted 5xx share across all services)
    is reported so a flagged service can be read *relative* to its peers.
    """
    rows = []
    fleet_total = fleet_5xx = 0.0
    for c in counters or []:
        total = num(c.get("total"))
        five, dominant = _five_xx(c.get("codes") or {}, c.get("classes") or {})
        fleet_total += total
        fleet_5xx += five
        rate = (five / total * 100) if total else 0.0
        rows.append({
            "service": opt(c.get("service"), 200),
            "requestsTotal": total,
            "errors5xx": five,
            "errorRatePct": round(rate, 2),
            "dominantCode": dominant,
        })

    baseline = round(fleet_5xx / fleet_total * 100, 2) if fleet_total else 0.0
    flagged = []
    for row in rows:
        if row["requestsTotal"] < min_requests or row["errorRatePct"] < error_rate_pct:
            continue
        cause, action = _CODE_CAUSES.get(row["dominantCode"] or "", _GENERIC_5XX)
        severity = "critical" if row["errorRatePct"] >= _CRITICAL_RATE_PCT else "warning"
        flagged.append({
            **row,
            "vsBaselineX": round(row["errorRatePct"] / baseline, 1) if baseline else None,
            "severity": severity,
            "cause": cause,
            "action": action,
        })
    flagged.sort(key=lambda r: r["errorRatePct"], reverse=True)

    return {
        "servicesEvaluated": len(rows),
        "flaggedCount": len(flagged),
        "fleetErrorRatePct": baseline,
        "thresholds": {"errorRatePct": error_rate_pct, "minRequests": min_requests},
        "flagged": flagged[:MAX_ROWS],
        "note": (
            "Advisory read-only heuristic over cumulative counters: rate = "
            "5xx/total since proxy start; 'vsBaselineX' compares to the "
            "fleet-weighted 5xx share. For a spike-vs-now view, diff two runs."
        ),
    }


# ── 4. route conflict & shadow analysis ──────────────────────────────────────
_MAX_REDIRECT_HOPS = 10


def _host_covers(earlier: list, later: list) -> bool:
    """Earlier route's hosts cover the later's if it matches any host (empty)
    or every later host is in the earlier set."""
    if not earlier:
        return True
    return bool(later) and all(h in earlier for h in later)


def _path_covers(earlier: list, later: list) -> bool:
    if not earlier:
        return True
    if not later:
        return False
    cleaned = [str(p).rstrip("*") for p in earlier]
    return all(any(str(lp).startswith(ep) for ep in cleaned) for lp in later)


def _covers(earlier: dict, later: dict) -> bool:
    return (
        _host_covers(earlier.get("hosts") or [], later.get("hosts") or [])
        and _path_covers(earlier.get("paths") or [], later.get("paths") or [])
    )


def _match_redirect(target_host: str, target_path: str, routes: list[dict]) -> dict | None:
    for r in routes:
        hosts = r.get("hosts") or []
        if hosts and target_host not in hosts:
            continue
        paths = [str(p).rstrip("*") for p in (r.get("paths") or [])]
        if paths and not any(target_path.startswith(p) for p in paths):
            continue
        return r
    return None


def _split_url(url: str) -> tuple[str, str]:
    from urllib.parse import urlparse

    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    return host, parsed.path or "/"


def route_conflict_analysis(
    routes: list[dict],
    services: list[dict] | None = None,
) -> dict:
    """[READ] Shadowed routes, dead routes, and redirect loops (static).

    Pure analysis over normalised route rows (from ``list_routes`` or
    injected): ``{name, hosts, paths, priority, service, enabled, redirectTo}``
    plus optional service rows ``{name, serversTotal, serversUp}``. Routes are
    compared in evaluation order (priority desc, then list order — how the
    proxy matches). Findings:

      * **shadowedRoutes** — a route whose hosts+paths are fully covered by an
        earlier/higher-priority enabled route (it can never match).
      * **deadRoutes** — a route pointing at a service that does not exist or
        has zero servers up.
      * **redirectLoops** — a redirect whose target resolves back into a
        redirecting route (cycle within the fetched table).

    Every finding names the covering/missing piece so the operator can act.
    """
    ordered = sorted(
        [r for r in (routes or []) if r.get("enabled", True)],
        key=lambda r: num(r.get("priority")),
        reverse=True,
    )

    shadowed = []
    for idx, route in enumerate(ordered):
        for earlier in ordered[:idx]:
            if earlier.get("name") == route.get("name"):
                continue
            if not (earlier.get("hosts") or earlier.get("paths")):
                continue  # a match-all catch-all is usually intentional (default route)
            if _covers(earlier, route):
                shadowed.append({
                    "route": opt(route.get("name"), 200),
                    "shadowedBy": opt(earlier.get("name"), 200),
                    "hosts": route.get("hosts") or [],
                    "paths": route.get("paths") or [],
                })
                break

    dead = []
    if services is not None:
        known = {str(svc.get("name")): svc for svc in services}
        for route in ordered:
            service = str(route.get("service") or "")
            if not service or service.startswith("("):
                continue
            svc = known.get(service)
            if svc is None:
                dead.append({
                    "route": opt(route.get("name"), 200),
                    "service": s(service, 200),
                    "reason": "service not found",
                })
            elif num(svc.get("serversTotal")) and num(svc.get("serversUp")) == 0:
                dead.append({
                    "route": opt(route.get("name"), 200),
                    "service": s(service, 200),
                    "reason": "service has zero servers up",
                })

    loops = []
    redirectors = [r for r in ordered if r.get("redirectTo")]
    for route in redirectors:
        hops = [opt(route.get("name"), 200)]
        current = route
        for _ in range(_MAX_REDIRECT_HOPS):
            host, path = _split_url(current.get("redirectTo"))
            nxt = _match_redirect(host, path, ordered)
            if nxt is None or not nxt.get("redirectTo"):
                break
            name = opt(nxt.get("name"), 200)
            if name in hops:
                loops.append({"chain": hops + [name], "startsAt": hops[0]})
                break
            hops.append(name)
            current = nxt

    return {
        "routesEvaluated": len(ordered),
        "shadowedCount": len(shadowed),
        "deadCount": len(dead),
        "redirectLoopCount": len(loops),
        "shadowedRoutes": shadowed[:MAX_ROWS],
        "deadRoutes": dead[:MAX_ROWS],
        "redirectLoops": loops[:MAX_ROWS],
        "note": (
            "Advisory static analysis of the fetched route table: 'shadowed' = "
            "hosts+paths fully covered by an earlier/higher-priority route; "
            "'dead' = service missing or zero servers up; loops followed up to "
            f"{_MAX_REDIRECT_HOPS} hops. Header/method matchers are not evaluated."
        ),
    }
