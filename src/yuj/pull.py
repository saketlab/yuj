"""Pull outputs from the fleet to a local directory, tolerant of down hosts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from yuj.exceptions import TransportError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.transport import make_transport

DEFAULT_PULL_TIMEOUT = 1800.0
# Cap concurrent SSH connections. Hammering many hosts at once invites sshd
# MaxStartups throttling and false "unreachable" readings; 8 parallel pulls is a
# safe ceiling.
DEFAULT_MAX_WORKERS = 8


@dataclass(frozen=True)
class PullResult:
    """Outcome of pulling from one host."""

    host: str
    ok: bool
    destination: Path
    error: str | None = None


def pull_once(
    fleet: Fleet,
    *,
    remote_dir: str,
    dest_dir: str | Path,
    includes: Sequence[str] = ("*",),
    excludes: Sequence[str] = (".*", "*.log"),
    per_host_subdir: bool = False,
    connect_timeout: int = 20,
    timeout: float = DEFAULT_PULL_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, PullResult]:
    """Pull ``remote_dir`` from every host into ``dest_dir`` once, in parallel.

    Args:
        fleet: The hosts to pull from.
        remote_dir: Source directory on each host (its *contents* are pulled).
        dest_dir: Local directory to receive results.
        includes / excludes: rsync filters; defaults keep data files, drop dot-
            files and logs.
        per_host_subdir: If True, each host's results land in
            ``dest_dir/<host>/``; if False, all hosts merge into ``dest_dir``
            (the canonical-store pattern; resume-by-presence makes the merge
            safe because filenames encode the work item).
        connect_timeout: Per-connection TCP/SSH timeout.
        timeout: Per-host overall transfer timeout.
        max_workers: Maximum concurrent pulls.

    Returns:
        A mapping of host name to :class:`PullResult`. Never raises for a single
        host being down.
    """
    dest_root = Path(dest_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    return map_fleet(
        fleet,
        lambda host: _pull_host(
            host,
            remote_dir=remote_dir,
            dest_root=dest_root,
            includes=includes,
            excludes=excludes,
            per_host_subdir=per_host_subdir,
            connect_timeout=connect_timeout,
            timeout=timeout,
        ),
        max_workers=max_workers,
    )


def _pull_host(
    host: Host,
    *,
    remote_dir: str,
    dest_root: Path,
    includes: Sequence[str],
    excludes: Sequence[str],
    per_host_subdir: bool,
    connect_timeout: int,
    timeout: float,
) -> PullResult:
    """Pull from a single host, returning a result rather than raising."""
    dest = dest_root / host.name if per_host_subdir else dest_root
    dest.mkdir(parents=True, exist_ok=True)
    transport = make_transport(host, connect_timeout=connect_timeout)
    # Trailing slash on the source: pull the directory's *contents*, not the dir.
    source = remote_dir.rstrip("/") + "/"
    try:
        result = transport.get(
            source,
            str(dest) + "/",
            includes=includes,
            excludes=excludes,
            timeout=timeout,
        )
    except TransportError as exc:
        return PullResult(host=host.name, ok=False, destination=dest, error=str(exc))
    if not result.ok:
        return PullResult(
            host=host.name,
            ok=False,
            destination=dest,
            error=result.stderr.strip() or f"rsync exited {result.returncode}",
        )
    return PullResult(host=host.name, ok=True, destination=dest)
