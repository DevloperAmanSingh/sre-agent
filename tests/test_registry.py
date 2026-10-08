import builtins
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from opensre.config import Settings
from opensre.connectors.registry import build_connectors, collect_tools


def test_disabled_connector_is_not_imported(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if "connectors.kubernetes" in name:
            raise AssertionError("Disabled connector imported")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    settings = Settings(connectors={"kubernetes": {"enabled": False}})
    assert build_connectors(settings) == []


@pytest.mark.parametrize("marker", [None, False, "true", 1, True])
def test_registry_enforces_read_only(marker):
    @tool
    def observe() -> str:
        """Observe the target."""
        return "ok"

    observe.metadata = {"read_only": marker}
    connector = SimpleNamespace(name="fake", tools=lambda: [observe])
    if marker is True:
        snapshot = collect_tools([connector])
        assert snapshot.tools == (observe,)
        assert snapshot.sources == {"observe": "fake"}
    else:
        with pytest.raises(ValueError, match="observe.*read-only"):
            collect_tools([connector])


@pytest.mark.parametrize(
    "name",
    [
        "ls",
        "read_file",
        "glob",
        "grep",
        "execute",
        "write_file",
        "edit_file",
        "task",
        "Diagnosis",
    ],
)
def test_registry_rejects_reserved_names(name):
    @tool(name)
    def reserved() -> str:
        """Reserved connector tool."""
        return "unsafe"

    reserved.metadata = {"read_only": True}
    with pytest.raises(ValueError, match="reserved"):
        collect_tools([SimpleNamespace(name="fake", tools=lambda: [reserved])])
