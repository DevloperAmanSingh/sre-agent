import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("conflict", ["none", "caps", "subagent", "inherited"])
def test_owned_profile_registration_in_isolated_process(conflict):
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
import deepagents
from deepagents import HarnessProfile, GeneralPurposeSubagentProfile
from deepagents.backends import StateBackend
from fakes.model import ScriptedModel

conflict = sys.argv[1]
if conflict != "none":
    profile = HarnessProfile(
        excluded_middleware=(
            frozenset({"OutputCapMiddleware"}) if conflict != "subagent" else frozenset()
        ),
        general_purpose_subagent=(
            GeneralPurposeSubagentProfile(enabled=True) if conflict == "subagent" else None
        ),
    )
    key = "opensre" if conflict == "inherited" else "opensre:harness"
    deepagents.register_harness_profile(key, profile)

registrations = []
original = deepagents.register_harness_profile
def register(key, profile):
    registrations.append(key)
    return original(key, profile)
deepagents.register_harness_profile = register

try:
    from opensre.agents.graph import build_agent
    from opensre.agents.model import HarnessModel
    from opensre.connectors.registry import collect_tools
    from opensre.agents.lock import inspect_tools
except ValueError as exc:
    assert conflict != "none"
    assert "Conflicting harness profile" in str(exc)
    assert not registrations
else:
    assert conflict == "none", "Conflicting profile accepted"
    inspect_tools(collect_tools([]))
    inspect_tools(collect_tools([]))
    assert registrations == ["opensre:harness"], registrations
    model = ScriptedModel()
    agent = deepagents.create_deep_agent(model=model, backend=StateBackend())
    agent.invoke({"messages": [{"role": "user", "content": "Hello"}]})
    assert {"task", "write_file", "edit_file"} <= set(model.bound_names)
    assert registrations == ["opensre:harness"]
print("profile checks passed")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, conflict],
        cwd=root,
        env={
            **os.environ,
            "PYTHONPATH": str(root / "tests"),
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "profile checks passed"
