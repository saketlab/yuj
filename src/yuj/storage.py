"""Probe each host's disk in one SSH round-trip: work partition + all mounts."""

from __future__ import annotations

from dataclasses import dataclass

from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.shell_safety import validate_remote_path
from yuj.transport import make_transport

_BEGIN = "YUJDISK"
_END = "YUJEND"
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_WORKERS = 8


@dataclass(frozen=True)
class Partition:
    """One mounted filesystem, sizes in KiB (``df -Pk`` blocks)."""

    filesystem: str
    size_kb: int
    avail_kb: int
    use_pct: int
    mount: str


@dataclass(frozen=True)
class HostStorage:
    """Disk snapshot for one host: the work partition plus every mount."""

    name: str
    ip: str
    reachable: bool
    work_dir: str | None = None
    work_avail_kb: int | None = None
    work_dev: str | None = None
    partitions: tuple[Partition, ...] = ()
    error: str | None = None

    def is_work_partition(self, part: Partition) -> bool:
        """True if ``part`` is the filesystem the job writes into."""
        return self.work_dev is not None and part.filesystem == self.work_dev


def _disk_command(work_dir: str) -> str:
    """Remote one-liner emitting a YUJDISK/YUJEND block: work partition + all mounts."""
    wd = validate_remote_path(work_dir, label="work dir")
    return "\n".join(
        (
            f"printf '{_BEGIN}\\n'",
            f'printf "work=%s\\n" "{wd}"',
            f"df -Pk {wd} 2>/dev/null |"
            ' awk \'NR==2{print "workavail="$4; print "workdev="$1}\'',
            "df -Pk 2>/dev/null | tail -n +2 |"
            " while read -r fs size used avail cap mount; do"
            " printf 'part=%s|%s|%s|%s|%s\\n'"
            ' "$fs" "$size" "$avail" "$cap" "$mount";'
            " done",
            f"printf '{_END}\\n'",
        )
    )


def parse_storage(stdout: str, host: Host) -> HostStorage:
    """Parse a YUJDISK/YUJEND block into a :class:`HostStorage`."""
    work_dir: str | None = None
    work_avail: int | None = None
    work_dev: str | None = None
    parts: list[Partition] = []
    in_block = False
    saw_end = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped == _BEGIN:
            in_block = True
            continue
        if stripped == _END:
            saw_end = True
            break
        if not in_block:
            continue
        if stripped.startswith("work="):
            work_dir = stripped[len("work=") :] or None
        elif stripped.startswith("workavail="):
            work_avail = _to_int(stripped[len("workavail=") :])
        elif stripped.startswith("workdev="):
            work_dev = stripped[len("workdev=") :] or None
        elif stripped.startswith("part="):
            part = _parse_part(stripped[len("part=") :])
            if part is not None:
                parts.append(part)
    if not saw_end:
        raise YujError("truncated disk probe: no YUJEND marker")
    return HostStorage(
        name=host.name,
        ip=host.ip,
        reachable=True,
        work_dir=work_dir,
        work_avail_kb=work_avail,
        work_dev=work_dev,
        partitions=tuple(parts),
    )


def probe_storage(
    host: Host,
    *,
    work_dir: str = "~",
    connect_timeout: int = 20,
    timeout: float = DEFAULT_TIMEOUT,
) -> HostStorage:
    """Probe a single host's disk, returning a :class:`HostStorage` (never raising)."""
    transport = make_transport(host, connect_timeout=connect_timeout)
    try:
        result = transport.run(_disk_command(work_dir), timeout=timeout)
    except YujError as exc:
        return HostStorage(name=host.name, ip=host.ip, reachable=False, error=str(exc))
    if not result.ok or _BEGIN not in result.stdout:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        return HostStorage(name=host.name, ip=host.ip, reachable=False, error=detail)
    try:
        return parse_storage(result.stdout, host)
    except YujError as exc:
        return HostStorage(name=host.name, ip=host.ip, reachable=False, error=str(exc))


def storage_fleet(
    fleet: Fleet,
    *,
    work_dir: str = "~",
    connect_timeout: int = 20,
    timeout: float = DEFAULT_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[HostStorage]:
    """Probe every host's disk in parallel, returning results in fleet order."""
    out = map_fleet(
        fleet,
        lambda host: probe_storage(
            host, work_dir=work_dir, connect_timeout=connect_timeout, timeout=timeout
        ),
        max_workers=max_workers,
    )
    return [out[name] for name in fleet.names]


def _parse_part(raw: str) -> Partition | None:
    """Parse ``fs|size|avail|cap|mount`` into a :class:`Partition`."""
    fields = raw.split("|", 4)
    if len(fields) != 5:
        return None
    fs, size, avail, cap, mount = fields
    size_kb = _to_int(size)
    avail_kb = _to_int(avail)
    if size_kb is None or avail_kb is None:
        return None
    return Partition(
        filesystem=fs,
        size_kb=size_kb,
        avail_kb=avail_kb,
        use_pct=_to_int(cap.rstrip("%")) or 0,
        mount=mount,
    )


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
