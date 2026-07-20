# Release notes — proxy-aiops 0.3.0

Previous release: 0.2.0.

## Fixed: route priority lost precision and collapsed distinct values

Traefik route priority is an int64. Routing it through the float helper rendered it
in scientific notation (`9.223372036854776e+18`) *and* lost precision, because a
float64 cannot represent values near 2**63 exactly.

The practical consequence, seen on a live Traefik: two routers with **different**
priorities (…806 and …805) displayed as the **same** number. Route priority decides
matching order, so this actively misleads anyone debugging which route wins.

Integer quantities — route priority, server weight, request totals, session counts —
now use an exact `as_int` that returns an existing `int` untouched instead of
round-tripping it through float64. Genuinely fractional values are unchanged.

If you parse these fields, the JSON changes from `9.223372036854776e+18` to
`9223372036854775806`, and counters from `12.0` to `12`.

## Live-verified

Against **Traefik 3.2.5** and **Caddy 2**: reads cross-checked against each proxy's
own API, plus the three analyses. See [docs/VERIFICATION.md](docs/VERIFICATION.md) —
**HAProxy remains unverified** (its Data Plane API setup was not built for this
round) and is now the largest gap here.
