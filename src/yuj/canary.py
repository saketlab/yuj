"""Pre-submit canary: run the work command once on a single host."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from yuj.exec_cmd import exec_on_host
from yuj.probe import probe_fleet

if TYPE_CHECKING:
    from yuj.fleet import Fleet
    from yuj.supervise import SuperviseConfig

_TIMEOUT_RC = 124  # GNU `timeout` exit code when it kills the command
_RC_RE = re.compile(r"YUJ_CANARY_RC=(-?\d+)")
DEFAULT_TIMEOUT_S = 90


@dataclass(frozen=True)
class CanaryResult:
    """Outcome of the pre-submit canary run."""

    host: str | None
    ok: bool
    detail: str
    output: str = ""


def _batch_body(cfg: SuperviseConfig) -> str:
    return "\n".join(
        [
            "echo YUJ_CANARY_START",
            cfg.work_command,
            "rc=$?",
            'echo "YUJ_CANARY_RC=$rc"',
            'exit "$rc"',
        ]
    )


def _input_body(cfg: SuperviseConfig) -> str:
    return "\n".join(
        [
            f'INPUT="{cfg.input_file}"',
            f'OUTDIR="{cfg.output_dir}"',
            '[ -f "$INPUT" ] || { echo YUJ_CANARY_NOINPUT >&2; exit 94; }',
            'mkdir -p "$OUTDIR"',
            'export YUJ_OUT="$OUTDIR"',
            "while IFS= read -r item; do",
            '  [ -z "$item" ] && continue',
            f'  [ -e "${{OUTDIR}}/${{item}}{cfg.output_suffix}" ] && continue',
            "  echo YUJ_CANARY_START",
            f'  {cfg.work_command} "$item"',
            "  rc=$?",
            '  echo "YUJ_CANARY_RC=$rc"',
            '  exit "$rc"',
            'done < "$INPUT"',
            "echo YUJ_CANARY_NOWORK",
            "exit 0",
        ]
    )


def canary_script(cfg: SuperviseConfig, *, timeout_s: int) -> str:
    """Bash reproducing ``run_chunk``'s first pending iteration, timeout-bounded."""
    body = _input_body(cfg) if cfg.input_file else _batch_body(cfg)
    script = "\n".join(
        [
            "set -uo pipefail",
            f'cd "$HOME/{cfg.remote_dir}" || '
            '{ echo "yuj-canary: remote_dir missing" >&2; exit 1; }',
            "[ -f env.sh ] && . ./env.sh",
            body,
        ]
    )
    # -k force-kills a work command that ignores TERM.
    return (
        f"timeout -k 5s -s TERM {timeout_s}s bash <<'YUJ_CANARY'\n"
        f"{script}\nYUJ_CANARY\n"
    )


def _classify(
    host: str, rc: int | None, output: str, *, timeout_s: int
) -> CanaryResult:
    tail = output.strip()[-800:]
    codes = _RC_RE.findall(output)
    if codes:  # RC marker present: the command returned, so its code is trusted
        code = int(codes[-1])
        ok = code == 0
        detail = (
            "work command ran clean" if ok else f"work command failed (exit {code})"
        )
    elif "YUJ_CANARY_NOWORK" in output:
        ok, detail = True, "no pending items to run"
    elif "YUJ_CANARY_NOINPUT" in output:
        ok, detail = False, "input file missing on host"
    elif "YUJ_CANARY_START" in output and rc == _TIMEOUT_RC:
        ok, detail = True, f"still running after {timeout_s}s (no fast failure)"
    else:
        ok, detail = False, f"work command never started (exit {rc})"
    return CanaryResult(host, ok=ok, detail=detail, output=tail)


def run_canary(
    fleet: Fleet,
    cfg: SuperviseConfig,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    connect_timeout: int = 20,
) -> CanaryResult:
    """Run the canary on a reachable host and classify the result."""
    usable = fleet.usable
    statuses = probe_fleet(usable, connect_timeout=connect_timeout)
    reachable = [s.name for s in statuses if s.reachable]
    if not reachable:
        return CanaryResult(
            host=None, ok=True, detail="no reachable host to canary; skipped"
        )
    by_name = {h.name: h for h in usable}
    script = canary_script(cfg, timeout_s=timeout_s)
    for name in reachable:
        res = exec_on_host(
            by_name[name],
            script,
            connect_timeout=connect_timeout,
            timeout=timeout_s + 30,  # outlast the remote `timeout` to read its code
        )
        if res.reachable:
            output = f"{res.stdout}\n{res.stderr}"
            return _classify(name, res.returncode, output, timeout_s=timeout_s)
    return CanaryResult(
        host=None, ok=True, detail="reachable hosts went away before canary; skipped"
    )
