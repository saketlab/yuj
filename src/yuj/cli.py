"""The yuj CLI. Settings load from yuj.yaml in the cwd; flags override them."""

from __future__ import annotations

import getpass
import time
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
import yaml
from rich.console import Console
from rich.live import Live
from rich.table import Table

from yuj import __version__
from yuj._render import diagnosis_table, status_table, summary_line
from yuj.bootstrap import BootstrapConfig, bootstrap_fleet
from yuj.decommission import decommission as _decommission
from yuj.decommission import schedule_decommission as _schedule_decommission
from yuj.deploy import DeployPlan, deploy_fleet
from yuj.exceptions import YujError
from yuj.fleet import Fleet, load_from_csv, load_from_yaml
from yuj.probe import DEFAULT_STALL_MIN, diagnose_fleet, probe_fleet
from yuj.provision import (
    DEFAULT_FLEET_OUT,
    DEFAULT_KEY_DIR,
    ProvisionConfig,
    provision_fleet,
)
from yuj.pull import pull_once
from yuj.scaffolds import scaffold_files
from yuj.supervise import SuperviseConfig, submit_fleet
from yuj.transport import SSHTransport

app = typer.Typer(
    name="yuj",
    help=(
        "Scatter a batch across opportunistic SSH targets and gather it back.\n\n"
        "Typical flow: yuj init -> edit fleet.csv + yuj.yaml -> yuj run -> "
        "yuj status -> yuj pull.\n\n"
        "All settings live in yuj.yaml (fleet, job, work_command, input_file, "
        "deploy.payload)."
    ),
    no_args_is_help=True,
    add_completion=False,
)
fleet_app = typer.Typer(
    help="Inspect and probe the fleet inventory.", no_args_is_help=True
)
app.add_typer(fleet_app, name="fleet")

console = Console()
err_console = Console(stderr=True)

_FLEET_CANDIDATES = ("fleet.csv", "fleet.yaml", "fleet.yml")
_DEFAULT_RESULTS_GLOB = "~/*"
_DEFAULT_REMOTE_DIR = "yuj-run"

_FleetOpt = Annotated[
    Path | None, typer.Option("--fleet", "-f", help="Fleet CSV/YAML path.")
]
_HostsOpt = Annotated[
    str | None,
    typer.Option(
        "--hosts",
        help="Comma-separated host names (default: all hosts not marked do_not_use).",
    ),
]
_TotalOpt = Annotated[
    int | None,
    typer.Option("--total", help="Total items for progress % (else from input_file)."),
]


@app.callback()
def main() -> None:
    """yuj: scatter a batch across opportunistic SSH targets and gather it back."""


@app.command()
def version() -> None:
    """Print the installed yuj version."""
    typer.echo(__version__)


@app.command()
def init(
    directory: Annotated[
        Path, typer.Argument(help="Directory to scaffold into.")
    ] = Path(),
    template: Annotated[
        str,
        typer.Option("--template", "-t", help="Project template: bare | python | r."),
    ] = "bare",
) -> None:
    """Scaffold a ready-to-edit project (idempotent; never clobbers your files).

    ``--template python`` and ``--template r`` add a worker, a sample input, and
    (for R) a conda ``environment.yaml`` + ``r-packages.txt`` so you can go from
    ``yuj init`` to a running job in four commands.
    """
    try:
        files = scaffold_files(template)
    except YujError as exc:
        _die(str(exc))
    directory.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    skipped: list[str] = []
    for name, content in files.items():
        target = directory / name
        if target.exists():
            skipped.append(name)
            continue
        target.write_text(content, encoding="utf-8")
        created.append(name)
    for name in created:
        console.print(f"[green]created[/green] {directory / name}")
    for name in skipped:
        console.print(f"[yellow]exists, left as-is[/yellow] {directory / name}")
    if created:
        next_steps = (
            "yuj bootstrap → yuj run → yuj status → yuj pull"
            if template in ("python", "r")
            else "yuj run → yuj status"
        )
        console.print(
            f"\nNext: edit [bold]fleet.csv[/bold] with your hosts, then run "
            f"[bold]{next_steps}[/bold]."
        )


