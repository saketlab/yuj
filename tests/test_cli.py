"""Tests for the yuj CLI: version, init scaffolding, and the status dashboard."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import yuj
from yuj import cli as cli_module
from yuj import cli_support as cli_support_module
from yuj.cli import app
from yuj.decommission import DecommissionResult
from yuj.deploy import DeployResult
from yuj.provision import ProvisionResult
from yuj.status import Diagnosis, HostStatus
from yuj.supervise import SubmitResult

runner = CliRunner()


@pytest.fixture
def _stub_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the network probe with fixed statuses."""

    def fake_probe_fleet(fleet, **kwargs):  # type: ignore[no-untyped-def]
        return [
            HostStatus(
                name=h.name,
                ip=h.ip,
                reachable=True,
                nproc=8,
                n_outputs=7,
                newest_age_min=3,
            )
            for h in fleet
        ]

    # probe_fleet is called from the watch loop (cli) and from _render_status
    # (cli_support); stub both so every status path uses the fixed statuses.
    monkeypatch.setattr(cli_module, "probe_fleet", fake_probe_fleet)
    monkeypatch.setattr(cli_support_module, "probe_fleet", fake_probe_fleet)


def _write_fleet(directory: Path) -> Path:
    path = directory / "fleet.csv"
    path.write_text("username,ip,name,password\nu,10.0.0.1,a,p\nu,10.0.0.2,b,p\n")
    return path


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert yuj.__version__ in result.stdout


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("version", "init", "status", "fleet"):
        assert command in result.stdout


class TestInit:
    def test_creates_files(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "fleet.csv").is_file()
        assert (tmp_path / "yuj.yaml").is_file()
        assert "created" in result.stdout

    def test_is_idempotent_and_does_not_clobber(self, tmp_path: Path) -> None:
        (tmp_path / "fleet.csv").write_text("PRECIOUS USER DATA\n")
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        # Existing file is preserved, not overwritten.
        assert (tmp_path / "fleet.csv").read_text() == "PRECIOUS USER DATA\n"
        assert "exists" in result.stdout

    def test_r_template_scaffolds_everything(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--template", "r"])
        assert result.exit_code == 0
        for name in ("worker.R", "environment.yaml", "r-packages.txt", "yuj.yaml"):
            assert (tmp_path / name).is_file()
        assert "bootstrap" in result.stdout  # next-steps hint

    def test_python_template(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "-t", "python"])
        assert result.exit_code == 0
        assert (tmp_path / "worker.py").is_file()

    def test_unknown_template_errors(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "-t", "rust"])
        assert result.exit_code == 1
        assert "unknown template" in result.output


class TestStatus:
    def test_status_with_explicit_fleet(
        self, tmp_path: Path, _stub_probe: None
    ) -> None:
        fleet = _write_fleet(tmp_path)
        result = runner.invoke(app, ["status", "--fleet", str(fleet)])
        assert result.exit_code == 0
        assert "producing" in result.stdout
        assert "2/2 up" in result.stdout

    def test_status_autodetects_fleet_in_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stub_probe: None
    ) -> None:
        _write_fleet(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "2/2 up" in result.stdout

    def test_status_reads_yuj_yaml_for_fleet_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stub_probe: None
    ) -> None:
        (tmp_path / "hosts.csv").write_text(
            "username,ip,name,password\nu,10.0.0.9,z,p\n"
        )
        (tmp_path / "yuj.yaml").write_text("fleet: hosts.csv\nresults_glob: ~/out/*\n")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "1/1 up" in result.stdout

    def test_status_with_yaml_fleet(self, tmp_path: Path, _stub_probe: None) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text("user: u\nmachines:\n  - name: a\n    ip: 1.1.1.1\n")
        result = runner.invoke(app, ["status", "--fleet", str(yml)])
        assert result.exit_code == 0
        assert "1/1 up" in result.stdout

    def test_status_watch_mode_runs_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stub_probe: None
    ) -> None:
        fleet = _write_fleet(tmp_path)

        class _FakeLive:
            def __init__(self, *a: object, **k: object) -> None:
                pass

            def __enter__(self) -> _FakeLive:
                return self

            def __exit__(self, *a: object) -> bool:
                return False

            def update(self, *a: object, **k: object) -> None:
                pass

        def stop(_seconds: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_module, "Live", _FakeLive)
        monkeypatch.setattr(cli_module.time, "sleep", stop)
        result = runner.invoke(app, ["status", "--fleet", str(fleet), "--watch", "1"])
        assert result.exit_code == 0

    def test_status_no_fleet_errors_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 1
        assert "no fleet file found" in result.output

    def test_status_bad_fleet_errors_cleanly(
        self, tmp_path: Path, _stub_probe: None
    ) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_text("not,the,right,columns\n1,2,3,4\n")
        result = runner.invoke(app, ["status", "--fleet", str(bad)])
        assert result.exit_code == 1
        assert "error" in result.output


