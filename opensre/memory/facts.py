import logging
from pathlib import Path

from opensre.memory.store import clean

logger = logging.getLogger(__name__)


def remember(directory: Path, text: str) -> None:
    text = text.strip()
    if not text or len(text.splitlines()) != 1:
        raise ValueError("Fact must be a non-empty single line")
    path = directory / "memory/environment.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        stream.seek(0)
        existing = stream.read()
        if existing and not existing.endswith("\n"):
            stream.write("\n")
        stream.write(f"- {clean(text)}\n")


def load_facts(directory: Path) -> dict[str, str]:
    facts: dict[str, str] = {}
    for name, path in (
        ("environment", directory / "memory/environment.md"),
        ("project", Path.cwd() / ".opensre/memory.md"),
    ):
        try:
            facts[f"/memory/{name}.md"] = clean(path.read_text(encoding="utf-8"), 6000)
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            logger.warning("Memory facts could not be read (%s); continuing", name)
    return facts
