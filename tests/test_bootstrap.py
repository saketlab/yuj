"""Tests for `yuj bootstrap`: config, script generation, recipes, and install."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yuj._shell import CommandResult
from yuj.bootstrap import (
    BootstrapConfig,
    BootstrapResult,
    available_extras,
    bootstrap,
    bootstrap_fleet,
    build_bootstrap_script,
    load_recipe,
)
from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host

_shellcheck = shutil.which("shellcheck")


@dataclass
class _FakeHost:
    name: str = "box"


@dataclass
class _FakeTransport:
    host: _FakeHost = field(default_factory=_FakeHost)
    runs: list[str] = field(default_factory=list)
    os_pretty: str = "Ubuntu 22.04.5 LTS"
    script_stdout: str = "BOOTSTRAP-OK\n"
    script_rc: int = 0

    def run(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.runs.append(command)
        if "os-release" in command:
            return CommandResult(0, self.os_pretty + "\n", "")
        return CommandResult(self.script_rc, self.script_stdout, "")


class TestConfig:
    def test_unknown_manager_raises(self) -> None:
        with pytest.raises(YujError, match="unknown env manager"):
            BootstrapConfig(env_manager="npm")

    def test_defaults(self) -> None:
        cfg = BootstrapConfig()
        assert cfg.env_manager == "uv"
        assert cfg.python == "3.12"

    def test_unsafe_remote_dir_raises(self) -> None:
        with pytest.raises(YujError, match="unsafe remote_dir"):
            BootstrapConfig(remote_dir='x"; rm -rf /')


class TestRecipes:
    def test_available_extras(self) -> None:
        extras = set(available_extras())
        assert {"OLLAMA", "SHELLCHECK", "RCLONE"} <= extras

    def test_load_recipe_case_insensitive(self) -> None:
        assert "ollama" in load_recipe("OLLAMA").lower()
        assert load_recipe("ollama") == load_recipe("OLLAMA")

    def test_unknown_recipe_raises(self) -> None:
        with pytest.raises(YujError, match="unknown bootstrap extra"):
            load_recipe("nonexistent")


class TestScript:
    def test_includes_install_and_marker(self) -> None:
        script = build_bootstrap_script(BootstrapConfig(env_manager="uv"))
        assert "astral.sh/uv/install.sh" in script
        assert ".yuj-bootstrap-marker" in script
        assert "ALREADY-BOOTSTRAPPED" in script

    def test_check_mode_installs_nothing(self) -> None:
        script = build_bootstrap_script(BootstrapConfig(env_manager="uv", check=True))
        assert "[dry-run] would install" in script
        assert "curl -LsSf https://astral.sh/uv/install.sh | sh" not in script.replace(
            "would install uv via: curl -LsSf https://astral.sh/uv/install.sh | sh", ""
        )

    def test_extras_embedded(self) -> None:
        script = build_bootstrap_script(
            BootstrapConfig(env_manager="uv", extras=("OLLAMA",))
        )
        assert "ollama.com/install.sh" in script

    def test_micromamba_extra_pins_root_prefix_in_env_sh(self) -> None:
        script = build_bootstrap_script(
            BootstrapConfig(env_manager="uv", extras=("micromamba",))
        )
        assert 'MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"' in script

    def test_unsafe_tarball_rejected(self) -> None:
        with pytest.raises(YujError, match="unsafe --from-tarball"):
            build_bootstrap_script(
                BootstrapConfig(env_manager="uv", from_tarball="$HOME/x; rm -rf /")
            )

    def test_tarball_expands_home(self) -> None:
        script = build_bootstrap_script(
            BootstrapConfig(env_manager="uv", from_tarball="$HOME/cache/uv.tar.gz")
        )
        assert 'tar -xf "$HOME/cache/uv.tar.gz"' in script

    @pytest.mark.skipif(_shellcheck is None, reason="shellcheck not installed")
    @pytest.mark.parametrize("manager", ["uv", "pixi", "micromamba", "conda"])
    def test_generated_script_passes_shellcheck(self, manager: str) -> None:
        script = build_bootstrap_script(
            BootstrapConfig(
                env_manager=manager, extras=("OLLAMA", "RCLONE"), remote_dir="yuj-test"
            )
        )
        assert _shellcheck is not None
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

    @pytest.mark.skipif(_shellcheck is None, reason="shellcheck not installed")
    def test_all_recipes_pass_shellcheck(self) -> None:
        assert _shellcheck is not None
        for name in available_extras():
            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
                fh.write(load_recipe(name))
                path = fh.name
            try:
                result = subprocess.run(
                    [_shellcheck, "-s", "bash", path],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert result.returncode == 0, f"{name}:\n{result.stdout}"
            finally:
                Path(path).unlink()


class TestBootstrap:
    def test_success_reports_os(self) -> None:
        t = _FakeTransport()
        result = bootstrap(t, BootstrapConfig())  # type: ignore[arg-type]
        assert result.ok
        assert result.os_pretty == "Ubuntu 22.04.5 LTS"

    def test_already_bootstrapped_detected(self) -> None:
        t = _FakeTransport(script_stdout="ALREADY-BOOTSTRAPPED (uv)\n")
        result = bootstrap(t, BootstrapConfig())  # type: ignore[arg-type]
        assert result.ok and result.already_done

    def test_failure_returns_error(self) -> None:
        t = _FakeTransport(script_rc=1, script_stdout="boom\n")
        result = bootstrap(t, BootstrapConfig())  # type: ignore[arg-type]
        assert not result.ok and result.error is not None


class TestBootstrapFleet:
    def test_parallel_and_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = Fleet(
            (
                Host(name="a", ip="1", user="u", password="p"),
                Host(name="b", ip="2", user="u", password="p"),
            )
        )
        import sys

        # `yuj.__init__` re-exports `bootstrap`, shadowing the submodule attr.
        bootstrap_mod = sys.modules["yuj.bootstrap"]
        monkeypatch.setattr(
            bootstrap_mod,
            "bootstrap",
            lambda transport, cfg, **kw: BootstrapResult(
                host=transport.host.name, ok=True, os_pretty="X", log="log-for-host"
            ),
        )
        results = bootstrap_fleet(fleet, BootstrapConfig(), log_dir=tmp_path / "logs")
        assert set(results) == {"a", "b"}
        assert (tmp_path / "logs" / "a.log").read_text() == "log-for-host"

    def test_empty_fleet(self, tmp_path: Path) -> None:
        assert bootstrap_fleet(Fleet(()), BootstrapConfig(), log_dir=tmp_path) == {}
