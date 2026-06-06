"""Tests for deploy: idempotent mkdir + code/payload rsync, with a fake transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yuj._shell import CommandResult
from yuj.deploy import DeployPlan, DeployResult, deploy, deploy_fleet
from yuj.fleet import Fleet, Host


@dataclass
class _FakeHost:
    name: str = "box"


@dataclass
class _FakeTransport:
    """Records run/put calls instead of touching the network."""

    host: _FakeHost = field(default_factory=_FakeHost)
    runs: list[str] = field(default_factory=list)
    puts: list[tuple[str, str]] = field(default_factory=list)
    mkdir_ok: bool = True
    put_ok: bool = True

    def run(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.runs.append(command)
        return CommandResult(
            0 if self.mkdir_ok else 1, "", "" if self.mkdir_ok else "denied"
        )

    def put(
        self,
        local: str,
        remote: str,
        *,
        excludes: tuple[str, ...] = (),
        timeout: float | None = None,
    ) -> CommandResult:
        self.puts.append((str(local), remote))
        return CommandResult(
            0 if self.put_ok else 23, "", "" if self.put_ok else "fail"
        )


def _plan(tmp_path: Path) -> DeployPlan:
    code = tmp_path / "run.sh"
    code.write_text("echo hi\n")
    payload = tmp_path / "cache.pkl"
    payload.write_bytes(b"\x00\x01")
    return DeployPlan(
        remote_dir="yuj-run",
        code_paths=(code,),
        payload_paths=(payload,),
    )


def test_deploy_creates_remote_dir_first(tmp_path: Path) -> None:
    transport = _FakeTransport()
    deploy(transport, _plan(tmp_path))  # type: ignore[arg-type]
    assert transport.runs[0].startswith("mkdir -p")
    assert "yuj-run" in transport.runs[0]


def test_deploy_push_payload_sends_code_and_payload(tmp_path: Path) -> None:
    transport = _FakeTransport()
    result = deploy(transport, _plan(tmp_path), push_payload=True)  # type: ignore[arg-type]
    assert result.ok
    sent = {Path(local).name for local, _remote in transport.puts}
    assert sent == {"run.sh", "cache.pkl"}
    assert set(result.transferred) == {"run.sh", "cache.pkl"}


def test_deploy_trim_only_skips_payload(tmp_path: Path) -> None:
    transport = _FakeTransport()
    result = deploy(transport, _plan(tmp_path), push_payload=False)  # type: ignore[arg-type]
    assert result.ok
    sent = {Path(local).name for local, _remote in transport.puts}
    assert sent == {"run.sh"}
    assert "cache.pkl" not in result.transferred


def test_deploy_destination_has_trailing_slash(tmp_path: Path) -> None:
    transport = _FakeTransport()
    deploy(transport, _plan(tmp_path), push_payload=False)  # type: ignore[arg-type]
    _local, remote = transport.puts[0]
    assert remote == "yuj-run/"


def test_deploy_reports_mkdir_failure(tmp_path: Path) -> None:
    transport = _FakeTransport(mkdir_ok=False)
    result = deploy(transport, _plan(tmp_path))  # type: ignore[arg-type]
    assert not result.ok
    assert "could not create" in (result.error or "")
    assert transport.puts == []


def test_deploy_reports_transfer_failure(tmp_path: Path) -> None:
    transport = _FakeTransport(put_ok=False)
    result = deploy(transport, _plan(tmp_path))  # type: ignore[arg-type]
    assert not result.ok
    assert result.error is not None


def test_deploy_missing_source_reports_error(tmp_path: Path) -> None:
    transport = _FakeTransport()
    plan = DeployPlan(remote_dir="r", code_paths=(tmp_path / "ghost.sh",))
    result = deploy(transport, plan)  # type: ignore[arg-type]
    assert not result.ok
    assert "does not exist" in (result.error or "")


def test_deploy_is_idempotent_in_calls(tmp_path: Path) -> None:
    # Re-running issues the same mkdir + puts; nothing accumulates host-side.
    transport = _FakeTransport()
    plan = _plan(tmp_path)
    deploy(transport, plan)  # type: ignore[arg-type]
    first = (list(transport.runs), list(transport.puts))
    transport.runs.clear()
    transport.puts.clear()
    deploy(transport, plan)  # type: ignore[arg-type]
    assert (transport.runs, transport.puts) == first


class TestDeployFleet:
    def _fleet(self) -> Fleet:
        return Fleet(
            (
                Host(name="a", ip="1", user="u", password="p"),
                Host(name="b", ip="2", user="u", password="p"),
            )
        )

    def test_parallel_deploy_per_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        # `yuj.__init__` re-exports the `deploy` function, shadowing the
        # `yuj.deploy` submodule attribute, so fetch the module via sys.modules.
        deploy_module = sys.modules["yuj.deploy"]
        monkeypatch.setattr(
            deploy_module,
            "deploy",
            lambda transport, plan, **kw: DeployResult(
                host=transport.host.name, ok=True, transferred=("run.sh",)
            ),
        )
        results = deploy_fleet(self._fleet(), _plan(tmp_path))
        assert set(results) == {"a", "b"}
        assert all(r.ok for r in results.values())

    def test_empty_fleet(self, tmp_path: Path) -> None:
        assert deploy_fleet(Fleet(()), _plan(tmp_path)) == {}
