import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, cast

import typer

from opensre.config import Settings
from opensre.memory.facts import remember as append_fact
from opensre.memory.store import IncidentStore, clean


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
