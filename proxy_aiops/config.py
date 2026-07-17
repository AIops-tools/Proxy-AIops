"""Configuration management for Proxy AIops.

Loads proxy connection targets from a YAML config file. Each target names its
``platform`` — ``traefik``, ``caddy`` or ``haproxy`` — so one config can span a
mixed estate. See :mod:`proxy_aiops.platform` for how the platform name selects
the API shape (auth + resource paths + support matrix).

The secret is NEVER stored in the config file or in plaintext on disk: it lives
in the encrypted store ``~/.proxy-aiops/secrets.enc`` (see
:mod:`proxy_aiops.secretstore`).

  * **haproxy** — the Data Plane API **password** (paired with ``username``,
    presented as HTTP Basic auth). REQUIRED.
  * **traefik / caddy** — OPTIONAL: both usually run unauthenticated on
    localhost. Store a secret only when the endpoint sits behind Basic auth
    (then ``username`` + the secret are presented as HTTP Basic).

A legacy env var (``PROXY_<TARGET>_SECRET``) is honoured as a fallback.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from proxy_aiops.governance.paths import ops_home
from proxy_aiops.platform import PLATFORMS, TRAEFIK, get_platform
from proxy_aiops.secretstore import SecretStoreError, get_secret, has_store

if TYPE_CHECKING:
    from proxy_aiops.platform import Platform

CONFIG_DIR = ops_home()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

SECRET_ENV_PREFIX = "PROXY_"  # nosec B105 — env-var name, not a secret
SECRET_ENV_SUFFIX = "_SECRET"  # nosec B105 — env-var name, not a secret

_log = logging.getLogger("proxy-aiops.config")


def _secret_env_key(name: str) -> str:
    """Legacy per-target secret env var name, e.g. PROXY_EDGE1_SECRET."""
    return f"{SECRET_ENV_PREFIX}{name.upper().replace('-', '_')}{SECRET_ENV_SUFFIX}"


def _lookup_secret(name: str) -> str | None:
    """Encrypted store first, then the legacy env var; None when absent."""
    if has_store():
        try:
            return get_secret(name)
        except SecretStoreError:
            pass  # fall through to legacy env var
    legacy = os.environ.get(_secret_env_key(name))
    if legacy:
        _log.warning(
            "Using plaintext env var %s. Migrate to the encrypted store with "
            "'proxy-aiops secret migrate'.",
            _secret_env_key(name),
        )
        return legacy
    return None


def _resolve_secret(name: str, required: bool) -> str:
    """Return a target's secret; optional-auth platforms degrade to ''. """
    found = _lookup_secret(name)
    if found is not None:
        return found
    if not required:
        return ""  # traefik/caddy without auth — a valid, common setup
    raise OSError(
        f"No secret for target '{name}'. Add one with "
        f"'proxy-aiops secret set {name}' (stored encrypted), or run "
        f"'proxy-aiops init'."
    )


@dataclass(frozen=True)
class TargetConfig:
    """A connection target for one proxy endpoint.

    ``platform`` is ``traefik``, ``caddy`` or ``haproxy`` (validated at
    construction). ``base_url`` is the API endpoint (scheme required);
    ``username`` holds the HAProxy Data Plane API user (or an optional Basic
    user in front of Traefik/Caddy); the secret comes from the encrypted store
    and is optional for traefik/caddy (both commonly unauthenticated on
    localhost — same spirit as a local socket).
    """

    name: str
    platform: str = TRAEFIK
    base_url: str = ""
    username: str = ""
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise ValueError(
                f"Target '{self.name}': platform must be one of {PLATFORMS}, "
                f"got '{self.platform}'."
            )
        url = (self.base_url or self.platform_obj.default_base_url).rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ValueError(
                f"Target '{self.name}': base_url must start with http:// or "
                f"https://, got '{self.base_url}'."
            )
        object.__setattr__(self, "base_url", url)

    @property
    def platform_obj(self) -> Platform:
        return get_platform(self.platform)

    @property
    def secret(self) -> str:
        return _resolve_secret(self.name, required=self.platform_obj.requires_secret)

    @property
    def has_secret(self) -> bool:
        return _lookup_secret(self.name) is not None


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets) or "(none)"
        raise KeyError(f"Target '{name}' not found. Available: {available}")

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError("No targets configured. Check config.yaml")
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML; the secret comes from the encrypted store."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Run 'proxy-aiops init' to set up a traefik, caddy or haproxy "
            f"target, or create {CONFIG_FILE} with a 'targets' list."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    targets = tuple(
        TargetConfig(
            name=t["name"],
            platform=t.get("platform", TRAEFIK),
            base_url=t.get("base_url", ""),
            username=t.get("username", ""),
            verify_ssl=t.get("verify_ssl", True),
        )
        for t in raw.get("targets", [])
    )

    return AppConfig(targets=targets)
