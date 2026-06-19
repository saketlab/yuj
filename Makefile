.DEFAULT_GOAL := help
.PHONY: help install lint format format-check typecheck test cov check docs docs-build clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install dev dependencies
	uv sync --locked
	uv run pre-commit install

lint:  ## Run ruff lint
	uv run --frozen ruff check .

format:  ## Auto-format with ruff
	uv run --frozen ruff format .
	uv run --frozen ruff check --fix .

format-check:  ## Check formatting without modifying files
	uv run --frozen ruff format --check .

typecheck:  ## Run mypy (strict)
	uv run --frozen mypy src

test:  ## Run the test suite (skips e2e)
	uv run --frozen pytest -m "not e2e"

cov:  ## Run tests with coverage report
	uv run --frozen pytest --cov=yuj --cov-report=term-missing

check: lint format-check typecheck test  ## Run lint + format + typecheck + test (CI parity)

docs: docs-build  ## Build and serve Sphinx docs at http://127.0.0.1:8000
	python -m http.server 8000 --directory site

docs-build:  ## Build the Sphinx docs into site/ (for CI/pre-flight)
	uv sync --locked --extra docs
	uv run --frozen sphinx-build -W --keep-going -b html -d /tmp/yuj-doctrees docs site

clean:  ## Remove caches and build artifacts
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf site/
