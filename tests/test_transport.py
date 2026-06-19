"""Tests for SSHTransport command/env construction and failure classification.

These tests never touch the network: they assert on the argv/env the transport
builds, and they stub the subprocess layer (``yuj._shell.run``) to exercise the
auth/connection classification logic.
"""

from __future__ import annotations

import pytest

from yuj import _shell
from yuj._shell import CommandResult
from yuj.exceptions import AuthError, TransportError
from yuj.fleet import Host
from yuj.transport import SSHTransport, is_auth_failure

PW_HOST = Host(name="pw", ip="10.0.0.1", user="saket", password="s3cret")
KEY_HOST = Host(name="key", ip="10.0.0.2", user="saket", key_path="/home/saket/.ssh/id")


class TestBuilders:
    def test_run_command_password_sets_sshpass_and_env(self) -> None:
        argv, env = SSHTransport(PW_HOST).build_run_command("echo hi")
        assert argv[:2] == ["sshpass", "-e"]
        assert env["SSHPASS"] == "s3cret"
        assert argv[-1] == "echo hi"

    def test_run_command_password_not_in_argv(self) -> None:
        argv, _env = SSHTransport(PW_HOST).build_run_command("echo hi")
        assert "s3cret" not in " ".join(argv)

    def test_run_command_key_has_no_sshpass_no_secret_env(self) -> None:
        argv, env = SSHTransport(KEY_HOST).build_run_command("echo hi")
        assert argv[0] == "ssh"
        assert "SSHPASS" not in env
        assert "-i" in argv and "/home/saket/.ssh/id" in argv

    def test_put_command_uploads_to_remote_spec(self) -> None:
        argv, _env = SSHTransport(PW_HOST).build_put_command(
            "local/dir/", "/home/saket/run/"
        )
        assert argv[:2] == ["sshpass", "-e"]
        assert argv[-2] == "local/dir/"
        assert argv[-1] == "saket@10.0.0.1:/home/saket/run/"

    def test_get_command_downloads_from_remote_spec(self) -> None:
        argv, _env = SSHTransport(PW_HOST).build_get_command(
            "/home/saket/results/", "local/out/"
        )
        assert argv[-2] == "saket@10.0.0.1:/home/saket/results/"
        assert argv[-1] == "local/out/"

    def test_includes_excludes_propagate(self) -> None:
        argv, _env = SSHTransport(KEY_HOST).build_get_command(
            "r/", "l/", includes=["*.csv"], excludes=["*.log"]
        )
        assert "--include=*.csv" in argv
        assert "--exclude=*.log" in argv

    def test_strict_host_key_propagates(self) -> None:
        host = Host(
            name="strict",
            ip="10.0.0.3",
            user="saket",
            key_path="/k",
            strict_host_key=True,
            known_hosts_file="/tmp/known_hosts",
        )
        argv, _env = SSHTransport(host).build_run_command("echo hi")
        assert "StrictHostKeyChecking=yes" in argv
        assert "UserKnownHostsFile=/tmp/known_hosts" in argv

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(TransportError, match="unknown transport backend"):
            SSHTransport(PW_HOST, backend="carrier-pigeon")


class TestRunExecution:
    def test_run_returns_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _shell, "run", lambda *a, **k: CommandResult(0, "yuj-ok\n", "")
        )
        result = SSHTransport(PW_HOST).run("echo yuj-ok")
        assert result.ok
        assert "yuj-ok" in result.stdout

    def test_run_passes_env_with_sshpass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
            captured["argv"] = argv
            captured["env"] = kwargs.get("env")
            return CommandResult(0, "", "")

        monkeypatch.setattr(_shell, "run", fake_run)
        SSHTransport(PW_HOST).run("ls")
        assert captured["env"]["SSHPASS"] == "s3cret"  # type: ignore[index]

    def test_auth_failure_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _shell,
            "run",
            lambda *a, **k: CommandResult(255, "", "Permission denied (publickey)."),
        )
        with pytest.raises(AuthError, match="authentication refused"):
            SSHTransport(PW_HOST).run("ls")

    def test_nonzero_non_auth_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _shell, "run", lambda *a, **k: CommandResult(2, "", "ls: no such file")
        )
        result = SSHTransport(PW_HOST).run("ls /nope")
        assert result.returncode == 2

    def test_check_true_on_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _shell, "run", lambda *a, **k: CommandResult(0, "yuj-ok\n", "")
        )
        assert SSHTransport(PW_HOST).check() is True

    def test_check_false_on_transport_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a, **k):  # type: ignore[no-untyped-def]
            raise AuthError("nope")

        monkeypatch.setattr(_shell, "run", boom)
        assert SSHTransport(PW_HOST).check() is False


class TestTransfersExecution:
    def test_put_classifies_auth_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _shell,
            "run",
            lambda *a, **k: CommandResult(255, "", "Permission denied"),
        )
        with pytest.raises(AuthError):
            SSHTransport(PW_HOST).put("l/", "r/")

    def test_get_returns_result_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_shell, "run", lambda *a, **k: CommandResult(0, "", ""))
        result = SSHTransport(PW_HOST).get("r/", "l/")
        assert result.ok

    def test_put_returns_result_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_shell, "run", lambda *a, **k: CommandResult(0, "", ""))
        result = SSHTransport(PW_HOST).put("l/", "r/")
        assert result.ok


class TestIsAuthFailure:
    def test_matches_each_marker_case_insensitively(self) -> None:
        for text in (
            "Permission denied (publickey,password).",
            "ssh: Authentication failed.",
            "Received disconnect: Too many authentication failures",
        ):
            assert is_auth_failure(text) is True
            assert is_auth_failure(text.lower()) is True

    def test_non_auth_message_is_false(self) -> None:
        assert is_auth_failure("ssh: connect to host: Connection refused") is False
        assert is_auth_failure("") is False
