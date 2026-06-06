"""Rsync code (always) and payload (optional) to each host's remote directory."""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from yuj._shell import CommandResult
from yuj.exceptions import TransportError
from yuj.fleet import Fleet, map_fleet
from yuj.transport import SSHTransport

# 30 minutes: a multi-GB payload over a slow link should not be killed
# prematurely, but a wedged transfer should not block forever either.
DEFAULT_TRANSFER_TIMEOUT = 1800.0


@dataclass(frozen=True)
class DeployPlan:
    """What to place on a host and where.

    Args:
        remote_dir: Destination directory on the remote, relative to ``$HOME``
            (or absolute). Created with ``mkdir -p`` before any transfer.
        code_paths: Local files/dirs that are always sent (your worker scripts).
        payload_paths: Heavy shared data sent only when ``push_payload`` is True.
        excludes: rsync exclude patterns applied to every transfer.
    """

    remote_dir: str
    code_paths: tuple[Path, ...] = ()
    payload_paths: tuple[Path, ...] = ()
    excludes: tuple[str, ...] = field(default=(".git", "__pycache__", "*.pyc"))


@dataclass(frozen=True)
class DeployResult:
    """Outcome of deploying to one host."""

    host: str
    ok: bool
    transferred: tuple[str, ...]
    error: str | None = None


def deploy(
    transport: SSHTransport,
    plan: DeployPlan,
    *,
    push_payload: bool = True,
    timeout: float = DEFAULT_TRANSFER_TIMEOUT,
) -> DeployResult:
    """Deploy ``plan`` to the host behind ``transport``.

    Ensures ``remote_dir`` exists, then rsyncs each code path (and, if
    ``push_payload``, each payload path) into it. Safe to re-run.

    Returns:
        A :class:`DeployResult`; ``ok`` is False (rather than raising) if any
        transfer fails, so a fleet-wide deploy can continue past a bad host.
    """
    host_name = transport.host.name
    remote = plan.remote_dir
    transferred: list[str] = []

    try:
        mkdir = transport.run(f"mkdir -p {shlex.quote(remote)}", timeout=timeout)
        if not mkdir.ok:
            return DeployResult(
                host=host_name,
                ok=False,
                transferred=(),
                error=f"could not create {remote!r}: {mkdir.stderr.strip()}",
            )

        sources: list[Path] = list(plan.code_paths)
        if push_payload:
            sources += list(plan.payload_paths)

        for source in sources:
            _send(transport, source, remote, plan.excludes, timeout)
            transferred.append(source.name)
    except TransportError as exc:
        return DeployResult(
            host=host_name, ok=False, transferred=tuple(transferred), error=str(exc)
        )

    return DeployResult(host=host_name, ok=True, transferred=tuple(transferred))


def deploy_fleet(
    fleet: Fleet,
    plan: DeployPlan,
    *,
    push_payload: bool = True,
    connect_timeout: int = 20,
    timeout: float = DEFAULT_TRANSFER_TIMEOUT,
    max_workers: int = 8,
) -> dict[str, DeployResult]:
    """Deploy ``plan`` to every host in parallel; tolerant of individual failures."""
    return map_fleet(
        fleet,
        lambda host: deploy(
            SSHTransport(host, connect_timeout=connect_timeout),
            plan,
            push_payload=push_payload,
            timeout=timeout,
        ),
        max_workers=max_workers,
    )


def _send(
    transport: SSHTransport,
    source: Path,
    remote_dir: str,
    excludes: Sequence[str],
    timeout: float,
) -> CommandResult:
    """Rsync one local path into ``remote_dir``, raising on failure."""
    if not source.exists():
        raise TransportError(f"deploy source does not exist: {source}")
    # A trailing slash on a directory source copies its *contents*; without it
    # rsync would nest the directory. We always want the named path to land
    # inside remote_dir, so we sync the path itself (no trailing slash) and let
    # rsync place it under the destination directory.
    dest = remote_dir.rstrip("/") + "/"
    result = transport.put(str(source), dest, excludes=tuple(excludes), timeout=timeout)
    if not result.ok:
        raise TransportError(
            f"rsync of {source} to {transport.host.name} failed: "
            f"{result.stderr.strip()}"
        )
    return result
