"""Governed proxy writes — the only state-changing operations in the tool.

Platform dispatch is enforced by the support matrix: the first thing every
write does is resolve its resource path, and on the wrong platform that raises
the registry's teaching error (Traefik writes → "edit the provider"; HAProxy
config writes → transactions out of scope), never a silent no-op.

Every reversible write reads the current state **before** it changes anything,
so the harness records a faithful undo / audit trail (the before-state is
fetched via a real GET, never guessed):

  * ``set_config_value`` / ``delete_config_path`` (caddy) — read the config
    subtree at the path first; undo restores the prior subtree.
  * ``load_config`` (caddy) — snapshot the FULL config first; undo re-loads it.
  * ``set_server_state`` / ``set_server_weight`` (haproxy) — read the runtime
    server's admin state / weight first; undo sets it back.

The caddy writes additionally refuse to touch the ``admin`` subtree
(:class:`SelfLockout`). ``encode_config_path`` stops a path from traversing
*out* of ``/config/``, but ``admin`` is a legitimate top-level key *inside* the
config tree — and the admin API is this tool's own transport. Setting
``admin/disabled`` tears the listener down immediately, and the undo would then
have to re-POST the prior config over an API that no longer exists. The tool's
own setup guide already lists "caddy admin listener disabled" as a terminal
failure; it must not be able to cause it.

Each function returns a plain descriptor; the MCP layer adds dry-run + the
governance harness (risk tier + audit + undo).
"""

from __future__ import annotations

import json
from typing import Any

from proxy_aiops.connection import ProxyApiError
from proxy_aiops.ops._util import s
from proxy_aiops.platform import encode_config_path

SERVER_STATES = ("ready", "drain", "maint")
_MIN_WEIGHT, _MAX_WEIGHT = 0, 256
_MAX_VALUE_BYTES = 200_000  # bound on a config payload we will write


def _validate_value_size(value: Any) -> None:
    if len(json.dumps(value, default=str)) > _MAX_VALUE_BYTES:
        raise ValueError(
            f"Config value too large (> {_MAX_VALUE_BYTES} bytes) — write "
            f"smaller subtrees, or use load_config for a full replace."
        )


# ── caddy admin-subtree guard (the admin API is our own transport) ──────────

_ADMIN_KEY = "admin"