class TestFleetProbe:
    def test_fleet_probe(self, tmp_path: Path, _stub_probe: None) -> None:
        fleet = _write_fleet(tmp_path)
        result = runner.invoke(app, ["fleet", "probe", "--fleet", str(fleet)])
        assert result.exit_code == 0
        assert "producing" in result.stdout


class TestDeploy:
    def test_deploy_reports_per_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_fleet(tmp_path)
        (tmp_path / "yuj.yaml").write_text(
            "fleet: fleet.csv\nremote_dir: yuj-run\ndeploy:\n  code: [worker.sh]\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            cli_support_module,
            "deploy_fleet",
            lambda fleet, plan, **kw: {
                h.name: DeployResult(host=h.name, ok=True, transferred=("worker.sh",))
                for h in fleet
            },
        )
        result = runner.invoke(app, ["deploy"])
        assert result.exit_code == 0
        assert "ok" in result.stdout
        assert "worker.sh" in result.stdout

    def test_deploy_nonzero_exit_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_fleet(tmp_path)
        (tmp_path / "yuj.yaml").write_text("fleet: fleet.csv\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            cli_support_module,
            "deploy_fleet",
            lambda fleet, plan, **kw: {
                "a": DeployResult(host="a", ok=True, transferred=()),
                "b": DeployResult(host="b", ok=False, transferred=(), error="boom"),
            },
        )
        result = runner.invoke(app, ["deploy"])
        assert result.exit_code == 1
        assert "failed" in result.output


class TestSubmit:
    def _config(self, tmp_path: Path) -> None:
        _write_fleet(tmp_path)
        (tmp_path / "yuj.yaml").write_text(
            "fleet: fleet.csv\njob: b20\nremote_dir: yuj-run\n"
            "results_glob: ~/yuj-run/results/*\nwork_command: bash worker.sh\n"
        )

    def test_submit_reports_per_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._config(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            cli_support_module,
            "submit_fleet",
            lambda fleet, cfg, **kw: {
                h.name: SubmitResult(
                    host=h.name, ok=True, watchdog_running=True, cron_installed=True
                )
                for h in fleet
            },
        )
        result = runner.invoke(app, ["submit"])
        assert result.exit_code == 0
        assert "watchdog=True" in result.stdout

    def test_submit_requires_work_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_fleet(tmp_path)
        (tmp_path / "yuj.yaml").write_text("fleet: fleet.csv\njob: b20\n")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["submit"])
        assert result.exit_code == 1
        assert "work_command" in result.output


class TestProvision:
    def test_provision_reports_and_points_to_bootstrap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = _write_fleet(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            cli_module,
            "provision_fleet",
            lambda admin_fleet, cfg, **kw: {
                h.name: ProvisionResult(
                    host=h.name, ok=True, ip=h.ip, new_user=cfg.new_user, created=True
                )
                for h in admin_fleet
            },
        )
        result = runner.invoke(
            app, ["provision", "--fleet", str(fleet), "--user", "worker"]
        )
        assert result.exit_code == 0
        assert "created user worker" in result.stdout
        assert "yuj bootstrap" in result.stdout  # next-step hint

    def test_provision_invalid_user_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = _write_fleet(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["provision", "--fleet", str(fleet), "--user", "Bad User"]
        )
        assert result.exit_code == 1
        assert "invalid worker username" in result.output

    def test_provision_failure_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = _write_fleet(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            cli_module,
            "provision_fleet",
            lambda admin_fleet, cfg, **kw: {
                "a": ProvisionResult(host="a", ok=True, new_user="yuj", created=True),
                "b": ProvisionResult(
                    host="b", ok=False, error="sudo password rejected"
                ),
            },
        )
        result = runner.invoke(app, ["provision", "--fleet", str(fleet)])
        assert result.exit_code == 1
        assert "sudo password rejected" in result.output


