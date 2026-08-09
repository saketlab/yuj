"""Support layer for the yuj CLI."""

from __future__ import annotations

import contextlib
import json
import time
import webbrowser
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

import typer
import yaml
from rich.console import Console
from rich.table import Table

from yuj._html import status_html
from yuj._render import sizing_table, status_table, summary_line
from yuj.canary import DEFAULT_TIMEOUT_S, run_canary
from yuj.config import ProjectConfig
from yuj.deploy import DeployPlan, deploy_fleet
from yuj.exceptions import YujError
from yuj.fleet import Fleet, load_from_csv, load_from_yaml
from yuj.preflight import local_preflight
from yuj.probe import probe_fleet
from yuj.scatter import read_items
from yuj.sizing import WorkerProfile, plan_workers
from yuj.status import HostStatus
from yuj.supervise import SuperviseConfig, submit_fleet
from yuj.window import Window

console = Console()
err_console = Console(stderr=True)

_FLEET_CANDIDATES = ("fleet.csv", "fleet.yaml", "fleet.yml")


def _die(message: str) -> NoReturn:
    """Print an error to stderr and exit with status 1."""
    err_console.print(f"[bold red]error:[/bold red] {message}")
    raise typer.Exit(code=1)


def _read_config() -> ProjectConfig:
    """Read ``yuj.yaml`` from the cwd if present; return defaults otherwise."""
    config_path = Path("yuj.yaml")
    if not config_path.is_file():
        return ProjectConfig.from_mapping({})
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return ProjectConfig.from_mapping(data if isinstance(data, dict) else {})


def _autodetect_fleet(config: ProjectConfig) -> Path:
    """Find the fleet file from config or the conventional filenames."""
    if config.fleet:
        return Path(config.fleet)
    for candidate in _FLEET_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    _die("no fleet file found. Run `yuj init` or pass --fleet PATH.")


def _load_fleet(path: Path) -> Fleet:
    """Load a fleet from a path, dispatching on file extension."""
    if path.suffix.lower() in (".yaml", ".yml"):
        return load_from_yaml(path)
    return load_from_csv(path)


def _load(fleet_path: Path | None) -> tuple[Fleet, ProjectConfig]:
    """Read config, then find and load the fleet; exits with status 1 on error."""
    config = _read_config()
    path = fleet_path or _autodetect_fleet(config)
    try:
        fleet = _load_fleet(path)
    except YujError as exc:
        _die(str(exc))
    return fleet, config


def _select_hosts(
    fleet: Fleet, hosts: str | None, *, allow_do_not_use: bool = False
) -> Fleet:
    """Resolve the --hosts selection, refusing explicitly-named do_not_use hosts.

    ``allow_do_not_use`` lifts that refusal for commands that deliberately target
    flagged-down hosts (e.g. ``rescue``, whose whole job is reviving them).
    """
    if not hosts or hosts.strip().lower() == "all":
        return fleet if allow_do_not_use else fleet.usable
    names = [n.strip() for n in hosts.split(",") if n.strip()]
    try:
        selected = fleet.select(names)
    except YujError as exc:
        _die(str(exc))
    if not allow_do_not_use:
        refused = [h.name for h in selected if h.do_not_use]
        if refused:
            _die(f"{', '.join(refused)} is marked do_not_use; refusing.")
    return selected


def _deploy_plan(config: ProjectConfig) -> DeployPlan:
    """Build the :class:`DeployPlan` from ``yuj.yaml``'s deploy section."""
    return DeployPlan(
        remote_dir=config.remote_dir,
        code_paths=tuple(Path(p) for p in config.deploy.get("code", [])),
        payload_paths=tuple(Path(p) for p in config.deploy.get("payload", [])),
    )


def _supervise_config(config: ProjectConfig) -> SuperviseConfig:
    """Build a :class:`SuperviseConfig` from ``yuj.yaml`` (work_command required)."""
    if not config.work_command:
        _die("'work_command' is required in yuj.yaml for `yuj submit`.")
    try:
        return SuperviseConfig(
            job=config.job,
            remote_dir=config.remote_dir,
            work_command=config.work_command,
            results_glob=config.results_glob,
            input_file=config.input_file,
            output_dir=config.output_dir,
            output_suffix=config.output_suffix,
            stall_min=config.stall_min,
            active_window=config.active_window,
            off_window_command=config.off_window_command,
            concurrency=config.concurrency,
        )
    except YujError as exc:
        _die(str(exc))


