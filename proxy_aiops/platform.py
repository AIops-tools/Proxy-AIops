"""Platform descriptors — the reverse proxies / load balancers proxy-aiops speaks to.

proxy-aiops is multi-platform by construction. A registry maps a *platform
name* to a :class:`Platform` descriptor that captures everything the connection
and ops layers need to talk to that proxy: how it authenticates, the concrete
REST path for each *logical resource*, an explicit **support matrix** (an op a
platform cannot do raises a teaching error, never a silent no-op), and how a
raw response is normalised (injection-safe).

v0.1 registers three platforms:

  * **traefik** — Traefik's API (``/api/http/routers|services|middlewares``,
    ``/api/overview``, ``/api/entrypoints``; metrics-text counters via
    ``/metrics`` when enabled). Mostly READ: Traefik's dynamic configuration
    comes from its *providers* (file, container labels, orchestrator CRDs), so
    write resources are unmapped with a teaching error pointing at the provider.
  * **caddy** — Caddy's admin API (default ``localhost:2019``): full config
    read via ``GET /config/``, targeted config writes via ``PATCH/POST/DELETE
    /config/<path>`` and full replace via ``POST /load``. This platform carries
    the write surface (prior-config capture → replayable undo).
  * **haproxy** — HAProxy Data Plane API v2 (``/v2/services/haproxy/...``,
    HTTP Basic auth): frontends/backends/servers/stats reads plus runtime
    server writes (admin state ready/drain/maint, weight).

Additional proxies can ``register`` their own descriptor later without touching
the ops / CLI / MCP layers — a registry keyed by ``platform`` name.

The concrete REST paths below are modelled from each project's public API and
are exercised against mocked HTTP responses only; see the README's preview note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from proxy_aiops.governance import sanitize


def _seg(value: Any) -> str:
    """URL-encode one path/query value so agent-supplied identifiers (router
    names, backend/server names, config paths) cannot smuggle ``/``, ``../`` or
    query metacharacters into the request URL."""
    return quote(str(value), safe="")


def encode_config_path(path: str) -> str:
    """Encode a slash-separated Caddy config path, one segment at a time.

    Each segment is URL-encoded (``quote(..., safe="")``) and dot-segments
    (``.`` / ``..``) are rejected outright, so an agent-supplied path can never
    traverse out of the ``/config/`` tree (e.g. to ``/load`` or ``/stop``).
    """
    segments = [seg for seg in str(path).split("/") if seg != ""]
    for seg in segments:
        if seg in (".", ".."):
            raise ValueError(
                f"Config path segment '{seg}' is not allowed — dot-segments "
                f"could traverse out of the /config/ tree. Use an absolute "
                f"config path like 'apps/http/servers/srv0/routes/0'."
            )
    return "/".join(_seg(seg) for seg in segments)


# ─── registered platform names ──────────────────────────────────────────────
TRAEFIK = "traefik"
CADDY = "caddy"
HAPROXY = "haproxy"
PLATFORMS = (TRAEFIK, CADDY, HAPROXY)

# Auth styles.
AUTH_BASIC = "basic"  # HTTP Basic, secret REQUIRED (HAProxy Data Plane API)
AUTH_OPTIONAL_BASIC = "optional-basic"  # HTTP Basic only when a secret is stored

# Bounds for the response normaliser (defensive against a hostile endpoint).
_MAX_STR = 512
_MAX_DEPTH = 20

# Keys under which a platform wraps a list payload, tried in order before
# falling back to a bare JSON array (Data Plane API v2 wraps in ``data``).
_LIST_KEYS = ("data", "rows", "results", "items")


def _sanitize_obj(obj: Any, depth: int = 0) -> Any:
    """Recursively fold proxy-returned JSON into injection-safe values.

    Every string leaf passes through ``sanitize`` (bounded length); numbers,
    booleans and ``None`` pass through unchanged. Depth is capped so a
    pathological nesting cannot exhaust the stack.
    """
    if depth > _MAX_DEPTH:
        return None
    if isinstance(obj, dict):
        return {str(k): _sanitize_obj(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_obj(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return sanitize(obj, _MAX_STR)
    return obj


class UnsupportedOperation(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """A resource/op this platform genuinely cannot do — carries a teaching
    message telling the operator what to use instead (never a silent no-op)."""


@dataclass(frozen=True)
class Platform:
    """A proxy's API shape: auth style + logical-resource path map + normaliser.

    ``unsupported`` is the explicit support matrix: a logical resource this
    platform cannot serve maps to a *teaching message* explaining why and what
    to do instead. ``path()`` raises :class:`UnsupportedOperation` for those.
    """

    name: str
    label: str
    auth_style: str
    default_base_url: str
    paths: dict[str, str] = field(default_factory=dict)
    unsupported: dict[str, str] = field(default_factory=dict)

    @property
    def requires_secret(self) -> bool:
        return self.auth_style == AUTH_BASIC

    def path(self, resource: str, **fmt: Any) -> str:
        """Return the concrete REST path for a logical ``resource``.

        Raises :class:`UnsupportedOperation` with the platform's teaching
        message when the resource is in the support matrix as unsupported, and
        a teaching ``KeyError`` when it is simply unmapped — so a caller asking
        for an impossible surface fails fast with guidance, never a silent 404.

        Every substituted value is URL-encoded (``quote(..., safe="")``) so an
        agent-supplied identifier can never rewrite the path (e.g. via ``../``).
        """
        if resource in self.unsupported:
            raise UnsupportedOperation(
                f"'{resource}' is not supported on platform '{self.name}': "
                f"{self.unsupported[resource]}"
            )
        try:
            template = self.paths[resource]
        except KeyError as exc:
            available = ", ".join(sorted(self.paths)) or "(none)"
            raise KeyError(
                f"Resource '{resource}' is not mapped for platform '{self.name}'. "
                f"Mapped resources: {available}."
            ) from exc
        if not fmt:
            return template
        return template.format(**{k: _seg(v) for k, v in fmt.items()})

    def supports(self, resource: str) -> bool:
        return resource in self.paths

    def teach_unsupported(self, resource: str) -> str:
        """The teaching message for an unsupported resource (or a generic one)."""
        return self.unsupported.get(
            resource, f"'{resource}' is not available on platform '{self.name}'."
        )

    def rows(self, payload: Any) -> list[dict]:
        """Normalise a list payload to a sanitised list of dict rows.

        A bare JSON array passes through; a dict is unwrapped via the first of
        ``data``/``rows``/``results``/``items`` that is present. Every row is
        run through the injection-safe normaliser.
        """
        if isinstance(payload, dict):
            items: Any = []
            for key in _LIST_KEYS:
                value = payload.get(key)
                if isinstance(value, list):
                    items = value
                    break
        else:
            items = payload
        return [_sanitize_obj(r) for r in (items or []) if isinstance(r, dict)]

    def normalise(self, payload: Any) -> Any:
        """Return an injection-safe copy of a raw response payload."""
        return _sanitize_obj(payload)


# ─── registry ───────────────────────────────────────────────────────────────
_REGISTRY: dict[str, Platform] = {}


def register(platform: Platform) -> None:
    """Register a platform descriptor under its name (idempotent overwrite)."""
    _REGISTRY[platform.name] = platform


def get_platform(name: str) -> Platform:
    """Return the descriptor for ``name`` or raise with the registered names."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unknown platform '{name}'. Registered platforms: {available}."
        ) from exc


