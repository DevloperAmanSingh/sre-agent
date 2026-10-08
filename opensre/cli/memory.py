from typing import cast

import typer

from opensre.config import Settings
from opensre.memory.facts import remember as append_fact
from opensre.memory.store import clean


def remember(ctx: typer.Context, text: str) -> None:
    """Append a human-provided environment fact."""
    settings = cast(Settings, ctx.obj)
    try:
        append_fact(settings.memory.dir, text)
    except (OSError, ValueError) as exc:
        typer.echo(f"Memory failed: {clean(str(exc))}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("Remembered.")
