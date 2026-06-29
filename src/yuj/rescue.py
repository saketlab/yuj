"""Rescue an OOM-melted host: catch a transient sshd window and kill the orphan
processes that are starving it.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.transport import make_transport

_MARKER = "YUJRESCUE"
DEFAULT_ATTEMPTS = 40
DEFAULT_INTERVAL = 20.0
DEFAULT_CONNECT_TIMEOUT = 50
DEFAULT_DRAIN_ROUNDS = 3
DEFAULT_MAX_WORKERS = 8


def _inner_kill(pattern: Sequence[str]) -> str:
    """Shell that frees RAM by killing the work loop's leftover processes.

    With ``pattern``, kill only the user's processes whose comm matches one of the
    names
    """
    if pattern:
        kills = "; ".join(
            f'pkill -"$sig" -u "$U" {shlex.quote(p)} 2>/dev/null' for p in pattern
        )
        return f"U=$(id -un); for sig in TERM KILL; do {kills}; sleep 1; done"
    return (
        'U=$(id -un); me="$1"; parent="$2"; '
        "for sig in TERM KILL; do "
        'for pid in $(ps -u "$U" -o pid= 2>/dev/null); do '
        'case " $me $parent " in *" $pid "*) continue ;; esac; '
        'kill -"$sig" "$pid" 2>/dev/null; '
        "done; sleep 1; done"
    )


def _rescue_payload(*, strip_cron: bool, pattern: Sequence[str]) -> str:
    """Strip the relaunch cron, detach the kill."""
    inner = _inner_kill(pattern)
    cron = "crontab -r 2>/dev/null; " if strip_cron else ""
    detach = f'setsid sh -c {shlex.quote(inner)} _ "$me" "$parent"'
    return (
        "me=$$; parent=${PPID:-0}; "
        + cron
        + f"{detach} </dev/null >/dev/null 2>&1 & "
        + f"echo {_MARKER} load=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
    )


def _parse_load(stdout: str) -> float | None:
    """Pull ``load=<float>`` off the marker line."""
    for line in stdout.splitlines():
        if _MARKER in line:
            for token in line.split():
                if token.startswith("load="):
                    try:
                        return float(token[5:])
                    except ValueError:
                        return None
    return None


def _try_window(
    host: Host, payload: str, connect_timeout: int
) -> tuple[bool, float | None]:
    """One connection attempt. Returns ``(window_opened, load)``.

    ``window_opened`` is False both when the box is too thrashed to connect and
    when it connects but the marker is missing -- either way the kill didn't land.
    """
    try:
        result = make_transport(host, connect_timeout=connect_timeout).run(
            payload, timeout=connect_timeout + 10
        )
    except YujError:
        return False, None
    if _MARKER not in result.stdout:
        return False, None
    return True, _parse_load(result.stdout)


@dataclass(frozen=True)
class RescueResult:
    """Outcome of rescuing one host."""

    host: str
    rescued: bool
    attempts: int = 0
    load_before: float | None = None
    load_after: float | None = None
    error: str | None = None


def rescue_host(
    host: Host,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    interval: float = DEFAULT_INTERVAL,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    strip_cron: bool = True,
    drain_rounds: int = DEFAULT_DRAIN_ROUNDS,
    pattern: Sequence[str] = (),
    sleep_fn: Callable[[float], None] = time.sleep,
) -> RescueResult:
    """Hammer ``host`` until a RAM window lets sshd fork, then drain the orphans."""
    payload = _rescue_payload(strip_cron=strip_cron, pattern=tuple(pattern))
    for used in range(1, attempts + 1):
        opened, load_before = _try_window(host, payload, connect_timeout)
        if opened:
            break
        if used < attempts:
            sleep_fn(interval)
    else:
        return RescueResult(
            host.name,
            rescued=False,
            attempts=attempts,
            error=f"no sshd window after {attempts} attempts (needs a power-cycle)",
        )
    load_after = load_before
    for _ in range(drain_rounds):
        sleep_fn(min(interval, 12.0))
        opened, load = _try_window(host, payload, connect_timeout)
        if opened and load is not None:
            load_after = load
    return RescueResult(
        host.name,
        rescued=True,
        attempts=used,
        load_before=load_before,
        load_after=load_after,
    )


def rescue_fleet(
    fleet: Fleet,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    interval: float = DEFAULT_INTERVAL,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    strip_cron: bool = True,
    drain_rounds: int = DEFAULT_DRAIN_ROUNDS,
    pattern: Sequence[str] = (),
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[RescueResult]:
    """Rescue every host in ``fleet`` concurrently, in fleet order."""
    out = map_fleet(
        fleet,
        lambda host: rescue_host(
            host,
            attempts=attempts,
            interval=interval,
            connect_timeout=connect_timeout,
            strip_cron=strip_cron,
            drain_rounds=drain_rounds,
            pattern=pattern,
        ),
        max_workers=max_workers,
    )
    return [out[name] for name in fleet.names]
