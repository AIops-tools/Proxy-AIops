"""``proxy-aiops init`` — a friendly, interactive onboarding wizard.

Walks a new user through connecting their first proxy target: collects the
non-secret connection details into ``config.yaml`` and any credential into the
*encrypted* store (never plaintext on disk). Traefik and Caddy commonly run
unauthenticated on localhost, so their secret is optional; HAProxy's Data Plane
API credentials are required. Designed to be run on a terminal; everything it
needs is prompted with sensible defaults.
"""

from __future__ import annotations

import getpass

import typer
import yaml

from proxy_aiops.cli._common import cli_errors, console
from proxy_aiops.config import CONFIG_DIR, CONFIG_FILE
from proxy_aiops.platform import CADDY, HAPROXY, PLATFORMS, TRAEFIK, get_platform
from proxy_aiops.secretstore import SecretStore, resolve_master_password


def _load_existing_targets() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    raw = yaml.safe_load(CONFIG_FILE.read_text("utf-8")) or {}
    return list(raw.get("targets", []))


def _write_targets(targets: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    CONFIG_FILE.write_text(yaml.safe_dump({"targets": targets}, sort_keys=False), "utf-8")


@cli_errors
def init_cmd() -> None:
    """Interactively set up your first proxy connection."""
    console.print("[bold cyan]Proxy AIops — setup wizard[/]")
    console.print(
        "This collects traefik, caddy or haproxy connection details (saved to "
        "config.yaml) and any credential (saved [bold]encrypted[/] to "
        "secrets.enc).\n"
    )

    console.print("[bold]Step 1 — master password[/]")
    console.print(
        "[dim]Encrypts secrets.enc. You'll set it via the "
        "PROXY_AIOPS_MASTER_PASSWORD env var for non-interactive/MCP use.[/]"
    )
    password = resolve_master_password(confirm_if_new=True)
    store = SecretStore.unlock(password)

    targets = _load_existing_targets()
    existing_names = {t.get("name") for t in targets}

    while True:
        console.print("\n[bold]Step 2 — add a target[/]")
        name = typer.prompt("Target name (e.g. edge1)").strip()
        if name in existing_names:
            if not typer.confirm(f"'{name}' already exists — overwrite?", default=False):
                continue
            targets = [t for t in targets if t.get("name") != name]

        platform = typer.prompt(
            f"Platform ({TRAEFIK} / {CADDY} / {HAPROXY})", default=TRAEFIK
        ).strip().lower()
        if platform not in PLATFORMS:
            console.print("[red]Platform must be 'traefik', 'caddy' or 'haproxy'.[/]")
            continue
        platform_obj = get_platform(platform)

        base_url = typer.prompt(
            "API base URL", default=platform_obj.default_base_url
        ).strip()
        console.print("[dim]Lab/self-signed setups can answer No here.[/]")
        verify_ssl = typer.confirm(
            "Verify TLS certificate? (No for self-signed lab certs)", default=True
        )

        username = ""
        secret = ""
        if platform == HAPROXY:
            username = typer.prompt("Data Plane API username").strip()
            secret = getpass.getpass(f"Data Plane API password for '{name}' (hidden): ")
        else:
            console.print(
                f"[dim]{platform_obj.label} usually runs unauthenticated on "
                f"localhost — leave both empty for none.[/]"
            )
            username = typer.prompt(
                "Basic-auth username (empty for none)", default=""
            ).strip()
            secret = getpass.getpass(
                f"Basic-auth password for '{name}' (hidden, empty for none): "
            )
        if secret:
            store = store.set(name, secret)

        entry = {
            "name": name,
            "platform": platform,
            "base_url": base_url,
            "username": username,
            "verify_ssl": verify_ssl,
        }
        targets.append(entry)
        existing_names.add(name)
        _write_targets(targets)
        secret_note = "secret encrypted" if secret else "no secret (unauthenticated)"
        console.print(f"[green]✓ Saved target '{name}' ({platform}, {secret_note}).[/]")

        if not typer.confirm("\nAdd another target?", default=False):
            break

    console.print(f"\n[green]✓ Setup complete.[/] Config: {CONFIG_FILE}")
    console.print(
        "[dim]Tip: export PROXY_AIOPS_MASTER_PASSWORD=... in your shell profile "
        "so the MCP server and CLI can unlock secrets non-interactively.[/]"
    )
    if typer.confirm("Run a connectivity check now (proxy-aiops doctor)?", default=True):
        from proxy_aiops.doctor import run_doctor

        raise typer.Exit(run_doctor())
