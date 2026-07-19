# Live verification — Traefik / Caddy / HAProxy

`proxy-aiops` is exercised by a **mock-only** test suite (`uv run pytest`, no real
proxy). It has **not** yet been validated end-to-end against a live Traefik, Caddy, or
HAProxy instance. This document says exactly what the mock suite already guarantees, and
what a live run has to prove before anyone may describe this tool as verified against a
real proxy.

It is deliberately checklist-shaped so the result is reproducible and auditable — not a
subjective "seems fine".

## What the mock suite already guarantees

- Every module imports; the CLI builds; **all 28 MCP tools** carry the `@governed_tool`
  harness marker (`tests/test_smoke.py`, which also asserts the tool count and that
  `__version__` matches `pyproject.toml`).
- The four flagship analyses (`backend_health_rca`, `cert_expiry_sweep`,
  `error_rate_rca`, `route_conflict_analysis`) are unit-tested against synthetic
  telemetry: the health-check failure classes (connection refused / L4 timeout / TLS /
  L7 / DNS / maint) each map to the right cause, the dominant-status-code mapping
  (502/503/504/500) fires correctly, expiry bucketing respects the warn/critical
  thresholds, and shadowed / dead / looping routes are each detected with the covering
  route or missing service named.
- The **platform registry** resolves each tool to the correct Traefik (`/api/...` plus
  the `/metrics` text format), Caddy (admin API), and HAProxy (Data Plane API v2
  `/v2/...`, HTTP Basic) request shape.
- The **support matrix** is asserted to raise a *teaching error* — never a silent no-op —
  for operations a platform cannot perform (Traefik writes → edit its providers; Caddy
  error counters → access logs; HAProxy certs → the `.pem` pipeline).
- Reversible writes record a faithful **inverse** undo descriptor built from a fetched
  before-state (`set_config_value` restores the prior subtree, or deletes a path it
  created; `delete_config_path` re-creates the captured subtree; `set_server_state` /
  `set_server_weight` restore the prior admin state / weight), and the descriptor's
  params replay against the tool's own signature.
- Governance persistence is tested against a real on-disk SQLite audit DB: calls land as
  rows, failures record `status=error` and no undo, and the secure-by-default approver
  gate refuses high-risk ops when no `rules.yaml` exists.

What it does **not** guarantee: that the concrete REST paths, JSON field names, and the
Traefik `/metrics` text format match a real build of each proxy. Those are modelled from
each project's public API documentation and are the **largest verification debt in this
repo** — the metrics-text parsing in particular is brittle against format changes.

## Prerequisites for a live run

All three proxies are free and self-hostable; a single compose file gets you the whole
matrix in an evening:

- **Traefik** — expose the API (`--api=true`, a router on `api@internal`) **and**
  metrics (`--metrics.prometheus=true`), since `traffic_stats` / `error_counters` read
  the metrics endpoint, not the API.
- **Caddy** — the admin API on `localhost:2019` (its default). This is the target that
  carries the config write surface, so it is where sections 4–6 actually run.
- **HAProxy** — plus the **Data Plane API v2** (`dataplaneapi`), with a Basic-auth user.
  This is the target that carries the runtime server writes.

Behind each proxy, put **at least two throwaway backends** so you can drain one, and a
deliberately broken one (a closed port, and a TLS-mismatched one) so the health RCA has
real failures to classify. Use a **lab edge** — the checklist drains servers and rewrites
config, and `load_config` replaces a Caddy instance's *entire* configuration.

```bash
uv tool install proxy-aiops
proxy-aiops init      # wizard: pick platform, optional encrypted secret
```

