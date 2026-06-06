"""Tests for parallel, host-down-tolerant result pulling (stubbed transport)."""

from __future__ import annotations

from pathlib import Path

import pytest

from yuj import pull as pull_module
from yuj._shell import CommandResult
from yuj.exceptions import AuthError
from yuj.fleet import Fleet, Host


def _fleet() -> Fleet:
    return Fleet(
        (
            Host(name="a", ip="1", user="u", password="p"),
            Host(name="b", ip="2", user="u", password="p"),
            Host(name="down", ip="3", user="u", password="p"),
        )
    )


class _StubTransport:
    """Stand-in for SSHTransport whose get() outcome depends on the host name."""

    def __init__(self, host: Host, *, connect_timeout: int = 20) -> None:
        self.host = host

    def get(self, remote, local, **kwargs):  # type: ignore[no-untyped-def]
        if self.host.name == "down":
            raise AuthError("host down / banned")
        if self.host.name == "b":
            return CommandResult(23, "", "rsync: partial transfer")
        return CommandResult(0, "", "")


@pytest.fixture
def _patch_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull_module, "SSHTransport", _StubTransport)


def test_pull_once_reports_per_host(tmp_path: Path, _patch_transport: None) -> None:
    results = pull_module.pull_once(_fleet(), remote_dir="~/out", dest_dir=tmp_path)
    assert set(results) == {"a", "b", "down"}
    assert results["a"].ok is True
    assert results["b"].ok is False  # non-zero rsync exit
    assert results["down"].ok is False  # raised, caught


def test_pull_down_host_does_not_break_others(
    tmp_path: Path, _patch_transport: None
) -> None:
    results = pull_module.pull_once(_fleet(), remote_dir="~/out", dest_dir=tmp_path)
    assert results["down"].error is not None
    assert results["a"].error is None


def test_pull_creates_dest_dir(tmp_path: Path, _patch_transport: None) -> None:
    dest = tmp_path / "central"
    pull_module.pull_once(_fleet(), remote_dir="~/out", dest_dir=dest)
    assert dest.is_dir()


def test_pull_merges_into_single_dir_by_default(
    tmp_path: Path, _patch_transport: None
) -> None:
    results = pull_module.pull_once(_fleet(), remote_dir="~/out", dest_dir=tmp_path)
    assert results["a"].destination == tmp_path


def test_pull_per_host_subdir(tmp_path: Path, _patch_transport: None) -> None:
    results = pull_module.pull_once(
        _fleet(), remote_dir="~/out", dest_dir=tmp_path, per_host_subdir=True
    )
    assert results["a"].destination == tmp_path / "a"
    assert (tmp_path / "a").is_dir()


def test_pull_empty_fleet_returns_empty(tmp_path: Path, _patch_transport: None) -> None:
    results = pull_module.pull_once(Fleet(()), remote_dir="~/out", dest_dir=tmp_path)
    assert results == {}
