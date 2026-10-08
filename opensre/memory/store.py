import json
import sqlite3
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from opensre.domain import Diagnosis, Evidence
from opensre.output import cap_text
from opensre.redaction import redact


def clean(text: str, limit: int = 2000) -> str:
    return cap_text(redact(text) or "", limit - 40)


class Signature(BaseModel):
    connector: str
    reason: str
    resource: str


class Incident(Diagnosis):
    id: int
    created_at: datetime
    target: str
    question: str
    signature: list[Signature]
    status: Literal["unconfirmed", "right", "wrong"] = "unconfirmed"
    note: str = ""
    feedback_at: datetime | None = None


def decode(row: sqlite3.Row) -> Incident:
    data = dict(row)
    for key in ("signature", "evidence"):
        data[key] = json.loads(data[key])
    return Incident.model_validate(data)


class IncidentStore:
    def __init__(
        self, directory: Path, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.path = directory / "memory.db"
        self.now = now
        directory.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported memory schema version: {version}")
            db.execute(
                """CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, target TEXT NOT NULL,
                question TEXT NOT NULL, signature TEXT NOT NULL, summary TEXT NOT NULL,
                cause TEXT NOT NULL, suggested_fix TEXT NOT NULL, evidence TEXT NOT NULL,
                confidence REAL NOT NULL, status TEXT NOT NULL DEFAULT 'unconfirmed'
                CHECK(status IN ('unconfirmed', 'right', 'wrong')),
                note TEXT NOT NULL DEFAULT '', feedback_at TEXT)"""
            )
            db.execute("PRAGMA user_version = 1")

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=0.1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(
        self, *, target: str, question: str, signature: Sequence[Signature], diagnosis: Diagnosis
    ) -> int:
        symptoms = [
            Signature(**{key: clean(value) for key, value in item.model_dump().items()})
            for item in signature[:50]
        ]
        evidence = [
            Evidence(source=clean(item.source), detail=clean(item.detail))
            for item in diagnosis.evidence[:10]
        ]
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO incidents
                (created_at, target, question, signature, summary, cause, suggested_fix,
                evidence, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.now().astimezone(UTC).isoformat(),
                    clean(target),
                    clean(question),
                    json.dumps([item.model_dump() for item in symptoms]),
                    clean(diagnosis.summary),
                    clean(diagnosis.cause),
                    clean(diagnosis.suggested_fix),
                    json.dumps([item.model_dump() for item in evidence]),
                    diagnosis.confidence,
                ),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def get(self, incident_id: int) -> Incident | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        return decode(row) if row is not None else None

    def list_recent(self, limit: int = 20) -> list[Incident]:
        if not 1 <= limit <= 1000:
            raise ValueError("Limit must be between 1 and 1000")
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM incidents ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [decode(row) for row in rows]
