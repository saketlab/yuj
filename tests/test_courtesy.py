from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from yuj.canary import canary_script
from yuj.cli_support import _supervise_config
from yuj.config import ProjectConfig
from yuj.sizing import courteous_gpus
from yuj.status import Gpu
from yuj.supervise import SuperviseConfig, render_run, render_watchdog

ME = "me"


def gpu(index: int, users: tuple[str, ...] = (), used: int = 0) -> Gpu:
    return Gpu(index, "A6000", 49_140, used, users)


def test_idle_cards_are_ours() -> None:
    assert courteous_gpus([gpu(0), gpu(1), gpu(2)], ME) == (0, 1, 2)


def test_a_card_with_someone_else_is_left_alone() -> None:
    gpus = [gpu(0, ("yashvi",), 672), gpu(1), gpu(2)]
    assert courteous_gpus(gpus, ME) == (1, 2)


def test_our_own_work_does_not_evict_us() -> None:
    assert courteous_gpus([gpu(0, (ME,), 22_000), gpu(1)], ME) == (0, 1)


def test_every_card_busy_yields_nothing() -> None:
    gpus = [gpu(0, ("a",), 500), gpu(1, ("b",), 500)]
    assert courteous_gpus(gpus, ME) == ()


def _cfg(
    *, courtesy: bool = False, active_window: str | None = None
) -> SuperviseConfig:
    return SuperviseConfig(
        job="enrich",
        remote_dir="work",
        work_command="./do.sh",
        results_glob="out/*.csv",
        concurrency=12,
        courtesy=courtesy,
        active_window=active_window,
    )


def test_courtesy_is_off_by_default() -> None:
    wd = render_watchdog(_cfg())
    assert "gpu_courtesy_list" not in wd
    assert "refresh_courtesy" not in render_run(_cfg())


def test_courtesy_renders_into_both_scripts() -> None:
    cfg = _cfg(courtesy=True)
    wd, run = render_watchdog(cfg), render_run(cfg)
    assert "gpu_courtesy_list" in wd
    assert "TOTAL_WORKERS=12" in wd
    # the run loop must source the file the watchdog writes, or it keeps the
    # stale card list and quietly runs on a GPU we just gave back
    assert cfg.gpu_file in run
    assert cfg.gpu_file in wd


def test_courtesy_checks_before_stall_logic() -> None:
    wd = render_watchdog(_cfg(courtesy=True))
    body = wd.split("while [ ! -f")[1]
    assert body.index("refresh_courtesy") < body.index("age=$(newest_age_min)")


def test_courtesy_composes_with_active_window() -> None:
    wd = render_watchdog(_cfg(courtesy=True, active_window="22:00-08:00"))
    assert "within_window" in wd
    assert "have_gpu" in wd


def _shell_function(script: str, name: str) -> str:
    """Cut ``name()`` out of a rendered script, so tests run the shipped code."""
    start = script.index(f"{name}() {{")
    return script[start : script.index("\n}\n", start) + 3]


@pytest.mark.skipif(shutil.which("awk") is None, reason="awk required")
def test_shell_selection_matches_python(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "nvidia-smi").write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            case "$1" in
              --query-gpu=*) printf '0, GPU-aaa\n1, GPU-bbb\n2, GPU-ccc\n' ;;
              --query-compute-apps=*) printf 'GPU-aaa, 101\nGPU-bbb, 102\n' ;;
            esac
            """),
        encoding="utf-8",
    )
    (bin_dir / "ps").write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            case "${@: -1}" in
              101) echo yashvi ;;   # someone else -> card 0 is theirs
              102) echo me ;;       # our own work -> card 1 stays ours
            esac
            """),
        encoding="utf-8",
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)

    harness = tmp_path / "sel.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nME=me\n"
        + _shell_function(render_watchdog(_cfg(courtesy=True)), "gpu_courtesy_list")
        + "\ngpu_courtesy_list\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["/usr/bin/env", "bash", str(harness)],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    ).stdout.split()
    assert out == ["1", "2"]

    gpus = [gpu(0, ("yashvi",), 672), gpu(1, (ME,), 22_000), gpu(2)]
    assert [str(i) for i in courteous_gpus(gpus, ME)] == out


