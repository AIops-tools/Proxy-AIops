<!-- mcp-name: io.github.AIops-tools/proxy-aiops -->

# Proxy AIops

Governed, audited AI-ops for **Traefik**, **Caddy** and **HAProxy** reverse proxies / load balancers — for AI agents (via MCP) and humans (via CLI).

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Traefik Labs, the Caddy project, HAProxy Technologies, or the HAProxy project.** Traefik, Caddy and HAProxy are trademarks of their respective owners. MIT licensed.

proxy-aiops speaks to three proxy platforms behind one MCP server — **Traefik**
(its API under `/api/...`, metrics-text counters via `/metrics`), **Caddy** (the
admin API, default `localhost:2019`) and **HAProxy** (the Data Plane API v2 under
`/v2/...`, HTTP Basic auth) — with the **same tools working on all three**. Each
target in the config names its own `platform`; a name-keyed platform registry
selects the API shape (auth + resource paths), and an explicit **support matrix**
raises teaching errors for ops a platform genuinely cannot do — e.g. Traefik
writes point you at its providers (file, container labels, orchestrator CRDs), never
a silent no-op.

Every tool runs through a **built-in governance harness** (vendored, zero external
dependency): audit log, token/call budget with runaway circuit-breaker,
descriptive risk-tier labelling, undo-token recording, and output sanitisation.

## Why this exists

- **One server, three proxies** — Traefik, Caddy and HAProxy in a mixed edge,
  spoken to through identical tool names. Adding another proxy later is a new
  platform descriptor, not a rewrite.
- **Read the whole edge** — version, entrypoints/listeners, routes (routers /
  caddy routes / frontends) with parsed hosts+paths, services and server-level
  upstream health, middlewares, TLS domain inventory, traffic/error counters,
  and the live config tree (snapshot + search).
- **Flagship RCA analyses** — transparent heuristics that show their numbers,
  never a black-box verdict: `backend_health_rca` (down upstreams → cause class
  L4/L6/L7/DNS/maint + action), `cert_expiry_sweep` (days-to-expiry buckets +
  per-platform renewal hints), `error_rate_rca` (5xx share vs the fleet
  baseline, dominant code → 502/503/504/500 cause), and
  `route_conflict_analysis` (shadowed routes, dead routes, redirect loops).
- **Governed writes** — caddy config `set` / `delete` / full `load` (the prior
  subtree/config is fetched first, so the recorded undo replays a real restore)
  and haproxy runtime server `state` (ready/drain/maint) and `weight` (undo
  restores the prior value) — all with `dry_run` previews; delete/load are
  **risk=high** with double confirmation at the CLI.

## What this tool does, and does not, decide

It delivers proxy operations — reads and writes — accurately and efficiently,
and records every one of them. It does **not** decide whether a write is allowed
to happen. That is the agent's judgement, or the permission of the account you
connect it with: give the HAProxy Data Plane API a read-only role, or scope down
the Traefik/Caddy admin API you point it at, and the writes fail at the server —
the place that actually owns the permission.

So there is no read-only switch, no policy file, no approval gate to configure.
The one thing the tool guarantees is that nothing is silent: **every call, over
MCP and over the CLI alike, lands an audit row** in `~/.proxy-aiops/audit.db`,
and reversible writes still capture their before-state and record an inverse.

> Each tool declares a `risk_level`, kept in agreement with its `[READ]`/`[WRITE]`
> documentation tag by a test, and carried into the audit row as a descriptive
> tier — so a reviewer can see at a glance that a row was a high-risk delete. It
> is a label, not a gate.

Running a smaller / local model? See
[agent-guardrails.md](skills/proxy-aiops/references/agent-guardrails.md) — it lists
the guardrails this tool now enforces for you (so you don't spend prompt budget
restating them) and gives a ready-made system prompt for what's left.

## Tool inventory (28 tools)

