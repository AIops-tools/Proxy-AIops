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
  generation and supports both.
  - ~~The Data Plane API would not start under HAProxy's `program` directive~~ —
    **unblocked 2026-08-10 by running it as its own container**, which was the
    recorded next thing to try. Recipe: share the config volume, share HAProxy's
    PID namespace (`--pid=container:<haproxy>`) so `--reload-cmd "kill -SIGUSR2 1"`
    reaches the master, and pass the master socket with `--master-runtime`.
    Two details cost time and are worth writing down: the API **exits 1 silently
    when started with plain `-d`** and needs `-i` (stdin open) to stay up, and it
    refuses to start at all unless *both* `--reload-cmd` and `--restart-cmd` are
    given.
  - ~~Runtime server state changes~~ — **closed 2026-08-10, and they had never
    worked**: `/v3/services/haproxy/runtime/servers/{name}?backend=...` is the v2
    shape, and v3 moved the backend into the path. Drain now really drains (every
    subsequent request moved to the other server) and `undo apply` restores
    `ready`; weight is verified on a build that supports it, and refused on one
    that does not (see below).
  - ~~Stats-derived traffic analyses under real load~~ — **closed 2026-08-10**
    against genuine traffic (137 requests, 37 real 5xx from a backend returning
    500/503). `analyze errors` matched the server's own counters exactly
    (37/137 = 27.01%) and ranked correctly; the same run on Traefik 3.3 matched
    its Prometheus counters exactly (27/73 = 36.99%) and named the dominant code.
    That comparison is also what exposed the counts being rendered as floats.
- **A Data Plane API build may not support a runtime weight at all.** 3.0.22
  represents a runtime server without a weight field and answers a PUT carrying
  one with 200 while changing nothing; 3.4.1 exposes `weight`/`uweight` and
  applies it. Both measured. The tool now refuses on the former rather than
  reporting a change that did not happen, and verifies the value stuck on the
  latter.
- **The v2 downgrade path: reshaping verified over the wire; a real v2 server is
  unobtainable.** Every haproxytech image carries a Data Plane API 3.x that
  serves **only** `/v3` — measured on `haproxy-alpine` **2.2 (DPA 3.2.0)**, 2.4
  (3.3.5), 2.6 (3.4.1) and 3.0 (3.0.22); on the 2.2 build `/v1/info` and
  `/v2/info` both 404 while `/v3/info` answers 200. There is no
  `haproxytech/dataplaneapi` image at all. So a real v2 Data Plane API cannot be
  stood up from containers, and the fallback is only reachable by an install that
  pinned an old dataplaneapi *binary*.
  What **is** now verified live: the generation probe (correctly choosing `/v3` on two
  different builds), and — against a **v2-shaped stub** that 404s `/v3/info` and
  serves the v2 routes — the downgrade actually reshapes the path. The stub saw
  `GET /v3/info` (404) → `GET /v2/info` → `GET /v2/services/haproxy/runtime/servers/app2?backend=be_app`
  → `PUT` the same, i.e. the backend really moves back into a query parameter.
  The stub cannot attest to a real v2 server's *semantics*, only to the tool's
  own behaviour, which previously had nothing but unit tests behind it.
- ~~**TLS / certificate expiry** (`certs`) against real certificates~~ —
  **closed 2026-08-10.** `certs --sweep` against a Traefik serving a real
  certificate read the expiry from a live handshake and got it exactly right:
  14.0 days against a ground truth of 13.99, correct `notAfter`, bucketed
  `warning`; re-run against a certificate expiring in one day it bucketed
  `critical`. The **expired** bucket is closed too: `openssl ca` *can* back-date
  with explicit `-startdate`/`-enddate` (it is `req -x509` that cannot), so a
  certificate that expired three days earlier was signed by a throwaway CA and
  served through Traefik. The sweep read `daysToExpiry: -3.0` against a ground
  truth of -3.0 and bucketed it `expired`. All three buckets are now verified
  against a live handshake with exact day arithmetic. `certs` on HAProxy correctly reports
  itself unsupported (certificates are `.pem` files bound in haproxy.cfg).
- **The tool's own transport over TLS is verified** (2026-08-10): with
  `verify_ssl: true` against a self-signed Data Plane API the connection fails
  with `CERTIFICATE_VERIFY_FAILED` and `doctor` exits non-zero; with verification
  off, reads, a governed `server state` write and its `undo apply` all completed
  over HTTPS.
