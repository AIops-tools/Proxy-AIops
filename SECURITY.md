# Security Policy

## Disclaimer

Community-maintained open-source project. **Not affiliated with, endorsed by, or
sponsored by Traefik Labs, the Caddy project, HAProxy Technologies, or the
HAProxy project.** Product and trademark names (Traefik, Caddy, HAProxy) belong
to their owners. Source is auditable under the MIT license.

## Reporting Vulnerabilities

Report privately via a GitHub Security Advisory on
[github.com/AIops-tools/Proxy-AIops](https://github.com/AIops-tools/Proxy-AIops/security/advisories)
or email zhouwei008@gmail.com. Please do not open public issues for security
reports.

## Security Design

### Credential Management
- Per-target secrets — the HAProxy Data Plane API **password** (paired with the
  `username` for HTTP Basic auth) or an optional Basic-auth credential in front
  of Traefik/Caddy — live **encrypted** in `~/.proxy-aiops/secrets.enc`
  (Fernet/AES-128 + scrypt-derived key; chmod 600), never in `config.yaml` and
  never in source. The master password is never stored — only a per-store
  random salt and the ciphertext are on disk.
- Traefik and Caddy commonly run unauthenticated on localhost: for those
  platforms the secret is **optional** — no store entry simply means no auth
  header is sent. HAProxy's credential is required.
- A legacy plaintext env var `PROXY_<TARGET_NAME_UPPER>_SECRET` is still
  honoured as a fallback with a deprecation warning (migrate with
  `proxy-aiops secret migrate`).
- The secret is held only in memory and never logged or echoed. It is presented
  as HTTP Basic auth at request time; the config file holds only platform,
  base_url, username, and TLS settings.

### Governed Operations
Every MCP tool runs through the bundled `@governed_tool` harness
(`proxy_aiops.governance`):
- **Audit** — every call logged to a local SQLite DB under `~/.proxy-aiops/`
  (relocatable via `PROXY_AIOPS_HOME`), agent-attributed, secret-redacted.
- **Token/runaway budget** — hard ceilings (`PROXY_MAX_TOOL_CALLS` /
  `PROXY_MAX_TOOL_SECONDS`) plus an on-by-default guard that trips a tight
  poll/retry loop, preventing unbounded API consumption.
- **Graduated risk tiers** — `~/.proxy-aiops/rules.yaml` `risk_tiers` gate
  writes; **secure by default**: with no rules.yaml, high-risk operations
  require a recorded approver (`PROXY_AUDIT_APPROVED_BY`).
- **Undo-token recording** — reversible writes capture the BEFORE state (via a
  real GET) and record an inverse descriptor (config subtree restore, prior
  admin state / weight) whose params match the target tool's signature, so it
  replays as-is.

### State-Changing Operations
The caddy config writes and haproxy runtime-server writes are the only
state-changing tools. `delete_config_path` and `load_config` (full config
replace) are `risk_level=high`, accept a `dry_run` preview, and (under
`risk_tiers`) require a recorded approver (`PROXY_AUDIT_APPROVED_BY` +
`PROXY_AUDIT_RATIONALE`). `set_config_value`, `set_server_state` and
`set_server_weight` are `risk_level=medium`; all capture before-state and
record an undo token. Traefik targets accept **no writes at all** — the support
matrix raises a teaching error pointing at its providers.

### Path-Traversal Hardening
Every value substituted into a URL path is percent-encoded centrally in
`Platform.path()`; Caddy config paths are additionally encoded per segment with
dot-segments (`.` / `..`) rejected outright, so an agent-supplied path can
never escape the `/config/` tree.

### SSL/TLS Verification
`verify_ssl` defaults to true; disable only for self-signed lab certificates.
The certificate expiry probe intentionally skips chain verification (it reads
expiry off whatever leaf is served, including an expired one) — it never trusts
or transmits anything over that connection.

### Output Sanitisation
All proxy-returned text (router names, upstream addresses, config values,
check statuses) is passed through a `sanitize()` truncate + control-character
strip before reaching the agent.

### Network Scope
No webhooks, no telemetry. Outbound calls are limited to the configured
Traefik / Caddy / HAProxy API endpoints, plus — only when an operator invokes
the cert sweep with probing — a bounded TLS handshake to the inventoried
domains (max 25, 5s timeout each). No post-install scripts or background
services.

## Static Analysis

```bash
uvx bandit -r proxy_aiops/ mcp_server/
uv run ruff check .
```

## Supported Versions

The latest released version receives security fixes. This is a preview (0.x);
pin a version in production.