Record the exact versions tested (e.g. "Traefik 3.2, Caddy 2.8, HAProxy 3.0 +
dataplaneapi 3.0") — a tick is only meaningful with the build it was ticked against.

## Verification checklist

Tick every box, **per platform**. A box that cannot be ticked is a verification gap —
record it, do not silently pass.

### 1. Connectivity (the fastest live gate)
- [ ] `proxy-aiops doctor` → green on each target: config parsed, secret store unlocks
      (HAProxy), and a real health/info probe returns.
- [ ] `proxy-aiops doctor --skip-auth` → passes offline (config/secret checks only).
- [ ] One config spanning **all three** platforms works with `--target` switching — this
      mixed-edge case is the reason the platform registry exists.

### 2. Reads return real, well-shaped data
- [ ] `proxy-aiops overview` → real platform/version, route and service counts, and
      upstream up/down totals matching each proxy's own dashboard.
- [ ] `proxy-aiops routes list` → the real routers/routes/frontends with correct hosts,
      paths, priority, and TLS flags.
- [ ] `proxy-aiops routes show <name>` → one route's full detail.
- [ ] `proxy-aiops routes find <host> --path /api` → returns the route that **actually**
      serves that request. Verify by curling the host and comparing which backend
      answers — this is the single most valuable correctness check in the file.
- [ ] `proxy-aiops services list` / `services show <name>` / `services upstreams <name>`
      → real servers with correct address, status (up/down/maint/drain), check info, and
      weight.
- [ ] MCP `list_middlewares` → real Traefik middlewares; a **teaching error** on Caddy
      and HAProxy rather than an empty list.
- [ ] MCP `traffic_stats` / `error_counters` → numbers that move when you generate load,
      and that agree with the proxy's own stats page. On Traefik confirm the
      **`/metrics` text parsing** survives the real format; on Caddy confirm the
      teaching error (counters come from access logs there).
- [ ] `proxy-aiops config snapshot` / `config search <needle>` / `config get <path>` →
      the real Caddy config tree; teaching errors on Traefik/HAProxy.
- [ ] `proxy-aiops certs` → the real TLS domain inventory; teaching note on HAProxy.

### 3. The analyses are right, not just non-crashing
- [ ] Stop one backend; `proxy-aiops analyze health` names that server and classifies the
      failure as connection-refused (not a generic "down").
- [ ] Point a backend at a TLS-mismatched endpoint; the class comes back TLS, not L4.
- [ ] Drive real 503s (all servers of one service down) and real 504s (a backend that
      sleeps past the timeout); `proxy-aiops analyze errors --rate 5 --min-requests 100`
      flags the right service and maps each **dominant code to the correct cause**.
- [ ] Confirm `--min-requests` actually suppresses a low-traffic service with a single
      failure.
- [ ] `proxy-aiops certs --sweep --warn-days 30 --critical-days 7` → live-probed expiry
      dates match `openssl s_client` for the same domain, and bucketing is correct
      either side of both thresholds. Repeat with `--port` on a non-443 listener.
- [ ] Create a genuinely shadowed route (a broad higher-priority route above a specific
      one) and a dead route (pointing at a nonexistent service);
      `proxy-aiops analyze conflicts` names the covering route and the missing service.

### 4. A reversible write + its undo — Caddy config
- [ ] `proxy-aiops config set <path> '<json>' --dry-run` → prints the write, changes
      nothing (confirm with `config get <path>`).
- [ ] `proxy-aiops config set <path> '<json>'` → the config actually changes, the result
      carries an `_undo_id`, and a row lands in `~/.proxy-aiops/audit.db`.
- [ ] `proxy-aiops undo list` shows it; `undo apply <id>` restores the **prior subtree
      byte-for-byte** (compare against the `config snapshot` you took first).
- [ ] `config set` on a path that did **not** previously exist, then `undo apply` → the
      created path is *deleted*, not reset to a guessed value. This asymmetric case is
      where a naive undo silently leaves debris.
- [ ] MCP `delete_config_path` then `undo apply` → the deleted subtree is re-created
      intact.

### 5. A reversible write + its undo — HAProxy runtime
- [ ] `proxy-aiops server state <backend> <server> drain --dry-run` → nothing changes.
- [ ] `proxy-aiops server state <backend> <server> drain` → the server actually drains
      (confirm in the HAProxy stats page), in-flight connections finish, and an
      `_undo_id` is returned.
- [ ] `undo apply <id>` restores the **prior** state — verify starting from a server that
      was already in `maint`, where a naive undo would wrongly set `ready`.
- [ ] `proxy-aiops server weight <backend> <server> 50` then `undo apply` → the prior
      weight returns, not a default.
- [ ] The same commands against a Traefik or Caddy target raise a **teaching error**
      naming the right mechanism.

### 6. Governance actually gates
- [ ] With no `~/.proxy-aiops/rules.yaml`, `delete_config_path` and `load_config` are
      **refused** unless `PROXY_AUDIT_APPROVED_BY` is set (secure-by-default); with it
      plus `PROXY_AUDIT_RATIONALE`, both appear in the audit row.
- [ ] `load_config` on a lab Caddy replaces the whole config, and `undo apply` restores
      the snapshotted original — then confirm traffic actually flows again, since a
      restored-but-not-reloaded config would still be an outage.
- [ ] A tight poll loop trips the runaway budget guard rather than hammering the API.
- [ ] A failed call (nonexistent route name) is audited `status=error` with no undo.

### 7. Cleanup
- [ ] Return every drained server to its prior state, restore every weight, and undo
      every config edit.
- [ ] `proxy-aiops overview` and `proxy-aiops config snapshot` match the baseline you
      captured before starting.
- [ ] Skim `~/.proxy-aiops/audit.db` — every write is there with the right risk tier.

## Criteria to consider it live-verified

All of the following must hold:

1. Every box above is ticked against **all three** platforms, with the exact builds
   recorded (e.g. "Traefik 3.2 + Caddy 2.8 + HAProxy 3.0/dataplaneapi 3.0").
2. Every REST-path, field-shape, or metrics-format mismatch found is **fixed and covered
   by a regression test**, so the mock suite would now catch it.
3. Sections 4 and 5 passed, including the asymmetric cases (creating a config path then
   undoing it; undoing a drain on a server that started in `maint`). Recording an undo
   descriptor is not the same as the undo working, and this product line has shipped
   broken undo pairs before.
4. Every support-matrix teaching error was confirmed **live** — a real unsupported call
   must teach, not silently succeed or silently do nothing.
5. The run is written up in the release notes / product-line memory with the date and
   package version, matching how the line records its other live-verified tools.

Until then, this repo says only what is true: mock-validated, live-unverified. Claiming
otherwise would break that promise.

## Notes for maintainers

- `proxy-aiops doctor` is the single fastest live entry point; start there.
- Weight the run toward **Traefik `/metrics` text parsing** and the **Caddy config-path
  semantics** — those are the two places the mocks are least able to model, and both
  change between releases.
- Managed cloud load balancers are explicitly out of scope; do not infer support for them
  from a self-hosted run.
- Add this tool's result to the product-line verification ledger once green, so the
  central "verification debt" list stays accurate.