@fleet_app.command("probe")
def fleet_probe(
    fleet_path: _FleetOpt = None,
    results_glob: Annotated[
        str | None, typer.Option("--results-glob", help="Glob counted per host.")
    ] = None,
    total: _TotalOpt = None,
    timeout: Annotated[float, typer.Option(help="Per-host probe timeout (s).")] = 20.0,
) -> None:
    """Probe every host once and print a status dashboard."""
    _render_status(
        fleet_path, results_glob, stall_min=None, timeout=timeout, total_items=total
    )


@app.command()
def status(
    fleet_path: _FleetOpt = None,
    results_glob: Annotated[
        str | None, typer.Option("--results-glob", help="Glob counted per host.")
    ] = None,
    stall_min: Annotated[
        int | None, typer.Option("--stall-min", help="Stall threshold (minutes).")
    ] = None,
    total: _TotalOpt = None,
    watch: Annotated[
        float | None,
        typer.Option("--watch", "-w", help="Refresh every N seconds (live mode)."),
    ] = None,
    timeout: Annotated[float, typer.Option(help="Per-host probe timeout (s).")] = 20.0,
) -> None:
    """Show fleet-wide status: producing, idle, stalled, down, or owner-occupied."""
    if watch is None:
        _render_status(
            fleet_path,
            results_glob,
            stall_min=stall_min,
            timeout=timeout,
            total_items=total,
        )
        return
    fleet, config = _load(fleet_path)
    glob, threshold, total_items = _resolve_status_opts(
        config, results_glob, stall_min, total
    )
    try:
        with Live(console=console, auto_refresh=False, screen=True) as live:
            while True:
                statuses = probe_fleet(fleet, results_glob=glob, timeout=timeout)
                live.update(
                    status_table(
                        statuses,
                        stall_threshold_min=threshold,
                        total_items=total_items,
                    ),
                    refresh=True,
                )
                time.sleep(watch)
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        console.print("stopped.")


@app.command()
def deploy(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    no_payload: Annotated[
        bool, typer.Option("--no-payload", help="Skip heavy payload (light deploy).")
    ] = False,
    timeout: Annotated[
        float, typer.Option(help="Per-host transfer timeout (s).")
    ] = 1800.0,
) -> None:
    """Rsync code (and payload) to every host, per ``yuj.yaml``'s deploy section.

    ``deploy.code`` is synced on every run (your scripts, input list).
    ``deploy.payload`` is heavy data sent once (e.g. a PDF or image directory);
    skip re-sending it on subsequent runs with ``--no-payload``.
    """
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    _do_deploy(fleet, config, push_payload=not no_payload, timeout=timeout)


@app.command()
def submit(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    no_start: Annotated[
        bool, typer.Option("--no-start", help="Install but don't start the watchdog.")
    ] = False,
    timeout: Annotated[float, typer.Option(help="Per-host op timeout (s).")] = 120.0,
) -> None:
    """Install the self-healing watchdog + cron on every host."""
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    _do_submit(fleet, config, start=not no_start, timeout=timeout)


@app.command()
def bootstrap(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    env_manager: Annotated[
        str | None,
        typer.Option("--env-manager", help="uv | pixi | micromamba | conda."),
    ] = None,
    python: Annotated[
        str | None, typer.Option("--python", help="Python version for the env.")
    ] = None,
    extras: Annotated[
        str | None,
        typer.Option("--extras", help="Comma-separated extras, e.g. OLLAMA,RCLONE."),
    ] = None,
    env_file: Annotated[
        str | None, typer.Option("--env-file", help="Env spec file on each host.")
    ] = None,
    check: Annotated[
        bool, typer.Option("--check", help="Dry-run: report, install nothing.")
    ] = False,
    max_workers: Annotated[
        int, typer.Option("--max-workers", help="Per-fleet concurrency cap.")
    ] = 4,
    timeout: Annotated[float, typer.Option(help="Per-host timeout (s).")] = 1800.0,
) -> None:
    """Install an env manager (+ extras) on every host, from a bare login shell."""
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    bcfg = config.get("bootstrap") or {}
    extras_list = (
        [e.strip() for e in extras.split(",") if e.strip()]
        if extras is not None
        else [str(e) for e in bcfg.get("extras", [])]
    )
    try:
        cfg = BootstrapConfig(
            env_manager=env_manager or str(bcfg.get("env_manager", "uv")),
            python=python or str(bcfg.get("python", "3.12")),
            extras=tuple(extras_list),
            env_file=env_file or bcfg.get("env_file"),
            remote_dir=_remote_dir(config),
            check=check,
        )
    except YujError as exc:
        _die(str(exc))
    results = bootstrap_fleet(fleet, cfg, max_workers=max_workers, timeout=timeout)
    _print_op_table(
        "bootstrap",
        [
            (
                name,
                r.ok,
                r.error
                or (
                    ("already bootstrapped" if r.already_done else "bootstrapped")
                    + (f" [{r.os_pretty}]" if r.os_pretty else "")
                ),
            )
            for name, r in sorted(results.items())
        ],
    )


