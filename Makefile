.PHONY: test lint fmt typecheck check

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .

typecheck:
	uv run pyright

check: test lint typecheck
