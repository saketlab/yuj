"""Tests for fail2ban-aware host diagnosis classification."""

from __future__ import annotations

import pytest

from yuj import probe as probe_module
from yuj._shell import CommandResult
from yuj.exceptions import AuthError, CommandTimeout
from yuj.fleet import Fleet, Host
from yuj.probe import classify_host, diagnose_fleet

HOST = Host(name="h", ip="10.0.0.1", user="u", password="p")


def _patch_run(monkeypatch: pytest.MonkeyPatch, result=None, exc=None):  # type: ignore[no-untyped-def]
    def fake(self, cmd, timeout=None):  # type: ignore[no-untyped-def]
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(probe_module.SSHTransport, "run", fake)


class TestClassify:
    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, result=CommandResult(0, "yuj-auth-ok\n", ""))
        d = classify_host(HOST)
        assert d.status == "ok" and d.ok

    def test_auth_refused_via_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, exc=AuthError("authentication refused by h"))
        assert classify_host(HOST).status == "auth_refused"

    def test_auth_refused_via_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(
            monkeypatch, result=CommandResult(255, "", "Permission denied (publickey).")
        )
        assert classify_host(HOST).status == "auth_refused"

    def test_sshd_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(
            monkeypatch,
            result=CommandResult(255, "", "ssh: connect ... Connection refused"),
        )
        assert classify_host(HOST).status == "sshd_down"

    def test_banner_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(
            monkeypatch,
            result=CommandResult(
                255, "", "kex_exchange: Connection timed out during banner exchange"
            ),
        )
        assert classify_host(HOST).status == "banner_fail"

    def test_net_down_no_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, result=CommandResult(255, "", "ssh: No route to host"))
        assert classify_host(HOST).status == "net_down"

    def test_net_down_on_timeout_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_run(monkeypatch, exc=CommandTimeout("timed out"))
        assert classify_host(HOST).status == "net_down"

    def test_unknown_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, result=CommandResult(255, "", "something weird"))
        assert classify_host(HOST).status == "unknown"


class TestDiagnoseFleet:
    def test_order_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fleet = Fleet(
            tuple(Host(name=n, ip="1", user="u", password="p") for n in "abc")
        )
        _patch_run(monkeypatch, result=CommandResult(0, "yuj-auth-ok\n", ""))
        assert [d.name for d in diagnose_fleet(fleet)] == ["a", "b", "c"]

    def test_empty(self) -> None:
        assert diagnose_fleet(Fleet(())) == []
