"""Test isolation: redirect the governance harness state at a tmp dir.

Governed-tool calls write an audit row (and, for reversible writes, an undo
token). This autouse fixture points ``PROXY_AIOPS_HOME`` at a throwaway
directory and resets the harness singletons so nothing touches the real
``~/.proxy-aiops`` during tests.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_harness_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("proxy-home")
    monkeypatch.setenv("PROXY_AIOPS_HOME", str(home))

    import proxy_aiops.governance.audit as audit
    import proxy_aiops.governance.undo as undo

    monkeypatch.setattr(audit, "_engine", None, raising=False)
    monkeypatch.setattr(audit, "_DEFAULT_DB", None, raising=False)
    monkeypatch.setattr(undo, "_store", None, raising=False)
    yield


@pytest.fixture(autouse=True)
def _default_approver(monkeypatch):
    """The approver is an optional audit annotation now, not a gate: record a
    synthetic one globally so audit rows carry a who; the governance-persistence
    tests remove it to prove a high-risk write runs without one."""
    monkeypatch.setenv("PROXY_AUDIT_APPROVED_BY", "pytest")