@app.command()
def provision(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    user: Annotated[
        str, typer.Option("--user", help="Worker username to create on each host.")
    ] = "yuj",
    ask_sudo_pass: Annotated[
        bool,
        typer.Option(
            "--ask-sudo-pass",
            help="Prompt for the admin sudo password (default: reuse SSH password).",
        ),
    ] = False,
    key_dir: Annotated[
        str, typer.Option("--key-dir", help="Where to store the generated private key.")
    ] = DEFAULT_KEY_DIR,
    out: Annotated[
        str, typer.Option("--out", help="Path for the generated worker fleet CSV.")
    ] = DEFAULT_FLEET_OUT,
    check: Annotated[
        bool, typer.Option("--check", help="Dry-run: report, create nothing.")
    ] = False,
    max_workers: Annotated[
        int, typer.Option("--max-workers", help="Per-fleet concurrency cap.")
    ] = 4,
    timeout: Annotated[float, typer.Option(help="Per-host timeout (s).")] = 120.0,
) -> None:
    """Create an unprivileged worker user on every host (admin + sudo) and save creds.

    The ``--fleet`` here is an *admin* fleet: accounts that can ``sudo``. yuj
    generates one SSH keypair, installs the public key for the new user on each
    host, and writes a ready-to-use worker fleet CSV at ``--out``.
    """
    fleet = _load(fleet_path)[0]
    fleet = _select_hosts(fleet, hosts)
    sudo_password = getpass.getpass("admin sudo password: ") if ask_sudo_pass else None
    try:
        cfg = ProvisionConfig(new_user=user, sudo_password=sudo_password, check=check)
        results = provision_fleet(
            fleet,
            cfg,
            key_dir=key_dir,
            fleet_out=out,
            max_workers=max_workers,
            timeout=timeout,
        )
    except YujError as exc:
        _die(str(exc))
    if not check and any(r.ok for r in results.values()):
        console.print(
            f"[green]wrote[/green] {out} (private key under {key_dir}/); "
            f"next: [bold]yuj bootstrap --fleet {out}[/bold]"
        )
    _print_op_table(
        "provision",
        [
            (
                name,
                r.ok,
                r.error
                or (
                    f"created user {r.new_user}" if r.created else "user already exists"
                ),
            )
            for name, r in sorted(results.items())
        ],
    )


@app.command()
def diagnose(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    timeout: Annotated[float, typer.Option(help="Per-host probe timeout (s).")] = 12.0,
) -> None:
    """Classify why hosts are (un)reachable: net/sshd/banner/auth (fail2ban-aware)."""
    fleet = _load(fleet_path)[0]
    selected = _select_hosts(fleet, hosts)
    diagnoses = diagnose_fleet(selected, timeout=timeout)
    console.print(diagnosis_table(diagnoses))


