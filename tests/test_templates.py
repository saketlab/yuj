"""Tests for the on-host bash templates: render correctly and pass shellcheck."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from yuj.supervise import (
    SuperviseConfig,
    render_ensure,
    render_run,
    render_watchdog,
    rendered_scripts,
)

ITEM_CFG = SuperviseConfig(
    job="b20",
    remote_dir="yuj-run",
    work_command="bash $HOME/yuj-run/worker.sh",
    results_glob="~/yuj-run/results/*_done",
    input_file="items.txt",
    output_dir="results",
    output_suffix="_done",
)
BATCH_CFG = SuperviseConfig(
    job="batch1",
    remote_dir="yuj-run",
    work_command="python -m mypkg --all",
    results_glob="~/yuj-run/out/*",
)

_shellcheck = shutil.which("shellcheck")
needs_shellcheck = pytest.mark.skipif(
    _shellcheck is None, reason="shellcheck not installed"
)


def _run_shellcheck(script: str) -> subprocess.CompletedProcess[str]:
    assert _shellcheck is not None
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        return subprocess.run(
            [_shellcheck, "-s", "bash", path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink()


class TestRenderRun:
    def test_item_mode_has_resume_loop(self) -> None:
        script = render_run(ITEM_CFG)
        assert "while IFS= read -r item" in script
        assert '[ -e "$out" ] && return 0' in script  # resume by output presence
        assert "bash $HOME/yuj-run/worker.sh" in script

    def test_batch_mode_runs_command_once(self) -> None:
        script = render_run(BATCH_CFG)
        assert "python -m mypkg --all" in script
        assert "while IFS= read" not in script

    def test_serial_by_default(self) -> None:
        script = render_run(ITEM_CFG)
        assert "while IFS= read" in script
        assert "xargs -P" not in script

    def test_concurrency_uses_bounded_xargs(self) -> None:
        script = render_run(replace(ITEM_CFG, concurrency=6))
        assert "xargs -P 6" in script
        assert "run_item" in script


class TestRenderWatchdog:
    def test_embeds_config_values(self) -> None:
        script = render_watchdog(ITEM_CFG)
        assert 'STOP="/tmp/b20.yuj.stop"' in script
        assert "STALL_MIN=90" in script
        assert "GRACE=2700" in script
        assert "yuj-watchdog" in script  # so a probe can detect supervision

    def test_has_stall_and_relaunch_logic(self) -> None:
        script = render_watchdog(ITEM_CFG)
        assert "relaunch" in script
        assert 'age" -ge "$STALL_MIN"' in script

    def test_reaps_run_group_on_stop(self) -> None:
        # on the stop sentinel the watchdog must reap the run group, not just exit
        script = render_watchdog(ITEM_CFG)
        done = script.rindex("done")
        tail = script[done:]
        assert "stop_run" in tail
        assert tail.index("stop_run") < tail.index('log "stop sentinel')


class TestRenderEnsure:
    def test_respects_stop_sentinel_and_dedupes(self) -> None:
        script = render_ensure(ITEM_CFG)
        assert "/tmp/b20.yuj.stop" in script
        assert "grep -q" in script  # single-instance guard before restart

    def test_guards_on_readable_not_executable(self) -> None:
        # watchdog runs via bash "$WD", so a reset +x (after re-sync) must not
        # disable self-heal
        script = render_ensure(ITEM_CFG)
        assert '[ -f "$WD" ]' in script
        assert '[ -x "$WD" ]' not in script


@needs_shellcheck
class TestShellcheck:
    @pytest.mark.parametrize("cfg", [ITEM_CFG, BATCH_CFG], ids=["item", "batch"])
    @pytest.mark.parametrize("which", ["run", "watchdog", "ensure"])
    def test_each_script_is_clean(self, cfg: SuperviseConfig, which: str) -> None:
        renderers = {
            "run": render_run,
            "watchdog": render_watchdog,
            "ensure": render_ensure,
        }
        result = _run_shellcheck(renderers[which](cfg))
        assert result.returncode == 0, result.stdout

    def test_all_rendered_scripts_clean(self) -> None:
        for name, script in rendered_scripts(ITEM_CFG).items():
            result = _run_shellcheck(script)
            assert result.returncode == 0, f"{name}:\n{result.stdout}"
