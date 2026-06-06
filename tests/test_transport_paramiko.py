"""Tests for the pure-Python paramiko transport fallback.

paramiko is an optional extra and may not be installed, so these tests inject a
fake ``paramiko`` module into ``sys.modules`` to exercise the backend's wiring
(connect args, exec_command result handling, sftp put/get, error translation)
without a real SSH server or the dependency.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from yuj.exceptions import AuthError, TransportError
from yuj.fleet import Host
from yuj.transport import SSHTransport

PW_HOST = Host(name="pw", ip="10.0.0.1", user="saket", password="s3cret")
KEY_HOST = Host(name="key", ip="10.0.0.2", user="saket", key_path="/k/id")


class _FakeChannel:
    def __init__(self, code: int) -> None:
        self._code = code

    def recv_exit_status(self) -> int:
        return self._code


class _FakeStream:
    def __init__(self, data: bytes, code: int = 0) -> None:
        self._data = data
        self.channel = _FakeChannel(code)

    def read(self) -> bytes:
        return self._data


class _FakeSFTP:
    def __init__(self, record: list[tuple[str, str, str]]) -> None:
        self.record = record

    def put(self, local: str, remote: str) -> None:
        self.record.append(("put", local, remote))

    def get(self, remote: str, local: str) -> None:
        self.record.append(("get", remote, local))

    def close(self) -> None:
        pass


def _make_paramiko(
    *,
    connect_error: Exception | None = None,
    auth_fail: bool = False,
    exit_code: int = 0,
    sftp_error: Exception | None = None,
    sftp_record: list[tuple[str, str, str]] | None = None,
    connect_capture: dict[str, Any] | None = None,
) -> types.ModuleType:
    """Build a fake ``paramiko`` module with configurable behavior."""

    class AuthenticationException(Exception):
        pass

    class AutoAddPolicy:
        pass

    class SSHClient:
        def set_missing_host_key_policy(self, policy: object) -> None:
            pass

        def connect(self, **kwargs: Any) -> None:
            if connect_capture is not None:
                connect_capture.update(kwargs)
            if auth_fail:
                raise AuthenticationException("bad password")
            if connect_error is not None:
                raise connect_error

        def exec_command(
            self, command: str, timeout: float | None = None
        ) -> tuple[None, _FakeStream, _FakeStream]:
            return (None, _FakeStream(b"out", exit_code), _FakeStream(b"err"))

        def open_sftp(self) -> _FakeSFTP:
            if sftp_error is not None:
                raise sftp_error
            return _FakeSFTP(sftp_record if sftp_record is not None else [])

        def close(self) -> None:
            pass

    module = types.ModuleType("paramiko")
    module.AuthenticationException = AuthenticationException  # type: ignore[attr-defined]
    module.AutoAddPolicy = AutoAddPolicy  # type: ignore[attr-defined]
    module.SSHClient = SSHClient  # type: ignore[attr-defined]
    return module


def _install(monkeypatch: pytest.MonkeyPatch, module: types.ModuleType) -> None:
    monkeypatch.setitem(sys.modules, "paramiko", module)


def test_paramiko_run_returns_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _make_paramiko(exit_code=0))
    result = SSHTransport(PW_HOST, backend="paramiko").run("echo hi")
    assert result.ok
    assert result.stdout == "out"
    assert result.stderr == "err"


def test_paramiko_run_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _make_paramiko(exit_code=7))
    result = SSHTransport(PW_HOST, backend="paramiko").run("false")
    assert result.returncode == 7


def test_paramiko_connect_passes_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _install(monkeypatch, _make_paramiko(connect_capture=captured))
    SSHTransport(KEY_HOST, backend="paramiko").run("ls")
    assert captured["username"] == "saket"
    assert captured["key_filename"] == "/k/id"
    assert captured["look_for_keys"] is True


def test_paramiko_auth_error_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _make_paramiko(auth_fail=True))
    with pytest.raises(AuthError, match="authentication refused"):
        SSHTransport(PW_HOST, backend="paramiko").run("ls")


def test_paramiko_connect_oserror_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _make_paramiko(connect_error=OSError("no route")))
    with pytest.raises(TransportError, match="could not connect"):
        SSHTransport(PW_HOST, backend="paramiko").run("ls")


def test_paramiko_put_via_sftp(monkeypatch: pytest.MonkeyPatch) -> None:
    record: list[tuple[str, str, str]] = []
    _install(monkeypatch, _make_paramiko(sftp_record=record))
    result = SSHTransport(PW_HOST, backend="paramiko").put("local.txt", "remote.txt")
    assert result.ok
    assert record == [("put", "local.txt", "remote.txt")]


def test_paramiko_get_via_sftp(monkeypatch: pytest.MonkeyPatch) -> None:
    record: list[tuple[str, str, str]] = []
    _install(monkeypatch, _make_paramiko(sftp_record=record))
    SSHTransport(PW_HOST, backend="paramiko").get("remote.txt", "local.txt")
    assert record == [("get", "remote.txt", "local.txt")]


def test_paramiko_sftp_error_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _make_paramiko(sftp_error=OSError("disk full")))
    with pytest.raises(TransportError, match="sftp transfer failed"):
        SSHTransport(PW_HOST, backend="paramiko").put("l", "r")


def test_paramiko_not_installed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate paramiko being unavailable.
    monkeypatch.setitem(sys.modules, "paramiko", None)
    with pytest.raises(TransportError, match="paramiko is not installed"):
        SSHTransport(PW_HOST, backend="paramiko").run("ls")