def _autotune(
    fleet: Fleet, statuses: Sequence[HostStatus], config: ProjectConfig
) -> Fleet:
    """Size each host from its free resources; return a fleet carrying the counts.

    Hosts with no headroom are dropped, not floored at one worker, so
    :func:`plan_workers` stays the only place that decides. Dies if none survive.
    """
    try:
        profile = WorkerProfile(**config.sizing)
    except (TypeError, YujError) as exc:
        _die(f"bad sizing section in yuj.yaml: {exc}")
    users = {h.name: h.user for h in fleet.hosts}
    plans = {
        s.name: plan_workers(
            s.gpus,
            nproc=s.nproc or 1,
            mem_avail_gb=s.mem_avail_gb or 0,
            load1=s.load1 or 0.0,
            me=users.get(s.name, ""),
            profile=profile,
        )
        for s in statuses
        if s.reachable
    }
    console.print(sizing_table(statuses, plans))
    usable = {n: p for n, p in plans.items() if p.usable}
    if not usable:
        _die(
            "no host has room for a worker right now. Sizing measures headroom, "
            "so autotune a fleet that is idle, or submit without --autotune."
        )
    return fleet.select(list(usable)).with_sizing(usable)


def _teardown_config(config: ProjectConfig) -> SuperviseConfig:
    """Build a SuperviseConfig for teardown (work_command/results_glob unused)."""
    return SuperviseConfig(
        job=config.job,
        remote_dir=config.remote_dir,
        work_command="true",
        results_glob=config.results_glob,
    )


def _count_items(config: ProjectConfig) -> int | None:
    """Total work-list size for progress, from scatter.input or input_file."""
    src = config.input_source
    if not src or not Path(src).is_file():
        return None
    return len(read_items(src))


def _resolve_status_opts(
    config: ProjectConfig,
    results_glob: str | None,
    stall_min: int | None,
    total: int | None,
) -> tuple[str, int, int | None]:
    """Resolve (glob, stall threshold, total items) from flags then config."""
    glob = results_glob or config.results_glob
    threshold = stall_min or config.stall_min
    total_items = total if total is not None else _count_items(config)
    return glob, threshold, total_items


def _print_op_table(verb: str, rows: list[tuple[str, bool, str]]) -> None:
    """Render a per-host outcome table for deploy/submit."""
    table = Table(title=f"yuj {verb}", header_style="bold cyan")
    table.add_column("host", style="bold")
    table.add_column("result")
    table.add_column("detail", style="dim")
    for name, ok, detail in rows:
        mark = "[green]✓ ok[/green]" if ok else "[bold red]✗ failed[/bold red]"
        table.add_row(name, mark, detail)
    console.print(table)
    failures = [name for name, ok, _ in rows if not ok]
    if failures:
        console.print(
            f"[yellow]{len(failures)} host(s) failed:[/yellow] " + ", ".join(failures)
        )
        raise typer.Exit(code=1)


def _preflight_or_die(config: ProjectConfig, *, push_payload: bool) -> None:
    """Abort with a clear message if any locally-referenced path is missing."""
    problems = local_preflight(config, push_payload=push_payload)
    if problems:
        _die("pre-flight failed:\n  - " + "\n  - ".join(problems))


def _print_canary(
    fleet: Fleet,
    cfg: SuperviseConfig,
    *,
    timeout_s: int,
    statuses: Sequence[HostStatus] | None = None,
) -> bool:
    """Dry-run the work command on one host, print the outcome, return ``ok``."""
    console.print(
        f"[dim]canary: dry-running work command on one host (≤{timeout_s}s)…[/dim]"
    )
    result = run_canary(fleet, cfg, timeout_s=timeout_s, statuses=statuses)
    if result.ok:
        where = f" on {result.host}" if result.host else ""
        console.print(f"[green]✓ canary passed[/green]{where}: {result.detail}")
    else:
        err_console.print(f"[bold red]✗ canary failed[/bold red]: {result.detail}")
        if result.output:
            err_console.print(f"[dim]{result.output}[/dim]")
    return result.ok


def do_canary(fleet: Fleet, config: ProjectConfig, *, timeout_s: int) -> None:
    """Run the standalone canary and exit non-zero if it fails."""
    if not _print_canary(fleet, _supervise_config(config), timeout_s=timeout_s):
        raise typer.Exit(code=1)


