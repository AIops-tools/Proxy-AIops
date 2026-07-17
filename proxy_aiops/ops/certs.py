"""TLS certificate inventory (read-only).

Builds the domain inventory that feeds ``cert_expiry_sweep``:

  * **traefik** — TLS-enabled routers: hosts parsed from the rule string plus
    explicit ``tls.domains`` (main + sans).
  * **caddy** — hosts on TLS-ish listeners plus ``apps.tls`` automation-policy
    subjects and loaded certificate tags.
  * **haproxy** — unsupported with a teaching error (certs are .pem files bound
    in haproxy.cfg; use your cert pipeline / the Data Plane storage endpoints).

Expiry itself comes from a bounded live TLS handshake per domain
(:func:`probe_certificate`) — injectable/skippable, so the sweep also works as
pure analysis over supplied rows.
"""

from __future__ import annotations

import socket
import ssl
from datetime import UTC, datetime
from typing import Any

from proxy_aiops.ops import routes as route_ops
from proxy_aiops.ops._util import as_obj, s
from proxy_aiops.platform import CADDY, TRAEFIK, UnsupportedOperation

_PROBE_TIMEOUT_SEC = 5.0
MAX_PROBES = 25
_WILDCARD_PREFIX = "*."


def pull_tls_inventory(conn: Any) -> list[dict]:
    """[READ] Domains this proxy terminates TLS for: {domain, source}."""
    platform = conn.target.platform
    if platform == TRAEFIK:
        rows = conn.platform.rows(conn.get(conn.platform.path("routers")))
        seen: dict[str, str] = {}
        from proxy_aiops.ops._util import parse_rule_hosts

        for r in rows:
            tls = r.get("tls")
            if tls is None:  # an EMPTY {} still means "TLS on" in traefik
                continue
            source = f"router {s(r.get('name'), 128)}"
            for host in parse_rule_hosts(str(r.get("rule", ""))):
                if host in ("*", ""):  # HostSNI(`*`) is not a certificate domain
                    continue
                seen.setdefault(s(host, 128), source)
            for dom in (as_obj(tls).get("domains") or []):
                dom = as_obj(dom)
                main = str(dom.get("main", "")).lower()
                if main:
                    seen.setdefault(s(main, 128), source)
                for san in dom.get("sans") or []:
                    seen.setdefault(s(str(san).lower(), 128), source)
        return [{"domain": d, "source": src} for d, src in seen.items()]

    if platform == CADDY:
        cfg = conn.platform.normalise(conn.get(conn.platform.path("config_root")))
        seen: dict[str, str] = {}
        for srv_name, srv in route_ops.caddy_http_servers(cfg).items():
            srv = as_obj(srv)
            listen = [str(a) for a in srv.get("listen") or []]
            tls_ish = any(a.endswith(":443") or a.endswith(":8443") for a in listen)
            if not tls_ish:
                continue
            for route in srv.get("routes") or []:
                for m in as_obj(route).get("match") or []:
                    for host in as_obj(m).get("host") or []:
                        seen.setdefault(s(str(host).lower(), 128), f"server {s(srv_name, 64)}")
        tls_app = as_obj(as_obj(as_obj(cfg).get("apps")).get("tls"))
        for policy in as_obj(tls_app.get("automation")).get("policies") or []:
            for subject in as_obj(policy).get("subjects") or []:
                seen.setdefault(s(str(subject).lower(), 128), "tls automation policy")
        return [{"domain": d, "source": src} for d, src in seen.items()]

    # haproxy — explicit teaching error, never a silent empty list.
    raise UnsupportedOperation(
        "TLS certificate inventory is not supported on platform 'haproxy': "
        "certificates are .pem files bound in haproxy.cfg (crt/crt-list) — "
        "inspect them with your cert pipeline or the Data Plane storage "
        "endpoints; the expiry sweep covers traefik and caddy targets."
    )


def probe_certificate(domain: str, port: int = 443,
                      timeout: float = _PROBE_TIMEOUT_SEC) -> dict:
    """Live TLS handshake to read a domain's leaf-cert expiry (bounded).

    Wildcard subjects are probed at their base domain. Failures are reported in
    the row (``error``), never raised — a sweep must survive one dead vhost.
    """
    host = domain[len(_WILDCARD_PREFIX):] if domain.startswith(_WILDCARD_PREFIX) else domain
    try:
        context = ssl.create_default_context()
        # The sweep reads expiry off whatever cert is served — including an
        # expired or mismatched one — so verification must not end the probe.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # nosec B502 — inspection, not trust
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        not_after = _der_not_after(der)
        days = (not_after - datetime.now(UTC)).total_seconds() / 86400
        return {
            "domain": s(domain, 128),
            "notAfter": not_after.isoformat(),
            "daysToExpiry": round(days, 1),
        }
    except Exception as exc:  # noqa: BLE001 — a dead vhost must not kill the sweep
        return {"domain": s(domain, 128), "error": s(exc, 160)}


def _der_not_after(der: bytes) -> datetime:
    """Extract notAfter from a DER cert via the cryptography package."""
    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    try:
        return cert.not_valid_after_utc
    except AttributeError:  # older cryptography
        return cert.not_valid_after.replace(tzinfo=UTC)


def list_certificates(conn: Any, probe: bool = False, port: int = 443) -> dict:
    """[READ] TLS domain inventory; optionally handshake-probe each for expiry."""
    try:
        inventory = pull_tls_inventory(conn)
    except UnsupportedOperation as exc:
        return {"platform": conn.target.platform, "unsupported": s(exc, 400)}
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 300)}
    rows = inventory[:MAX_PROBES] if probe else inventory
    if probe:
        rows = [{**row, **probe_certificate(row["domain"], port=port)} for row in rows]
    return {
        "platform": conn.target.platform,
        "total": len(inventory),
        "probed": len(rows) if probe else 0,
        "certificates": rows,
        "note": (
            f"probe=True performs a live TLS handshake per domain "
            f"(max {MAX_PROBES}, {int(_PROBE_TIMEOUT_SEC)}s timeout each)."
        ),
    }
