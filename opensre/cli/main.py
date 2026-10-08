from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.table import Table

from opensre.config import ConfigError, Settings, load_settings
from opensre.doctor import DoctorReport, check_kube, check_llm

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
    overrides["connectors"] = {"kubernetes": overrides.pop("kube")}
    try:
        ctx.obj = load_settings(config, overrides)
    except ConfigError as exc:
        typer.echo(f"Invalid config: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def doctor(
    ctx: typer.Context,
    live: Annotated[bool, typer.Option("--live", help="Send a tiny prompt to each model.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print a JSON report.")] = False,
) -> None:
    settings = cast(Settings, ctx.obj)
    report = DoctorReport(
        checks=[check_kube(settings.connectors.kubernetes), *check_llm(settings.llm, live=live)]
    )
    if json_output:
        typer.echo(report.model_dump_json())
    else:
        table = Table("Check", "Status", "Detail", "Latency")
        for result in report.checks:
            table.add_row(
                result.name,
                "PASS" if result.ok else "FAIL",
                result.detail,
                f"{result.latency_s:.3f}s" if result.latency_s is not None else "—",
            )
        Console().print(table)
    raise typer.Exit(0 if report.ok else 1)
