from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.table import Table

from opensre.cli.memory import remember
from opensre.config import ConfigError, Settings, load_settings
from opensre.connectors.registry import build_connectors, collect_tools
from opensre.doctor import diagnose_setup
from opensre.output import cap_text
from opensre.scan import run_checks

app = typer.Typer(name="opensre", help="Read-only SRE agent harness.")
app.command()(remember)


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
    report = diagnose_setup(build_connectors(settings), settings.llm, live=live)
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


@app.command()
def scan(
    ctx: typer.Context,
    namespace: Annotated[str | None, typer.Option("--namespace", "-n")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run quick checks across enabled connectors without AI."""
    settings = cast(Settings, ctx.obj)
    connectors = (
        build_connectors(settings, namespace=namespace)
        if namespace is not None
        else build_connectors(settings)
    )
    report = run_checks(connectors)
    if json_output:
        typer.echo(report.model_dump_json())
    else:
        table = Table("Severity", "Resource", "Reason", "Evidence")
        for finding in report.findings:
            table.add_row(
                finding.severity.value,
                finding.resource,
                finding.reason,
                "\n".join(f"{item.source}: {item.detail}" for item in finding.evidence),
            )
        if report.findings:
            Console().print(table)
        if report.omitted:
            typer.echo(f"{report.omitted} more omitted.")
        for error in report.errors:
            typer.echo(f"Scan failed ({error.name}): {error.detail}", err=True)
        if report.ok:
            typer.echo("No findings.")
    raise typer.Exit(0 if report.ok else 1)


@app.command()
def tools(ctx: typer.Context) -> None:
    """List effective read-only agent tools by source, without contacting targets."""
    settings = cast(Settings, ctx.obj)
    try:
        from opensre.agents.lock import inspect_tools

        sources = inspect_tools(collect_tools(build_connectors(settings)))
    except Exception as exc:
        typer.echo(f"Tool self-check failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    table = Table("Tool", "Source")
    for name, source in sorted(sources.items()):
        table.add_row(name, source)
    Console().print(table)


@app.command()
def ask(
    ctx: typer.Context,
    question: str,
    json_output: Annotated[bool, typer.Option("--json", help="Print a JSON diagnosis.")] = False,
) -> None:
    """Investigate a question using read-only tools and markdown skills."""
    settings = cast(Settings, ctx.obj)
    try:
        from opensre.agents.run import investigate

        diagnosis = investigate(question, build_connectors(settings), settings.llm)
    except Exception as exc:
        typer.echo(f"Investigation failed: {cap_text(str(exc), 2000)}", err=True)
        raise typer.Exit(1) from exc
    if json_output:
        typer.echo(diagnosis.model_dump_json())
        return
    table = Table("Diagnosis", "Detail")
    table.add_row("Summary", diagnosis.summary)
    table.add_row("Cause", diagnosis.cause)
    for evidence in diagnosis.evidence:
        table.add_row(f"Evidence ({evidence.source})", evidence.detail)
    table.add_row("Suggested fix", diagnosis.suggested_fix)
    table.add_row("Confidence", f"{diagnosis.confidence:.0%}")
    Console().print(table)