@app.command()
def decommission(
    host: Annotated[str, typer.Argument(help="Host name to decommission.")],
    fleet_path: _FleetOpt = None,
    at: Annotated[
        str | None,
        typer.Option("--at", help='Schedule (e.g. "+90 seconds", "9am tomorrow").'),
    ] = None,
    remove_dir: Annotated[
        bool, typer.Option("--remove-dir", help="Also delete the deploy directory.")
    ] = False,
    timeout: Annotated[float, typer.Option(help="Per-host op timeout (s).")] = 60.0,
) -> None:
    """Politely tear down the yuj job on HOST (now, or scheduled with --at)."""
    fleet, config = _load(fleet_path)
    try:
        target = fleet.get(host)
    except YujError as exc:
        _die(str(exc))
    cfg = _teardown_config(config)
    transport = SSHTransport(target)
    if at is not None:
        result = _schedule_decommission(
            transport, cfg, at, remove_dir=remove_dir, timeout=timeout
        )
        if not result.ok:
            _die(str(result.error))
        console.print(f"[green]scheduled[/green] decommission of {host} at {at}")
        return
    result = _decommission(transport, cfg, remove_dir=remove_dir, timeout=timeout)
    if not result.ok:
        _die(str(result.error))
    console.print(
        f"[green]decommissioned[/green] {host}: cron removed, "
        f"{result.processes_remaining} processes remaining"
    )


@app.command()
def pull(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    dest: Annotated[
        str,
        typer.Option("--dest", "-d", help="Local directory to receive results."),
    ] = "results",
    per_host: Annotated[
        bool,
        typer.Option("--per-host", help="Store each host's results in dest/<host>/."),
    ] = False,
    timeout: Annotated[
        float, typer.Option(help="Per-host transfer timeout (s).")
    ] = 1800.0,
) -> None:
    """Pull results from every host into a local directory.

    Rsyncs ``output_dir`` (from yuj.yaml) from each host into ``--dest``.
    Run after ``yuj status`` shows the job is done or whenever you want
    partial results.
    """
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    remote_dir = str(config.get("remote_dir", _DEFAULT_REMOTE_DIR))
    output_dir = config.get("output_dir")
    source = f"{remote_dir}/{output_dir}" if output_dir else remote_dir
    results = pull_once(
        fleet,
        remote_dir=source,
        dest_dir=dest,
        per_host_subdir=per_host,
        timeout=timeout,
    )
    _print_op_table(
        "pull",
        [
            (name, r.ok, r.error or f"→ {r.destination}")
            for name, r in sorted(results.items())
        ],
    )


@app.command()
def run(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    no_payload: Annotated[
        bool, typer.Option("--no-payload", help="Skip heavy payload (light deploy).")
    ] = False,
    no_start: Annotated[
        bool, typer.Option("--no-start", help="Install watchdog but don't start it.")
    ] = False,
    deploy_timeout: Annotated[
        float, typer.Option("--deploy-timeout", help="Per-host deploy timeout (s).")
    ] = 1800.0,
    timeout: Annotated[
        float, typer.Option(help="Per-host submit timeout (s).")
    ] = 120.0,
) -> None:
    """Deploy then submit in one step (shorthand for yuj deploy && yuj submit).

    Use ``--no-payload`` on re-runs to skip heavy data already on each host
    (e.g. a large PDF directory synced on the first run).
    """
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    _do_deploy(fleet, config, push_payload=not no_payload, timeout=deploy_timeout)
    _do_submit(fleet, config, start=not no_start, timeout=timeout)


# -- helpers ---------------------------------------------------------------


def _die(message: str) -> NoReturn:
    """Print an error to stderr and exit with status 1."""
    err_console.print(f"[bold red]error:[/bold red] {message}")
    raise typer.Exit(code=1)


def _remote_dir(config: dict[str, Any]) -> str:
    """The remote deploy directory from config (relative to each host's $HOME)."""
    return str(config.get("remote_dir", _DEFAULT_REMOTE_DIR))


def _deploy_plan(config: dict[str, Any]) -> DeployPlan:
    """Build the :class:`DeployPlan` from ``yuj.yaml``'s deploy section."""
    deploy_cfg = config.get("deploy") or {}
    return DeployPlan(
        remote_dir=_remote_dir(config),
        code_paths=tuple(Path(p) for p in deploy_cfg.get("code", [])),
        payload_paths=tuple(Path(p) for p in deploy_cfg.get("payload", [])),
    )


def _do_deploy(
    fleet: Fleet, config: dict[str, Any], *, push_payload: bool, timeout: float
) -> None:
    """Deploy to the fleet and print the outcome table (exits on any failure)."""
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
    fleet: Fleet, config: dict[str, Any], *, start: bool, timeout: float
) -> None:
    """Install supervision on the fleet and print the outcome table."""
    results = submit_fleet(
        fleet, _supervise_config(config), start=start, timeout=timeout
    )
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


