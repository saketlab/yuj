"""Tests for the pre-submit canary."""

from __future__ import annotations

from yuj.canary import canary_script, run_canary
from yuj.fleet import Fleet, Host
from yuj.supervise import SuperviseConfig


def _cfg(**kw) -> SuperviseConfig:
    return SuperviseConfig(
        job="j",
        remote_dir="work",
        work_command="python run.py",
        results_glob="out/*.json",
        **kw,
    )


def _one_host_fleet() -> Fleet:
    return Fleet((Host(name="h1", ip="10.0.0.1", user="u"),))


def _stub_probe(monkeypatch, *, reachable: bool) -> None:
    monkeypatch.setattr(
        "yuj.canary.probe_fleet",
        lambda fleet, **k: [
            type("S", (), {"name": h.name, "reachable": reachable})() for h in fleet
        ],
    )


def _stub_exec(monkeypatch, **attrs) -> None:
    attrs.setdefault("reachable", True)
    attrs.setdefault("stdout", "")
    attrs.setdefault("stderr", "")
    attrs.setdefault("returncode", 0)
    monkeypatch.setattr(
        "yuj.canary.exec_on_host", lambda *a, **k: type("R", (), attrs)()
    )


def test_script_reproduces_run_loop_input_mode():
    script = canary_script(_cfg(input_file="items.txt", output_dir="out"), timeout_s=30)
    assert "timeout -k 5s -s TERM 30s" in script
    assert 'cd "$HOME/work"' in script
    assert "[ -f env.sh ]" in script
    assert 'export YUJ_OUT="$OUTDIR"' in script  # same env contract as run_chunk
    assert "while IFS= read -r item" in script  # resume-skip loop, not first line
    assert 'python run.py "$item"' in script


def test_script_batch_mode_has_no_item():
    script = canary_script(_cfg(), timeout_s=30)
    assert "python run.py" in script
    assert "$item" not in script


def test_run_canary_skips_when_no_reachable_host(monkeypatch):
    _stub_probe(monkeypatch, reachable=False)
    tried = []
    monkeypatch.setattr("yuj.canary.exec_on_host", lambda *a, **k: tried.append(1))
    result = run_canary(_one_host_fleet(), _cfg(), timeout_s=5)
    assert result.ok
    assert result.host is None
    assert not tried  # never opened a connection to run the command


def test_completed_clean_passes(monkeypatch):
    _stub_probe(monkeypatch, reachable=True)
    _stub_exec(monkeypatch, returncode=0, stdout="YUJ_CANARY_START\nYUJ_CANARY_RC=0")
    assert run_canary(_one_host_fleet(), _cfg(), timeout_s=5).ok


def test_fast_nonzero_fails(monkeypatch):
    _stub_probe(monkeypatch, reachable=True)
    _stub_exec(
        monkeypatch,
        returncode=1,
        stdout="YUJ_CANARY_START\nYUJ_CANARY_RC=1",
        stderr="boom",
    )
    result = run_canary(_one_host_fleet(), _cfg(), timeout_s=5)
    assert not result.ok
    assert result.host == "h1"
    assert "boom" in result.output


def test_work_command_exiting_124_is_a_failure_not_a_timeout(monkeypatch):
    # The work command itself returned 124 (it completed): RC marker present, so
    # this is a real fast-fail, NOT our timeout kill.
    _stub_probe(monkeypatch, reachable=True)
    _stub_exec(
        monkeypatch, returncode=124, stdout="YUJ_CANARY_START\nYUJ_CANARY_RC=124"
    )
    assert not run_canary(_one_host_fleet(), _cfg(), timeout_s=5).ok


def test_long_job_timeout_passes(monkeypatch):
    # START seen, no RC marker, rc==124 => still running, no fast failure.
    _stub_probe(monkeypatch, reachable=True)
    _stub_exec(monkeypatch, returncode=124, stdout="YUJ_CANARY_START")
    assert run_canary(_one_host_fleet(), _cfg(), timeout_s=5).ok


def test_setup_hang_before_work_fails(monkeypatch):
    # No START marker + timeout => env.sh/setup hung before reaching work.
    _stub_probe(monkeypatch, reachable=True)
    _stub_exec(monkeypatch, returncode=124, stdout="")
    assert not run_canary(_one_host_fleet(), _cfg(), timeout_s=5).ok


def test_missing_input_file_fails(monkeypatch):
    _stub_probe(monkeypatch, reachable=True)
    _stub_exec(monkeypatch, returncode=94, stderr="YUJ_CANARY_NOINPUT")
    assert not run_canary(_one_host_fleet(), _cfg(), timeout_s=5).ok


def test_no_pending_items_passes(monkeypatch):
    _stub_probe(monkeypatch, reachable=True)
    _stub_exec(monkeypatch, returncode=0, stdout="YUJ_CANARY_NOWORK")
    assert run_canary(_one_host_fleet(), _cfg(), timeout_s=5).ok
