"""Environment and connectivity diagnostics for Proxy AIops."""

from __future__ import annotations

from rich.console import Console

from proxy_aiops.config import CONFIG_FILE, ENV_FILE, load_config
from proxy_aiops.secretstore import SECRETS_FILE, check_permissions, has_store

_console = Console()


def run_doctor(skip_auth: bool = False) -> int:
    """Check config, secrets, and (optionally) connectivity.

    Returns a process exit code: 0 healthy, 1 problems found. Connectivity
    failures are reported as status, never raised as tracebacks (a doctor must
    survive the thing it diagnoses being unhealthy).
    """
    problems = 0

    if not CONFIG_FILE.exists():
        _console.print(f"[red]✗ Config file missing: {CONFIG_FILE}[/]")
        _console.print("[yellow]  Run 'proxy-aiops init' to set up your first target.[/]")
        return 1
    _console.print(f"[green]✓ Config file present: {CONFIG_FILE}[/]")

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001 — report, do not crash
        _console.print(f"[red]✗ Config load failed: {exc}[/]")
        return 1

    if not config.targets:
        _console.print("[red]✗ No targets configured[/]")
        return 1
    _console.print(f"[green]✓ {len(config.targets)} target(s) configured[/]")

    if has_store():
        _console.print(f"[green]✓ Encrypted secret store present: {SECRETS_FILE}[/]")
        perm_warning = check_permissions()
        if perm_warning:
            _console.print(f"[yellow]! {perm_warning}[/]")
    elif ENV_FILE.exists():
        _console.print(
            f"[yellow]! Using legacy plaintext .env ({ENV_FILE}). Migrate with "
            f"'proxy-aiops secret migrate'.[/]"
        )
    else:
        _console.print(
            "[dim]No secret store yet — fine for unauthenticated traefik/caddy "
            "endpoints; haproxy targets need 'proxy-aiops secret set <target>'.[/]"
        )

    for target in config.targets:
        try:
            if target.platform_obj.requires_secret:
                _ = target.secret
                _console.print(
                    f"[green]✓ Secret present for '{target.name}' ({target.platform})[/]"
                )
            elif target.has_secret:
                _console.print(
                    f"[green]✓ Optional secret present for '{target.name}' "
                    f"({target.platform})[/]"
                )
            else:
                _console.print(
                    f"[dim]• No secret for '{target.name}' ({target.platform}) — "
                    f"treating the endpoint as unauthenticated.[/]"
                )
        except OSError as exc:
            _console.print(f"[red]✗ {exc}[/]")
            problems += 1

    if skip_auth:
        _console.print("[dim]Skipping connectivity check (--skip-auth).[/]")
        return 1 if problems else 0

    from proxy_aiops.connection import ConnectionManager

    mgr = ConnectionManager(config)
    for target in config.targets:
        try:
            conn = mgr.connect(target.name)
            # Each platform's cheap health/info endpoint: traefik /api/version,
            # caddy /config/, haproxy /v2/info.
            conn.get(conn.platform.path("probe"))
            _console.print(
                f"[green]✓ Connected to '{target.name}' ({target.platform} "
                f"{target.base_url}) — health/info probe OK[/]"
            )
        except Exception as exc:  # noqa: BLE001 — connectivity is a status, not a crash
            _console.print(f"[red]✗ Connect to '{target.name}' failed: {exc}[/]")
            problems += 1

    return 1 if problems else 0
