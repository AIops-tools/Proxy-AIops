"""Traffic / error-counter reads.

Two very different counter sources normalised to one row shape:

  * **traefik** — the ``/metrics`` text exposition
    (``traefik_service_requests_total{code="502",service="app@file"} 17``),
    parsed with a small line parser (no client_golang needed).
  * **haproxy** — Data Plane API native stats (``req_tot``, ``hrsp_5xx``, …).
  * **caddy** — unsupported with a teaching error (no per-route status-code
    counters over the admin API).

Normalised counter row: ``{service, total, codes: {"500": n, ...} | classes:
{"2xx": n, ...}}`` — exactly what ``error_rate_rca`` consumes.
"""

from __future__ import annotations

import re
from typing import Any

from proxy_aiops.ops._util import s
from proxy_aiops.platform import HAPROXY, TRAEFIK

MAX_SERVICES = 200

# service label candidates in traefik metric lines
_METRIC_LINE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)\{([^}]*)\}\s+([0-9eE.+-]+)")
_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')

_REQUESTS_METRIC = "traefik_service_requests_total"
_DURATION_SUM = "traefik_service_request_duration_seconds_sum"
_DURATION_COUNT = "traefik_service_request_duration_seconds_count"

_MAX_METRICS_BYTES = 2_000_000  # defensive bound on the scraped text


def parse_metrics_text(text: str) -> list[dict]:
    """Parse metrics exposition text into ``{metric, labels, value}`` rows.

    Only labelled samples are kept (that is all the traffic analysis needs);
    input is size-bounded so a hostile endpoint cannot balloon memory.
    """
    rows: list[dict] = []
    for line in str(text or "")[:_MAX_METRICS_BYTES].splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE_RE.match(line)
        if not m:
            continue
        metric, labelstr, value = m.groups()
        try:
            val = float(value)
        except ValueError:
            continue
        rows.append({
            "metric": metric,
            "labels": dict(_LABEL_RE.findall(labelstr)),
            "value": val,
        })
    return rows


def _traefik_counters(conn: Any) -> list[dict]:
    """Aggregate traefik_service_requests_total by (service, code)."""
    text = conn.get(conn.platform.path("metrics"))
    samples = parse_metrics_text(text if isinstance(text, str) else "")
    by_service: dict[str, dict] = {}
    for sample in samples:
        if sample["metric"] != _REQUESTS_METRIC:
            continue
        labels = sample["labels"]
        service = s(labels.get("service", "(unknown)"), 200)
        code = s(labels.get("code", "?"), 8)
        bucket = by_service.setdefault(service, {"service": service, "total": 0.0, "codes": {}})
        bucket["total"] += sample["value"]
        bucket["codes"][code] = bucket["codes"].get(code, 0.0) + sample["value"]
    return list(by_service.values())


def _haproxy_counters(conn: Any) -> list[dict]:
    from proxy_aiops.ops.services import _haproxy_stat_rows

    rows = [r for r in _haproxy_stat_rows(conn) if r.get("type") == "backend"]
    return [
        {
            "service": r["name"],
            "total": r["requestsTotal"],
            "codes": {},
            "classes": {
                "2xx": r["hrsp2xx"],
                "4xx": r["hrsp4xx"],
                "5xx": r["hrsp5xx"],
            },
        }
        for r in rows
    ]


def error_counters(conn: Any) -> dict:
    """[READ] Per-service request/status-code counters (feeds error_rate_rca).

    Unsupported platforms raise the support-matrix teaching error (Caddy has no
    per-route status-code counters) instead of returning silent empties.
    """
    try:
        if conn.target.platform == TRAEFIK:
            counters = _traefik_counters(conn)
        elif conn.target.platform == HAPROXY:
            counters = _haproxy_counters(conn)
        else:
            # Raises UnsupportedOperation with the teaching message.
            conn.platform.path("stats")
            counters = []
        return {
            "platform": conn.target.platform,
            "services": counters[:MAX_SERVICES],
            "total": len(counters),
            "note": "Counters are cumulative since proxy start — compare rates, not raw totals.",
        }
    except Exception as exc:  # noqa: BLE001 — report as partial (incl. teaching)
        return {"error": s(exc, 400)}


def traffic_stats(conn: Any) -> dict:
    """[READ] Per-service traffic snapshot: requests, error share, latency/load
    where the platform exposes it."""
    try:
        if conn.target.platform == TRAEFIK:
            text = conn.get(conn.platform.path("metrics"))
            samples = parse_metrics_text(text if isinstance(text, str) else "")
            per: dict[str, dict] = {}
            for sample in samples:
                service = s(sample["labels"].get("service", ""), 200)
                if not service:
                    continue
                bucket = per.setdefault(
                    service,
                    {"service": service, "requestsTotal": 0.0,
                     "durationSumSec": 0.0, "durationCount": 0.0},
                )
                if sample["metric"] == _REQUESTS_METRIC:
                    bucket["requestsTotal"] += sample["value"]
                elif sample["metric"] == _DURATION_SUM:
                    bucket["durationSumSec"] += sample["value"]
                elif sample["metric"] == _DURATION_COUNT:
                    bucket["durationCount"] += sample["value"]
            stats = []
            for bucket in per.values():
                count = bucket.pop("durationCount")
                total_sec = bucket.pop("durationSumSec")
                bucket["avgLatencyMs"] = round(total_sec / count * 1000, 2) if count else None
                stats.append(bucket)
        elif conn.target.platform == HAPROXY:
            from proxy_aiops.ops.services import _haproxy_stat_rows

            stats = [
                {
                    "service": r["name"],
                    "requestsTotal": r["requestsTotal"],
                    "ratePerSec": r["rate"],
                    "currentSessions": r["currentSessions"],
                    "errors5xx": r["hrsp5xx"],
                }
                for r in _haproxy_stat_rows(conn)
                if r.get("type") == "backend"
            ]
        else:
            conn.platform.path("stats")  # raises the teaching error for caddy
            stats = []
        stats.sort(key=lambda r: r.get("requestsTotal", 0), reverse=True)
        return {
            "platform": conn.target.platform,
            "total": len(stats),
            "services": stats[:MAX_SERVICES],
        }
    except Exception as exc:  # noqa: BLE001 — report as partial (incl. teaching)
        return {"error": s(exc, 400)}
