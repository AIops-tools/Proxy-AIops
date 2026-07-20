"""HAProxy Data Plane API generation must be detected, not assumed.

Regression from live verification against HAProxy 3.0 / dataplaneapi v3.0.21,
which serves ONLY /v3 — every /v2 path 404s. The registry previously hardcoded
/v2, so the entire HAProxy branch was unusable against any current HAProxy.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from proxy_aiops.connection import ProxyConnection


def _conn(probe_status: int) -> ProxyConnection:
    c = ProxyConnection.__new__(ProxyConnection)
    client = MagicMock(name="client")
    client.request.return_value = MagicMock(status_code=probe_status)
    c._client = client
    c._dpapi_prefix = None
    return c


@pytest.mark.unit
def test_v3_server_keeps_v3_paths():
    c = _conn(200)
    assert c._haproxy_path("/v3/info") == "/v3/info"
    assert c._haproxy_path("/v3/services/haproxy/configuration/backends") == (
        "/v3/services/haproxy/configuration/backends"
    )


@pytest.mark.unit
def test_v2_server_gets_paths_rewritten():
    c = _conn(404)  # /v3/info missing => an older Data Plane API
    assert c._haproxy_path("/v3/info") == "/v2/info"
    assert c._haproxy_path("/v3/services/haproxy/configuration/backends") == (
        "/v2/services/haproxy/configuration/backends"
    )


@pytest.mark.unit
def test_probe_is_cached_not_repeated_per_request():
    c = _conn(200)
    for _ in range(5):
        c._haproxy_path("/v3/info")
    assert c._client.request.call_count == 1


@pytest.mark.unit
def test_non_versioned_paths_are_untouched():
    c = _conn(200)
    assert c._haproxy_path("/metrics") == "/metrics"
    assert c._client.request.call_count == 0, "must not probe for unrelated paths"
