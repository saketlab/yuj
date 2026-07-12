"""Tests for local pre-flight path checks (deploy scope)."""

from __future__ import annotations

from yuj.config import ProjectConfig
from yuj.preflight import local_preflight


def _config(**data: object) -> ProjectConfig:
    return ProjectConfig.from_mapping(data)


def test_clean_when_all_paths_exist(tmp_path):
    code = tmp_path / "src"
    code.mkdir()
    payload = tmp_path / "data"
    payload.mkdir()
    config = _config(deploy={"code": [str(code)], "payload": [str(payload)]})
    assert local_preflight(config, push_payload=True) == []


def test_missing_code_is_reported(tmp_path):
    config = _config(deploy={"code": [str(tmp_path / "nope")]})
    problems = local_preflight(config, push_payload=True)
    assert any("deploy.code" in p for p in problems)


def test_payload_skipped_when_not_pushing(tmp_path):
    config = _config(deploy={"payload": [str(tmp_path / "nope")]})
    assert local_preflight(config, push_payload=False) == []
    assert local_preflight(config, push_payload=True) != []


def test_input_file_is_not_a_deploy_concern(tmp_path):
    # input_file is consumed by scatter / read on the remote, not sent by deploy,
    # so a missing local input_file must not fail deploy pre-flight.
    config = _config(input_file=str(tmp_path / "missing.txt"))
    assert local_preflight(config, push_payload=True) == []
