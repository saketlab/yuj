"""Tests for host probing, status parsing, and state classification."""

from __future__ import annotations

import re

import pytest

from yuj import probe as probe_module
from yuj._shell import CommandResult
from yuj.exceptions import AuthError, YujError
from yuj.fleet import Fleet, Host
from yuj.probe import parse_gpus, parse_status, probe_fleet, probe_host
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
    "outputs=42\n"
    "age=3\n"
    "wd=1\n"
    "console=\n"
    "YUJEND\n"
    "gpu:0, NVIDIA A40, 46068, 0, GPU-aaa\n"
)


class TestParseGpus:
    def test_reads_cards_and_owners(self) -> None:
        gpus = parse_gpus(
            "gpu:0, NVIDIA A6000, 49140, 22330, GPU-aaa\n"
            "gpu:1, NVIDIA A6000, 49140, 7170, GPU-bbb\n"
            "gpuproc:GPU-bbb, 101\n"
            "gpuproc:GPU-bbb, 102\n"
            "pid:  101 bgoswami /opt/ollama runner --model x\n"
            "pid:  102 bgoswami /opt/ollama runner --model y\n"
        )
        assert len(gpus) == 2
        assert gpus[0].free_mb == 26_810
        assert gpus[1].users == ("bgoswami",)  # duplicate pids collapse to one user
        assert gpus[1].shared_with_others("me")
        assert not gpus[0].shared_with_others("me")

    def test_own_stale_run_is_foreign_when_a_marker_is_given(self) -> None:
        # Same Unix user, different launch: yuj must not stack on its leftovers.
        gpus = parse_gpus(
            "gpu:0, A6000, 49140, 22000, GPU-aaa\n"
            "gpu:1, A6000, 49140, 22000, GPU-bbb\n"
            "gpuproc:GPU-aaa, 101\n"
            "gpuproc:GPU-bbb, 102\n"
            "pid:  101 me ollama runner --old\n"
            "pid:  102 me ollama runner --job=abc\n"
        )
        assert gpus[0].shared_with_others("me", own_marker="--job=abc")
        assert not gpus[1].shared_with_others("me", own_marker="--job=abc")

    def test_card_name_containing_a_comma_still_parses(self) -> None:
        gpus = parse_gpus("gpu:0, NVIDIA RTX A6000, Gen4, 49140, 22330, GPU-aaa\n")
        assert len(gpus) == 1
        assert gpus[0].name == "NVIDIA RTX A6000, Gen4"
        assert gpus[0].mem_total_mb == 49_140
        assert gpus[0].free_mb == 26_810

    def test_unattributable_process_makes_every_card_shared(self) -> None:
        # MIG reports MIG-… device uuids that match no parent card. We cannot
        # tell which card is busy, so no card may be claimed as free.
        gpus = parse_gpus(
            "gpu:0, A100, 40960, 20000, GPU-aaa\n"
            "gpu:1, A100, 40960, 0, GPU-bbb\n"
            "gpuproc:MIG-deadbeef, 101\n"
            "pid:  101 someone /opt/train.py\n"
        )
        assert all(g.shared_with_others("me") for g in gpus)

    def test_tolerates_junk(self) -> None:
        assert parse_gpus("nvidia-smi: command not found") == ()
        assert parse_gpus("gpu:not,a,card\ngpuproc:x,y\n") == ()


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

    def test_empty_cpu_and_no_card_are_none(self) -> None:
        s = parse_status("YUJSTATUS\ncpu=\nYUJEND\n", HOST)
        assert s.cpu_model is None
        assert s.gpu is None

    def test_gpu_without_memory_keeps_name(self) -> None:
        s = parse_status("YUJSTATUS\nYUJEND\ngpu:0, NVIDIA A100, 0, 0, GPU-a\n", HOST)
        assert s.gpu == "A100"

    def test_lines_before_block_ignored(self) -> None:
        # A stray key=value before YUJSTATUS must not be parsed.
        s = parse_status("host=evil\nYUJSTATUS\nnproc=4\nYUJEND\n", HOST)
        assert s.hostname is None
        assert s.nproc == 4

    def test_do_not_use_host_marked_excluded(self) -> None:
        host = Host(name="r", ip="1", user="u", password="p", do_not_use=True)
        s = parse_status("YUJSTATUS\nhost=r\nYUJEND\n", host)
        assert s.excluded is True
        assert s.state() == "excluded"


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

    def test_stalled_watchdog_up_no_output(self) -> None:
        assert self._status(watchdog_running=True).state(90) == "stalled"

    def test_dead_cron_installed_watchdog_gone(self) -> None:
        assert self._status(cron_installed=True).state(90) == "dead"
        s = self._status(cron_installed=True, n_outputs=5, newest_age_min=200)
        assert s.state(90) == "dead"

    def test_live_watchdog_beats_dead(self) -> None:
        s = self._status(cron_installed=True, watchdog_running=True)
        assert s.state(90) == "stalled"

    def test_idle_nothing(self) -> None:
        assert self._status().state(90) == "idle"

    def test_excluded_wins(self) -> None:
        assert self._status(excluded=True).state(90) == "excluded"
        assert self._status(excluded=True, reachable=False).state(90) == "excluded"


class TestStatusCommandJobScope:
    def test_generic_marker_without_job(self) -> None:
        cmd = probe_module._status_command("~/results/*")
        assert "[y]uj-watchdog" in cmd

    def test_job_scoped_marker_in_command(self) -> None:
        cmd = probe_module._status_command("~/results/*", job="preflight-sc")
        assert r"[ /]preflight-sc\.yuj-watchdog\.sh" in cmd

    def test_cron_probed_only_when_job_given(self) -> None:
        assert "cron=" not in probe_module._status_command("~/results/*")
        cmd = probe_module._status_command("~/results/*", job="preflight-sc")
        assert "cron=" in cmd
        assert r"[ /]preflight-sc\.yuj-ensure\.sh" in cmd

    def test_marker_anchors_against_substring_collision(self) -> None:
        rx = re.compile(probe_module._watchdog_grep("sc"))
        assert rx.search("bash sc.yuj-watchdog.sh")
        assert rx.search("bash /home/u/d/sc.yuj-watchdog.sh")
        assert not rx.search("bash preflight-sc.yuj-watchdog.sh")

    def test_marker_escapes_dot_in_job(self) -> None:
        rx = re.compile(probe_module._watchdog_grep("a.b"))
        assert not rx.search("bash axb.yuj-watchdog.sh")


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
