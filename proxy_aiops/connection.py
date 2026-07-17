"""Connection management for proxies (Traefik + Caddy + HAProxy).

Thin httpx wrapper with per-target session reuse. Authentication is selected by
the target's :class:`~proxy_aiops.platform.Platform` descriptor:

  * **haproxy** — Data Plane API credentials (``username`` + secret) presented
    as HTTP Basic auth. Required.
  * **traefik / caddy** — usually unauthenticated on localhost; when a secret
    is stored for the target it is presented (with ``username``) as HTTP Basic
    auth for endpoints fronted by an auth layer.

Ops modules never hard-code a path or a payload key: they ask
``conn.platform.path("routers")`` for the concrete URL and
``conn.platform.rows()`` to unwrap a list payload, so the same op works across
platforms — and an op a platform cannot do raises a teaching error from the
support matrix instead of a confusing 404.

All non-2xx responses are translated centrally into ``ProxyApiError`` with a
teaching message — HTTP errors are translated at the connection layer rather
than leaking raw tracebacks. The httpx client is injectable for tests: pass
``client=`` a mock implementing ``request`` / ``close``.
"""

from __future__ import annotations

from typing import Any

import httpx

from proxy_aiops.config import AppConfig, TargetConfig, load_config

_TIMEOUT = 30.0


class ProxyApiError(Exception):
    """A proxy API call failed; carries a teaching message + status."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str = "") -> None:
        self.status_code = status_code
        self.path = path
        super().__init__(message)


def _teaching_message(status: int, path: str, body: str, label: str) -> str:
    """Map a non-2xx status to an actionable, teaching error message."""
    snippet = body[:200].strip()
    if status in (401, 403):
        return (
            f"Authentication/authorization failed ({status}) on {label} {path}. "
            f"Check the credentials (haproxy: the Data Plane API userlist "
            f"user/password; traefik/caddy: the Basic auth in front of the "
            f"endpoint, or remove the stored secret if none is needed). {snippet}"
        )
    if status == 404:
        return (
            f"Resource not found (404) on {label} {path}. The name/path may be "
            f"stale — list the parent collection first to get a current one, and "
            f"check the API is enabled (traefik: api.insecure or a router on "
            f"api@internal; caddy: the admin endpoint; haproxy: the dataplaneapi "
            f"program). {snippet}"
        )
    if status == 400:
        return (
            f"Bad request (400) on {label} {path}. The endpoint rejected the "
            f"request — check required fields and value formats. {snippet}"
        )
    if status in (500, 502, 503, 504):
        return (
            f"{label} server error ({status}) on {path}. The endpoint may be "
            f"busy or mid-reload; retry shortly. {snippet}"
        )
    return f"{label} API error ({status}) on {path}. {snippet}"


class ProxyConnection:
    """A single authenticated session against one Traefik/Caddy/HAProxy target."""

    def __init__(self, target: TargetConfig, client: Any | None = None) -> None:
        self._target = target
        self._client = client or httpx.Client(
            base_url=target.base_url,
            verify=target.verify_ssl,
            timeout=_TIMEOUT,
            auth=self._build_auth(target),
            headers={"Accept": "application/json"},
        )

    @staticmethod
    def _build_auth(target: TargetConfig) -> Any | None:
        """HAProxy always sends Basic auth; Traefik/Caddy only when a secret
        is stored (both commonly run unauthenticated on localhost)."""
        platform = target.platform_obj
        if platform.requires_secret:
            return httpx.BasicAuth(target.username, target.secret)
        if target.has_secret:
            return httpx.BasicAuth(target.username, target.secret)
        return None

    @property
    def target(self) -> TargetConfig:
        return self._target

    @property
    def platform(self) -> Any:
        return self._target.platform_obj

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        label = self._target.platform_obj.label
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ProxyApiError(
                f"Could not reach {label} at {self._target.base_url} "
                f"({method} {path}): {exc}. Check base_url and that the API/"
                f"admin endpoint is enabled and reachable.",
                path=path,
            ) from exc
        if not (200 <= resp.status_code < 300):
            raise ProxyApiError(
                _teaching_message(resp.status_code, path, resp.text, label),
                status_code=resp.status_code,
                path=path,
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            # the /metrics text endpoint (and other text endpoints) are not JSON.
            return resp.text

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self._request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self._request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._request("DELETE", path, **kwargs)

    def close(self) -> None:
        self._client.close()


class ConnectionManager:
    """Manages connections to multiple proxy targets with session reuse."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, ProxyConnection] = {}

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> ProxyConnection:
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        cached = self._connections.get(target.name)
        if cached is not None:
            return cached
        conn = ProxyConnection(target)
        self._connections[target.name] = conn
        return conn

    def disconnect(self, target_name: str) -> None:
        conn = self._connections.pop(target_name, None)
        if conn is not None:
            conn.close()

    def disconnect_all(self) -> None:
        for name in list(self._connections):
            self.disconnect(name)

    def list_targets(self) -> list[str]:
        return [t.name for t in self._config.targets]

    def list_connected(self) -> list[str]:
        return list(self._connections.keys())
