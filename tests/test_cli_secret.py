"""CLI ``secret`` subcommands over a fake in-memory store.

The encrypted store, master-password resolution and getpass prompts are all
monkeypatched, so these tests drive the command wiring (set / list / rm /
migrate / rotate-password) without deriving a scrypt key or touching
``~/.proxy-aiops``. A secret value is never asserted to appear in output.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from proxy_aiops.cli import app
from proxy_aiops.cli import secret as mod

runner = CliRunner()


class _FakeStore:
    """Mutable-shared fake: unlock() returns the same instance so writes stick."""

    def __init__(self, data=None):
        self._data = dict(data or {})
        self.rotated_to = None

    @classmethod
    def make(cls, data=None):
        store = cls(data)
        return lambda password=None: store

    def set(self, name, value):
        self._data[name] = value
        return self

    def names(self):
        return tuple(self._data)

    def delete(self, name):
        self._data.pop(name, None)
        return self

    def with_password(self, new_pw):
        self.rotated_to = new_pw
        return self


@pytest.mark.unit
def test_secret_set_with_value_option(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(mod, "resolve_master_password", lambda *, confirm_if_new=False: "pw")
    monkeypatch.setattr(mod.SecretStore, "unlock", lambda password=None: store)
    result = runner.invoke(app, ["secret", "set", "lb1", "--value", "s3cr3t"])
    assert result.exit_code == 0
    assert "Stored encrypted API key for 'lb1'" in result.output
    assert store.names() == ("lb1",)
    assert "s3cr3t" not in result.output  # value never echoed


@pytest.mark.unit
def test_secret_set_prompts_hidden_when_value_omitted(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(mod, "resolve_master_password", lambda *, confirm_if_new=False: "pw")
    monkeypatch.setattr(mod.SecretStore, "unlock", lambda password=None: store)
    monkeypatch.setattr(mod.getpass, "getpass", lambda prompt="": "typed-secret")
    result = runner.invoke(app, ["secret", "set", "lb1"])
    assert result.exit_code == 0
    assert store._data["lb1"] == "typed-secret"


@pytest.mark.unit
def test_secret_list_shows_names_and_permission_warning(monkeypatch):
    store = _FakeStore({"lb1": "x", "edge1": "y"})
    monkeypatch.setattr(mod.SecretStore, "unlock", lambda password=None: store)
    monkeypatch.setattr(mod, "check_permissions", lambda: "secrets.enc should be 600")
    result = runner.invoke(app, ["secret", "list"])
    assert result.exit_code == 0
    assert "lb1" in result.output and "edge1" in result.output
    assert "should be 600" in result.output


@pytest.mark.unit
def test_secret_list_empty_hints_how_to_add(monkeypatch):
    monkeypatch.setattr(mod.SecretStore, "unlock", lambda password=None: _FakeStore())
    result = runner.invoke(app, ["secret", "list"])
    assert result.exit_code == 0
    assert "No secrets stored yet" in result.output


@pytest.mark.unit
def test_secret_rm(monkeypatch):
    store = _FakeStore({"lb1": "x"})
    monkeypatch.setattr(mod.SecretStore, "unlock", lambda password=None: store)
    result = runner.invoke(app, ["secret", "rm", "lb1"])
    assert result.exit_code == 0
    assert "Deleted API key for 'lb1'" in result.output
    assert store.names() == ()


@pytest.mark.unit
def test_secret_migrate_imports(monkeypatch):
    monkeypatch.setattr(mod, "resolve_master_password", lambda *, confirm_if_new=False: "pw")
    monkeypatch.setattr(mod, "migrate_legacy_env", lambda prefix, suffix, pw: ["lb1", "edge1"])
    result = runner.invoke(app, ["secret", "migrate"])
    assert result.exit_code == 0
    assert "Imported 2 secret(s)" in result.output
    assert "lb1" in result.output and ".env.migrated" in result.output


@pytest.mark.unit
def test_secret_migrate_nothing_to_do(monkeypatch):
    monkeypatch.setattr(mod, "resolve_master_password", lambda *, confirm_if_new=False: "pw")
    monkeypatch.setattr(mod, "migrate_legacy_env", lambda prefix, suffix, pw: [])
    result = runner.invoke(app, ["secret", "migrate"])
    assert result.exit_code == 0
    assert "Nothing to migrate" in result.output


@pytest.mark.unit
def test_secret_rotate_password_success(monkeypatch):
    store = _FakeStore({"lb1": "x"})
    monkeypatch.setattr(mod.SecretStore, "unlock", lambda password=None: store)
    monkeypatch.setattr(mod.getpass, "getpass", lambda prompt="": "new-master")
    result = runner.invoke(app, ["secret", "rotate-password"])
    assert result.exit_code == 0
    assert store.rotated_to == "new-master"
    assert "Master password rotated" in result.output


@pytest.mark.unit
def test_secret_rotate_password_mismatch_aborts(monkeypatch):
    store = _FakeStore({"lb1": "x"})
    monkeypatch.setattr(mod.SecretStore, "unlock", lambda password=None: store)
    answers = iter(["first-pw", "different-pw"])
    monkeypatch.setattr(mod.getpass, "getpass", lambda prompt="": next(answers))
    result = runner.invoke(app, ["secret", "rotate-password"])
    assert result.exit_code == 1
    assert "did not match" in result.output
    assert store.rotated_to is None
