---
name: proxy-aiops
description: >
  Use this skill whenever the user needs to operate a Traefik, Caddy or HAProxy reverse proxy / load balancer — a one-shot overview, routes (routers / caddy routes / frontends) with host/path matching, services and server-level upstream health, middlewares, TLS certificate inventory with an expiry sweep, traffic and 5xx error counters, config snapshot/search, four flagship RCAs (backend health, cert expiry, error rate, route conflicts), and governed writes (caddy config set/delete/load with prior-config capture; haproxy server drain/maint/ready and weight).
  Always use this skill for "Traefik", "Caddy", "HAProxy", "reverse proxy", "load balancer", "upstream down", "502/503/504 errors", "bad gateway", "cert expiring", "TLS certificate", "route not matching", "which route serves this host", "drain a server", "server weight", "redirect loop" when the context is a Traefik/Caddy/HAProxy edge.
  Do NOT use when the target is something other than a Traefik/Caddy/HAProxy proxy (a hypervisor, storage appliance, backup product, container-orchestration cluster, multi-vendor router/switch config, or OT/industrial equipment) — route those to the appropriate other AIops-tools skill. Do NOT use for firewall rules — use firewall-aiops. Managed cloud load balancers are out of scope.
  Preview — governed proxy operations with a built-in governance harness (audit, policy, token budget, undo, risk-tiers). Mock-validated only, not run against a live proxy; all three platforms are free/self-hostable, so a small lab is the easiest live check.
installer:
  kind: uv
  package: proxy-aiops
argument-hint: "[a route/service/backend name, a hostname, or describe your proxy task]"
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"env":["PROXY_AIOPS_CONFIG"],"bins":["proxy-aiops"],"config":["~/.proxy-aiops/config.yaml"]},"optional":{"env":["PROXY_AIOPS_MASTER_PASSWORD"],"config":["~/.proxy-aiops/secrets.enc"]},"primaryEnv":"PROXY_AIOPS_CONFIG","homepage":"https://github.com/AIops-tools/Proxy-AIops","emoji":"🔀","os":["macos","linux"]}}
compatibility: >
  Standalone, self-governed reverse-proxy operations across Traefik (API /api/..., metrics-text counters via /metrics), Caddy (admin API, default localhost:2019 — carries the write surface) and HAProxy (Data Plane API v2 /v2/..., HTTP Basic auth) — preview. Each target in the config names its own platform, and a name-keyed platform registry selects the API shape; an explicit support matrix raises teaching errors for ops a platform cannot do (traefik writes → its providers; caddy error counters → access logs; haproxy certs → the .pem pipeline), never a silent no-op. The governance harness (audit, policy, token/runaway budget, undo, risk-tiers) is bundled in the package — no external skill-family dependency.
  All write operations are audited to a local SQLite DB under ~/.proxy-aiops/ (relocatable via PROXY_AIOPS_HOME).
  Credentials: the HAProxy Data Plane API password (required) or an optional Basic-auth credential for Traefik/Caddy is stored ENCRYPTED in ~/.proxy-aiops/secrets.enc (Fernet/AES-128 + scrypt-derived key) — never plaintext on disk. Traefik and Caddy usually run unauthenticated on localhost, so their secret is optional (no store entry = no auth header). Run 'proxy-aiops init' to onboard (it asks for the platform), or 'proxy-aiops secret set <target>'. The store is unlocked by a master password from PROXY_AIOPS_MASTER_PASSWORD (non-interactive/MCP/CI) or an interactive prompt (CLI on a TTY). A legacy plaintext env var PROXY_<TARGET_NAME_UPPER>_SECRET is still honoured as a fallback with a deprecation warning (migrate with 'proxy-aiops secret migrate'). Secrets are never logged or echoed.
  State-changing operations pass through the @governed_tool decorator (pre-check + budget guard + audit + risk-tier gate). delete_config_path and load_config (full config replace) are risk=high with dry_run + an approver gate. Reversible writes (set_config_value, delete_config_path, load_config, set_server_state, set_server_weight) capture the real fetched before-state and record an inverse undo descriptor whose params replay against the tool's own signature.
  Webhooks: none — no outbound network calls beyond the configured proxy APIs, plus (only when the operator runs the cert sweep with probing) a bounded TLS handshake per inventoried domain.
  SSL: verify_ssl defaults to true; disable only for self-signed lab certs.
  Transitive dependencies: httpx (HTTP client), cryptography (secret store + cert parsing), and the MCP SDK. No post-install scripts or background services.
  PREVIEW: mock-validated only — not run against a live proxy. All three platforms are free/self-hostable, so a small lab is the easiest live check.