class TestDiagnose:
    def test_diagnose_renders(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = _write_fleet(tmp_path)
        monkeypatch.setattr(
            cli_module,
            "diagnose_fleet",
            lambda f, **kw: [
                Diagnosis(name="a", ip="10.0.0.1", status="ok"),
                Diagnosis(
                    name="b", ip="10.0.0.2", status="banner_fail", detail="timeout"
                ),
            ],
        )
        result = runner.invoke(app, ["diagnose", "--fleet", str(fleet)])
        assert result.exit_code == 0
        assert "ok" in result.stdout
        assert "fail2ban" in result.stdout  # banner_fail label


class TestDoNotUse:
    def _fleet_with_dnu(self, tmp_path: Path) -> Path:
        path = tmp_path / "fleet.csv"
        path.write_text(
            "username,ip,name,password,do_not_use\n"
            "u,10.0.0.1,good,p,false\n"
            "u,10.0.0.2,charlie,p,true\n"
        )
        return path

    def test_deploy_all_skips_do_not_use(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = self._fleet_with_dnu(tmp_path)
        seen: dict[str, object] = {}

        def fake_deploy_fleet(f, plan, **kw):  # type: ignore[no-untyped-def]
            seen["names"] = f.names
            return {
                h.name: DeployResult(host=h.name, ok=True, transferred=()) for h in f
            }

        monkeypatch.setattr(cli_support_module, "deploy_fleet", fake_deploy_fleet)
        result = runner.invoke(app, ["deploy", "--fleet", str(fleet)])
        assert result.exit_code == 0
        assert seen["names"] == ("good",)  # charlie skipped

    def test_explicit_do_not_use_refused(self, tmp_path: Path) -> None:
        fleet = self._fleet_with_dnu(tmp_path)
        result = runner.invoke(
            app, ["deploy", "--fleet", str(fleet), "--hosts", "charlie"]
        )
        assert result.exit_code == 1
        assert "do_not_use" in result.output


class TestDecommission:
    def test_decommission_now(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = _write_fleet(tmp_path)
        monkeypatch.setattr(
            cli_module,
            "_decommission",
            lambda transport, cfg, **kw: DecommissionResult(
                host=transport.host.name, ok=True, cron_removed=True
            ),
        )
        result = runner.invoke(app, ["decommission", "a", "--fleet", str(fleet)])
        assert result.exit_code == 0
        assert "decommissioned" in result.stdout

    def test_decommission_scheduled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = _write_fleet(tmp_path)
        monkeypatch.setattr(
            cli_module,
            "_schedule_decommission",
            lambda transport, cfg, when, **kw: DecommissionResult(
                host=transport.host.name, ok=True, scheduled=when
            ),
        )
        result = runner.invoke(
            app, ["decommission", "a", "--fleet", str(fleet), "--at", "+90 seconds"]
        )
        assert result.exit_code == 0
        assert "scheduled" in result.stdout

    def test_decommission_unknown_host(self, tmp_path: Path) -> None:
        fleet = _write_fleet(tmp_path)
        result = runner.invoke(app, ["decommission", "ghost", "--fleet", str(fleet)])
        assert result.exit_code == 1

    def test_decommission_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = _write_fleet(tmp_path)
        monkeypatch.setattr(
            cli_module,
            "_decommission",
            lambda transport, cfg, **kw: DecommissionResult(
                host=transport.host.name, ok=True, cron_removed=True
            ),
        )
        for arg in ("--all", "all"):
            result = runner.invoke(app, ["decommission", arg, "--fleet", str(fleet)])
            assert result.exit_code == 0, arg
            assert "decommissioned a" in result.stdout
            assert "decommissioned b" in result.stdout

    def test_decommission_all_failure_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet = _write_fleet(tmp_path)
        monkeypatch.setattr(
            cli_module,
            "_decommission",
            lambda transport, cfg, **kw: DecommissionResult(
                host=transport.host.name, ok=False, error="boom"
            ),
        )
        result = runner.invoke(app, ["decommission", "--all", "--fleet", str(fleet)])
        assert result.exit_code == 1
        assert "failed" in result.stdout

    def test_decommission_needs_host_or_all(self, tmp_path: Path) -> None:
        fleet = _write_fleet(tmp_path)
        neither = runner.invoke(app, ["decommission", "--fleet", str(fleet)])
        assert neither.exit_code == 1
        both = runner.invoke(app, ["decommission", "a", "--all", "--fleet", str(fleet)])
        assert both.exit_code == 1
