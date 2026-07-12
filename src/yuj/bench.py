"""Benchmark each host's throughput (cores/RAM/GPU/disk/download) in one round-trip."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.shell_safety import validate_remote_path
from yuj.transport import make_transport

_BEGIN = "YUJBENCH"
_END = "YUJEND"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_WORKERS = 8
DEFAULT_URL = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"
DEFAULT_MAX_TIME = 20
_RANGE_BYTES = 67108863  # 64 MiB - 1


@dataclass(frozen=True)
class HostBench:
    """Throughput snapshot for one host. ``None`` means "did not report"."""

    name: str
    ip: str
    reachable: bool
    cores: int | None = None
    mem_gb: int | None = None
    gpu_vram_gb: int | None = None
    gpu_name: str | None = None
    gpu_count: int = 0
    disk_gb: int | None = None
    download_mbps: float | None = None
    error: str | None = None

    def metric(self, dim: str) -> float | None:
        """The value used to sort/weight this host along ``dim``."""
        spec = DIMENSION.get(dim)
        if spec is None:
            raise YujError(f"unknown dimension {dim!r}; use one of {DIMENSIONS}")
        return spec.accessor(self)


@dataclass(frozen=True)
class Dimension:
    """One throughput axis. Single source of truth"""

    key: str
    label: str
    accessor: Callable[[HostBench], float | None]
    cell: Callable[[HostBench], str]
    needs_download: bool = False


def _gpu_cell(b: HostBench) -> str:
    if not b.gpu_vram_gb:
        return "-"
    tag = f"{b.gpu_count}x " if b.gpu_count > 1 else ""
    return f"{tag}{b.gpu_name or 'GPU'} {b.gpu_vram_gb}G"


_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "cores",
        "cores",
        lambda b: b.cores,
        lambda b: "-" if b.cores is None else str(b.cores),
    ),
    Dimension(
        "mem",
        "RAM",
        lambda b: b.mem_gb,
        lambda b: "-" if b.mem_gb is None else f"{b.mem_gb}G",
    ),
    Dimension("gpu", "GPU", lambda b: b.gpu_vram_gb, _gpu_cell),
    Dimension(
        "disk",
        "disk free",
        lambda b: b.disk_gb,
        lambda b: "-" if b.disk_gb is None else f"{b.disk_gb}G",
    ),
    Dimension(
        "download",
        "download",
        lambda b: b.download_mbps,
        lambda b: "-" if b.download_mbps is None else f"{b.download_mbps:.0f} MB/s",
        needs_download=True,
    ),
)
DIMENSION = {d.key: d for d in _DIMENSIONS}
DIMENSIONS = tuple(DIMENSION)


def _bench_command(work_dir: str, url: str, max_time: int, download: bool) -> str:
    """Remote one-liner emitting a YUJBENCH/YUJEND key=value block."""
    wd = validate_remote_path(work_dir, label="work dir")
    lines = [
        f"printf '{_BEGIN}\\n'",
        "printf 'cores=%s\\n' \"$(nproc 2>/dev/null)\"",
        "printf 'memgb=%s\\n' \"$(awk '/MemTotal/{print int($2/1048576)}'"
        ' /proc/meminfo 2>/dev/null)"',
        "printf 'gpuvram=%s\\n' \"$(nvidia-smi --query-gpu=memory.total"
        " --format=csv,noheader,nounits 2>/dev/null"
        " | awk '{s+=$1} END{if(NR)print int(s/1024)}')\"",
        "printf 'gpucount=%s\\n' \"$(nvidia-smi --query-gpu=name"
        ' --format=csv,noheader 2>/dev/null | wc -l)"',
        "printf 'gpuname=%s\\n' \"$(nvidia-smi --query-gpu=name"
        ' --format=csv,noheader 2>/dev/null | head -1)"',
        f"printf 'diskgb=%s\\n' \"$(df -Pk {wd} 2>/dev/null"
        " | awk 'NR==2{print int($4/1048576)}')\"",
    ]
    if download:
        q = shlex.quote(url)
        lines.append(
            "printf 'download=%s\\n' \"$(curl -fsSL -o /dev/null"
            f" --range 0-{_RANGE_BYTES} --max-time {int(max_time)}"
            f" -w '%{{speed_download}}' {q} 2>/dev/null)\""
        )
    lines.append(f"printf '{_END}\\n'")
    return "\n".join(lines)


def parse_bench(stdout: str, host: Host) -> HostBench:
    """Parse a YUJBENCH/YUJEND block into a :class:`HostBench`."""
    fields = _extract_fields(stdout)
    download = _to_float(fields.get("download"))
    return HostBench(
        name=host.name,
        ip=host.ip,
        reachable=True,
        cores=_to_int(fields.get("cores")),
        mem_gb=_to_int(fields.get("memgb")),
        gpu_vram_gb=_to_int(fields.get("gpuvram")),
        gpu_name=(fields.get("gpuname") or "").replace("NVIDIA ", "").strip() or None,
        gpu_count=_to_int(fields.get("gpucount")) or 0,
        disk_gb=_to_int(fields.get("diskgb")),
        download_mbps=round(download / 1e6, 1) if download else None,
    )


def bench_host(
    host: Host,
    *,
    work_dir: str = "~",
    url: str = DEFAULT_URL,
    max_time: int = DEFAULT_MAX_TIME,
    download: bool = True,
    connect_timeout: int = 20,
    timeout: float = DEFAULT_TIMEOUT,
) -> HostBench:
    """Benchmark a single host, returning a :class:`HostBench` (never raising)."""
    transport = make_transport(host, connect_timeout=connect_timeout)
    try:
        result = transport.run(
            _bench_command(work_dir, url, max_time, download), timeout=timeout
        )
    except YujError as exc:
        return HostBench(name=host.name, ip=host.ip, reachable=False, error=str(exc))
    if not result.ok or _BEGIN not in result.stdout:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        return HostBench(name=host.name, ip=host.ip, reachable=False, error=detail)
    return parse_bench(result.stdout, host)


def bench_fleet(
    fleet: Fleet,
    *,
    work_dir: str = "~",
    url: str = DEFAULT_URL,
    max_time: int = DEFAULT_MAX_TIME,
    download: bool = True,
    connect_timeout: int = 20,
    timeout: float = DEFAULT_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[HostBench]:
    """Benchmark every host in parallel, returning results in fleet order."""
    out = map_fleet(
        fleet,
        lambda host: bench_host(
            host,
            work_dir=work_dir,
            url=url,
            max_time=max_time,
            download=download,
            connect_timeout=connect_timeout,
            timeout=timeout,
        ),
        max_workers=max_workers,
    )
    return [out[name] for name in fleet.names]


def weights_for(
    benches: Sequence[HostBench], dim: str
) -> tuple[dict[str, float], list[str]]:
    """Split weights along ``dim``: ``{host: weight}``."""
    weights: dict[str, float] = {}
    dropped: list[str] = []
    for b in benches:
        value = b.metric(dim) if b.reachable else None
        if value is None or value <= 0:
            weights[b.name] = 0.0
            if b.reachable:
                dropped.append(b.name)
        else:
            weights[b.name] = float(value)
    return weights, dropped


def _extract_fields(stdout: str) -> dict[str, str]:
    """Pull ``key=value`` lines between the begin/end markers."""
    fields: dict[str, str] = {}
    in_block = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped == _BEGIN:
            in_block = True
            continue
        if stripped == _END:
            break
        if in_block and "=" in stripped:
            key, _, value = stripped.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
