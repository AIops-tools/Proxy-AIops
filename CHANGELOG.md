# Changelog

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
