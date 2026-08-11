"""Generate and install the on-host run/watchdog/ensure scripts and cron entry."""

from __future__ import annotations

import re
import shlex
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from jinja2 import Environment, PackageLoader

from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.shell_safety import validate_remote_glob, validate_remote_path
from yuj.transport import Transport, make_transport
from yuj.window import Window

_JOB_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# autoescape is for HTML; these are shell scripts, so it must stay off. Trim
# block whitespace so the rendered bash is clean.
_ENV = Environment(  # nosec B701 - renders shell scripts, not HTML/XML
    loader=PackageLoader("yuj", "templates"),
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,  # noqa: S701  # nosec B701 - shell scripts, not HTML/XML
)


@dataclass(frozen=True)
class SuperviseConfig:
    """How to supervise one job on each host.

    Args:
        job: Short job name (used in script/sentinel/log names; ``[A-Za-z0-9_.-]``).
        remote_dir: Directory on each host, relative to ``$HOME``.
        work_command: The operator's command. With ``input_file`` set it is
            invoked once per item with the item as its final argument; otherwise
            it runs once and must resume itself.
        results_glob: Glob whose newest file's age drives stall detection.
        input_file: Per-host item list (relative to ``remote_dir``), enabling the
            resume-by-presence loop.
        output_dir: Where outputs land (relative to ``remote_dir``); required with
            ``input_file``.
        output_suffix: Output filename suffix, appended to the item.
        interval_sec: Seconds between watchdog checks.
        stall_min: Minutes of no new output before the job is considered stalled.
        grace_sec: Seconds after a (re)launch during which stalls are tolerated.
        cron_minutes: Cron cadence for the ensure self-heal.
        active_window: Optional daily run window ``"HH:MM-HH:MM"`` (may wrap past
            midnight). Outside it the watchdog pauses the work loop and does not
            relaunch, so a borrowed desktop is free for its owner during the day.
        off_window_command: Optional shell run when leaving the window, in
            addition to killing the work loop (e.g. stop a GPU server).
        courtesy: Share the GPUs. Each watchdog tick re-reads which cards carry
            someone else's compute and runs only on the idle ones, rewriting
            ``GPUS``/``WORKERS_PER_GPU`` and relaunching when the set changes.
    """

    job: str
    remote_dir: str
    work_command: str
    results_glob: str
    input_file: str | None = None
    output_dir: str | None = None
    output_suffix: str = ""
    interval_sec: int = 1800
    stall_min: int = 90
    grace_sec: int = 2700
    cron_minutes: int = 15
    active_window: str | None = None
    off_window_command: str | None = None
    concurrency: int = 1
    gpus: str = ""
    workers_per_gpu: str = ""
    courtesy: bool = False

    def __post_init__(self) -> None:
        if not _JOB_RE.match(self.job):
            raise YujError(
                f"invalid job name {self.job!r}",
                hint="use only letters, digits, '.', '_', '-'",
            )
        if self.input_file and not self.output_dir:
            raise YujError("output_dir is required when input_file is set")
        if self.concurrency < 1:
            raise YujError(f"concurrency must be >= 1, got {self.concurrency}")
        validate_remote_path(self.remote_dir, label="remote_dir")
        validate_remote_glob(self.results_glob)
        if self.input_file:
            validate_remote_path(self.input_file, label="input_file")
        if self.output_dir:
            validate_remote_path(self.output_dir, label="output_dir")
        if self.active_window:
            Window.parse(self.active_window)  # validate early; raises on bad format

    @property
    def window(self) -> Window | None:
        """The parsed :class:`Window`, or ``None`` when always-on."""
        return Window.parse(self.active_window) if self.active_window else None

    def for_host(self, host: Host) -> SuperviseConfig:
        """Return this config specialised for ``host``: window, then sizing."""
        window = self.active_window if host.window is None else (host.window or None)
        # 0 workers is a real size; only None means the host was never sized
        if host.concurrency is None:
            return replace(self, active_window=window)
        return replace(
            self,
            active_window=window,
            concurrency=host.concurrency,
            gpus=host.gpus,
            workers_per_gpu=host.workers_per_gpu,
        )

    @property
    def run_script(self) -> str:
        """Filename of the rendered work-loop script."""
        return f"{self.job}.yuj-run.sh"

    @property
    def watchdog_script(self) -> str:
        """Filename of the rendered watchdog script."""
        return f"{self.job}.yuj-watchdog.sh"

    @property
    def ensure_script(self) -> str:
        """Filename of the rendered cron self-heal script."""
        return f"{self.job}.yuj-ensure.sh"

    @property
    def gpu_file(self) -> str:
        """File the watchdog writes with the cards courtesy currently allows."""
        return f".{self.job}.yuj-gpus"

    @property
    def stop_sentinel(self) -> str:
        """Path of the stop sentinel that cleanly halts the job."""
        return f"/tmp/{self.job}.yuj.stop"  # noqa: S108  # nosec B108

    @property
    def cron_line(self) -> str:
        """The crontab entry that runs the ensure script."""
        return (
            f"*/{self.cron_minutes} * * * * /bin/bash "
            f"$HOME/{self.remote_dir}/{self.ensure_script} >/dev/null 2>&1"
        )