def _do_deploy(
    fleet: Fleet, config: ProjectConfig, *, push_payload: bool, timeout: float
) -> None:
    """Deploy to the fleet and print the outcome table (exits on any failure)."""
    _preflight_or_die(config, push_payload=push_payload)
    results = deploy_fleet(
        fleet, _deploy_plan(config), push_payload=push_payload, timeout=timeout
    )
    _print_op_table(
        "deploy",
        [
            (name, r.ok, r.error or f"sent {', '.join(r.transferred) or '(nothing)'}")
            for name, r in sorted(results.items())
        ],
    )


def _do_submit(
    fleet: Fleet,
    config: ProjectConfig,
    *,
    start: bool,
    timeout: float,
    canary: bool = True,
    canary_timeout: int = DEFAULT_TIMEOUT_S,
    autotune: bool = False,
) -> None:
    """Install supervision on the fleet and print the outcome table.

    Unless ``canary`` is False, first does a single-host dry run of the work
    command, so a broken job aborts before the watchdog is installed fleet-wide.
    With ``autotune``, each host's worker count is sized from its free resources
    instead of the job-wide ``concurrency``.
    """
    cfg = _supervise_config(config)
    statuses = None
    if autotune:
        if not config.input_file:
            _die(
                "--autotune needs 'input_file' in yuj.yaml; batch jobs "
                "self-parallelise."
            )
        statuses = probe_fleet(
            fleet, results_glob=config.results_glob, job=config.job, timeout=timeout
        )
        fleet = _autotune(fleet, statuses, config)
    if canary and not _print_canary(
        fleet, cfg, timeout_s=canary_timeout, statuses=statuses
    ):
        _die("aborting submit; fix the work command or pass --no-canary to skip.")
    results = submit_fleet(fleet, cfg, start=start, timeout=timeout)
    _print_op_table(
        "submit",
        [
            (
                name,
                r.ok,
                r.error or f"watchdog={r.watchdog_running} cron={r.cron_installed}",
            )
            for name, r in sorted(results.items())
        ],
    )


def _render_status(
    fleet_path: Path | None,
    results_glob: str | None,
    *,
    stall_min: int | None,
    timeout: float,
    total_items: int | None,
) -> None:
    """Resolve the fleet, probe once, and print the table + summary."""
    fleet, config = _load(fleet_path)
    glob, threshold, total = _resolve_status_opts(
        config, results_glob, stall_min, total_items
    )
    statuses = probe_fleet(fleet, results_glob=glob, job=config.job, timeout=timeout)
    eta, host_eta = _status_etas(config.job, fleet, statuses, total)
    console.print(
        status_table(
            statuses,
            stall_threshold_min=threshold,
            total_items=total,
            eta=eta,
            host_eta=host_eta,
        )
    )
    console.print(
        summary_line(statuses, stall_threshold_min=threshold, total_items=total),
        style="dim",
    )


def _window_status(active_window: str | None) -> tuple[str | None, str | None]:
    """Return ``(label, stops_in)`` for the configured run window, or ``(None, None)``.

    ``stops_in`` is how long until the window closes when we're inside it now,
    a paused note when we're outside it, or ``None`` for an always-on window.
    """
    if not active_window:
        return None, None
    try:
        window = Window.parse(active_window)
    except YujError:
        return None, None
    if window.always_on:
        return window.label, None
    now = datetime.now()
    now_min = now.hour * 60 + now.minute
    if not window.contains_minute(now_min):
        return window.label, "paused (outside window)"
    left = (window.end_min - now_min) % (24 * 60)
    return window.label, _humanize_minutes(left)


def _humanize_minutes(minutes: int) -> str:
    """``135`` → ``"2h15m"``, ``45`` → ``"45m"``."""
    hours, mins = divmod(max(minutes, 0), 60)
    return f"{hours}h{mins:02d}m" if hours else f"{mins}m"


_ETA_SMOOTH = 0.3


def _blend(prev_rate: float | None, dd: int, dt: float) -> float | None:
    """Blend the instantaneous rate into the running one."""
    if dt <= 0 or dd <= 0:
        return prev_rate
    inst = dd / (dt / 3600)
    if prev_rate is None:
        return inst
    return _ETA_SMOOTH * inst + (1 - _ETA_SMOOTH) * prev_rate


def _read_snap(snap: Path) -> dict[str, Any] | None:
    if not snap.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(snap.read_text())
        return data
    except (OSError, ValueError):
        return None


_FLEET_KEY = ""  # reserved series name for the fleet-wide total (no host is unnamed)


