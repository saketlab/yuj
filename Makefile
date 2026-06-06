.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test cov check clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install dev dependencies
	uv sync
	uv run pre-commit install

lint:  ## Run ruff lint
	uv run ruff check .

format:  ## Auto-format with ruff
	uv run ruff format .
	uv run ruff check --fix .

typecheck:  ## Run mypy (strict)
	uv run mypy src

test:  ## Run the test suite (skips e2e)
	uv run pytest -m "not e2e"

cov:  ## Run tests with coverage report
	uv run pytest --cov=yuj --cov-report=term-missing

check: lint typecheck test  ## Run lint + typecheck + test (CI parity)

docs:  ## Build the mkdocs site locally (opens at http://127.0.0.1:8000)
	uv pip install -e ".[docs]"
	mkdocs serve

docs-build:  ## Build the docs into site/ (for CI/pre-flight)
	uv pip install -e ".[docs]"
	mkdocs build --strict

clean:  ## Remove caches and build artifacts
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf site/
