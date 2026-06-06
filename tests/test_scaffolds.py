"""Tests for `yuj init` scaffold templates and the R bootstrap recipe."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from yuj.bootstrap import load_recipe
from yuj.exceptions import YujError
from yuj.scaffolds import TEMPLATES, scaffold_files


def test_bare_template() -> None:
    files = scaffold_files("bare")
    assert set(files) == {"fleet.csv", "yuj.yaml", "worker.sh"}
    assert "work_command" in files["yuj.yaml"]


def test_python_template() -> None:
    files = scaffold_files("python")
    assert set(files) == {"fleet.csv", "yuj.yaml", "worker.py", "items.txt"}
    assert "python3 $HOME/yuj-run/worker.py" in files["yuj.yaml"]
    assert "env_manager: uv" in files["yuj.yaml"]
    assert "def main()" in files["worker.py"]


def test_r_template_is_turnkey() -> None:
    files = scaffold_files("r")
    assert set(files) == {
        "fleet.csv",
        "yuj.yaml",
        "worker.R",
        "items.txt",
        "environment.yaml",
        "r-packages.txt",
    }
    yaml = files["yuj.yaml"]
    assert "Rscript $HOME/yuj-run/worker.R" in yaml
    assert "env_manager: micromamba" in yaml
    assert "extras: [R]" in yaml
    assert "env_file: environment.yaml" in yaml
    assert "r-base" in files["environment.yaml"]
    assert "commandArgs" in files["worker.R"]


def test_all_templates_listed() -> None:
    for t in TEMPLATES:
        assert scaffold_files(t)["fleet.csv"]


def test_unknown_template_raises() -> None:
    with pytest.raises(YujError, match="unknown template"):
        scaffold_files("julia")


_shellcheck = shutil.which("shellcheck")


@pytest.mark.skipif(_shellcheck is None, reason="shellcheck not installed")
def test_bare_worker_passes_shellcheck() -> None:
    assert _shellcheck is not None
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(scaffold_files("bare")["worker.sh"])
        path = fh.name
    try:
        result = subprocess.run(
            [_shellcheck, path], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stdout
    finally:
        Path(path).unlink()


class TestRRecipe:
    def test_recipe_loads_and_mentions_user_library(self) -> None:
        recipe = load_recipe("R")
        assert ".yuj-rlib" in recipe
        assert "r-packages.txt" in recipe
        assert "install.packages" in recipe

    @pytest.mark.skipif(_shellcheck is None, reason="shellcheck not installed")
    def test_recipe_passes_shellcheck(self) -> None:
        assert _shellcheck is not None
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(load_recipe("R"))
            path = fh.name
        try:
            result = subprocess.run(
                [_shellcheck, "-s", "bash", path],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stdout
        finally:
            Path(path).unlink()
