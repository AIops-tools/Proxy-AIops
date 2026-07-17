# Proxy AIops v0.1.0 — preview

Governed AI-ops for **Traefik**, **Caddy** and **HAProxy** reverse proxies /
load balancers for AI agents, with a built-in governance harness (audit,
policy, token/runaway budget, undo-token recording, graduated risk tiers) and
an encrypted credential store. Standalone — no external skill-family
dependency. One MCP server spans all three platforms: a per-target `platform`
field selects the API shape, and the same 26 tools work on Traefik (API +
`/metrics`), Caddy (admin API) and HAProxy (Data Plane API v2, HTTP Basic).

> **Not affiliated with, endorsed by, or sponsored by Traefik Labs, the Caddy
> project, HAProxy Technologies, or the HAProxy project.** Traefik, Caddy and
> HAProxy are trademarks of their respective owners.

> **Preview / mock-only.** All behaviour is validated against mocked JSON /
> metrics-text responses; it has **not** been run against a live proxy. The
> concrete REST paths are modelled from each project's public API and need
> live verification. All three platforms are free/self-hostable, so a small
> lab is the easiest live check — `proxy-aiops doctor` is the fastest.

## Highlights

- **26 MCP tools** (21 read, 5 write), every one wrapped with `@governed_tool`:
  - **Status** — `proxy_overview`, `version_info`, `list_entrypoints`.
  - **Routes** — `list_routes` (hosts/paths normalised across platforms),
    `route_detail`, `find_route`.
  - **Services** — `list_services`, `service_detail`, `list_upstreams`,
    `upstream_detail`, `list_middlewares`.
  - **Certificates** — `list_certificates` (+ bounded live expiry probe).
  - **Traffic** — `traffic_stats`, `error_counters`.
  - **Config** — `config_snapshot`, `search_config`, `get_config_value`.
  - **Writes** — caddy `set_config_value` / `delete_config_path` /
    `load_config` (prior config captured → replayable undo), haproxy
    `set_server_state` (ready/drain/maint) / `set_server_weight` (undo
    restores the prior value). High-risk writes gate on an approver.
- **Flagship analyses** (transparent heuristics that show their numbers):
  - `backend_health_rca` — down upstreams per service with the failure class
    (connection refused / L4 timeout / TLS / L7 check / DNS / maint) + action.
  - `cert_expiry_sweep` — days-to-expiry buckets (expired / critical /
    warning / ok) + per-platform renewal hints.
  - `error_rate_rca` — per-service 5xx share vs the fleet baseline; the
    dominant code maps to a cause (503 no-upstream / 502 conn-fail /
    504 timeout / 500 app error).
  - `route_conflict_analysis` — shadowed routes, dead routes (service missing
    or zero servers up), redirect loops.
- **Support matrix with teaching errors** — ops a platform cannot do fail fast
  with what to use instead (traefik writes → its providers; caddy counters →
  access logs; haproxy certs → the .pem pipeline). Never a silent no-op.
- **Governance** — audit to `~/.proxy-aiops/audit.db`, budget/runaway guard,
  secure-by-default approver gate for high-risk writes, undo descriptors built
  from the real fetched before-state (replayable as-is), output sanitisation.

## Install

```bash
uv tool install proxy-aiops
proxy-aiops init && proxy-aiops doctor
```

Routing note: this tool operates reverse proxies / load balancers. Do NOT use
it for firewall rules — use firewall-aiops.