| Domain | Tools | # | Kind |
|--------|-------|:-:|------|
| **Status** | `proxy_overview`, `version_info`, `list_entrypoints` | 3 | read |
| **Routes** | `list_routes`, `route_detail`, `find_route` | 3 | read |
| **Services** | `list_services`, `service_detail`, `list_upstreams`, `upstream_detail`, `list_middlewares` | 5 | read |
| **Certificates** | `list_certificates` | 1 | read |
| **Traffic** | `traffic_stats`, `error_counters` | 2 | read |
| **Config** | `config_snapshot`, `search_config`, `get_config_value` | 3 | read |
| **Flagship analyses** | `backend_health_rca`, `cert_expiry_sweep`, `error_rate_rca`, `route_conflict_analysis` | 4 | read |
| **Writes (caddy)** | `set_config_value` | 1 | write (**med**) |
| **Writes (caddy)** | `delete_config_path`, `load_config` | 2 | write (**high**) |
| **Writes (haproxy)** | `set_server_state`, `set_server_weight` | 2 | write (**med**) |
| **Undo** | `undo_list`, `undo_apply` | 2 | read / write |

Reversible writes record an inverse **undo descriptor** built from the real fetched
before-state (`set_config_value` restores the prior subtree; `delete_config_path`
re-creates it; `load_config` re-loads the snapshotted config; server state/weight
restore the prior value). The undo params match each tool's own signature, so the
descriptor replays as-is.

### Per-platform support matrix

| Capability | traefik | caddy | haproxy |
|------------|:-------:|:-----:|:-------:|
| Routes / services / upstream health | ✅ | ✅ | ✅ |
| Middlewares list | ✅ | teaching note (inline handlers) | teaching note (haproxy.cfg) |
| TLS cert inventory + expiry sweep | ✅ | ✅ | teaching note (.pem files) |
| Error counters / error-rate RCA | ✅ (/metrics) | teaching note (no per-route counters) | ✅ (stats) |
| Config snapshot / search | ✅ (rawdata, read-only) | ✅ | teaching note |
| Config writes | teaching error → edit the **provider** | ✅ (the write surface) | teaching error → runtime writes |
| Runtime server state / weight | teaching error → provider | teaching error → config tree | ✅ |

Unsupported combinations **raise a teaching error that says what to use instead**
— never a silent empty result.

## Install

```bash
uv tool install proxy-aiops        # or: pipx install proxy-aiops
```

## Quick start

```bash
proxy-aiops init                     # wizard: pick platform (traefik/caddy/haproxy) + optional encrypted secret
proxy-aiops doctor                   # verify config, secrets, and connectivity
proxy-aiops overview                 # one-shot: version + route/service counts + upstream health
proxy-aiops routes list              # normalised route table
proxy-aiops services upstreams       # server-level upstream health
proxy-aiops analyze health           # backend/upstream health RCA
proxy-aiops analyze errors           # 5xx error-rate RCA
proxy-aiops analyze conflicts        # shadowed/dead routes, redirect loops
proxy-aiops certs --sweep            # TLS cert expiry sweep (traefik/caddy)
proxy-aiops server state app web1 drain --dry-run   # governed haproxy write preview
proxy-aiops config set apps/http/servers/srv0 '{"listen":[":8080"]}' --dry-run
```

Run the MCP server (stdio) for an agent:

```bash
proxy-aiops mcp                      # or: proxy-aiops-mcp
```

### MCP client config

```json
{
  "mcpServers": {
    "proxy-aiops": {
      "command": "uvx",
      "args": ["--from", "proxy-aiops", "proxy-aiops-mcp"],
      "env": { "PROXY_AIOPS_MASTER_PASSWORD": "your-master-password" }
    }
  }
}
```

> **Env-block caveat**: the `env` block is only needed when a credential is
> stored (haproxy always; traefik/caddy only behind Basic auth). MCP clients do
> **not** inherit your shell profile — set `PROXY_AIOPS_MASTER_PASSWORD` (and
> `PROXY_AIOPS_CONFIG` / `PROXY_AIOPS_HOME` if you relocated them) explicitly in
> the client config, or the server cannot unlock `secrets.enc`.

