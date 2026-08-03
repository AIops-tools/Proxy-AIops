# Changelog

## v0.7.0 — 2026-08-03

### Fixed
- **Undoing a deleted array element could never work.** `delete_config_path` is a high-risk write that records an undo, but its inverse re-created the subtree with POST — and once `routes/1` is deleted the array is shorter, so Caddy answers *"array index out of bounds"*. Array paths (`routes/N`, `handle/N`, `upstreams/N`) are the norm in a Caddy config, so the token for a destructive write was routinely un-replayable. The inverse now inserts with PUT, which is Caddy's insert-at-index. Verified live against Caddy 2, including deleting the **last** element and restoring it into an empty array.
- **`set_config_value(insert=True)` no longer pre-reads the index.** There is nothing at that index by definition, and Caddy answers a GET of an out-of-range index with **400, not 404**, so the pre-read re-raised and the insert never reached the wire — which is what made the undo above look like a rejected write rather than a missing capability.
- **`undo apply` replays against the target the original write ran on.** It dispatched the inverse against whatever target the *caller* named — in practice the config's first entry — while the write's own target sat unused in the undo record. On a multi-target config the inverse therefore ran against the wrong host; it only looks harmless because the resource usually is not there, but two hosts holding the same name and the inverse **succeeds on the wrong one, silently**. An explicitly named target still wins. Line-wide: all 24 copies had the identical defect. Caught live in container-host-aiops, where a stop recorded against a Podman target replayed against a Portainer one.

## v0.6.0 — 2026-08-02

### Changed (BREAKING)
- **Requires MCP SDK 2.0** (`mcp[cli]>=2.0,<3.0`). `mcp.server.fastmcp` no longer exists in 2.0; the server is now built with `MCPServer` and reports its package version in the stdio handshake.

### Fixed
- **`undo apply` works from the CLI.** Every write tool is imported lazily inside its own CLI command, so a CLI-driven undo ran in a process where the inverse tool was never registered and failed with "inverse tool is not registered" — for every write tool. Only the MCP entry point, which imports the whole server, worked. Found while live-verifying against a real cluster.
- **An undetermined outcome is audited `unknown`, not `ok`.** The harness only classified a result as undetermined when the payload *also* carried an `error` key, so a write that looked successful but had not been confirmed was recorded as a success.


## v0.5.0 — 2026-07-21

### Changed (BREAKING)
- **Removed the authorization layer** — read-only mode, the approver gate, and rules.yaml deny are gone. The skill no longer decides read vs write; that is the agent's judgement or the connecting account's permissions. `<PREFIX>_READ_ONLY` now has no effect (a startup warning is logged); `<PREFIX>_AUDIT_APPROVED_BY`/`_RATIONALE` are optional audit annotations.
- The retained guarantee is **unbypassable audit over MCP and CLI alike** — no unaudited entry point. Harness = audit + runaway safety guard + undo + sanitize; `risk_level` is a descriptive audit label, not a gate.

See RELEASE_NOTES.md for tool-specific changes.


## v0.4.0 — 2026-07-20

### Fixed
- **Caddy config writes refuse the `admin` subtree.** The Caddy admin API is this tool's own transport, and `admin` is an ordinary top-level key in the config tree — so `set_config_value("admin/disabled", true)` tore down the listener mid-request and left the undo with nowhere to go.
- Harness: a write whose response is lost is audited `status=unknown`, not `error` — it may have taken effect. Undo tokens gain `effectVerified` (undo.db migrated in place).
- Harness: a dry-run no longer records an undo token, and no longer requires a named approver. Guards now run on the preview path.
- Truncated strings end in an ellipsis instead of being cut silently; error messages are capped at 800 chars, not 300.

See RELEASE_NOTES.md for the full detail.

All notable changes to proxy-aiops are documented here. This project adheres
to [Semantic Versioning](https://semver.org/).

## v0.1.0 — 2026-07-17

Initial preview release: governed AI-ops for **Traefik**, **Caddy** and
**HAProxy** reverse proxies / load balancers, with a bundled governance
harness. One MCP server spans all three platforms via a per-target `platform`
field; an explicit support matrix raises teaching errors for ops a platform
cannot do (never a silent no-op).
**Mock-validated only — not yet verified against a live proxy.**

### Added

- **26 MCP tools** (21 read, 5 write), every one wrapped with the bundled
  `@governed_tool` harness (audit, policy, token/runaway budget, undo,
  risk-tiers):
  - **Status (read)** — `proxy_overview`, `version_info`, `list_entrypoints`.
  - **Routes (read)** — `list_routes` (normalised hosts/paths across
    platforms), `route_detail`, `find_route` (static host/path match).
  - **Services (read)** — `list_services`, `service_detail`, `list_upstreams`
    (server-level up/down/maint/drain + check info), `upstream_detail`,
    `list_middlewares`.
  - **Certificates (read)** — `list_certificates` (TLS domain inventory +
    optional bounded live handshake probe).
  - **Traffic (read)** — `traffic_stats`, `error_counters` (traefik /metrics
    text parsed per status code; haproxy Data Plane stats per class).
  - **Config (read)** — `config_snapshot`, `search_config`, `get_config_value`.
  - **Flagship analyses (read)** — `backend_health_rca`, `cert_expiry_sweep`,
    `error_rate_rca`, `route_conflict_analysis` — transparent heuristics that
    report their numbers, not a black-box verdict.
  - **Writes** — caddy `set_config_value` (med, prior subtree captured →
    undo restores it), `delete_config_path` (**high**, subtree captured → undo
    re-creates it), `load_config` (**high**, full prior config snapshotted →
    undo re-loads it); haproxy `set_server_state` (med, ready/drain/maint,
    undo restores prior admin state), `set_server_weight` (med, undo restores
    prior weight). Every write takes a `dry_run` preview; high-risk writes
    require an approver.
- **Platform abstraction with a support matrix** — a name-keyed registry maps
  each target's `platform` (`traefik` / `caddy` / `haproxy`) to its auth style
  + REST resource paths, and unsupported ops raise **teaching errors**:
  traefik writes point at its providers, haproxy config edits point at the
  runtime writes, caddy error counters point at access logs.
- **Encrypted secret store** — the haproxy Data Plane API password (required)
  or an optional Basic-auth credential for traefik/caddy is stored encrypted
  in `~/.proxy-aiops/secrets.enc` (Fernet + scrypt); never plaintext on disk.
  Traefik/caddy targets without a secret are treated as unauthenticated
  localhost endpoints. Legacy `PROXY_<TARGET>_SECRET` env var honoured as a
  fallback.
- **CLI** (`proxy-aiops`) — `init` platform-picking wizard (TLS-verify default
  ON, seeds a starter rules.yaml with the dual-control high-risk tier),
  `overview`, `routes list/show/find`, `services list/show/upstreams`,
  `analyze health/errors/conflicts`, `certs --sweep`,
  `config snapshot/search/get/set/delete` and `server state/weight`
  (dry-run + double-confirm, executed through the governed twins so CLI writes
  are audited + undo-recorded), `secret` management, and a `doctor`
  connectivity check (health/info probe per platform).

### Known limitations

- Preview / mock-only: Traefik, Caddy and HAProxy responses are mocked and
  need live verification; the modelled REST paths especially (the haproxy
  runtime-server weight write in particular).
- haproxy host/path routing lives in haproxy.cfg ACLs and is not statically
  analysed; `route_conflict_analysis` covers dead-backend detection there.
- **Missing a capability? Open an issue or PR** — contributions welcome.
