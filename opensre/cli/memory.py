import json
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from typing import Annotated, cast

import typer
from rich.console import Console
from rich.table import Table

from opensre.config import Settings
from opensre.memory.facts import remember as append_fact
from opensre.memory.store import IncidentStore, clean

memory_app = typer.Typer(help="Inspect and prune incident history.")


@memory_app.command("list")
def list_incidents(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 20,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    settings = cast(Settings, ctx.obj)
    with memory_errors():
        incidents = IncidentStore(settings.memory.dir).list_recent(limit)
    if json_output:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in incidents]))
        return
    table = Table("ID", "Created", "Target", "Status", "Summary")
    for item in incidents:
        table.add_row(
            str(item.id), item.created_at.isoformat(), item.target, item.status, item.summary
        )
    Console().print(table)


@memory_app.command()
def prune(
    ctx: typer.Context,
    older_than: Annotated[str, typer.Option("--older-than")],
) -> None:
    if not re.fullmatch(r"[1-9][0-9]{0,5}d", older_than):
        raise typer.BadParameter("Age must be a positive number of days, e.g. 90d")
    settings = cast(Settings, ctx.obj)
    with memory_errors():
        count = IncidentStore(settings.memory.dir).prune(timedelta(days=int(older_than[:-1])))
    typer.echo(f"Pruned {count} incidents.")


@contextmanager
def memory_errors() -> Generator[None]:
    try:
        yield
    except (OSError, ValueError, sqlite3.Error) as exc:
        typer.echo(f"Memory failed: {clean(str(exc))}", err=True)
        raise typer.Exit(1) from exc


def feedback(
    ctx: typer.Context,
    incident_id: int,
    right: Annotated[bool, typer.Option("--right")] = False,
    wrong: Annotated[bool, typer.Option("--wrong")] = False,
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    """Mark an incident diagnosis right or wrong."""
    if right == wrong:
        raise typer.BadParameter("Choose exactly one of --right or --wrong")
    settings = cast(Settings, ctx.obj)
    with memory_errors():
        incident = IncidentStore(settings.memory.dir).set_feedback(
            incident_id, "right" if right else "wrong", note
        )
    typer.echo(f"Incident #{incident.id} marked {incident.status}.")


def remember(ctx: typer.Context, text: str) -> None:
    """Append a human-provided environment fact."""
    settings = cast(Settings, ctx.obj)
    with memory_errors():
        append_fact(settings.memory.dir, text)
    typer.echo("Remembered.")
