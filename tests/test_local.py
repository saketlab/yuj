"""Tests for LocalTransport and the local/SSH transport factory."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from yuj.fleet import Host
from yuj.local import LocalTransport, _expand
from yuj.transport import SSHTransport, Transport, make_transport

_HAS_RSYNC = shutil.which("rsync") is not None


class TestIsLocal:
    def test_explicit_flag(self) -> None:
        assert Host(name="me", ip="10.0.0.9", user="u", local=True).is_local

    @pytest.mark.parametrize("ip", ["localhost", "127.0.0.1", "::1"])
    def test_loopback_autodetect(self, ip: str) -> None:
        assert Host(name="me", ip=ip, user="u").is_local

    def test_remote_not_local(self) -> None:
        assert not Host(name="box", ip="10.0.0.9", user="u").is_local


class TestFactory:
    def test_local_host_gets_local_transport(self) -> None:
        t = make_transport(Host(name="me", ip="localhost", user="u"))
        assert isinstance(t, LocalTransport)
        assert isinstance(t, Transport)  # runtime-checkable protocol

    def test_remote_host_gets_ssh_transport(self) -> None:
        t = make_transport(Host(name="box", ip="10.0.0.9", user="u", password="p"))
        assert isinstance(t, SSHTransport)


class TestExpand:
    def test_relative_is_home_relative(self) -> None:
        assert _expand("yuj-run") == str(Path.home() / "yuj-run")

    def test_absolute_kept(self) -> None:
        assert _expand("/tmp/x") == "/tmp/x"

    def test_trailing_slash_preserved(self) -> None:
        assert _expand("yuj-run/").endswith("yuj-run/")


class TestRun:
    def _t(self) -> LocalTransport:
        return LocalTransport(Host(name="me", ip="localhost", user="u"))

    def test_run_captures_stdout(self) -> None:
        r = self._t().run("echo hello-yuj")
        assert r.ok and "hello-yuj" in r.stdout

    def test_run_nonzero(self) -> None:
        assert self._t().run("exit 7").returncode == 7

    def test_check_true(self) -> None:
        assert self._t().check() is True

    def test_input_text_to_stdin(self) -> None:
        r = self._t().run("cat", input_text="piped\n")
        assert "piped" in r.stdout


@pytest.mark.skipif(not _HAS_RSYNC, reason="rsync not installed")
class TestTransfer:
    def _t(self) -> LocalTransport:
        return LocalTransport(Host(name="me", ip="localhost", user="u"))

    def test_put_then_get(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("payload")
        dest_dir = tmp_path / "remote"
        dest_dir.mkdir()
        put = self._t().put(str(src), str(dest_dir) + "/")
        assert put.ok and (dest_dir / "src.txt").read_text() == "payload"

        back = tmp_path / "back"
        back.mkdir()
        got = self._t().get(str(dest_dir / "src.txt"), str(back) + "/")
        assert got.ok and (back / "src.txt").read_text() == "payload"
