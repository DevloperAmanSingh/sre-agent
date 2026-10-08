# Contributing

## Setup and checks

Install Python 3.12+ and uv, then run:

```bash
uv sync
make check
```

`make check` runs offline pytest tests, Ruff lint and format checks, and strict
Pyright on `opensre/`. Use `make fmt` to format changes. Tests must use injected
clients and models: no network, real cluster, sleeps, or timing assumptions.

Keep changes small and working. Use conventional present-tense commits such as
`feat(connectors): add namespace reader` or `fix(cli): report invalid settings`.
Run `make check` before each commit. Each plan step has its own branch and PR.

## Add a connector

The harness contract and registry are being introduced in Step 2:

1. Add `opensre/connectors/<name>/connector.py` and target-specific clients there.
2. Implement `Connector` from `opensre/connectors/base.py`: `name`,
   `health() -> CheckResult`, `tools() -> list[BaseTool]`, and
   `checks() -> list[QuickCheck]`. Shared result types live in `opensre/domain.py`.
3. Add settings in `opensre/config.py` and a lazy constructor in
   `opensre/connectors/registry.py`. Disabled connectors must not be imported.
4. Mark every tool with `metadata={"read_only": True}`. This is a safety promise,
   not permission to wrap arbitrary commands: no mutations or command execution.
   Return typed, bounded results and explicit errors. Own client timeouts locally.
5. Add fake-client tests for outputs, errors, bounds, and disabled registration.
   Keep core agent, CLI, and doctor code free of connector-specific imports.
6. Update `examples/opensre.yaml` and README with configuration and usage.

## Add a skill

Add `skills/<name>/SKILL.md` with YAML frontmatter containing `name` and
`description`, followed by a short markdown investigation playbook. Describe
when to use it, which read-only observations to gather, and how to support a
conclusion with evidence. Suggested fixes are advice, never actions to execute.

Test that the agent discovers the skill, using a fake model without credentials.
Skills must not require write tools, shell execution, or unrestricted subagents.
