# Release notes — proxy-aiops 0.3.1

Previous release: 0.3.0.

## Fixed: the HAProxy branch could not talk to any current HAProxy

Every HAProxy path was hardcoded to Data Plane API **v2**. HAProxy 3.x ships
Data Plane API v3, which serves **only** `/v3` — every `/v2` path returns 404.
So the whole HAProxy platform branch was unusable against a current HAProxy,
failing at the very first probe.

The path registry now holds v3 paths, and the connection probes `/v3/info` once
per connection and rewrites the prefix to `/v2` when it sees an older server.
Both generations work; the probe is cached, so a v3 server costs one extra
request per connection and a v2 server two.

## Live-verified: HAProxy

Verified against **HAProxy 3.0.25 with Data Plane API v3.0.21**: `doctor`, plus
reads cross-checked against the Data Plane API itself — the configured backend
and its two servers were reported accurately, including `serversUp: 0` for
servers pointed at closed ports.

With Traefik 3.2.5 and Caddy 2 verified in 0.3.0, **all three platforms are now
live-verified**. See [docs/VERIFICATION.md](docs/VERIFICATION.md) — guarded
config writes and TLS/certificate expiry against real certificates remain open.
