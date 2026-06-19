"""Tests for the typed ProjectConfig that replaces the raw yuj.yaml dict."""

from __future__ import annotations

from yuj.config import (
    DEFAULT_REMOTE_DIR,
    DEFAULT_RESULTS_GLOB,
    ProjectConfig,
)
from yuj.status import DEFAULT_STALL_MIN


def test_empty_mapping_yields_documented_defaults() -> None:
    cfg = ProjectConfig.from_mapping({})
    assert cfg.fleet is None
    assert cfg.job == "yuj"
    assert cfg.remote_dir == DEFAULT_REMOTE_DIR
    assert cfg.results_glob == DEFAULT_RESULTS_GLOB
    assert cfg.stall_min == DEFAULT_STALL_MIN
    assert cfg.work_command is None
    assert cfg.input_file is None
    assert cfg.output_dir is None
    assert cfg.output_suffix == ""
    assert cfg.active_window is None
    assert cfg.off_window_command is None
    assert cfg.deploy == {}
    assert cfg.scatter == {}
    assert cfg.authorize == {}
    assert cfg.bootstrap == {}


def test_scalar_overrides_are_coerced() -> None:
    cfg = ProjectConfig.from_mapping(
        {
            "fleet": "hosts.csv",
            "job": "myjob",
            "remote_dir": "run",
            "results_glob": "~/run/out/*",
            "stall_min": "45",  # str coerced to int
            "work_command": "bash worker.sh",
            "input_file": "items.txt",
            "output_dir": "out",
            "output_suffix": ".csv",
        }
    )
    assert cfg.fleet == "hosts.csv"
    assert cfg.job == "myjob"
    assert cfg.remote_dir == "run"
    assert cfg.results_glob == "~/run/out/*"
    assert cfg.stall_min == 45
    assert cfg.work_command == "bash worker.sh"
    assert cfg.input_file == "items.txt"
    assert cfg.output_dir == "out"
    assert cfg.output_suffix == ".csv"


def test_blank_window_fields_normalise_to_none() -> None:
    cfg = ProjectConfig.from_mapping({"active_window": "", "off_window_command": ""})
    assert cfg.active_window is None
    assert cfg.off_window_command is None

    cfg2 = ProjectConfig.from_mapping(
        {"active_window": "19:00-09:30", "off_window_command": "pkill -f srv"}
    )
    assert cfg2.active_window == "19:00-09:30"
    assert cfg2.off_window_command == "pkill -f srv"


def test_nested_sections_are_preserved_as_mappings() -> None:
    cfg = ProjectConfig.from_mapping(
        {
            "deploy": {"code": ["worker.py"], "payload": ["data/"]},
            "scatter": {"input": "items.txt", "into": "work.txt"},
            "authorize": {"key": "id_ed25519.pub"},
            "bootstrap": {"env_manager": "uv", "python": "3.12"},
        }
    )
    assert cfg.deploy == {"code": ["worker.py"], "payload": ["data/"]}
    assert cfg.scatter == {"input": "items.txt", "into": "work.txt"}
    assert cfg.authorize == {"key": "id_ed25519.pub"}
    assert cfg.bootstrap["env_manager"] == "uv"


def test_non_dict_sections_fall_back_to_empty() -> None:
    cfg = ProjectConfig.from_mapping({"deploy": None, "scatter": "oops"})
    assert cfg.deploy == {}
    assert cfg.scatter == {}


def test_is_immutable() -> None:
    cfg = ProjectConfig.from_mapping({})
    try:
        cfg.job = "other"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("ProjectConfig should be frozen")
