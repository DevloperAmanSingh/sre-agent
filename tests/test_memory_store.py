import sqlite3
from datetime import UTC, datetime

import pytest

from opensre.domain import Diagnosis, Evidence

NOW = datetime(2026, 10, 20, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    from opensre.memory.store import IncidentStore

    return IncidentStore(tmp_path, now=lambda: NOW)


@pytest.fixture
def diagnosis():
    return Diagnosis(
        summary="Failure",
        cause="password=hunter2",
        suggested_fix="Restart",
        evidence=[Evidence(source="logs", detail="Bearer abc123 " + "x" * 10000)],
        confidence=0.8,
    )


def save(store, diagnosis, **kwargs):
    from opensre.memory.store import Signature

    return store.save(
        target=kwargs.get("target", '["kubernetes/prod"]'),
        question="Why? token=secret",
        signature=kwargs.get(
            "signature",
            [Signature(connector="kubernetes", reason="OOMKilled", resource="pod/shop/pay")],
        ),
        diagnosis=diagnosis,
    )


def test_feedback_transitions_and_unknown_id(store, diagnosis):
    incident_id = save(store, diagnosis)
    for status in ("right", "wrong", "right"):
        incident = store.set_feedback(incident_id, status, "token=private")
        assert incident.status == status
        assert incident.feedback_at == NOW
        assert "private" not in incident.note
        assert store.get(incident_id) == incident
    with pytest.raises(ValueError, match="Unknown incident #999"):
        store.set_feedback(999, "right")
    with pytest.raises(ValueError, match="right or wrong"):
        store.set_feedback(incident_id, "unconfirmed")


def test_round_trip_schema_and_redaction(store, diagnosis, tmp_path):
    incident_id = save(store, diagnosis)
    incident = store.get(incident_id)
    assert incident.id == incident_id
    assert incident.created_at == NOW
    assert incident.status == "unconfirmed"
    assert incident.confidence == 0.8
    assert incident.signature[0].reason == "OOMKilled"
    assert "hunter2" not in incident.cause
    assert "secret" not in incident.question
    assert "abc123" not in incident.evidence[0].detail
    assert len(incident.evidence[0].detail) <= 2000
    with sqlite3.connect(tmp_path / "memory.db") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert b"hunter2" not in (tmp_path / "memory.db").read_bytes()
    assert store.get(999) is None
    assert [item.id for item in store.list_recent(1)] == [incident_id]
