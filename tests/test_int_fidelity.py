"""Integer quantities must stay exact integers.

Regression from live verification against Traefik 3.2: route priority is an
int64, and routing it through the float helper both rendered it in scientific
notation and lost precision — two routers with *different* priorities
(…806 and …805) both displayed as 9.223372036854776e+18. Route priority decides
matching order, so collapsing distinct values is actively misleading.
"""

from __future__ import annotations

import pytest

from proxy_aiops.ops._util import as_int, num


@pytest.mark.unit
def test_as_int_is_exact_for_large_int64_values():
    hi = 9223372036854775806  # 2**63 - 2, as Traefik reports for api@internal
    lo = 9223372036854775805
    assert as_int(hi) == hi
    assert as_int(lo) == lo
    assert as_int(hi) != as_int(lo), "distinct priorities must not collapse"
    # The float helper is exactly what could not do this:
    assert num(hi) == num(lo), "documents why num() is wrong for priorities"


@pytest.mark.unit
def test_as_int_types_and_edges():
    assert isinstance(as_int("42"), int) and as_int("42") == 42
    assert as_int(3.9) == 3
    assert as_int(None) == 0
    assert as_int("nonsense") == 0
    assert as_int(True) == 0, "a bool is not a quantity"
