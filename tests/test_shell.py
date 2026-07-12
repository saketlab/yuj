"""Tests for safe subprocess execution and SSH/rsync command construction."""

from __future__ import annotations

import pytest

from yuj._shell import (
    have_executable,
    remote_spec,
    rsync_command,
    run,
    ssh_command,
    with_sshpass,
)
from yuj.exceptions import CommandTimeout, TransportError


class TestRun:
    def test_captures_stdout_and_exit_zero(self) -> None:
        result = run(["printf", "hello"])
        assert result.ok
        assert result.returncode == 0
        assert result.stdout == "hello"

    def test_nonzero_exit_not_raised_without_check(self) -> None:
        result = run(["sh", "-c", "exit 3"])
        assert not result.ok
        assert result.returncode == 3

    def test_check_raises_on_failure(self) -> None:
        with pytest.raises(TransportError, match="command failed"):
            run(["sh", "-c", "echo boom >&2; exit 1"], check=True)

    def test_timeout_raises(self) -> None:
        with pytest.raises(CommandTimeout, match="timed out"):
            run(["sleep", "5"], timeout=0.1)

    def test_missing_executable_raises(self) -> None:
        with pytest.raises(TransportError, match="not found"):
            run(["this-binary-does-not-exist-xyz"])

    def test_env_is_passed(self) -> None:
        result = run(["sh", "-c", "echo $YUJ_TEST_VAR"], env={"YUJ_TEST_VAR": "42"})
        assert result.stdout.strip() == "42"

    def test_input_text_is_passed(self) -> None:
        result = run(["cat"], input_text="piped")
        assert result.stdout == "piped"


class TestHaveExecutable:
    def test_known_present(self) -> None:
        assert have_executable("sh") is True

    def test_known_absent(self) -> None:
        assert have_executable("definitely-not-a-real-binary-xyz") is False


class TestSshCommand:
    def test_password_path_forces_no_pubkey(self) -> None:
        argv = ssh_command(user="u", host="1.2.3.4", remote_command="ls")
        assert argv[0] == "ssh"
        assert "PubkeyAuthentication=no" in argv
        assert argv[-2:] == ["u@1.2.3.4", "ls"]

    def test_key_path_enables_pubkey(self) -> None:
        argv = ssh_command(user="u", host="h", remote_command="ls", key_path="/k/id")
        assert "-i" in argv
        assert "/k/id" in argv
        assert "PubkeyAuthentication=yes" in argv
        assert "PubkeyAuthentication=no" not in argv

    def test_force_password_with_key(self) -> None:
        argv = ssh_command(
            user="u",
            host="h",
            remote_command="ls",
            key_path="/k",
            force_password=True,
        )
        assert "PubkeyAuthentication=no" in argv

    def test_custom_port_and_connect_timeout(self) -> None:
        argv = ssh_command(
            user="u", host="h", remote_command="ls", port=2222, connect_timeout=5
        )
        assert "-p" in argv and "2222" in argv
        assert "ConnectTimeout=5" in argv

    def test_known_hosts_are_disabled(self) -> None:
        argv = ssh_command(user="u", host="h", remote_command="ls")
        assert "StrictHostKeyChecking=no" in argv
        assert "UserKnownHostsFile=/dev/null" in argv

    def test_strict_host_key_uses_known_hosts(self) -> None:
        argv = ssh_command(
            user="u",
            host="h",
            remote_command="ls",
            strict_host_key=True,
            known_hosts_file="/tmp/known_hosts",
        )
        assert "StrictHostKeyChecking=yes" in argv
        assert "UserKnownHostsFile=/tmp/known_hosts" in argv
        assert "UserKnownHostsFile=/dev/null" not in argv

    def test_remote_command_is_single_argument(self) -> None:
        # The whole remote command is one argv element, never shell-split locally.
        argv = ssh_command(user="u", host="h", remote_command="rm -rf $HOME/x && ls")
        assert argv[-1] == "rm -rf $HOME/x && ls"

    def test_multiplex_on_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv("YUJ_SSH_NO_MULTIPLEX", raising=False)
        argv = ssh_command(user="u", host="h", remote_command="ls")
        assert "ControlMaster=auto" in argv
        assert any(a.startswith("ControlPath=") for a in argv)
        assert any(a.startswith("ControlPersist=") for a in argv)

    def test_multiplex_opt_out(self, monkeypatch) -> None:
        monkeypatch.setenv("YUJ_SSH_NO_MULTIPLEX", "1")
        argv = ssh_command(user="u", host="h", remote_command="ls")
        assert "ControlMaster=auto" not in argv
        assert not any(a.startswith("ControlPath=") for a in argv)


class TestRsyncCommand:
    def test_basic_flags(self) -> None:
        argv = rsync_command(source="src/", destination="dst/")
        assert argv[:3] == ["rsync", "-az", "--partial"]
        assert argv[-2:] == ["src/", "dst/"]

    def test_ssh_transport_embeds_options(self) -> None:
        argv = rsync_command(
            source="local/",
            destination="u@h:remote/",
            ssh_host="h",
            port=2200,
        )
        assert "-e" in argv
        e_value = argv[argv.index("-e") + 1]
        assert e_value.startswith("ssh ")
        assert "ConnectTimeout=20" in e_value
        assert "-p 2200" in e_value

    def test_includes_and_excludes_order(self) -> None:
        argv = rsync_command(
            source="s",
            destination="d",
            includes=["*.csv"],
            excludes=[".*", "*.log"],
        )
        assert "--include=*.csv" in argv
        assert "--exclude=.*" in argv
        assert "--exclude=*.log" in argv
        assert argv.index("--include=*.csv") < argv.index("--exclude=.*")

    def test_key_path_in_ssh_transport(self) -> None:
        argv = rsync_command(
            source="s", destination="u@h:d", ssh_host="h", key_path="/k"
        )
        e_value = argv[argv.index("-e") + 1]
        assert "-i /k" in e_value
        assert "PubkeyAuthentication=yes" in e_value

    def test_strict_host_key_in_ssh_transport(self) -> None:
        argv = rsync_command(
            source="s",
            destination="u@h:d",
            ssh_host="h",
            strict_host_key=True,
            known_hosts_file="/tmp/kh",
        )
        e_value = argv[argv.index("-e") + 1]
        assert "StrictHostKeyChecking=yes" in e_value
        assert "UserKnownHostsFile=/tmp/kh" in e_value


class TestWithSshpass:
    def test_prefixes_when_password(self) -> None:
        wrapped = with_sshpass(["ssh", "u@h", "ls"], use_password=True)
        assert wrapped[:2] == ["sshpass", "-e"]

    def test_no_prefix_without_password(self) -> None:
        wrapped = with_sshpass(["ssh", "u@h", "ls"], use_password=False)
        assert wrapped[0] == "ssh"

    def test_password_never_in_argv(self) -> None:
        # sshpass -e reads SSHPASS from env; the password is never an argument.
        wrapped = with_sshpass(["ssh", "u@h", "ls"], use_password=True)
        assert all("-p" not in token for token in wrapped[:2])
        assert "sshpass" in wrapped and "-e" in wrapped


def test_remote_spec() -> None:
    assert remote_spec("u", "1.2.3.4", "/home/u/out") == "u@1.2.3.4:/home/u/out"
