"""Tests for SuperviseConfig and submit() install logic (fake transport)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from yuj import supervise as supervise_module
from yuj._shell import CommandResult
from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host
from yuj.supervise import SubmitResult, SuperviseConfig, stop, submit, submit_fleet

CFG = SuperviseConfig(
    job="b20",
    remote_dir="yuj-run",
    work_command="bash worker.sh",
    results_glob="~/yuj-run/results/*",
)


@dataclass
class _FakeHost:
    name: str = "box"


@dataclass
class _FakeTransport:
    """Records run/put calls and replies to the verify probe as configured."""

    host: _FakeHost = field(default_factory=_FakeHost)
    runs: list[str] = field(default_factory=list)
    puts: list[str] = field(default_factory=list)
    watchdog_ok: bool = True
    cron_ok: bool = True

    def run(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.runs.append(command)
        if command.startswith("echo wd="):
            wd = 1 if self.watchdog_ok else 0
            cron = 1 if self.cron_ok else 0
            return CommandResult(0, f"wd={wd}\ncron={cron}\n", "")
        return CommandResult(0, "", "")

    def put(
        self, local: str, remote: str, *, timeout: float | None = None
    ) -> CommandResult:
        self.puts.append(str(local))
        return CommandResult(0, "", "")


class TestSuperviseConfig:
    def test_invalid_job_name_raises(self) -> None:
        with pytest.raises(YujError, match="invalid job name"):
            SuperviseConfig(
                job="bad name!", remote_dir="d", work_command="x", results_glob="~/*"
            )

    def test_input_file_requires_output_dir(self) -> None:
        with pytest.raises(YujError, match="output_dir is required"):
            SuperviseConfig(
                job="j",
                remote_dir="d",
                work_command="x",
                results_glob="~/*",
                input_file="items.txt",
            )

    def test_unsafe_results_glob_raises(self) -> None:
        with pytest.raises(YujError, match="unsafe results glob"):
            SuperviseConfig(
                job="j",
                remote_dir="d",
                work_command="x",
                results_glob="~/out/*; rm -rf /",
            )

    def test_unsafe_remote_path_raises(self) -> None:
        with pytest.raises(YujError, match="unsafe remote_dir"):
            SuperviseConfig(
                job="j",
                remote_dir="d;rm -rf /",
                work_command="x",
                results_glob="~/*",
            )

    def test_derived_names(self) -> None:
        assert CFG.run_script == "b20.yuj-run.sh"
        assert CFG.watchdog_script == "b20.yuj-watchdog.sh"
        assert CFG.ensure_script == "b20.yuj-ensure.sh"
        assert CFG.stop_sentinel == "/tmp/b20.yuj.stop"
        assert CFG.cron_line.startswith("*/15 * * * *")
        assert "b20.yuj-ensure.sh" in CFG.cron_line


class TestSubmit:
    def test_uploads_three_scripts(self) -> None:
        t = _FakeTransport()
        submit(t, CFG)  # type: ignore[arg-type]
        names = {p.rsplit("/", 1)[-1] for p in t.puts}
        assert names == {
            "b20.yuj-run.sh",
            "b20.yuj-watchdog.sh",
            "b20.yuj-ensure.sh",
        }

    def test_creates_dir_chmods_and_clears_sentinel(self) -> None:
        t = _FakeTransport()
        submit(t, CFG)  # type: ignore[arg-type]
        joined = "\n".join(t.runs)
        assert "mkdir -p" in joined
        assert "chmod +x" in joined
        assert "rm -f" in joined and "b20.yuj.stop" in joined

    def test_cron_is_deduped_then_added(self) -> None:
        t = _FakeTransport()
        submit(t, CFG)  # type: ignore[arg-type]
        cron_cmd = next(r for r in t.runs if "crontab -" in r)
        assert "grep -Fv" in cron_cmd  # -F: drop only THIS job's line, keep others
        assert "b20.yuj-ensure.sh" in cron_cmd
        assert "crontab -l" in cron_cmd

    def test_verify_matches_only_this_jobs_watchdog(self) -> None:
        # regression: a generic watchdog grep counts other jobs' watchdogs on the
        # account, falsely confirming a submit whose own watchdog never started
        t = _FakeTransport()
        submit(t, CFG)  # type: ignore[arg-type]
        wd_cmd = next(r for r in t.runs if r.startswith("echo wd="))
        assert "b20.yuj-watchdog.sh" in wd_cmd

    def test_starts_watchdog_by_default(self) -> None:
        t = _FakeTransport()
        submit(t, CFG)  # type: ignore[arg-type]
        assert any("nohup bash" in r and "watchdog" in r for r in t.runs)

    def test_no_start_skips_watchdog_launch(self) -> None:
        t = _FakeTransport()
        submit(t, CFG, start=False)  # type: ignore[arg-type]
        assert not any("nohup bash" in r for r in t.runs)

    def test_ok_when_verify_confirms(self) -> None:
        result = submit(_FakeTransport(), CFG)  # type: ignore[arg-type]
        assert result.ok
        assert result.watchdog_running and result.cron_installed

    def test_not_ok_when_watchdog_missing(self) -> None:
        result = submit(_FakeTransport(watchdog_ok=False), CFG)  # type: ignore[arg-type]
        assert not result.ok
        assert result.error is not None

    def test_mkdir_failure_returns_error(self) -> None:
        @dataclass
        class _MkdirFails(_FakeTransport):
            def run(
                self, command: str, *, timeout: float | None = None
            ) -> CommandResult:
                self.runs.append(command)
                if command.startswith("mkdir"):
                    return CommandResult(1, "", "permission denied")
                return CommandResult(0, "", "")

        result = submit(_MkdirFails(), CFG)  # type: ignore[arg-type]
        assert not result.ok
        assert "could not create" in (result.error or "")

    def test_upload_failure_returns_error(self) -> None:
        @dataclass
        class _PutFails(_FakeTransport):
            def put(
                self, local: str, remote: str, *, timeout: float | None = None
            ) -> CommandResult:
                self.puts.append(str(local))
                return CommandResult(23, "", "rsync failed")

        result = submit(_PutFails(), CFG)  # type: ignore[arg-type]
        assert not result.ok
        assert "upload" in (result.error or "")

    def test_idempotent_calls(self) -> None:
        t = _FakeTransport()
        submit(t, CFG)  # type: ignore[arg-type]
        first_runs = list(t.runs)
        first_names = sorted(p.rsplit("/", 1)[-1] for p in t.puts)
        t.runs.clear()
        t.puts.clear()
        submit(t, CFG)  # type: ignore[arg-type]
        assert t.runs == first_runs
        assert sorted(p.rsplit("/", 1)[-1] for p in t.puts) == first_names


def test_stop_touches_sentinel() -> None:
    t = _FakeTransport()
    stop(t, CFG)  # type: ignore[arg-type]
    assert any("touch" in r and "b20.yuj.stop" in r for r in t.runs)


class TestSubmitFleet:
    def test_parallel_submit_per_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fleet = Fleet(
            (
                Host(name="a", ip="1", user="u", password="p"),
                Host(name="b", ip="2", user="u", password="p"),
            )
        )
        monkeypatch.setattr(
            supervise_module,
            "submit",
            lambda transport, cfg, **kw: SubmitResult(
                host=transport.host.name,
                ok=True,
                watchdog_running=True,
                cron_installed=True,
            ),
        )
        results = submit_fleet(fleet, CFG)
        assert set(results) == {"a", "b"}
        assert all(r.ok for r in results.values())

    def test_empty_fleet(self) -> None:
        assert submit_fleet(Fleet(()), CFG) == {}
