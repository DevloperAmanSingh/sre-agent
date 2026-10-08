from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.table import Table

from opensre.config import ConfigError, Settings, load_settings
from opensre.doctor import check_kube

app = typer.Typer(name="opensre", help="Read-only Kubernetes SRE assistant.")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    show_version: Annotated[bool, typer.Option("--version", is_eager=True)] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
    context: Annotated[str | None, typer.Option("--context")] = None,
    namespace: Annotated[str | None, typer.Option("--namespace", "-n")] = None,
    request_timeout: Annotated[float | None, typer.Option("--request-timeout")] = None,
    primary: Annotated[str | None, typer.Option("--primary")] = None,
    fallback: Annotated[str | None, typer.Option("--fallback")] = None,
    llm_timeout: Annotated[float | None, typer.Option("--llm-timeout")] = None,
) -> None:
    if show_version:
        typer.echo(version("opensre"))
        raise typer.Exit()
    overrides: dict[str, Any] = {
        "kube": {"context": context, "namespace": namespace, "request_timeout_s": request_timeout},
        "llm": {"primary": primary, "fallback": fallback, "timeout_s": llm_timeout},
    }
    overrides = {
        section: {key: value for key, value in fields.items() if value is not None}
        for section, fields in overrides.items()
    }
    try:
        ctx.obj = load_settings(config, overrides)
    except ConfigError as exc:
        typer.echo(f"Invalid config: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def doctor(ctx: typer.Context) -> None:
    settings = cast(Settings, ctx.obj)
    result = check_kube(settings.kube)
    table = Table("Check", "Status", "Detail")
    table.add_row(result.name, "PASS" if result.ok else "FAIL", result.detail)
    Console().print(table)
    raise typer.Exit(0 if result.ok else 1)
