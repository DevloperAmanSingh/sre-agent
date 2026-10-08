import shutil
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from fakes.model import ScriptedModel


def test_package_version():
    assert version("opensre") == "0.1.0"


def test_wheel_skills_use_package_owned_path(tmp_path, monkeypatch):
    from opensre.agents import graph
    from opensre.agents.backend import SkillsBackend

    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    included = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert included["skills"] == "opensre/skills"
    packaged = tmp_path / included["skills"]
    shutil.copytree(root / "skills", packaged)
    monkeypatch.setattr(graph, "__file__", str(packaged.parent / "agents/graph.py"))
    assert graph.resolve_skills_root() == packaged
    assert "General triage" in str(SkillsBackend(packaged).read("/triage/SKILL.md"))
    shutil.rmtree(packaged)
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills/SKILL.md").write_text("Unrelated site-packages skills")
    with pytest.raises(ValueError, match="Bundled skills"):
        graph.resolve_skills_root()


def test_agent_rejects_missing_playbooks(tmp_path):
    from opensre.agents.graph import build_agent
    from opensre.connectors.registry import collect_tools

    with pytest.raises(ValueError, match="SKILL.md"):
        build_agent(collect_tools([]), model=ScriptedModel(), skills_root=tmp_path)