@dataclass(frozen=True)
class SubmitResult:
    """Outcome of submitting (installing supervision on) one host."""

    host: str
    ok: bool
    watchdog_running: bool = False
    cron_installed: bool = False
    error: str | None = None


def _context(cfg: SuperviseConfig) -> dict[str, object]:
    window = cfg.window
    return {
        "job": cfg.job,
        "remote_dir": cfg.remote_dir,
        "work_command": cfg.work_command,
        "results_glob": cfg.results_glob,
        "input_file": cfg.input_file,
        "output_dir": cfg.output_dir,
        "output_suffix": cfg.output_suffix,
        "interval_sec": cfg.interval_sec,
        "stall_min": cfg.stall_min,
        "grace_sec": cfg.grace_sec,
        "cron_minutes": cfg.cron_minutes,
        "concurrency": cfg.concurrency,
        "gpus": cfg.gpus,
        "workers_per_gpu": cfg.workers_per_gpu,
        "courtesy": cfg.courtesy,
        "gpu_file": cfg.gpu_file,
        "run_script": cfg.run_script,
        "watchdog_script": cfg.watchdog_script,
        "ensure_script": cfg.ensure_script,
        "stop_sentinel": cfg.stop_sentinel,
        "has_window": window is not None,
        "window_start_min": window.start_min if window else 0,
        "window_end_min": window.end_min if window else 0,
        "window_label": window.label if window else "",
        "off_window_command": cfg.off_window_command or "",
    }


def render_run(cfg: SuperviseConfig) -> str:
    return str(_ENV.get_template("run_chunk.sh.j2").render(**_context(cfg)))


def render_watchdog(cfg: SuperviseConfig) -> str:
    return str(_ENV.get_template("watchdog.sh.j2").render(**_context(cfg)))


def render_ensure(cfg: SuperviseConfig) -> str:
    return str(_ENV.get_template("ensure.sh.j2").render(**_context(cfg)))


def rendered_scripts(cfg: SuperviseConfig) -> dict[str, str]:
    """Return ``{filename: contents}`` for all three on-host scripts."""
    return {
        cfg.run_script: render_run(cfg),
        cfg.watchdog_script: render_watchdog(cfg),
        cfg.ensure_script: render_ensure(cfg),
    }


def submit(
    transport: Transport,
    cfg: SuperviseConfig,
    *,
    start: bool = True,
    timeout: float = 120.0,
) -> SubmitResult:
    """Install supervision for ``cfg`` on the host behind ``transport``.

    Renders the scripts, uploads them, installs the de-duped cron entry, clears
    any stop sentinel, and (by default) starts the watchdog. Idempotent.

    Returns:
        A :class:`SubmitResult`; ``ok`` is False (rather than raising) on failure
        so a fleet-wide submit can continue past a bad host.
    """
    host = transport.host.name
    try:
        _upload_scripts(transport, cfg, timeout=timeout)
        _install_cron(transport, cfg, timeout=timeout)
        transport.run(f"rm -f {shlex.quote(cfg.stop_sentinel)}", timeout=timeout)
        if start:
            _start_watchdog(transport, cfg, timeout=timeout)
        wd, cron = _verify(transport, cfg, timeout=timeout)
    except YujError as exc:
        return SubmitResult(host=host, ok=False, error=str(exc))
    if wd:
        error = None
    elif cron:
        error = "cron not installed (self-heal disabled)"
    else:
        error = "watchdog not confirmed running"
    return SubmitResult(
        host=host,
        ok=wd,
        watchdog_running=wd,
        cron_installed=cron,
        error=error,
    )


