"""Tests for host probing, status parsing, and state classification."""

from __future__ import annotations

import pytest

from yuj import probe as probe_module
from yuj._shell import CommandResult
from yuj.exceptions import AuthError, YujError
from yuj.fleet import Fleet, Host
from yuj.probe import parse_status, probe_fleet, probe_host
from yuj.status import HostStatus
from yuj.transport import SSHTransport

HOST = Host(name="box", ip="10.0.0.1", user="u", password="p")

BLOCK = (
    "Last login: yesterday\n"  # banner noise before the block
    "YUJSTATUS\n"
    "host=box1\n"
    "load=1.50\n"
    "nproc=8\n"
    "mem=31\n"
    "cpu=Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz\n"
    "gpu=NVIDIA A40, 46068 MiB\n"
    "outputs=42\n"
    "age=3\n"
    "wd=1\n"
    "console=\n"
    "YUJEND\n"
)


class TestParseStatus:
    def test_parses_all_fields(self) -> None:
        s = parse_status(BLOCK, HOST)
        assert s.reachable is True
        assert s.hostname == "box1"
        assert s.load1 == 1.5
        assert s.nproc == 8
        assert s.mem_gb == 31
        assert s.n_outputs == 42
        assert s.newest_age_min == 3
        assert s.watchdog_running is True
        assert s.console_user is None

    def test_shortens_cpu_model(self) -> None:
        s = parse_status(BLOCK, HOST)
        assert s.cpu_model == "Intel Xeon Gold 6248"  # (R)/(TM)/CPU/@ stripped

    def test_shortens_gpu(self) -> None:
        s = parse_status(BLOCK, HOST)
        assert s.gpu == "A40 45G"

    def test_detects_owner(self) -> None:
        block = BLOCK.replace("console=\n", "console=alice\n")
        s = parse_status(block, HOST)
        assert s.console_user == "alice"
        assert s.owner_present is True

    def test_missing_fields_are_none(self) -> None:
        s = parse_status("YUJSTATUS\nhost=x\nYUJEND\n", HOST)
        assert s.load1 is None
        assert s.gpu is None
        assert s.newest_age_min is None
        assert s.watchdog_running is False

    def test_empty_cpu_and_gpu_are_none(self) -> None:
        s = parse_status("YUJSTATUS\ncpu=\ngpu=\nYUJEND\n", HOST)
        assert s.cpu_model is None
        assert s.gpu is None

    def test_gpu_without_memory_keeps_name(self) -> None:
        s = parse_status("YUJSTATUS\ngpu=NVIDIA A100\nYUJEND\n", HOST)
        assert s.gpu == "A100"

    def test_lines_before_block_ignored(self) -> None:
        # A stray key=value before YUJSTATUS must not be parsed.
        s = parse_status("host=evil\nYUJSTATUS\nnproc=4\nYUJEND\n", HOST)
        assert s.hostname is None
        assert s.nproc == 4


class TestState:
    def _status(self, **kw: object) -> HostStatus:
        base: dict[str, object] = {"name": "h", "ip": "1", "reachable": True}
        base.update(kw)
        return HostStatus(**base)  # type: ignore[arg-type]

    def test_down(self) -> None:
        assert self._status(reachable=False).state() == "down"

    def test_producing_recent_output(self) -> None:
        assert self._status(newest_age_min=5, n_outputs=10).state(90) == "producing"

    def test_stalled_old_output_with_watchdog(self) -> None:
        s = self._status(newest_age_min=200, n_outputs=10, watchdog_running=True)
        assert s.state(90) == "stalled"

    def test_stalled_old_output_no_watchdog(self) -> None:
        assert self._status(newest_age_min=200, n_outputs=10).state(90) == "stalled"

    def test_idle_watchdog_up_no_output_yet(self) -> None:
        assert self._status(watchdog_running=True).state(90) == "idle"

    def test_idle_nothing(self) -> None:
        assert self._status().state(90) == "idle"


class TestProbeHost:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            SSHTransport,
            "run",
            lambda self, cmd, timeout=None: CommandResult(0, BLOCK, ""),
        )
        s = probe_host(HOST)
        assert s.reachable and s.n_outputs == 42

    def test_unreachable_on_transport_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(self, cmd, timeout=None):  # type: ignore[no-untyped-def]
            raise AuthError("banned")

        monkeypatch.setattr(SSHTransport, "run", boom)
        s = probe_host(HOST)
        assert s.reachable is False and s.error is not None

    def test_missing_marker_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            SSHTransport,
            "run",
            lambda self, cmd, timeout=None: CommandResult(0, "weird", ""),
        )
        assert probe_host(HOST).reachable is False

    def test_nonzero_exit_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            SSHTransport,
            "run",
            lambda self, cmd, timeout=None: CommandResult(255, "", "no route"),
        )
        s = probe_host(HOST)
        assert s.reachable is False and s.error == "no route"

    def test_unsafe_glob_rejected(self) -> None:
        with pytest.raises(YujError, match="unsafe results glob"):
            probe_module._status_command("~/out/*; rm -rf /")


class TestProbeFleet:
    def test_returns_in_fleet_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fleet = Fleet(
            tuple(
                Host(name=n, ip=str(i), user="u", password="p")
                for i, n in enumerate("abc")
            )
        )
        monkeypatch.setattr(
            probe_module,
            "probe_host",
            lambda host, **kw: HostStatus(name=host.name, ip=host.ip, reachable=True),
        )
        assert [s.name for s in probe_fleet(fleet)] == ["a", "b", "c"]

    def test_empty_fleet(self) -> None:
        assert probe_fleet(Fleet(())) == []
