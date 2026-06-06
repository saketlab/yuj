"""Smoke tests for the Phase 0 skeleton: the package imports and the CLI runs."""

from __future__ import annotations

from typer.testing import CliRunner

import yuj
from yuj.cli import app

runner = CliRunner()


def test_version_is_exposed() -> None:
    assert isinstance(yuj.__version__, str)
    assert yuj.__version__


def test_cli_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert yuj.__version__ in result.stdout


def test_cli_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # no_args_is_help prints usage; click's exit code here is 0 or 2 by version.
    assert result.exit_code in (0, 2)
    assert "Usage" in result.stdout


def test_cli_help_flag() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.stdout