def submit_fleet(
    fleet: Fleet,
    cfg: SuperviseConfig,
    *,
    start: bool = True,
    connect_timeout: int = 20,
    timeout: float = 120.0,
    max_workers: int = 8,
) -> dict[str, SubmitResult]:
    """Submit ``cfg`` to every host in parallel; tolerant of individual failures.

    Each host gets its own :meth:`SuperviseConfig.for_host`.
    """
    return map_fleet(
        fleet,
        lambda host: submit(
            make_transport(host, connect_timeout=connect_timeout),
            cfg.for_host(host),
            start=start,
            timeout=timeout,
        ),
        max_workers=max_workers,
    )


def stop(transport: Transport, cfg: SuperviseConfig, *, timeout: float = 30.0) -> None:
    """Touch the stop sentinel so the watchdog tears the job down gracefully."""
    transport.run(f"touch {shlex.quote(cfg.stop_sentinel)}", timeout=timeout)


def _upload_scripts(
    transport: Transport, cfg: SuperviseConfig, *, timeout: float
) -> None:
    """Render scripts to a temp dir, rsync them up, and make them executable."""
    remote = cfg.remote_dir.rstrip("/")
    mkdir = transport.run(f"mkdir -p {shlex.quote(remote)}/logs", timeout=timeout)
    if not mkdir.ok:
        raise YujError(f"could not create {remote!r}: {mkdir.stderr.strip()}")
    scripts = rendered_scripts(cfg)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for name, contents in scripts.items():
            (tmp_path / name).write_text(contents, encoding="utf-8")
            result = transport.put(str(tmp_path / name), remote + "/", timeout=timeout)
            if not result.ok:
                raise YujError(f"upload of {name} failed: {result.stderr.strip()}")
    names = " ".join(shlex.quote(f"{remote}/{n}") for n in scripts)
    transport.run(f"chmod +x {names}", timeout=timeout)


def _install_cron(
    transport: Transport, cfg: SuperviseConfig, *, timeout: float
) -> None:
    """Install the ensure cron entry, removing any prior copy first (dedupe)."""
    # -F: literal match, drops only this job's line
    install = (
        f"( crontab -l 2>/dev/null | grep -Fv {shlex.quote(cfg.ensure_script)} ; "
        f"echo {shlex.quote(cfg.cron_line)} ) | crontab -"
    )
    transport.run(install, timeout=timeout)


def _start_watchdog(
    transport: Transport, cfg: SuperviseConfig, *, timeout: float
) -> None:
    """Launch the watchdog, fully detached so ssh returns promptly."""
    remote = shlex.quote(cfg.remote_dir.rstrip("/"))
    wd = shlex.quote(cfg.watchdog_script)
    cmd = (
        f"cd $HOME/{remote} && nohup bash {wd} </dev/null "
        f">>logs/{cfg.job}.watchdog.log 2>&1 &"
    )
    transport.run(cmd, timeout=timeout)


def _verify(
    transport: Transport, cfg: SuperviseConfig, *, timeout: float
) -> tuple[bool, bool]:
    """Return ``(watchdog_running, cron_installed)`` read back from the host."""
    ensure = shlex.quote(cfg.ensure_script)
    # job-specific match, else a generic pattern masks a failed start
    # [b]ash bracket avoids matching our own argv
    wd_pat = shlex.quote(f"[b]ash .*{cfg.watchdog_script}")
    cmd = (
        f"echo wd=$(ps -eo args 2>/dev/null | grep -c {wd_pat}); "
        f"echo cron=$(crontab -l 2>/dev/null | grep -Fc {ensure})"
    )
    result = transport.run(cmd, timeout=timeout)
    wd = cron = False
    for line in result.stdout.splitlines():
        if line.startswith("wd="):
            wd = _first_int(line[3:]) > 0
        elif line.startswith("cron="):
            cron = _first_int(line[5:]) > 0
    return wd, cron


def _first_int(text: str) -> int:
    match = re.match(r"\s*(\d+)", text)
    return int(match.group(1)) if match else 0
