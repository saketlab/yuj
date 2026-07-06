"""Run a shell command on every fleet host in parallel (``yuj exec``)."""

from __future__ import annotations

from dataclasses import dataclass

from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.transport import make_transport

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_WORKERS = 12


@dataclass(frozen=True)
class ExecResult:
    """Outcome of running one command on one host."""

    name: str
    ip: str
    reachable: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the host was reachable and the command exited 0."""
        return self.reachable and self.returncode == 0


def exec_on_host(
    host: Host,
    command: str,
    *,
    connect_timeout: int = 20,
    timeout: float = DEFAULT_TIMEOUT,
) -> ExecResult:
    """Run ``command`` on one host, returning an :class:`ExecResult` (never raising)."""
    transport = make_transport(host, connect_timeout=connect_timeout)
    try:
        result = transport.run(command, timeout=timeout)
    except YujError as exc:
        return ExecResult(name=host.name, ip=host.ip, reachable=False, error=str(exc))
    return ExecResult(
        name=host.name,
        ip=host.ip,
        reachable=True,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def exec_fleet(
    fleet: Fleet,
    command: str,
    *,
    connect_timeout: int = 20,
    timeout: float = DEFAULT_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[ExecResult]:
    """Run ``command`` on every host in parallel, returning results in fleet order."""
    out = map_fleet(
        fleet,
        lambda host: exec_on_host(
            host, command, connect_timeout=connect_timeout, timeout=timeout
        ),
        max_workers=max_workers,
    )
    return [out[name] for name in fleet.names]
