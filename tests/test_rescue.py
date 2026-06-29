"""Tests for the melt-rescue retry loop and kill payload (fake transport)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from yuj import rescue as rescue_mod
from yuj._shell import CommandResult
from yuj.exceptions import TransportError
from yuj.rescue import _rescue_payload, rescue_host

_OK = CommandResult(0, "YUJRESCUE load=3.5\n", "")
_MISS = CommandResult(0, "garbage, no marker\n", "")


@dataclass
class _FakeHost:
    name: str = "box"
    is_local: bool = False


def _patch(monkeypatch: pytest.MonkeyPatch, behaviors: list) -> list[str]:
    """Make every make_transport().run() pop the next scripted behaviour."""
    shared = iter(behaviors)
    commands: list[str] = []

    def fake_make_transport(host, *, connect_timeout=20, backend="subprocess"):
        class _T:
            def __init__(self) -> None:
                self.host = host

            def run(
                self, command: str, *, timeout=None, input_text=None
            ) -> CommandResult:
                commands.append(command)
                outcome = next(shared)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        return _T()

    monkeypatch.setattr(rescue_mod, "make_transport", fake_make_transport)
    return commands


class TestRescueHost:
    def test_first_window_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, [_OK])
        r = rescue_host(_FakeHost(), drain_rounds=0, sleep_fn=lambda _: None)  # type: ignore[arg-type]
        assert r.rescued
        assert r.attempts == 1
        assert r.load_before == 3.5

    def test_retries_through_banner_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        behaviors = [TransportError("banner exchange"), TransportError("banner"), _OK]
        _patch(monkeypatch, behaviors)
        r = rescue_host(
            _FakeHost(), attempts=5, drain_rounds=0, sleep_fn=lambda _: None
        )  # type: ignore[arg-type]
        assert r.rescued
        assert r.attempts == 3

    def test_exhausted_needs_power_cycle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, [TransportError("banner")] * 3)
        r = rescue_host(
            _FakeHost(), attempts=3, drain_rounds=0, sleep_fn=lambda _: None
        )  # type: ignore[arg-type]
        assert not r.rescued
        assert r.attempts == 3
        assert "power-cycle" in (r.error or "")

    def test_drain_updates_load_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, [_OK, CommandResult(0, "YUJRESCUE load=0.8\n", "")])
        r = rescue_host(_FakeHost(), drain_rounds=1, sleep_fn=lambda _: None)  # type: ignore[arg-type]
        assert r.rescued
        assert r.load_before == 3.5
        assert r.load_after == 0.8

    def test_marker_miss_is_not_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, [_MISS, _MISS])
        r = rescue_host(
            _FakeHost(), attempts=2, drain_rounds=0, sleep_fn=lambda _: None
        )  # type: ignore[arg-type]
        assert not r.rescued


class TestPayload:
    def test_default_strips_cron_and_detaches(self) -> None:
        p = _rescue_payload(strip_cron=True, pattern=())
        assert "crontab -r" in p
        assert "setsid sh -c" in p
        assert "YUJRESCUE" in p
        assert "ps -u" in p  # the all-user-procs kill

    def test_keep_cron(self) -> None:
        assert "crontab -r" not in _rescue_payload(strip_cron=False, pattern=())

    def test_pattern_uses_pkill(self) -> None:
        p = _rescue_payload(strip_cron=True, pattern=("python", "ollama"))
        assert "pkill" in p and "python" in p and "ollama" in p
        assert "ps -u" not in p  # pattern mode doesn't nuke all procs
