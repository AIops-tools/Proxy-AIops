"""Tests for the ``proxy-aiops init`` onboarding wizard.

The wizard is driven end-to-end through Typer's CliRunner with every path
(config.yaml, secrets.enc, rules.yaml) isolated under tmp_path. The master
password comes from PROXY_AIOPS_MASTER_PASSWORD (the non-interactive path)
and the hidden credential prompt is patched at the getpass boundary.
"""

from __future__ import annotations

import getpass as getpass_mod

import pytest
import yaml
from typer.testing import CliRunner

import proxy_aiops.cli.init as init_mod
import proxy_aiops.config as config_mod
import proxy_aiops.doctor as doctor_mod
import proxy_aiops.secretstore as ss

MASTER_PW = "init-master-pw"
API_SECRET = "proxy-credential-0123"

# Wizard answers (traefik): name, accept platform default (traefik), accept
# base-URL default, accept TLS-verify default (True), empty basic-auth
# username, no second target, decline the trailing doctor run. The credential
# itself comes via getpass.
WIZARD_INPUT = "edge1\n\n\n\n\nn\nn\n"


@pytest.fixture
def init_home(tmp_path, monkeypatch):
    """Isolate config + secret store + governance home under tmp_path."""
    config_file = tmp_path / "config.yaml"
    secrets_file = tmp_path / "secrets.enc"
    monkeypatch.setenv("PROXY_AIOPS_HOME", str(tmp_path))
    monkeypatch.setenv("PROXY_AIOPS_MASTER_PASSWORD", MASTER_PW)
    monkeypatch.setattr(init_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(init_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", secrets_file)
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    # The hidden credential prompt bypasses CliRunner stdin.
    monkeypatch.setattr(getpass_mod, "getpass", lambda prompt="": API_SECRET)
    return tmp_path


def _run_init(input_text: str = WIZARD_INPUT):
    from proxy_aiops.cli import app

    return CliRunner().invoke(app, ["init"], input=input_text)


@pytest.mark.unit
def test_init_writes_config_with_defaults(init_home):
    result = _run_init()
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"] == [
        {
            "name": "edge1",
            "platform": "traefik",
            "base_url": "http://localhost:8080",
            "username": "",
            "verify_ssl": True,  # accepted TLS confirm default=True must land
        }
    ]


@pytest.mark.unit
def test_init_tls_confirm_can_be_declined_for_lab_certs(init_home):
    result = _run_init("edge1\n\n\nn\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["verify_ssl"] is False


@pytest.mark.unit
def test_init_haproxy_branch_prompts_username_and_requires_secret(init_home):
    result = _run_init("lb1\nhaproxy\n\n\ndpapi\nn\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["platform"] == "haproxy"
    assert raw["targets"][0]["base_url"] == "http://localhost:5555"
    assert raw["targets"][0]["username"] == "dpapi"
    assert ss.SecretStore.unlock(MASTER_PW).get("lb1") == API_SECRET


@pytest.mark.unit
def test_init_caddy_default_base_url(init_home):
    result = _run_init("c1\ncaddy\n\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["base_url"] == "http://localhost:2019"


@pytest.mark.unit
def test_init_optional_secret_left_empty_stores_nothing(init_home, monkeypatch):
    """traefik/caddy secret is optional — an empty credential must not create
    a store entry (the target is treated as unauthenticated)."""
    monkeypatch.setattr(getpass_mod, "getpass", lambda prompt="": "")
    result = _run_init()
    assert result.exit_code == 0, result.output
    assert "no secret (unauthenticated)" in result.output
    store = ss.SecretStore.unlock(MASTER_PW)
    assert "edge1" not in store.names()


@pytest.mark.unit
def test_init_rejects_unknown_platform_then_reprompts(init_home):
    result = _run_init("edge1\nnginx\nedge1\n\n\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    assert "Platform must be 'traefik', 'caddy' or 'haproxy'." in result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert [t["name"] for t in raw["targets"]] == ["edge1"]


@pytest.mark.unit
def test_init_stores_secret_encrypted_not_in_config(init_home):
    result = _run_init("lb1\nhaproxy\n\n\ndpapi\nn\nn\n")
    assert result.exit_code == 0, result.output
    # Credential is readable back through the secret store API...
    assert ss.SecretStore.unlock(MASTER_PW).get("lb1") == API_SECRET
    # ...and never lands in plaintext in config.yaml or secrets.enc.
    assert API_SECRET not in (init_home / "config.yaml").read_text("utf-8")
    assert API_SECRET not in (init_home / "secrets.enc").read_text("utf-8")


@pytest.mark.unit
def test_init_seeds_default_rules_with_dual_control_tier(init_home):
    result = _run_init()
    assert result.exit_code == 0, result.output
    rules = yaml.safe_load((init_home / "rules.yaml").read_text("utf-8"))
    tiers = {r["name"]: r for r in rules["risk_tiers"]}
    assert "high-risk-requires-approver" in tiers
    assert tiers["high-risk-requires-approver"]["tier"] == "dual"
    assert tiers["high-risk-requires-approver"]["min_risk_level"] == "high"


@pytest.mark.unit
def test_init_rerun_does_not_clobber_existing_rules(init_home):
    sentinel = "# operator-authored rules — must survive re-init\nrisk_tiers: []\n"
    (init_home / "rules.yaml").write_text(sentinel, "utf-8")
    result = _run_init()
    assert result.exit_code == 0, result.output
    assert (init_home / "rules.yaml").read_text("utf-8") == sentinel


@pytest.mark.unit
def test_init_accepting_doctor_confirm_runs_doctor(init_home, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(doctor_mod, "run_doctor", lambda: calls.append(True) or 0)
    # Empty last answer accepts the confirm's default=True.
    result = _run_init("edge1\n\n\n\n\nn\n\n")
    assert result.exit_code == 0, result.output
    assert calls == [True]


@pytest.mark.unit
def test_init_overwrite_existing_target(init_home):
    result = _run_init()
    assert result.exit_code == 0, result.output
    # Same name again: confirm overwrite, new base URL, accept defaults.
    result = _run_init("edge1\ny\n\nhttp://edge2.local:8080\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert [t["base_url"] for t in raw["targets"]] == ["http://edge2.local:8080"]
