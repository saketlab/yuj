"""Tests for fleet-wide public-key authorization (passwordless setup)."""

from __future__ import annotations

import pytest

from yuj._shell import CommandResult
from yuj.authorize import authorize_key
from yuj.exceptions import YujError
from yuj.fleet import Host
from yuj.keys import read_public_key

_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1 yuj-fleet"


class _FakeTransport:
    """Records the install command + stdin; returns a scripted result."""

    def __init__(self, host: Host, stdout: str = "YUJ-INSTALLED", rc: int = 0) -> None:
        self.host = host
        self._stdout = stdout
        self._rc = rc
        self.last_input: str | None = None

    def run(self, command, *, timeout=None, input_text=None):  # type: ignore[no-untyped-def]
        self.last_input = input_text
        self.saw_authorized_keys = "authorized_keys" in command
        return CommandResult(self._rc, self._stdout, "")


class TestReadPublicKey:
    def test_reads_and_strips(self, tmp_path) -> None:
        p = tmp_path / "k.pub"
        p.write_text(_PUB + "\n")
        assert read_public_key(p) == _PUB

    def test_rejects_non_key(self, tmp_path) -> None:
        p = tmp_path / "bad.pub"
        p.write_text("not a key")
        with pytest.raises(YujError):
            read_public_key(p)


class TestAuthorizeKey:
    def _host(self) -> Host:
        return Host(name="h", ip="1.1.1.1", user="u", password="p")

    def test_installs_via_stdin(self) -> None:
        t = _FakeTransport(self._host())
        res = authorize_key(t, _PUB)
        assert res.ok and not res.already
        assert t.last_input == _PUB  # key delivered over stdin, not argv
        assert t.saw_authorized_keys

    def test_idempotent_already(self) -> None:
        t = _FakeTransport(self._host(), stdout="YUJ-ALREADY")
        res = authorize_key(t, _PUB)
        assert res.ok and res.already

    def test_failure_reported(self) -> None:
        t = _FakeTransport(self._host(), stdout="", rc=1)
        res = authorize_key(t, _PUB)
        assert not res.ok and res.error