## Configuration

`~/.proxy-aiops/config.yaml` (non-secret connection details only):

```yaml
targets:
  - name: edge1
    platform: traefik        # traefik | caddy | haproxy
    base_url: http://192.0.2.10:8080
    verify_ssl: true
  - name: caddy1
    platform: caddy
    base_url: http://127.0.0.1:2019
  - name: lb1
    platform: haproxy
    base_url: http://192.0.2.20:5555
    username: dpapi          # Data Plane API user
```

The **secret** — the HAProxy Data Plane API password, or an optional Basic-auth
password in front of Traefik/Caddy — is stored **encrypted** in
`~/.proxy-aiops/secrets.enc` (Fernet + scrypt-derived key), never plaintext on
disk. Traefik and Caddy commonly run unauthenticated on localhost, so their
secret is **optional** (like a local socket); HAProxy's is required. Set it with
`proxy-aiops secret set <target>` or the `init` wizard. The store is unlocked by
a master password from `PROXY_AIOPS_MASTER_PASSWORD` (non-interactive/MCP/CI) or
an interactive prompt (CLI on a TTY). A legacy plaintext env var
`PROXY_<TARGET>_SECRET` is honoured as a fallback (migrate with
`proxy-aiops secret migrate`).

## Governance

Every MCP tool — and every CLI write, which routes through the same governed
functions — passes through `@governed_tool`. It records; it does not authorize
(see above).

- **Audit** — every call is logged to `~/.proxy-aiops/audit.db` (tool, params with
  secrets redacted, status, duration, risk tier, and any operator-supplied
  approver/rationale). The CLI writes the same row the MCP path does — there is no
  unaudited entry point.
- **Budget / runaway guard** — a safety backstop, not an authorization gate:
  per-process token/call caps and a repeat-call circuit breaker stop a stuck agent
  from burning unbounded calls/time (`PROXY_MAX_TOOL_CALLS`, `PROXY_RUNAWAY_MAX`,
  …; disable the breaker with `PROXY_RUNAWAY_MAX=0`).
- **Undo recording** — reversible writes record an inverse descriptor to
  `~/.proxy-aiops/undo.db` from the fetched before-state (recording only; an
  external orchestrator executes it). Undo params match the target tool's own
  signature, so the descriptor replays as-is.
- **Risk tier** — a descriptive label on the audit row derived from `risk_level`;
  it gates nothing.
- **Sanitisation** — all proxy-returned text is bounded + control-character
  sanitised before it reaches the agent.

## Platform support & verification status

- **Platforms**: Traefik (API + /metrics), Caddy (admin API), HAProxy (Data Plane
  API v2).
- **Test coverage**: behaviour is validated against mocked JSON/metrics responses —
  every module imports, every MCP tool carries the governance marker, the four flagship
  analyses are unit-tested against synthetic telemetry, the support matrix is asserted
  to raise teaching errors (never a silent no-op), and reversible writes are asserted to
  record the correct inverse undo descriptor. The concrete REST paths are modelled from
  each project's public API and have not yet been exercised against a live proxy. See
  [docs/VERIFICATION.md](docs/VERIFICATION.md) for the checklist a live run must
  satisfy. All three platforms are free and self-hostable (a small container-compose lab
  with traefik + caddy + haproxy/dataplaneapi is a one-evening setup), and
  `proxy-aiops doctor` — a health/info probe per platform — is the fastest live check.
- **Routing note**: this tool operates reverse proxies / load balancers. Do NOT
  use it for firewall rules — use firewall-aiops.
- **Missing a capability?** Open an issue or PR at
  [github.com/AIops-tools/Proxy-AIops](https://github.com/AIops-tools/Proxy-AIops)
  — contributions and feedback welcome.

## License

MIT — see [LICENSE](LICENSE).
