"""Probe each host in one SSH round-trip; parse the YUJSTATUS/YUJEND key=value block."""

from __future__ import annotations

import re
from dataclasses import dataclass

from yuj.exceptions import AuthError, CommandTimeout, TransportError, YujError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.transport import _AUTH_FAIL_MARKERS, SSHTransport

_BEGIN = "YUJSTATUS"
_END = "YUJEND"
# The marker baked into watchdog process names so a probe can spot supervision.
WATCHDOG_MARKER = "yuj-watchdog"
DEFAULT_PROBE_TIMEOUT = 20.0
DEFAULT_MAX_WORKERS = 8
DEFAULT_STALL_MIN = 90

# A results glob is operator config, not untrusted input, but it is spliced
# unquoted into the remote shell so it can expand. Permit only glob/path
# characters; reject anything that could start a new command or substitution.
_SAFE_GLOB = re.compile(r"^[\w@%+=:,./~*?\[\]{}-]+$")


@dataclass(frozen=True)
class HostStatus:
    """A point-in-time snapshot of one host."""

    name: str
    ip: str
    reachable: bool
    hostname: str | None = None
    cpu_model: str | None = None
    nproc: int | None = None
    mem_gb: int | None = None
    gpu: str | None = None
    load1: float | None = None
    n_outputs: int | None = None
    newest_age_min: int | None = None
    watchdog_running: bool = False
    console_user: str | None = None
    error: str | None = None

    @property
    def owner_present(self) -> bool:
        """True if a human appears to be logged in at the console (be polite!)."""
        return bool(self.console_user)

    def state(self, stall_threshold_min: int = DEFAULT_STALL_MIN) -> str:
        """Classify the host: ``down``/``producing``/``stalled``/``idle``."""
        if not self.reachable:
            return "down"
        age = self.newest_age_min
        if age is not None and age < stall_threshold_min:
            return "producing"
        if self.watchdog_running:
            return "idle" if age is None else "stalled"
        if self.n_outputs:
            return "stalled"
        return "idle"


def _validate_glob(results_glob: str) -> str:
    if not _SAFE_GLOB.match(results_glob):
        raise YujError(
            f"unsafe results glob: {results_glob!r}",
            hint="use only path/glob characters (no spaces or shell metacharacters)",
        )
    return results_glob


def _status_command(results_glob: str) -> str:
    """Build the defensive remote one-liner emitting a YUJSTATUS/YUJEND block."""
    glob = _validate_glob(results_glob)
    marker = f"[{WATCHDOG_MARKER[0]}]{WATCHDOG_MARKER[1:]}"
    return "\n".join(
        (
            f"printf '{_BEGIN}\\n'",
            "printf 'host=%s\\n' \"$(hostname 2>/dev/null)\"",
            "printf 'load=%s\\n' \"$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)\"",
            "printf 'nproc=%s\\n' \"$(nproc 2>/dev/null)\"",
            "printf 'mem=%s\\n' \"$(free -g 2>/dev/null | awk '/Mem/{print $2}')\"",
            "printf 'cpu=%s\\n' \"$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null"
            " | cut -d: -f2- | sed 's/^ *//')\"",
            "printf 'gpu=%s\\n' \"$(nvidia-smi --query-gpu=name,memory.total"
            ' --format=csv,noheader 2>/dev/null | head -1)"',
            f"printf 'outputs=%s\\n' \"$(ls -t {glob} 2>/dev/null | wc -l)\"",
            f'newest="$(ls -t {glob} 2>/dev/null | head -1)";'
            " if [ -n \"$newest\" ]; then printf 'age=%s\\n'"
            ' "$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 60 ))"; fi',
            f"printf 'wd=%s\\n' \"$(ps -eo args 2>/dev/null | grep -c '{marker}')\"",
            "printf 'console=%s\\n'"
            " \"$(who 2>/dev/null | awk '$2 ~ /:0|tty|seat/{print $1; exit}')\"",
            f"printf '{_END}\\n'",
        )
    )


def parse_status(stdout: str, host: Host) -> HostStatus:
    """Parse the YUJSTATUS/YUJEND key=value block into a :class:`HostStatus`."""
    fields = _extract_fields(stdout)
    return HostStatus(
        name=host.name,
        ip=host.ip,
        reachable=True,
        hostname=fields.get("host") or None,
        cpu_model=_short_cpu(fields.get("cpu")),
        nproc=_to_int(fields.get("nproc")),
        mem_gb=_to_int(fields.get("mem")),
        gpu=_short_gpu(fields.get("gpu")),
        load1=_to_float(fields.get("load")),
        n_outputs=_to_int(fields.get("outputs")),
        newest_age_min=_to_int(fields.get("age")),
        watchdog_running=(_to_int(fields.get("wd")) or 0) > 0,
        console_user=fields.get("console") or None,
    )