def _rates(job: str, done_by_name: dict[str, int]) -> dict[str, float | None]:
    """Smoothed per-hour production rate for each named counter, persisted per job."""
    snap = Path(".yuj") / f"eta-{job}.json"
    now = time.time()
    prev = _read_snap(snap) or {}
    prev_t = prev.get("t", now)
    prev_series = prev.get("series", {})
    series: dict[str, dict[str, float | None]] = {}
    for name, done in done_by_name.items():
        s = prev_series.get(name, {})
        series[name] = {
            "done": done,
            "rate": _blend(s.get("rate"), done - s.get("done", done), now - prev_t),
        }
    snap.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        snap.write_text(json.dumps({"t": now, "series": series}))
    return {name: s["rate"] for name, s in series.items()}


def _eta(remaining: float, per_hour: float) -> str:
    """Completion estimate from a remaining count and a per-hour rate."""
    if per_hour <= 0:
        return "—"
    hours = remaining / per_hour
    if hours < 1:
        return f"~{int(hours * 60)}m at {per_hour:,.0f}/hr"
    if hours < 48:
        return f"~{hours:.1f}h at {per_hour:,.0f}/hr"
    return f"~{hours / 24:.1f}d at {per_hour:,.0f}/hr"


def _status_etas(
    job: str, fleet: Fleet, statuses: Sequence[HostStatus], total: int | None
) -> tuple[str | None, dict[str, str]]:
    """Fleet ETA and per-host ETA/rate strings from one rate snapshot.

    A host's target is its weight-share of ``total``; without a total, show rate.
    """
    done_by_host = {s.name: s.n_outputs or 0 for s in statuses}
    fleet_done = sum(done_by_host.values())
    rates = _rates(job, {_FLEET_KEY: fleet_done, **done_by_host})

    fleet_rate = rates.get(_FLEET_KEY)
    fleet_eta = _eta(total - fleet_done, fleet_rate) if total and fleet_rate else None

    usable = fleet.usable
    weights = {h.name: h.weight for h in usable.hosts}
    wsum = usable.total_weight or 1.0
    host_eta: dict[str, str] = {}
    for s in statuses:
        rate = rates.get(s.name)
        if not rate or rate <= 0:
            continue
        if total and s.name in weights:
            target = total * weights[s.name] / wsum
            host_eta[s.name] = _eta(max(0.0, target - (s.n_outputs or 0)), rate)
        else:
            host_eta[s.name] = f"{rate:,.0f}/hr"
    return fleet_eta, host_eta


def _run_html_dashboard(
    fleet_path: Path | None,
    results_glob: str | None,
    *,
    stall_min: int | None,
    timeout: float,
    total_items: int | None,
    html_path: Path,
    refresh: float | None,
    open_browser: bool,
) -> None:
    """Probe the fleet and write a self-contained HTML dashboard.

    Writes once when ``refresh`` is ``None``; otherwise re-probes and rewrites
    the file every ``refresh`` seconds (the page reloads itself via meta-refresh)
    until interrupted. ETA is derived from the rolling production rate since the
    dashboard started.
    """
    fleet, config = _load(fleet_path)
    glob, threshold, total = _resolve_status_opts(
        config, results_glob, stall_min, total_items
    )
    window_label, _ = _window_status(config.active_window)
    refresh_secs = int(refresh) if refresh else None

    base_done: int | None = None
    base_time = time.monotonic()
    opened = False
    while True:
        statuses = probe_fleet(
            fleet, results_glob=glob, job=config.job, timeout=timeout
        )
        done = sum(s.n_outputs or 0 for s in statuses)
        if base_done is None:
            base_done = done
        elapsed_hr = (time.monotonic() - base_time) / 3600
        eta = None
        if total and elapsed_hr > 0 and done > base_done:
            per_hour = (done - base_done) / elapsed_hr
            eta = _eta(total - done, per_hour)
        _, stops_in = _window_status(config.active_window)
        html = status_html(
            statuses,
            title="yuj fleet",
            stall_threshold_min=threshold,
            total_items=total,
            refresh_secs=refresh_secs,
            window_label=window_label,
            stops_in=stops_in,
            eta=eta,
            updated=datetime.now().strftime("%H:%M:%S"),
        )
        html_path.write_text(html, encoding="utf-8")
        console.print(
            summary_line(statuses, stall_threshold_min=threshold, total_items=total),
            style="dim",
        )
        if open_browser and not opened:
            webbrowser.open(html_path.resolve().as_uri())
            opened = True
        if refresh is None:
            console.print(f"wrote {html_path}")
            return
        try:
            time.sleep(refresh)
        except KeyboardInterrupt:  # pragma: no cover - interactive only
            console.print("stopped.")
            return
