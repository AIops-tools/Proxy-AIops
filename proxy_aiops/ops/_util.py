"""Shared helpers for the proxy ops modules.

Traefik, Caddy and HAProxy expose the same edge concepts under very different
JSON shapes (a Traefik router has a ``rule`` string; a Caddy route has a
``match`` list; an HAProxy frontend has ACLs). The ops modules stay
platform-neutral by asking the platform for paths/rows (see
:mod:`proxy_aiops.platform`) and by reading fields through :func:`pick` /
:func:`to_bool`, which try a list of candidate keys. All proxy text reaches the
caller only after ``sanitize()`` via ``s``.
"""

from __future__ import annotations

import re
from typing import Any

from proxy_aiops.governance import sanitize


def as_obj(data: Any) -> dict:
    """Return ``data`` as a dict (empty dict if it isn't one)."""
    return data if isinstance(data, dict) else {}


def s(value: Any, limit: int = 256) -> str:
    """Sanitize an arbitrary value to a bounded, injection-safe string."""
    return sanitize(str(value if value is not None else ""), limit)


def pick(row: dict, *keys: str, default: Any = None) -> Any:
    """Return the first present, non-None value among ``keys`` (else ``default``)."""
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return default


_TRUE = {"1", "true", "yes", "on", "enabled", "up", "active", "ready"}
_FALSE = {"0", "false", "no", "off", "disabled", "down", "", "none"}


def to_bool(value: Any) -> bool:
    """Coerce a truthy/falsy cell (``"1"``, ``true``, ``"up"``) to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return bool(text)


def num(value: Any) -> float:
    """Coerce a numeric cell to float; 0.0 when absent/non-numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ── Traefik rule-string parsing ──────────────────────────────────────────────
# Traefik encodes matching as a rule string: Host(`a.example`) &&
# PathPrefix(`/api`). We extract hosts and path prefixes for the static
# analyses; anything else (headers, methods, regexes) is left to the raw rule.
_HOST_RE = re.compile(r"Host(?:SNI|Regexp)?\(([^)]*)\)")
_PATH_RE = re.compile(r"Path(?:Prefix|Regexp)?\(([^)]*)\)")
_BACKTICK_RE = re.compile(r"[`'\"]([^`'\"]+)[`'\"]")


def parse_rule_hosts(rule: str) -> list[str]:
    """Extract hostnames from a Traefik rule string (lowercased)."""
    hosts: list[str] = []
    for group in _HOST_RE.findall(str(rule or "")):
        hosts.extend(h.lower() for h in _BACKTICK_RE.findall(group))
    return hosts


def parse_rule_paths(rule: str) -> list[str]:
    """Extract path prefixes from a Traefik rule string."""
    paths: list[str] = []
    for group in _PATH_RE.findall(str(rule or "")):
        paths.extend(_BACKTICK_RE.findall(group))
    return paths
