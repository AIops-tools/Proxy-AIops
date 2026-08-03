# Live verification status

This document records what has and has not been validated against real reverse
proxies, so the maturity claim is auditable rather than a vibe.

## Already live-verified ✅ — all three platforms (2026-07-20)

Traefik 3.2.5, Caddy 2, and HAProxy 3.0.25 (Data Plane API v3.0.21).

- `doctor` against both live endpoints (unauthenticated admin APIs, as the tool
  documents for traefik/caddy).
- Reads cross-checked against each proxy's own API: `overview`, `routes list`,
  `services list`, and the three analyses (`analyze health/errors/conflicts`).
  Router counts and names matched `/api/http/routers` (Traefik) and
  `/config/apps/http/servers/...` (Caddy) exactly.

**A real bug was found and fixed by this run**: route priority is an int64 in
Traefik, and routing it through the float helper both rendered it in scientific
notation (`9.223372036854776e+18`) *and* lost precision. Two routers with
**different** priorities (…806 and …805) displayed as the **same** value. Route
priority decides matching order, so collapsing distinct values actively misleads
anyone debugging route precedence. Integer quantities now use an exact `as_int`
that never round-trips through float64.

## Not yet live-verified ⚠️

- **HAProxy is now verified** — and that run found the branch was **entirely
  broken**: every path was hardcoded to Data Plane API v2, but HAProxy 3.x serves
  only `/v3`, so the first probe 404'd. The connection now detects the API
  generation and supports both. Still untested there: runtime server state changes
  and the stats-derived traffic analyses under real load. **Attempted again on
  2026-08-03 and blocked for a measured reason, not for lack of trying**: the
  Data Plane API will not start under HAProxy's `program` directive in the
  official 3.0 image. That directive does no shell quoting, so
  `--reload-cmd "kill -SIGUSR2 1"` arrives as separate argv entries and the API
  exits with *"the custom reload strategy requires these options to be set:
  ReloadCmd, RestartCmd"*; a wrapper script and its own YAML config both failed
  the same way, and HAProxy's `exit-on-failure` then tears down the container.
  Running the Data Plane API as its **own container** against a shared config
  volume is the next thing to try.
- **TLS / certificate expiry** (`certs`) against real certificates — both verified
  instances served plaintext on a lab port.
- ~~**Guarded config writes** (`config set/delete`) and their undo paths.~~
  **Closed 2026-08-03 against a real Caddy 2, and it found the undo of a deleted
  array element was un-replayable.** `config set` → the upstream list really
  changed → `undo apply` → the prior list restored. `config delete` of a route →
  the route really disappeared → `undo apply` **failed**: the inverse re-created
  the subtree with POST, and Caddy rejects that at an index past the (now
  shorter) array. Two things were wrong — the inverse needed PUT (Caddy's
  insert-at-index), and the pre-read had to go, because Caddy answers a GET of
  an out-of-range index with **400, not 404**, so it re-raised before the write
  reached the wire. Re-verified on the hardest case: deleting the **last**
  element and restoring it into an empty array, back at its original index.
- Traffic/error-rate analyses against a proxy under real load (the lab instances
  served no traffic, so `analyze errors` had nothing to rank).
