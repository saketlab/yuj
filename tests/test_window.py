"""Tests for the active-window (quiet-hours) gating."""

from __future__ import annotations

import pytest

from yuj.exceptions import YujError
from yuj.fleet import Host
from yuj.supervise import SuperviseConfig, render_watchdog
from yuj.window import Window


class TestParse:
    def test_basic(self) -> None:
        w = Window.parse("19:00-09:30")
        assert w.start_min == 19 * 60
        assert w.end_min == 9 * 60 + 30
        assert w.label == "19:00-09:30"

    def test_no_pad_hours(self) -> None:
        assert Window.parse("9:05-17:00").start_min == 9 * 60 + 5

    @pytest.mark.parametrize(
        "bad",
        ["", "19:00", "19:00-", "25:00-09:00", "19:60-09:00", "19-9", "abc"],
    )
    def test_invalid_raises(self, bad: str) -> None:
        with pytest.raises(YujError):
            Window.parse(bad)


class TestContains:
    def test_wrapping_window(self) -> None:
        w = Window.parse("19:00-09:30")  # night window
        assert w.wraps_midnight
        assert w.contains_minute(20 * 60)  # 20:00 inside
        assert w.contains_minute(2 * 60)  # 02:00 inside
        assert w.contains_minute(19 * 60)  # exactly 19:00 inside (inclusive start)
        assert not w.contains_minute(9 * 60 + 30)  # 09:30 excluded (end)
        assert not w.contains_minute(12 * 60)  # noon outside

    def test_same_day_window(self) -> None:
        w = Window.parse("09:00-17:00")
        assert not w.wraps_midnight
        assert w.contains_minute(12 * 60)
        assert not w.contains_minute(8 * 60)
        assert not w.contains_minute(17 * 60)  # end exclusive

    def test_always_on(self) -> None:
        w = Window(0, 0)
        assert w.always_on
        assert w.contains_minute(0)
        assert w.contains_minute(13 * 60)


class TestSuperviseConfig:
    def _cfg(self, **kw: object) -> SuperviseConfig:
        base: dict[str, object] = {
            "job": "t",
            "remote_dir": "yuj-run",
            "work_command": "bash run.sh",
            "results_glob": "~/yuj-run/results/*",
        }
        base.update(kw)
        return SuperviseConfig(**base)  # type: ignore[arg-type]

    def test_validates_window(self) -> None:
        with pytest.raises(YujError):
            self._cfg(active_window="nope")

    def test_window_property(self) -> None:
        assert self._cfg(active_window="19:00-09:30").window == Window(19 * 60, 570)
        assert self._cfg().window is None

    def test_watchdog_renders_gate(self) -> None:
        out = render_watchdog(self._cfg(active_window="19:00-09:30"))
        assert "within_window()" in out
        assert "WIN_START=1140" in out
        assert "WIN_END=570" in out
        assert "outside active window 19:00-09:30" in out

    def test_watchdog_no_gate_without_window(self) -> None:
        out = render_watchdog(self._cfg())
        assert "within_window()" not in out

    def test_off_window_command_rendered(self) -> None:
        out = render_watchdog(
            self._cfg(active_window="19:00-09:30", off_window_command="pkill ollama")
        )
        assert "pkill ollama" in out


class TestHostConfig:
    def _cfg(self) -> SuperviseConfig:
        return SuperviseConfig(
            job="t",
            remote_dir="yuj-run",
            work_command="bash run.sh",
            results_glob="~/r/*",
            active_window="19:00-09:30",  # job default
        )

    def test_host_window_overrides_job_default(self) -> None:
        host = Host(name="h", ip="1.1.1.1", user="u", window="08:00-18:00")
        assert self._cfg().for_host(host).active_window == "08:00-18:00"

    def test_no_host_window_keeps_job_default(self) -> None:
        host = Host(name="h", ip="1.1.1.1", user="u")
        assert self._cfg().for_host(host).active_window == "19:00-09:30"

    def test_invalid_host_window_rejected(self) -> None:
        with pytest.raises(YujError):
            Host(name="h", ip="1.1.1.1", user="u", window="nope")