def probe_host(
    host: Host,
    *,
    results_glob: str = "~/*",
    connect_timeout: int = 20,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> HostStatus:
    """Probe a single host, returning a :class:`HostStatus` (never raising)."""
    transport = SSHTransport(host, connect_timeout=connect_timeout)
    try:
        result = transport.run(_status_command(results_glob), timeout=timeout)
    except YujError as exc:
        return HostStatus(name=host.name, ip=host.ip, reachable=False, error=str(exc))
    if not result.ok or _BEGIN not in result.stdout:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        return HostStatus(name=host.name, ip=host.ip, reachable=False, error=detail)
    return parse_status(result.stdout, host)


def probe_fleet(
    fleet: Fleet,
    *,
    results_glob: str = "~/*",
    connect_timeout: int = 20,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[HostStatus]:
    """Probe every host in parallel, returning statuses in fleet order."""
    statuses = map_fleet(
        fleet,
        lambda host: probe_host(
            host,
            results_glob=results_glob,
            connect_timeout=connect_timeout,
            timeout=timeout,
        ),
        max_workers=max_workers,
    )
    return [statuses[name] for name in fleet.names]


@dataclass(frozen=True)
class Diagnosis:
    """Why a host can't be reached, classified by failure stage.

    ``status`` is one of: ``ok`` (auth succeeded), ``auth_refused`` (reachable,
    SSH speaks, but credentials rejected, often fail2ban after repeated tries),
    ``banner_fail`` (TCP connects but no SSH banner, a classic fail2ban drop),
    ``sshd_down`` (port closed / connection refused), ``net_down`` (no route /
    timeout), or ``unknown``.
    """

    name: str
    ip: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True only when authentication succeeded."""
        return self.status == "ok"


def classify_host(host: Host, *, timeout: float = 12.0) -> Diagnosis:
    """Diagnose why a host is (un)reachable using a single gentle SSH attempt.

    One connection only, since diagnosing must not hammer a host that may already be
    one failed auth away from a fail2ban ban.
    """
    transport = SSHTransport(host, connect_timeout=int(timeout))
    try:
        result = transport.run("echo yuj-auth-ok", timeout=timeout + 5)
    except AuthError as exc:
        return Diagnosis(host.name, host.ip, "auth_refused", str(exc).splitlines()[0])
    except CommandTimeout:
        return Diagnosis(host.name, host.ip, "net_down", "connection timed out")
    except TransportError as exc:
        return _classify_stderr(host, str(exc))
    if result.ok and "yuj-auth-ok" in result.stdout:
        return Diagnosis(host.name, host.ip, "ok", "")
    return _classify_stderr(host, result.stderr)


def diagnose_fleet(
    fleet: Fleet,
    *,
    timeout: float = 12.0,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[Diagnosis]:
    """Classify every host in parallel (capped), in fleet order."""
    out = map_fleet(
        fleet,
        lambda host: classify_host(host, timeout=timeout),
        max_workers=max_workers,
    )
    return [out[name] for name in fleet.names]


def _classify_stderr(host: Host, stderr: str) -> Diagnosis:
    """Map an ssh failure message to a :class:`Diagnosis` status."""
    s = stderr.lower()
    detail = stderr.strip().splitlines()[-1][:80] if stderr.strip() else ""
    if any(m in s for m in _AUTH_FAIL_MARKERS):
        return Diagnosis(host.name, host.ip, "auth_refused", detail)
    if "connection refused" in s:
        return Diagnosis(host.name, host.ip, "sshd_down", detail)
    if "banner exchange" in s:
        return Diagnosis(host.name, host.ip, "banner_fail", detail)
    if any(
        m in s
        for m in ("no route to host", "network is unreachable", "name or service")
    ):
        return Diagnosis(host.name, host.ip, "net_down", detail)
    if "timed out" in s or "timeout" in s:
        return Diagnosis(host.name, host.ip, "net_down", detail)
    return Diagnosis(host.name, host.ip, "unknown", detail)


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


def _short_cpu(model: str | None) -> str | None:
    """Trim a noisy CPU model string to something column-friendly."""
    if not model:
        return None
    cleaned = model.replace("(R)", "").replace("(TM)", "").replace("CPU", "")
    cleaned = cleaned.split("@", 1)[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:24] or None


def _short_gpu(raw: str | None) -> str | None:
    """Turn ``"NVIDIA A40, 46068 MiB"`` into ``"A40 45G"`` (or None)."""
    if not raw:
        return None
    name, _, mem = raw.partition(",")
    name = name.replace("NVIDIA", "").replace("GeForce", "").strip()
    mib = re.search(r"\d+", mem)
    if mib:
        gib = round(int(mib.group()) / 1024)
        return f"{name} {gib}G".strip()
    return name or None


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
