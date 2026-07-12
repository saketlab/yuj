"""Tests for decommission teardown + scheduling (fake transport)."""

from __future__ import annotations

from dataclasses import dataclass, field

from yuj._shell import CommandResult
from yuj.decommission import decommission, schedule_decommission
from yuj.supervise import SuperviseConfig

CFG = SuperviseConfig(
    job="yuj-test",
    remote_dir="yuj-test",
    work_command="true",
    results_glob="~/yuj-test/results/*",
)


@dataclass
class _FakeHost:
    name: str = "box"


@dataclass
class _FakeTransport:
    host: _FakeHost = field(default_factory=_FakeHost)
    runs: list[str] = field(default_factory=list)
    procs: int = 0
    cron: int = 0
    dir_present: int = 1

    def run(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.runs.append(command)
        out = f"procs={self.procs}\ncron={self.cron}\ndir={self.dir_present}\n"
        return CommandResult(0, out, "")


class TestDecommission:
    def test_clean_teardown_is_ok(self) -> None:
        result = decommission(_FakeTransport(procs=0, cron=0), CFG)  # type: ignore[arg-type]
        assert result.ok
        assert result.cron_removed
        assert result.processes_remaining == 0

    def test_job_scoped_kill_and_cron_removal(self) -> None:
        t = _FakeTransport()
        decommission(t, CFG)  # type: ignore[arg-type]
        script = t.runs[0]
        # Touches the stop sentinel, removes only this job's cron + processes.
        assert "yuj-test.yuj.stop" in script
        assert "grep -v" in script and "yuj-test.yuj-ensure.sh" in script
        assert "yuj-test.yuj-watchdog.sh" in script
        assert "yuj-test.yuj-run.sh" in script
        # Must NOT reference production names.
        assert "ensure_watchdog.sh" not in script
        assert "stall_watchdog.sh" not in script

    def test_kills_run_process_group_via_pgid(self) -> None:
        t = _FakeTransport()
        decommission(t, CFG)  # type: ignore[arg-type]
        script = t.runs[0]
        # kill by recorded pgid, else workers orphan and pile up
        assert ".yuj-test.run.pgid" in script
        assert "kill_group" in script
        assert 'kill "$sig" "-$pgid"' in script

    def test_not_ok_if_processes_remain(self) -> None:
        result = decommission(_FakeTransport(procs=2, cron=0), CFG)  # type: ignore[arg-type]
        assert not result.ok
        assert result.processes_remaining == 2

    def test_remove_dir_flag(self) -> None:
        t = _FakeTransport(dir_present=0)
        result = decommission(t, CFG, remove_dir=True)  # type: ignore[arg-type]
        assert "rm -rf" in t.runs[0]
        assert result.removed_dir


class TestScheduleDecommission:
    def test_relative_delay_uses_sleep(self) -> None:
        t = _FakeTransport()
        result = schedule_decommission(t, CFG, "+90 seconds")  # type: ignore[arg-type]
        assert result.ok and result.scheduled == "+90 seconds"
        assert "sleep 90" in t.runs[0]
        assert "setsid" in t.runs[0]

    def test_relative_minutes(self) -> None:
        t = _FakeTransport()
        schedule_decommission(t, CFG, "+2 minutes")  # type: ignore[arg-type]
        assert "sleep 120" in t.runs[0]

    def test_absolute_time_uses_at(self) -> None:
        t = _FakeTransport()
        schedule_decommission(t, CFG, "9am tomorrow")  # type: ignore[arg-type]
        assert "| at " in t.runs[0]
        assert "9am tomorrow" in t.runs[0]

    def test_at_failure_reported(self) -> None:
        @dataclass
        class _AtFails(_FakeTransport):
            def run(
                self, command: str, *, timeout: float | None = None
            ) -> CommandResult:
                self.runs.append(command)
                return CommandResult(1, "", "at: command not found")

        result = schedule_decommission(_AtFails(), CFG, "9am tomorrow")  # type: ignore[arg-type]
        assert not result.ok
        assert "at" in (result.error or "")