def platform_names() -> tuple[str, ...]:
    """All registered platform names (sorted)."""
    return tuple(sorted(_REGISTRY))


# Shared teaching messages for the write surface.
_TRAEFIK_WRITE_TEACH = (
    "Traefik's dynamic configuration is owned by its providers (dynamic-config "
    "file, container labels, orchestrator CRDs) — the API is read-only by "
    "design. Edit the provider source and Traefik hot-reloads it; there is "
    "nothing to write through this API."
)
_HAPROXY_CONFIG_TEACH = (
    "HAProxy configuration edits go through Data Plane API transactions, which "
    "this preview does not drive. Use the runtime writes instead "
    "(set_server_state, set_server_weight) or edit haproxy.cfg through your "
    "config pipeline."
)

# ─── Traefik (API under /api/..., mostly read-only) ─────────────────────────
_TRAEFIK_PATHS = {
    "probe": "/api/version",
    "version": "/api/version",
    "overview": "/api/overview",
    "entrypoints": "/api/entrypoints",
    "routers": "/api/http/routers",
    "router_detail": "/api/http/routers/{name}",
    "services": "/api/http/services",
    "service_detail": "/api/http/services/{name}",
    "middlewares": "/api/http/middlewares",
    "metrics": "/metrics",
    "config_snapshot": "/api/rawdata",
}
_TRAEFIK_UNSUPPORTED = {
    "config_get": _TRAEFIK_WRITE_TEACH + " Read the merged state via config_snapshot.",
    "config_set": _TRAEFIK_WRITE_TEACH,
    "config_delete": _TRAEFIK_WRITE_TEACH,
    "config_load": _TRAEFIK_WRITE_TEACH,
    "runtime_server": (
        "Traefik has no runtime server-state API — drain/weight changes belong "
        "to the provider (e.g. the file provider's loadBalancer weights)."
    ),
    "stats": (
        "Traefik exposes counters via the /metrics text endpoint, not a stats API — "
        "use 'metrics' (traffic_stats / error_counters read it)."
    ),
}

