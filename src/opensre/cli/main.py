from importlib.metadata import version
from typing import Annotated

import typer

app = typer.Typer(name="opensre", help="Read-only Kubernetes SRE assistant.")


@app.callback(invoke_without_command=True)
def main(
    show_version: Annotated[bool, typer.Option("--version", is_eager=True)] = False,
) -> None:
    if show_version:
        typer.echo(version("opensre"))
        raise typer.Exit()