def _select_hosts(fleet: Fleet, hosts: str | None) -> Fleet:
    """Resolve the --hosts selection, refusing explicitly-named do_not_use hosts."""
    if not hosts or hosts.strip().lower() == "all":
        return fleet.usable
    names = [n.strip() for n in hosts.split(",") if n.strip()]
    try:
        selected = fleet.select(names)
    except YujError as exc:
        _die(str(exc))
    refused = [h.name for h in selected if h.do_not_use]
    if refused:
        _die(f"{', '.join(refused)} is marked do_not_use; refusing.")
    return selected


def _teardown_config(config: dict[str, Any]) -> SuperviseConfig:
    """Build a SuperviseConfig for teardown (work_command/results_glob unused)."""
    return SuperviseConfig(
        job=str(config.get("job", "yuj")),
        remote_dir=_remote_dir(config),
        work_command="true",
        results_glob=str(config.get("results_glob", _DEFAULT_RESULTS_GLOB)),
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
    statuses = probe_fleet(fleet, results_glob=glob, timeout=timeout)
    console.print(
        status_table(statuses, stall_threshold_min=threshold, total_items=total)
    )
    console.print(
        summary_line(statuses, stall_threshold_min=threshold, total_items=total),
        style="dim",
    )


def _resolve_status_opts(
    config: dict[str, Any],
    results_glob: str | None,
    stall_min: int | None,
    total: int | None,
) -> tuple[str, int, int | None]:
    """Resolve (glob, stall threshold, total items) from flags then config."""
    glob = results_glob or config.get("results_glob") or _DEFAULT_RESULTS_GLOB
    threshold = stall_min or int(config.get("stall_min", DEFAULT_STALL_MIN))
    total_items = total if total is not None else _count_items(config)
    return glob, threshold, total_items


def _count_items(config: dict[str, Any]) -> int | None:
    """Count non-blank lines in the local input_file, if configured."""
    input_file = config.get("input_file")
    if not input_file:
        return None
    path = Path(str(input_file))
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _supervise_config(config: dict[str, Any]) -> SuperviseConfig:
    """Build a :class:`SuperviseConfig` from ``yuj.yaml`` (work_command required)."""
    work_command = config.get("work_command")
    if not work_command:
        _die("'work_command' is required in yuj.yaml for `yuj submit`.")
    try:
        return SuperviseConfig(
            job=str(config.get("job", "yuj")),
            remote_dir=_remote_dir(config),
            work_command=str(work_command),
            results_glob=str(config.get("results_glob", _DEFAULT_RESULTS_GLOB)),
            input_file=config.get("input_file"),
            output_dir=config.get("output_dir"),
            output_suffix=str(config.get("output_suffix", "")),
            stall_min=int(config.get("stall_min", DEFAULT_STALL_MIN)),
        )
    except YujError as exc:
        _die(str(exc))


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


def _load(fleet_path: Path | None) -> tuple[Fleet, dict[str, Any]]:
    """Read config, then find and load the fleet; exits with status 1 on error."""
    config = _read_config()
    path = fleet_path or _autodetect_fleet(config)
    try:
        fleet = _load_fleet(path)
    except YujError as exc:
        _die(str(exc))
    return fleet, config


def _read_config() -> dict[str, Any]:
    """Read ``yuj.yaml`` from the cwd if present; return an empty dict otherwise."""
    config_path = Path("yuj.yaml")
    if not config_path.is_file():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _autodetect_fleet(config: dict[str, Any]) -> Path:
    """Find the fleet file from config or the conventional filenames."""
    if config.get("fleet"):
        return Path(str(config["fleet"]))
    for candidate in _FLEET_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    _die("no fleet file found. Run `yuj init` or pass --fleet PATH.")


def _load_fleet(path: Path) -> Fleet:
    """Load a fleet from a path, dispatching on file extension."""
    if path.suffix.lower() in (".yaml", ".yml"):
        return load_from_yaml(path)
    return load_from_csv(path)


if __name__ == "__main__":  # pragma: no cover
    app()