# ─── Caddy (admin API, default localhost:2019 — carries the write surface) ──
_CADDY_PATHS = {
    "probe": "/config/",
    "config_root": "/config/",
    "config_get": "/config/",
    "config_set": "/config/",
    "config_delete": "/config/",
    "config_load": "/load",
    "upstreams": "/reverse_proxy/upstreams",
    "config_snapshot": "/config/",
}
_CADDY_UNSUPPORTED = {
    "version": (
        "Caddy's admin API has no version endpoint — run 'caddy version' on the "
        "host, or read the config snapshot for the loaded app shape."
    ),
    "metrics": (
        "Caddy's /metrics families are process-level (no per-route status-code "
        "counters) — this preview does not scrape them."
    ),
    "stats": (
        "Caddy has no per-route status-code counters over the admin API. For "
        "error-rate analysis use the access logs, or run error_rate_rca against "
        "a platform that exposes counters."
    ),
    "middlewares": (
        "Caddy models middleware as inline handlers inside each route — read "
        "them via list_routes / config_snapshot rather than a middleware list."
    ),
    "runtime_server": (
        "Caddy has no runtime server-state API — change the upstream in the "
        "config tree instead (set_config_value on the route's upstreams)."
    ),
}

# ─── HAProxy (Data Plane API v2, HTTP Basic auth) ────────────────────────────
_HAPROXY_PATHS = {
    "probe": "/v2/info",
    "version": "/v2/info",
    "frontends": "/v2/services/haproxy/configuration/frontends",
    "frontend_detail": "/v2/services/haproxy/configuration/frontends/{name}",
    "binds": "/v2/services/haproxy/configuration/binds?frontend={frontend}",
    "backends": "/v2/services/haproxy/configuration/backends",
    "backend_detail": "/v2/services/haproxy/configuration/backends/{name}",
    "servers": "/v2/services/haproxy/configuration/servers?backend={backend}",
    "runtime_servers": "/v2/services/haproxy/runtime/servers?backend={backend}",
    "runtime_server": "/v2/services/haproxy/runtime/servers/{name}?backend={backend}",
    "stats": "/v2/services/haproxy/stats/native",
}
_HAPROXY_UNSUPPORTED = {
    "metrics": (
        "This preview reads HAProxy counters from the Data Plane API stats "
        "endpoint — use 'stats' (traffic_stats / error_counters read it), not a "
        "/metrics scrape."
    ),
    "middlewares": (
        "HAProxy has no middleware concept — its equivalent lives in "
        "frontend/backend rules inside haproxy.cfg; read frontends/backends instead."
    ),
    "config_get": _HAPROXY_CONFIG_TEACH,
    "config_set": _HAPROXY_CONFIG_TEACH,
    "config_delete": _HAPROXY_CONFIG_TEACH,
    "config_load": _HAPROXY_CONFIG_TEACH,
    "config_snapshot": (
        "This preview does not fetch raw haproxy.cfg; read the structured "
        "frontends/backends/servers resources instead."
    ),
}


register(
    Platform(
        name=TRAEFIK,
        label="Traefik API",
        auth_style=AUTH_OPTIONAL_BASIC,
        default_base_url="http://localhost:8080",
        paths=_TRAEFIK_PATHS,
        unsupported=_TRAEFIK_UNSUPPORTED,
    )
)
register(
    Platform(
        name=CADDY,
        label="Caddy admin API",
        auth_style=AUTH_OPTIONAL_BASIC,
        default_base_url="http://localhost:2019",
        paths=_CADDY_PATHS,
        unsupported=_CADDY_UNSUPPORTED,
    )
)
register(
    Platform(
        name=HAPROXY,
        label="HAProxy Data Plane API v2",
        auth_style=AUTH_BASIC,
        default_base_url="http://localhost:5555",
        paths=_HAPROXY_PATHS,
        unsupported=_HAPROXY_UNSUPPORTED,
    )
)


__all__ = [
    "TRAEFIK",
    "CADDY",
    "HAPROXY",
    "PLATFORMS",
    "AUTH_BASIC",
    "AUTH_OPTIONAL_BASIC",
    "Platform",
    "UnsupportedOperation",
    "encode_config_path",
    "register",
    "get_platform",
    "platform_names",
]