class SelfLockout(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: the operation would tear down the admin API this tool speaks to."""


def _config_segments(path: str) -> list[str]:
    """Split a config path the same way ``encode_config_path`` does.

    Compared on the RAW segments, which is what caddy resolves: an encoded
    ``%61dmin`` is re-encoded to ``%2561dmin`` and addresses a key literally
    named ``%61dmin``, not ``admin``, so it is correctly left alone.
    """
    return [seg for seg in str(path).split("/") if seg != ""]


def _refuse_admin_path(path: str, action: str) -> None:
    """Refuse a config write whose FIRST segment is the ``admin`` subtree.

    Exact by construction: only the top-level ``admin`` key is refused, so
    ``apps/http/...`` (everything an operator actually tunes) is untouched, and
    a nested key that merely happens to be named ``admin`` deeper in the tree
    is not the listener config and is not blocked.
    """
    segments = _config_segments(path)
    if not segments or segments[0] != _ADMIN_KEY:
        return
    # Kept under the MCP layer's _safe_error truncation (400 chars) so the route
    # back — the part an agent acts on — is never the part that gets cut off.
    raise SelfLockout(
        f"Refusing to {action} config path '{path}': the 'admin' subtree configures the "
        f"caddy admin API, this tool's only transport. Changing it can stop the listener "
        f"mid-call, leaving the undo nothing to replay over — the write would destroy "
        f"its own reversibility. Edit the admin block in caddy's config file and reload "
        f"caddy locally instead."
    )


def _admin_teardown_reason(prior: Any, incoming: Any) -> str | None:
    """Why an incoming FULL config would cut the admin API off, or None if safe.

    Two things in the ``admin`` subtree end the connection: ``disabled: true``
    stops the listener outright, and a changed ``listen`` address moves it out
    from under the configured ``base_url``.

    Fails open on both halves. ``disabled`` is absolute and needs no prior. The
    ``listen`` comparison needs the live config, so when that could not be read
    (non-dict prior, or no admin block in it) the call proceeds — an
    undeterminable prior must never be reported as a change.
    """
    new_admin = incoming.get(_ADMIN_KEY) if isinstance(incoming, dict) else None
    if not isinstance(new_admin, dict):
        return None  # the incoming config does not describe the admin listener
    if new_admin.get("disabled"):
        return (
            "it sets admin.disabled — the admin API this tool speaks to would stop "
            "listening the moment the config applied"
        )
    prior_admin = prior.get(_ADMIN_KEY) if isinstance(prior, dict) else None
    if not isinstance(prior_admin, dict):
        return None  # no readable live admin block — unknown, never assumed changed
    if "listen" in new_admin and new_admin.get("listen") != prior_admin.get("listen"):
        return (
            f"it moves admin.listen from {prior_admin.get('listen')!r} to "
            f"{new_admin.get('listen')!r} — the configured base_url would no longer "
            f"reach the admin API"
        )
    return None


def _refuse_admin_teardown(prior: Any, incoming: Any, action: str) -> None:
    reason = _admin_teardown_reason(prior, incoming)
    if reason is None:
        return
    raise SelfLockout(
        f"Refusing to {action}: {reason}, so the undo could never be replayed. Keep "
        f"the admin block as it is (omit it, or send it unchanged), and make listener "
        f"changes in caddy's own config file with a local reload instead."
    )


# The three guard_* helpers below are what the MCP wrappers call ahead of their
# ``dry_run`` early return, and what the write functions themselves call. One
# implementation per guard means a preview can never contradict the real call —
# including its fail-open behaviour, which must be identical on both paths (a
# dry_run that refuses something the real call would allow is its own bug).


def guard_set_config_value(conn: Any, path: str, value: Any) -> None:
    """Raise the :class:`SelfLockout` ``set_config_value`` would raise, without writing."""
    _refuse_admin_path(path, "set")
    if _config_segments(path):
        return
    # A root path replaces the whole tree, so the admin block rides along and
    # has to be compared against the live one (costs one GET on the dry-run path).
    prior, _ = _get_config_subtree(conn, encode_config_path(path))
    _refuse_admin_teardown(prior, value, "replace the config root")


def guard_delete_config_path(path: str) -> None:
    """Raise the :class:`SelfLockout` ``delete_config_path`` would raise. No I/O."""
    _refuse_admin_path(path, "delete")
    if _config_segments(path):
        return
    raise SelfLockout(
        "Refusing to delete the config root: that removes every block including "
        "'admin', so the admin API this tool speaks to would stop listening and "
        "the undo could never be replayed. Delete a specific subtree, or use "
        "load_config to replace the tree with one that keeps an admin block."
    )


def guard_load_config(conn: Any, config: Any) -> None:
    """Raise the :class:`SelfLockout` ``load_config`` would raise, without loading.

    Needs the live config to compare ``admin.listen`` against, so the dry-run
    path pays the same GET the real call does.
    """
    if not isinstance(config, dict) or not config:
        return  # shape validation is load_config's own job, not the guard's
    _refuse_admin_teardown(conn.get(conn.platform.path("config_root")), config,
                           "load this config")


# ── caddy config writes (reversible via prior-config capture) ───────────────


def _get_config_subtree(conn: Any, enc_path: str) -> tuple[Any, bool]:
    """Fetch the current subtree at a config path; (value, existed)."""
    base = conn.platform.path("config_get")
    try:
        return conn.get(base + enc_path), True
    except ProxyApiError as exc:
        if exc.status_code == 404:
            return None, False
        raise


def set_config_value(conn: Any, path: str, value: Any) -> dict:
    """[WRITE][med] Set one config subtree (e.g. a route's upstreams),
    capturing the prior subtree for a replayable undo.

    PATCH replaces an existing subtree; a path that does not exist yet is
    created with POST. The prior value is fetched via a real GET first.

    **Refuses to write the ``admin`` subtree** — that is the admin API this tool
    speaks to, and disabling or moving it would leave the recorded undo with
    nothing to replay over. A root path (which writes the whole tree, admin
    included) is checked the same way ``load_config`` is.
    """
    _validate_value_size(value)
    _refuse_admin_path(path, "set")
    base = conn.platform.path("config_set")  # teaching error off-platform
    enc = encode_config_path(path)
    prior, existed = _get_config_subtree(conn, enc)
    if not _config_segments(path):
        # A root path replaces the whole tree, so the admin block rides along.
        # (Reuses the prior already in hand rather than re-fetching it.)
        _refuse_admin_teardown(prior, value, "replace the config root")
    if existed:
        conn.patch(base + enc, json=value)
    else:
        conn.post(base + enc, json=value)
    return {
        "action": "set_config_value",
        "path": s(path, 300),
        "priorState": {"value": prior, "existed": existed},
        "note": "Caddy applies config changes immediately (no separate apply step).",
    }


def delete_config_path(conn: Any, path: str) -> dict:
    """[WRITE][high] Delete a config subtree, capturing it first for undo.

    **Refuses the ``admin`` subtree, and the config root** — deleting either
    removes the admin listener this tool depends on, and the undo that would
    re-create it could not be delivered.
    """
    guard_delete_config_path(path)
    base = conn.platform.path("config_delete")  # teaching error off-platform
    enc = encode_config_path(path)
    prior, existed = _get_config_subtree(conn, enc)
    if not existed:
        raise KeyError(
            f"Config path '{path}' does not exist — nothing to delete. "
            f"Use search_config / config_snapshot to find the right path."
        )
    conn.delete(base + enc)
    return {
        "action": "delete_config_path",
        "path": s(path, 300),
        "priorState": {"value": prior, "existed": True},
    }


def load_config(conn: Any, config: dict) -> dict:
    """[WRITE][high] Replace the FULL running config, snapshotting the prior
    config first (undo re-loads the snapshot).

    **Refuses a config that would tear down the admin API** — ``admin.disabled``
    truthy, or an ``admin.listen`` that differs from the live one. Either would
    end the transport the undo needs to re-POST the snapshot. Send the admin
    block unchanged, or omit it.
    """
    if not isinstance(config, dict) or not config:
        raise ValueError("config must be a non-empty JSON object (the full config tree).")
    _validate_value_size(config)
    load_path = conn.platform.path("config_load")  # teaching error off-platform
    prior = conn.get(conn.platform.path("config_root"))
    _refuse_admin_teardown(prior, config, "load this config")
    conn.post(load_path, json=config)
    return {
        "action": "load_config",
        "priorState": {"config": prior},
        "note": "Full config replace — the prior config was snapshotted for undo.",
    }


# ── haproxy runtime-server writes (reversible via prior state) ──────────────


def _runtime_server(conn: Any, backend: str, server: str) -> dict:
    path = conn.platform.path("runtime_server", name=server, backend=backend)
    raw = conn.get(path)
    return raw if isinstance(raw, dict) else {}


def set_server_state(conn: Any, backend: str, server: str, state: str) -> dict:
    """[WRITE][med] Set a server's admin state (ready / drain / maint),
    capturing its prior admin state. Undo restores it.

    ``drain`` finishes in-flight sessions but takes no new ones; ``maint``
    removes the server immediately; ``ready`` returns it to rotation.
    """
    state = str(state).strip().lower()
    if state not in SERVER_STATES:
        raise ValueError(
            f"state must be one of {SERVER_STATES}, got '{state}'."
        )
    # Resolving the path first raises the teaching error on traefik/caddy.
    path = conn.platform.path("runtime_server", name=server, backend=backend)
    prior = _runtime_server(conn, backend, server)
    prior_state = str(prior.get("admin_state") or "").lower() or None
    conn.put(path, json={"admin_state": state})
    return {
        "action": "set_server_state",
        "backend": s(backend, 128),
        "server": s(server, 128),
        "state": state,
        "priorState": {"adminState": prior_state},
    }


def set_server_weight(conn: Any, backend: str, server: str, weight: int) -> dict:
    """[WRITE][med] Set a server's load-balancing weight, capturing the prior
    weight. Undo restores it. Weight 0 stops new traffic without a state change.
    """
    try:
        weight = int(weight)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"weight must be an integer, got '{weight}'.") from exc
    if not (_MIN_WEIGHT <= weight <= _MAX_WEIGHT):
        raise ValueError(
            f"weight must be between {_MIN_WEIGHT} and {_MAX_WEIGHT}, got {weight}."
        )
    path = conn.platform.path("runtime_server", name=server, backend=backend)
    prior = _runtime_server(conn, backend, server)
    prior_weight = prior.get("weight")
    conn.put(path, json={"weight": weight})
    return {
        "action": "set_server_weight",
        "backend": s(backend, 128),
        "server": s(server, 128),
        "weight": weight,
        "priorState": {
            "weight": int(prior_weight) if prior_weight is not None else None
        },
    }