---

# Proxy AIops (preview)

> **Disclaimer**: Community-maintained open-source project, **not affiliated with, endorsed by, or sponsored by Traefik Labs, the Caddy project, HAProxy Technologies, or the HAProxy project.** Traefik, Caddy and HAProxy are trademarks of their respective owners. Source at [github.com/AIops-tools/Proxy-AIops](https://github.com/AIops-tools/Proxy-AIops) under the MIT license.

Governed reverse-proxy operations — **26 MCP tools** across **Traefik** (API +
`/metrics`), **Caddy** (admin API) and **HAProxy** (Data Plane API v2), every
one wrapped with the bundled `@governed_tool` harness: a local unified audit
log under `~/.proxy-aiops/`, policy engine, token/runaway budget guard,
undo-token recording, and graduated-autonomy risk tiers. A per-target
`platform` field selects the API shape, so the same tools work on all three
proxies and one config can span a mixed edge. An explicit **support matrix**
raises teaching errors for ops a platform cannot do — never a silent no-op.
Credentials are stored **encrypted** (`~/.proxy-aiops/secrets.enc`, Fernet +
scrypt) — never plaintext on disk; Traefik/Caddy secrets are optional
(unauthenticated localhost is the common case).

> **Standalone**: the governance harness is bundled in the package
> (`proxy_aiops.governance`) — no external skill-family dependency.
> **Preview / mock-only**: not run against a live proxy; all three platforms
> are free/self-hostable, so a small lab is the easiest live check.

## What This Skill Does

| Group | Tools | Count | R/W |
|-------|-------|:-----:|:---:|
| **Status** | proxy_overview, version_info, list_entrypoints | 3 | read |
| **Routes** | list_routes, route_detail, find_route | 3 | read |
| **Services** | list_services, service_detail, list_upstreams, upstream_detail, list_middlewares | 5 | read |
| **Certificates** | list_certificates | 1 | read |
| **Traffic** | traffic_stats, error_counters | 2 | read |
| **Config** | config_snapshot, search_config, get_config_value | 3 | read |
| **Flagship analyses** | backend_health_rca, cert_expiry_sweep, error_rate_rca, route_conflict_analysis | 4 | read |
| **Writes (caddy)** | set_config_value (med), delete_config_path (**high**), load_config (**high**) | 3 | write |
| **Writes (haproxy)** | set_server_state, set_server_weight | 2 | write (med) |

The four flagship analyses are transparent heuristics that report their
numbers, never a black-box verdict: `backend_health_rca` groups down upstreams
per service and maps the health-check failure class (connection refused / L4
timeout / TLS / L7 / DNS / maint) to a cause + action; `cert_expiry_sweep`
buckets certs by days-to-expiry with per-platform renewal hints;
`error_rate_rca` ranks services by 5xx share vs the fleet baseline and maps
the dominant code (502/503/504/500) to a cause; `route_conflict_analysis`
finds shadowed routes, dead routes, and redirect loops.

## Quick Install

```bash
uv tool install proxy-aiops
proxy-aiops init       # wizard: pick platform (traefik/caddy/haproxy) + optional encrypted secret
proxy-aiops doctor
```

## When to Use This Skill

- Get a one-shot snapshot (`overview` / `version_info` / `list_entrypoints`)
- Investigate 502/503/504 spikes (`error_rate_rca`) → dominant code → cause
- Find why an upstream/backend is down (`list_upstreams`, `backend_health_rca`)
- Sweep TLS cert expiry across the edge (`certs --sweep` / `cert_expiry_sweep`)
- Audit routing hygiene (`route_conflict_analysis` — shadowed/dead routes,
  redirect loops) and answer "which route serves this host?" (`find_route`)
- Safely drain/return an haproxy server (`set_server_state`, reversible +
  undo-recorded) or adjust its weight (`set_server_weight`)
- Safely edit caddy config (`set_config_value` / `delete_config_path` /
  `load_config` — prior config captured, undo replays the restore)

**Do NOT use when** the target is not a Traefik/Caddy/HAProxy proxy — route
hypervisor, storage, backup, cluster, network-device, or OT/industrial work to
the appropriate other AIops-tools skill. Do NOT use for firewall rules — use
firewall-aiops.

## Related Skills — Skill Routing

| If the user wants… | Use |
|--------------------|-----|
| Traefik / Caddy / HAProxy proxy ops | **proxy-aiops** (this skill) |
| Firewall rules / NAT / gateway health | **firewall-aiops** |
| A non-proxy platform (hypervisor, storage, backup, cluster, network devices, OT edge) | the appropriate **other AIops-tools** skill |
| Managed cloud load balancers | out of scope for this tool |

## Common Workflows

### 5xx spike triage

1. `error_rate_rca` → flagged services ranked by 5xx share vs fleet baseline,
   dominant code mapped to a cause (503 no-upstream / 502 conn-fail / 504
   timeout / 500 app error)
2. `backend_health_rca` → is it an upstream availability problem? failing
   servers with their check class
3. If a server needs to come out: `set_server_state <backend> <server> drain`
   (dry-run first; undo restores the prior state)

### Cert expiry sweep

1. `certs --sweep` (CLI) or `cert_expiry_sweep` (MCP) on each traefik/caddy
   target → expired/critical/warning buckets + renewal hint
2. haproxy targets return a teaching note (certs are .pem files there)

### "Which route serves this host?" / routing hygiene

1. `find_route <host> --path /api` → best-match routes, most specific first
2. `route_conflict_analysis` → shadowed routes (covered by an earlier/higher-
   priority route), dead routes (service missing / zero servers up), redirect
   loops — each finding names the covering route or missing service

### Edit a caddy route upstream (reversible)

1. `search_config <needle>` / `config_snapshot` → find the config path
2. `config set <path> '<json>' --dry-run` → preview
3. Re-run without `--dry-run` (double-confirm) — the prior subtree is captured
   and an inverse undo descriptor is recorded
4. `delete_config_path` / `load_config` are risk=high; set
   `PROXY_AUDIT_APPROVED_BY` (+ `PROXY_AUDIT_RATIONALE`)

## Governance & Safety

- Every tool is audited to `~/.proxy-aiops/audit.db` (relocatable via
  `PROXY_AIOPS_HOME`).
- **Secure by default**: with no `~/.proxy-aiops/rules.yaml`, high-risk ops
  (`delete_config_path`, `load_config`) are denied unless
  `PROXY_AUDIT_APPROVED_BY` names an approver (set `PROXY_AUDIT_RATIONALE`
  too). `proxy-aiops init` seeds a starter rules.yaml; an operator-authored
  rules file is honoured as-is.
- Writes support `--dry-run` and double confirmation at the CLI; CLI writes
  execute through the same governed tools, so they are audited + undo-recorded.
- Reversible writes capture the real fetched before-state and record an
  inverse descriptor that replays against the tool's own signature.
- Traefik targets accept no writes at all — the support matrix teaches you to
  edit the provider source instead.

## References

- `references/capabilities.md` — full tool + platform + API-path reference
- `references/cli-reference.md` — CLI command reference
- `references/setup-guide.md` — onboarding, credentials, and connectivity
