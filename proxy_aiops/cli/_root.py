"""Top-level Typer app: assembles sub-apps and top-level commands."""

from __future__ import annotations

import typer

from proxy_aiops.cli._common import cli_errors
from proxy_aiops.cli.analyze import analyze_app
from proxy_aiops.cli.certs import certs_cmd
from proxy_aiops.cli.configcmd import config_app
from proxy_aiops.cli.doctor import doctor_cmd
from proxy_aiops.cli.init import init_cmd
from proxy_aiops.cli.overview import overview_cmd
from proxy_aiops.cli.routes import routes_app
from proxy_aiops.cli.secret import secret_app
from proxy_aiops.cli.server import server_app
from proxy_aiops.cli.services import services_app
from proxy_aiops.cli.undo import undo_app

app = typer.Typer(
    name="proxy-aiops",
    help="Governed AI-ops for Traefik + Caddy + HAProxy: routes, services, "
    "upstream health, certs, error-rate RCA, and governed writes (caddy "
    "config, haproxy server state/weight).",
    no_args_is_help=True,
)

app.add_typer(routes_app, name="routes")
app.add_typer(services_app, name="services")
app.add_typer(analyze_app, name="analyze")
app.add_typer(config_app, name="config")
app.add_typer(server_app, name="server")
app.add_typer(secret_app, name="secret")
app.add_typer(undo_app, name="undo")
app.command("init")(init_cmd)
app.command("overview")(overview_cmd)
app.command("certs")(certs_cmd)
app.command("doctor")(doctor_cmd)


@app.command("mcp")
@cli_errors
def mcp_cmd() -> None:
    """Start the MCP server (stdio transport).

    Single-command entry point for MCP clients (does not go through uvx/PyPI
    resolution at launch):
        proxy-aiops mcp
    """
    import sys

    if sys.version_info < (3, 11):
        typer.echo(
            f"ERROR: proxy-aiops requires Python >= 3.11 "
            f"(got {sys.version_info.major}.{sys.version_info.minor}).\n"
            f"Fix: uv python install 3.12 && "
            f"uv tool install --python 3.12 --force proxy-aiops",
            err=True,
        )
        raise typer.Exit(2)

    from mcp_server.server import main as _mcp_main

    _mcp_main()


if __name__ == "__main__":
    app()
