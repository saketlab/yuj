"""Remove a yuj job from a host (now or scheduled); touches only its cron/procs."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from yuj.exceptions import YujError
from yuj.supervise import SuperviseConfig
from yuj.transport import Transport

_REL_DELAY = re.compile(r"^\+\s*(\d+)\s*(second|minute|hour)s?$", re.IGNORECASE)
_SECONDS = {"second": 1, "minute": 60, "hour": 3600}


@dataclass(frozen=True)
class DecommissionResult:
    """Outcome of decommissioning one host."""

    host: str
    ok: bool
    cron_removed: bool = False
    processes_remaining: int = 0
    removed_dir: bool = False
    scheduled: str | None = None
    error: str | None = None


def _teardown_script(cfg: SuperviseConfig, *, remove_dir: bool) -> str:
    """Bash that tears down exactly this job (and nothing else) on a host.

    Kills are done with pgrep+kill while excluding this teardown shell's own PID
    and parent, because the script *text* contains the watchdog/run script names,
    a naive ``pkill -f`` would match (and kill) the teardown shell itself. The
    watchdog is killed and confirmed dead (retry loop) before any ``rm``, so a
    surviving watchdog can't relaunch the work loop and recreate the directory.
    """
    stop = shlex.quote(cfg.stop_sentinel)
    ensure = shlex.quote(cfg.ensure_script)
    wd = shlex.quote(cfg.watchdog_script)
    run = shlex.quote(cfg.run_script)
    remote_dir = cfg.remote_dir.rstrip("/")
    rm_line = f'rm -rf "$HOME/{remote_dir}"' if remove_dir else "true"
    return _TEARDOWN_TEMPLATE.format(
        stop=stop, ensure=ensure, wd=wd, run=run, remote_dir=remote_dir, rm_line=rm_line
    )


# Self-excluding kill loop. ``kill_job`` signals every match of $1 except this
# shell ($me) and its parent; ``count_wd`` counts live watchdogs excluding self.
_TEARDOWN_TEMPLATE = """\
set -u
me=$$
parent=${{PPID:-0}}
touch {stop}
( crontab -l 2>/dev/null | grep -v {ensure} ) | crontab - 2>/dev/null

kill_job() {{
    sig="$1"; pat="$2"
    for pid in $(pgrep -f "$pat" 2>/dev/null); do
        if [ "$pid" != "$me" ] && [ "$pid" != "$parent" ]; then
            kill "$sig" "$pid" 2>/dev/null
        fi
    done
}}
count_wd() {{
    n=0
    for pid in $(pgrep -f {wd} 2>/dev/null); do
        if [ "$pid" != "$me" ] && [ "$pid" != "$parent" ]; then n=$((n + 1)); fi
    done
    echo "$n"
}}

for _ in 1 2 3 4 5 6; do
    kill_job -TERM {wd}; kill_job -TERM {run}
    sleep 1
    kill_job -KILL {wd}; kill_job -KILL {run}
    [ "$(count_wd)" -eq 0 ] && break
    sleep 1
done
sleep 1
{rm_line}
echo "procs=$(count_wd)"
echo "cron=$(crontab -l 2>/dev/null | grep -c {ensure})"
echo "dir=$([ -d "$HOME/{remote_dir}" ] && echo 1 || echo 0)"
"""


def decommission(
    transport: Transport,
    cfg: SuperviseConfig,
    *,
    remove_dir: bool = False,
    timeout: float = 60.0,
) -> DecommissionResult:
    """Tear down ``cfg``'s job on the host now. Never raises for op failures."""
    host = transport.host.name
    try:
        result = transport.run(
            _teardown_script(cfg, remove_dir=remove_dir), timeout=timeout
        )
    except YujError as exc:
        return DecommissionResult(host=host, ok=False, error=str(exc))
    fields = {
        k: v
        for k, _, v in (line.partition("=") for line in result.stdout.splitlines())
        if k in ("procs", "cron", "dir")
    }
    procs = _int(fields.get("procs"))
    cron = _int(fields.get("cron"))
    dir_present = _int(fields.get("dir"))
    ok = procs == 0 and cron == 0 and (not remove_dir or dir_present == 0)
    return DecommissionResult(
        host=host,
        ok=ok,
        cron_removed=cron == 0,
        processes_remaining=procs,
        removed_dir=remove_dir and dir_present == 0,
        error=None if ok else "job artifacts still present after teardown",
    )


def schedule_decommission(
    transport: Transport,
    cfg: SuperviseConfig,
    when: str,
    *,
    remove_dir: bool = False,
    timeout: float = 30.0,
) -> DecommissionResult:
    """Schedule a decommission for ``when`` (e.g. ``+90 seconds``, ``9am tomorrow``).

    Relative ``+N seconds/minutes/hours`` use a detached ``sleep``; anything else
    is handed to ``at``. Returns immediately with ``scheduled`` set.
    """
    host = transport.host.name
    script = _teardown_script(cfg, remove_dir=remove_dir)
    log = f"$HOME/{cfg.remote_dir.rstrip('/')}/logs/{cfg.job}.decommission.log"
    rel = _REL_DELAY.match(when.strip())
    try:
        if rel:
            seconds = int(rel.group(1)) * _SECONDS[rel.group(2).lower()]
            wrapped = (
                f"mkdir -p $HOME/{shlex.quote(cfg.remote_dir.rstrip('/'))}/logs; "
                f"setsid bash -c {shlex.quote(f'sleep {seconds}; ' + script)} "
                f"</dev/null >>{log} 2>&1 &"
            )
            result = transport.run(wrapped, timeout=timeout)
        else:
            at_cmd = f"echo {shlex.quote(script)} | at {shlex.quote(when)}"
            result = transport.run(at_cmd, timeout=timeout)
            if not result.ok:
                return DecommissionResult(
                    host=host,
                    ok=False,
                    error=f"`at {when}` failed: {result.stderr.strip()}"
                    " (is the 'at' daemon installed?)",
                )
    except YujError as exc:
        return DecommissionResult(host=host, ok=False, error=str(exc))
    return DecommissionResult(host=host, ok=True, scheduled=when)


def _int(value: str | None) -> int:
    try:
        return int(value) if value else 0
    except ValueError:
        return 0
