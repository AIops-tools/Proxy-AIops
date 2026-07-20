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

from proxy_aiops.governance import opt_str, sanitize


def as_obj(data: Any) -> dict:
    """Return ``data`` as a dict (empty dict if it isn't one)."""
    return data if isinstance(data, dict) else {}


def s(value: Any, limit: int = 256) -> str:
    """Sanitize an arbitrary value to a bounded, injection-safe string."""
    return sanitize(str(value if value is not None else ""), limit)


def opt(value: Any, limit: int = 256) -> str | None:
    """Sanitize an *optional* field, preserving the difference between absent and empty.

    Companion to :func:`s`, which folds ``None`` into ``""``. Traefik, Caddy and
    HAProxy describe the same edge concepts with different keys, so :func:`pick`
    frequently finds none of its candidates — a Caddy route has no ``rule``, an
    HAProxy backend has no ``provider``. "This proxy does not express that
    concept" is a different fact from "the field is blank", and only the second
    should read as empty.

    Use this for anything read via :func:`pick` or out of a response row; keep
    :func:`s` for values that always exist (an exception message, a caller
    argument).
    """
    return opt_str(value, limit)


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
    """Coerce a numeric cell to float; 0.0 when absent/non-numeric.

    Use only for genuinely fractional values (rates, durations). For integer
    quantities — request counts, session counts, route priority — use
    :func:`as_int`.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_int(value: Any) -> int:
    """Coerce an integer quantity to ``int``; 0 when absent/non-numeric.

    Route priority is an int64 in Traefik: routing it through :func:`num` both
    rendered it in scientific notation (``9.223372036854776e+18``) *and* lost
    precision, since a float64 cannot represent 2**63-1 exactly. Counters have
    the same problem in miniature — a request total is never fractional.
    """
    if isinstance(value, bool):  # bool is an int subclass; not a quantity
        return 0
    if isinstance(value, int):
        return value  # already exact — never round-trip through float64
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


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
