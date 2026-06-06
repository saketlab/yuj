"""Tests for `yuj provision`: config, keypair, script generation, and rollout."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yuj._shell import CommandResult
from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host, load_from_csv
from yuj.provision import (
    ProvisionConfig,
    ProvisionResult,
    build_provision_script,
    generate_keypair,
    provision,
    provision_fleet,
    write_provisioned_fleet,
)

_shellcheck = shutil.which("shellcheck")
_ssh_keygen = shutil.which("ssh-keygen")

# A realistic ed25519 public key line (valid charset, no quotes).
_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc123+/=def yuj-yuj"


@dataclass
class _FakeHost:
    name: str = "box"
    ip: str = "10.0.0.1"
    port: int = 22
    password: str | None = "adminpw"


@dataclass
class _FakeTransport:
    """Records the remote command + stdin so we can assert on the sudo plumbing."""

    host: _FakeHost = field(default_factory=_FakeHost)
    stdout: str = "USER-CREATED\nPROVISION-OK\n"
    stderr: str = ""
    rc: int = 0
    last_command: str = ""
    last_input: str | None = None

    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        self.last_command = command
        self.last_input = input_text
        return CommandResult(self.rc, self.stdout, self.stderr)


class TestConfig:
    def test_defaults(self) -> None:
        cfg = ProvisionConfig()
        assert cfg.new_user == "yuj"
        assert cfg.shell == "/bin/bash"

    @pytest.mark.parametrize("bad", ["Root", "1abc", "a b", "x;rm -rf /", "", "-x"])
    def test_invalid_username_rejected(self, bad: str) -> None:
        with pytest.raises(YujError, match="invalid worker username"):
            ProvisionConfig(new_user=bad)

    def test_password_not_in_repr(self) -> None:
        cfg = ProvisionConfig(sudo_password="hunter2")
        assert "hunter2" not in repr(cfg)


class TestScript:
    def test_creates_user_and_installs_key(self) -> None:
        script = build_provision_script(ProvisionConfig(new_user="worker"), _PUBKEY)
        assert "useradd -m -s /bin/bash worker" in script
        assert _PUBKEY in script
        assert "authorized_keys" in script
        assert "passwd -l" in script  # password locked: key-only login
        assert "USER-CREATED" in script and "USER-EXISTS" in script

    def test_check_mode_creates_nothing(self) -> None:
        script = build_provision_script(
            ProvisionConfig(new_user="worker", check=True), _PUBKEY
        )
        assert "[dry-run] would: useradd" in script
        assert "useradd -m -s /bin/bash worker" not in script.replace(
            "would: useradd -m -s /bin/bash worker", ""
        )

    def test_malformed_pubkey_rejected(self) -> None:
        with pytest.raises(YujError, match="malformed SSH public key"):
            build_provision_script(ProvisionConfig(), "ssh-ed25519 AAAA'; rm -rf /")

    @pytest.mark.skipif(_shellcheck is None, reason="shellcheck not installed")
    @pytest.mark.parametrize("check", [False, True])
    def test_generated_script_passes_shellcheck(self, check: bool) -> None:
        assert _shellcheck is not None
        script = build_provision_script(
            ProvisionConfig(new_user="worker", check=check), _PUBKEY
        )
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            result = subprocess.run(
                [_shellcheck, "-s", "bash", path],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stdout
        finally:
            Path(path).unlink()


@pytest.mark.skipif(_ssh_keygen is None, reason="ssh-keygen not installed")
class TestKeypair:
    def test_generate_then_reuse(self, tmp_path: Path) -> None:
        key = tmp_path / "yuj_ed25519"
        pub1 = generate_keypair(key, comment="yuj-yuj")
        assert pub1.startswith("ssh-ed25519 ")
        assert key.is_file() and key.with_suffix(".pub").name.endswith(".pub")
        # Second call reuses the existing pair (same public key).
        pub2 = generate_keypair(key, comment="yuj-yuj")
        assert pub1 == pub2

    def test_private_key_permissions(self, tmp_path: Path) -> None:
        key = tmp_path / "yuj_ed25519"
        generate_keypair(key, comment="yuj-yuj")
        assert (key.stat().st_mode & 0o077) == 0  # not group/other readable


class TestKeypairMissingTool:
    def test_missing_ssh_keygen_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        prov_mod = sys.modules["yuj.provision"]
        monkeypatch.setattr(prov_mod, "have_executable", lambda name: False)
        with pytest.raises(YujError, match="ssh-keygen not found"):
            generate_keypair(tmp_path / "missing_ed25519", comment="yuj-yuj")


class TestProvision:
    def test_success_user_created(self) -> None:
        t = _FakeTransport()
        result = provision(t, ProvisionConfig(), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert result.ok and result.created
        assert result.new_user == "yuj"

    def test_sudo_password_goes_to_stdin_not_argv(self) -> None:
        t = _FakeTransport(host=_FakeHost(password="adminpw"))
        provision(t, ProvisionConfig(), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert t.last_input == "adminpw\n"  # password rides stdin
        assert "adminpw" not in t.last_command  # never on the command line
        assert t.last_command.startswith("sudo -S -p ''")

    def test_explicit_sudo_password_overrides_ssh_password(self) -> None:
        t = _FakeTransport(host=_FakeHost(password="sshpw"))
        provision(t, ProvisionConfig(sudo_password="diff"), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert t.last_input == "diff\n"

    def test_existing_user_not_recreated(self) -> None:
        t = _FakeTransport(stdout="USER-EXISTS\nPROVISION-OK\n")
        result = provision(t, ProvisionConfig(), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert result.ok and not result.created

    def test_bad_sudo_password_explained(self) -> None:
        t = _FakeTransport(rc=1, stderr="sudo: 1 incorrect password attempt")
        result = provision(t, ProvisionConfig(), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert not result.ok
        assert result.error == "sudo password rejected"

    def test_no_sudoers_explained(self) -> None:
        t = _FakeTransport(rc=1, stderr="alice is not in the sudoers file.")
        result = provision(t, ProvisionConfig(), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert result.error == "admin account lacks sudo on this host"

    def test_password_required_explained(self) -> None:
        t = _FakeTransport(rc=1, stderr="sudo: a password is required")
        result = provision(t, ProvisionConfig(), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert result.error is not None and "needs a password" in result.error

    def test_unknown_stderr_passed_through(self) -> None:
        t = _FakeTransport(rc=1, stderr="useradd: cannot create home dir")
        result = provision(t, ProvisionConfig(), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert result.error == "useradd: cannot create home dir"

    def test_empty_stderr_falls_back_to_exit_code(self) -> None:
        t = _FakeTransport(rc=3, stderr="")
        result = provision(t, ProvisionConfig(), public_key=_PUBKEY)  # type: ignore[arg-type]
        assert result.error == "exit 3"

    def test_malformed_key_returns_error_not_raises(self) -> None:
        t = _FakeTransport()
        result = provision(t, ProvisionConfig(), public_key="bad'key")  # type: ignore[arg-type]
        assert not result.ok and result.error is not None


class TestProvisionFleet:
    def test_writes_keys_logs_and_loadable_fleet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = Fleet(
            (
                Host(name="a", ip="10.0.0.1", user="admin", password="p"),
                Host(name="b", ip="10.0.0.2", user="admin", password="p"),
            )
        )
        import sys

        prov_mod = sys.modules["yuj.provision"]
        monkeypatch.setattr(
            prov_mod, "generate_keypair", lambda path, *, comment: _PUBKEY
        )
        monkeypatch.setattr(
            prov_mod,
            "provision",
            lambda transport, cfg, **kw: ProvisionResult(
                host=transport.host.name,
                ok=True,
                ip=transport.host.ip,
                port=transport.host.port,
                new_user=cfg.new_user,
                created=True,
                log=f"log-{transport.host.name}",
            ),
        )
        out = tmp_path / "provisioned-fleet.csv"
        results = provision_fleet(
            admin,
            ProvisionConfig(new_user="worker"),
            key_dir=tmp_path / "keys",
            fleet_out=out,
            log_dir=tmp_path / "logs",
        )
        assert set(results) == {"a", "b"}
        assert (tmp_path / "logs" / "a.log").read_text() == "log-a"
        # The generated CSV round-trips through the real loader.
        worker_fleet = load_from_csv(out)
        assert set(worker_fleet.names) == {"a", "b"}
        host_a = worker_fleet.get("a")
        assert host_a.user == "worker"
        assert host_a.key_path is not None and host_a.key_path.endswith(
            "worker_ed25519"
        )
        assert host_a.auth_kind == "key"

    def test_check_mode_writes_no_fleet_csv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = Fleet((Host(name="a", ip="10.0.0.1", user="admin", password="p"),))
        import sys

        prov_mod = sys.modules["yuj.provision"]
        monkeypatch.setattr(
            prov_mod, "generate_keypair", lambda path, *, comment: _PUBKEY
        )
        monkeypatch.setattr(
            prov_mod,
            "provision",
            lambda transport, cfg, **kw: ProvisionResult(
                host=transport.host.name, ok=True, ip=transport.host.ip
            ),
        )
        out = tmp_path / "provisioned-fleet.csv"
        provision_fleet(
            admin,
            ProvisionConfig(new_user="worker", check=True),
            key_dir=tmp_path / "keys",
            fleet_out=out,
            log_dir=tmp_path / "logs",
        )
        assert not out.exists()

    def test_failed_hosts_excluded_from_fleet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = Fleet(
            (
                Host(name="ok", ip="10.0.0.1", user="admin", password="p"),
                Host(name="bad", ip="10.0.0.2", user="admin", password="p"),
            )
        )
        import sys

        prov_mod = sys.modules["yuj.provision"]
        monkeypatch.setattr(
            prov_mod, "generate_keypair", lambda path, *, comment: _PUBKEY
        )

        def fake_provision(transport, cfg, **kw):  # type: ignore[no-untyped-def]
            ok = transport.host.name == "ok"
            return ProvisionResult(
                host=transport.host.name,
                ok=ok,
                ip=transport.host.ip,
                new_user=cfg.new_user,
                error=None if ok else "sudo password rejected",
            )

        monkeypatch.setattr(prov_mod, "provision", fake_provision)
        out = tmp_path / "provisioned-fleet.csv"
        provision_fleet(
            admin,
            ProvisionConfig(new_user="worker"),
            key_dir=tmp_path / "keys",
            fleet_out=out,
            log_dir=tmp_path / "logs",
        )
        worker_fleet = load_from_csv(out)
        assert worker_fleet.names == ("ok",)

    def test_all_failed_writes_no_fleet_csv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = Fleet((Host(name="a", ip="10.0.0.1", user="admin", password="p"),))
        import sys

        prov_mod = sys.modules["yuj.provision"]
        monkeypatch.setattr(
            prov_mod, "generate_keypair", lambda path, *, comment: _PUBKEY
        )
        monkeypatch.setattr(
            prov_mod,
            "provision",
            lambda transport, cfg, **kw: ProvisionResult(
                host=transport.host.name, ok=False, ip=transport.host.ip, error="nope"
            ),
        )
        out = tmp_path / "provisioned-fleet.csv"
        provision_fleet(
            admin,
            ProvisionConfig(new_user="worker"),
            key_dir=tmp_path / "keys",
            fleet_out=out,
            log_dir=tmp_path / "logs",
        )
        assert not out.exists()

    def test_empty_fleet(self, tmp_path: Path) -> None:
        assert (
            provision_fleet(
                Fleet(()), ProvisionConfig(), key_dir=tmp_path, log_dir=tmp_path
            )
            == {}
        )


class TestWriteFleet:
    def test_columns(self, tmp_path: Path) -> None:
        out = tmp_path / "f.csv"
        write_provisioned_fleet(
            out,
            [
                ProvisionResult(
                    host="h1", ok=True, ip="1.2.3.4", port=2222, new_user="w"
                )
            ],
            key_path="/keys/w_ed25519",
        )
        text = out.read_text()
        assert text.splitlines()[0] == "name,ip,username,key_path,port"
        assert "h1,1.2.3.4,w,/keys/w_ed25519,2222" in text
