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
  and the stats-derived traffic analyses under real load.
- **TLS / certificate expiry** (`certs`) against real certificates — both verified
  instances served plaintext on a lab port.
- **Guarded config writes** (`config set/delete`) and their undo paths.
- Traffic/error-rate analyses against a proxy under real load (the lab instances
  served no traffic, so `analyze errors` had nothing to rank).
