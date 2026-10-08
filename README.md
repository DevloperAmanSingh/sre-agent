# OpenSre

A read-only command-line SRE assistant for Kubernetes. OpenSre never changes the cluster.
The current skeleton checks cluster access and LLM credentials; rule checks and AI
investigation are coming in later steps.

## Install

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run opensre --version
uv run opensre --help
```

## Check your setup

Configure Kubernetes access with your kubeconfig (or run in-cluster). Store LLM keys
in environment variables, never in the YAML config:

```bash
export DEEPSEEK_API_KEY='your-deepseek-key'
export OPENAI_API_KEY='your-openai-key'
uv run opensre doctor
uv run opensre doctor --json
```

`doctor` displays a Rich table with the Kubernetes server version and whether the
keys for each configured model are present. It does not contact LLM providers unless
you use `uv run opensre doctor --live`, which sends one tiny prompt to each configured
model and reports latency or errors. Live checks may incur provider charges.

Exit codes: **0** means all checks pass, **1** means a check failed, and **2** means
invalid configuration. Missing kubeconfig or keys are reported as failures, not crashes.
JSON reports contain `ok` and a `checks` array; each check has `name`, `ok`, `detail`,
and `latency_s` (null unless a live request was attempted).

## Configuration

Copy `examples/opensre.yaml` to `opensre.yaml`:

```yaml
kube:
  context: null
  namespace: default
  request_timeout_s: 10
llm:
  primary: deepseek/deepseek-chat
  fallback: openai/gpt-5.6-luna
  timeout_s: 60
```

Set `fallback: null` to disable fallback. All model calls use LiteLLM; the chat model
passes fallback handling and request timeouts through to LiteLLM.

Values take precedence in this order: **CLI flags → `OPENSRE_*` environment variables
→ YAML → defaults**. Nested environment variables use `__`:

```bash
export OPENSRE_KUBE__NAMESPACE=shop
uv run opensre --context kind-demo --request-timeout 5 doctor
uv run opensre --config ./custom.yaml --primary deepseek/deepseek-chat doctor --json
```

Global flags go before `doctor`: `--config`, `--context`, `--namespace`/`-n`,
`--request-timeout`, `--primary`, `--fallback`, and `--llm-timeout`.

The YAML path is selected by `--config`, then `OPENSRE_CONFIG`, then `./opensre.yaml`
if it exists, otherwise `~/.config/opensre/config.yaml`. A missing file is fine.
Unknown fields and invalid or non-positive timeouts are rejected.

## Development

```bash
make check       # pytest, ruff lint + format check, strict pyright
make fmt         # format Python files
```

Unit tests use injected fakes, with no network or cluster dependency.