class TestWiring:
    @staticmethod
    def _build(*, yaml_key: bool = False, flag: bool = False) -> SuperviseConfig:
        data: dict[str, object] = {"work_command": "w"}
        if yaml_key:
            data["courtesy"] = True
        return _supervise_config(ProjectConfig.from_mapping(data), courtesy=flag)

    def test_off_by_default(self) -> None:
        cfg = self._build()
        assert cfg.courtesy is False
        assert "gpu_courtesy_list" not in render_watchdog(cfg)

    def test_yaml_key_turns_it_on(self) -> None:
        cfg = self._build(yaml_key=True)
        assert cfg.courtesy is True
        assert "gpu_courtesy_list" in render_watchdog(cfg)

    def test_flag_turns_it_on_without_the_yaml_key(self) -> None:
        cfg = self._build(flag=True)
        assert cfg.courtesy is True
        assert "gpu_courtesy_list" in render_watchdog(cfg)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_a_host_without_nvidia_smi_still_starts(tmp_path: Path) -> None:
    wd = render_watchdog(_cfg(courtesy=True))
    harness = tmp_path / "nogpu.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        'GPU_FILE="gpus.env"\nTOTAL_WORKERS=12\nHAVE_SMI=0\nALLOWED=""\n'
        + _shell_function(wd, "gpu_courtesy_list")
        + _shell_function(wd, "refresh_courtesy")
        + _shell_function(wd, "have_gpu")
        + "\nrefresh_courtesy\nif have_gpu; then echo STARTED; else echo IDLE; fi\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["/usr/bin/env", "bash", str(harness)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert out.stdout.split() == ["STARTED"], out.stderr


def _run_helpers(wd: str, tmp_path: Path, preamble: str, body: str) -> str:
    harness = tmp_path / "h.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        + preamble
        + _shell_function(wd, "gpu_courtesy_list")
        + _shell_function(wd, "refresh_courtesy")
        + _shell_function(wd, "have_gpu")
        + body,
        encoding="utf-8",
    )
    done = subprocess.run(
        ["/usr/bin/env", "bash", str(harness)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def _fake_smi(tmp_path: Path, n_cards: int) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    rows = "".join(f"{i}, GPU-{i}\\n" for i in range(n_cards))
    smi = bin_dir / "nvidia-smi"
    smi.write_text(
        f"#!/usr/bin/env bash\ncase \"$1\" in --query-gpu=*) printf '{rows}' ;; esac\n",
        encoding="utf-8",
    )
    smi.chmod(0o755)
    return bin_dir


def test_idle_cards_never_exceed_the_worker_budget(tmp_path: Path) -> None:
    bin_dir = _fake_smi(tmp_path, 4)
    out = _run_helpers(
        render_watchdog(_cfg(courtesy=True)),
        tmp_path,
        f'PATH="{bin_dir}:$PATH"\nGPU_FILE="gpus.env"\nTOTAL_WORKERS=1\n'
        'HAVE_SMI=1\nALLOWED=""\n',
        "\nrefresh_courtesy\ncat gpus.env\n",
    )
    workers = [int(n) for n in out.split('WORKERS_PER_GPU="')[1].split('"')[0].split()]
    assert sum(workers) == 1, out


def test_a_restarted_watchdog_still_yields_a_busy_card() -> None:
    wd = render_watchdog(_cfg(courtesy=True))
    guard = wd.split("if ! have_gpu; then")[1].split("continue")[0]
    assert "stop_run" in guard


def test_canary_defines_the_placement_vars_courtesy_will_supply() -> None:
    cfg = _cfg(courtesy=True)
    assert 'export GPUS=""' in canary_script(cfg, timeout_s=90)
    assert "export GPUS" not in canary_script(_cfg(), timeout_s=90)
