"""The yuj CLI. Settings load from yuj.yaml in the cwd; flags override them."""

from __future__ import annotations

import getpass
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.live import Live

from yuj import __version__
from yuj._render import (
    bench_summary,
    bench_table,
    diagnosis_table,
    exec_raw,
    exec_summary,
    exec_table,
    status_table,
    storage_summary,
    storage_table,
    usable_summary,
)
from yuj.authorize import authorize_fleet
from yuj.bench import (
    DEFAULT_MAX_TIME,
    DEFAULT_URL,
    DIMENSION,
    DIMENSIONS,
    bench_fleet,
    weights_for,
)
from yuj.bootstrap import BootstrapConfig, bootstrap_fleet
from yuj.canary import DEFAULT_TIMEOUT_S
from yuj.cli_support import (
    _die,
    _do_deploy,
    _do_submit,
    _load,
    _print_op_table,
    _render_status,
    _resolve_status_opts,
    _run_html_dashboard,
    _select_hosts,
    _status_etas,
    _teardown_config,
    console,
    do_canary,
)
from yuj.config import ProjectConfig
from yuj.decommission import DecommissionResult
from yuj.decommission import decommission as _decommission
from yuj.decommission import schedule_decommission as _schedule_decommission
from yuj.exceptions import YujError
from yuj.exec_cmd import exec_fleet
from yuj.fleet import Fleet, Host, map_fleet
from yuj.keys import read_public_key
from yuj.probe import diagnose_fleet, probe_fleet
from yuj.provision import (
    DEFAULT_FLEET_OUT,
    DEFAULT_KEY_DIR,
    ProvisionConfig,
    generate_keypair,
    provision_fleet,
)
from yuj.pull import pull_once
from yuj.rebalance import rebalance_once
from yuj.rescue import (
    DEFAULT_ATTEMPTS,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_INTERVAL,
    rescue_fleet,
)
from yuj.scaffolds import scaffold_files
from yuj.scatter import read_items, scatter_fleet
from yuj.storage import storage_fleet
from yuj.supervise import stop as supervise_stop
from yuj.transport import make_transport

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
_NoCanaryOpt = Annotated[
    bool, typer.Option("--no-canary", help="Skip the pre-submit single-host dry run.")
]
_CanaryTimeoutOpt = Annotated[
    int, typer.Option("--canary-timeout", help="Canary dry-run timeout (s).")
]
_TotalOpt = Annotated[
    int | None,
    typer.Option(
        "--total", help="Override the item total (else from scatter.input/input_file)."
    ),
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
        typer.Option(
            "--template", "-t", help="Project template: bare | python | r | r-python."
        ),
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
            if template in ("python", "r", "r-python")
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


_SortOpt = Annotated[
    str,
    typer.Option(
        "--sort",
        "-s",
        help=f"Rank hosts by throughput dimension: {'/'.join(DIMENSIONS)}.",
    ),
]


@fleet_app.command("bench")
def fleet_bench(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    sort_by: _SortOpt = "cores",
    no_download: Annotated[
        bool,
        typer.Option("--no-download", help="Skip the download test (hardware only)."),
    ] = False,
    url: Annotated[
        str | None,
        typer.Option("--url", help="URL to time the download against (else NCBI FTP)."),
    ] = None,
    max_time: Annotated[
        int, typer.Option("--max-time", help="Cap the download test at N seconds.")
    ] = DEFAULT_MAX_TIME,
    timeout: Annotated[float, typer.Option(help="Per-host op timeout (s).")] = 60.0,
) -> None:
    """Benchmark and rank the fleet by throughput: cores, RAM, GPU, disk, or download.

    Sorts hosts (best first) along ``--sort`` with a bar per host. The download
    test is included by default (skip with ``--no-download``); ``--sort download``
    always runs it. The same numbers drive ``yuj scatter --by <dim>``.
    """
    if sort_by not in DIMENSIONS:
        _die(f"--sort must be one of {', '.join(DIMENSIONS)} (got {sort_by!r}).")
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    download = (not no_download) or DIMENSION[sort_by].needs_download
    benches = bench_fleet(
        fleet,
        work_dir=config.remote_dir,
        url=url or config.scatter.get("bench_url") or DEFAULT_URL,
        max_time=max_time,
        download=download,
        timeout=timeout,
    )
    console.print(bench_table(benches, sort_by=sort_by))
    console.print(bench_summary(benches))


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
    html: Annotated[
        Path | None,
        typer.Option("--html", help="Write a self-contained HTML dashboard to PATH."),
    ] = None,
    open_browser: Annotated[
        bool, typer.Option("--open", help="Open the HTML dashboard in a browser.")
    ] = False,
    timeout: Annotated[float, typer.Option(help="Per-host probe timeout (s).")] = 20.0,
) -> None:
    """Show fleet-wide status: producing, idle, stalled, down, or owner-occupied."""
    if html is not None:
        _run_html_dashboard(
            fleet_path,
            results_glob,
            stall_min=stall_min,
            timeout=timeout,
            total_items=total,
            html_path=html,
            refresh=watch,
            open_browser=open_browser,
        )
        return
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
                statuses = probe_fleet(
                    fleet, results_glob=glob, job=config.job, timeout=timeout
                )
                eta, host_eta = _status_etas(config.job, fleet, statuses, total_items)
                live.update(
                    status_table(
                        statuses,
                        stall_threshold_min=threshold,
                        total_items=total_items,
                        eta=eta,
                        host_eta=host_eta,
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
    no_canary: _NoCanaryOpt = False,
    canary_timeout: _CanaryTimeoutOpt = DEFAULT_TIMEOUT_S,
    timeout: Annotated[float, typer.Option(help="Per-host op timeout (s).")] = 120.0,
) -> None:
    """Install the self-healing watchdog + cron on every host.

    Before installing fleet-wide, a canary runs the work command once on a
    single host; if it fails fast, submit aborts (skip with ``--no-canary``).
    """
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    _do_submit(
        fleet,
        config,
        start=not no_start,
        timeout=timeout,
        canary=not no_canary,
        canary_timeout=canary_timeout,
    )


@app.command()
def canary(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    canary_timeout: _CanaryTimeoutOpt = DEFAULT_TIMEOUT_S,
) -> None:
    """Dry-run the work command once on one host, without installing anything.

    Runs on the first reachable host (narrow it with ``--hosts``); needs the code
    already deployed (``yuj deploy``). Exits non-zero if the command fails fast.
    """
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    do_canary(fleet, config, timeout_s=canary_timeout)


def _smart_reweight(
    fleet: Fleet, config: ProjectConfig, dim: str, *, timeout: float
) -> Fleet:
    """Benchmark ``fleet`` and return a sub-fleet weighted by live ``dim`` capacity.

    Hosts that are unreachable or don't report the metric are dropped from the
    scatter entirely (not just zero-weighted), so no dead host gets an empty
    slice pushed to it. Dies if nothing is left to scatter to.
    """
    if dim not in DIMENSIONS:
        _die(f"--by must be one of {', '.join(DIMENSIONS)} (got {dim!r}).")
    benches = bench_fleet(
        fleet,
        work_dir=config.remote_dir,
        url=config.scatter.get("bench_url") or DEFAULT_URL,
        max_time=DEFAULT_MAX_TIME,
        download=DIMENSION[dim].needs_download,
        timeout=timeout,
    )
    weights, dropped = weights_for(benches, dim)
    targets = {name: w for name, w in weights.items() if w > 0}
    if not targets:
        _die(f"no host reported usable {dim!r} capacity; nothing to scatter to.")
    if dropped:
        names = ", ".join(sorted(dropped))
        console.print(f"[yellow]dropped (no {dim}): {names}[/yellow]")
    shares = ", ".join(f"{n}={w:g}" for n, w in sorted(targets.items()))
    console.print(f"weighting split by [bold]{dim}[/bold]: {shares}")
    return fleet.select(list(targets)).with_weights(targets)


@app.command()
def scatter(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    input_path: Annotated[
        str | None,
        typer.Option("--input", help="Work list to split (else scatter.input)."),
    ] = None,
    into: Annotated[
        str | None,
        typer.Option("--into", help="Per-host filename to write (else scatter.into)."),
    ] = None,
    exclude: Annotated[
        str | None,
        typer.Option(
            "--exclude", help="File of items to drop before split (e.g. done)."
        ),
    ] = None,
    by: Annotated[
        str | None,
        typer.Option(
            "--by",
            help=f"Weight the split by live capacity: {'/'.join(DIMENSIONS)} "
            "(default: static fleet weights).",
        ),
    ] = None,
    timeout: Annotated[float, typer.Option(help="Per-host op timeout (s).")] = 300.0,
) -> None:
    """Weighted-split a work list and write each host only its own slice.

    The slice lands at ``remote_dir/<into>`` (the file your work loop reads).
    Item counts follow each host's ``weight`` by default; with ``--by cores|mem|
    gpu|disk|download`` they follow live measured capacity instead (the machine
    that can take more load gets more items). Re-run any time to re-split.
    """
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    if by is not None:
        fleet = _smart_reweight(fleet, config, by, timeout=timeout)
    scfg = config.scatter
    src = input_path or scfg.get("input")
    dest = into or scfg.get("into") or config.input_file
    if not src:
        _die("scatter needs an input list (--input or yuj.yaml scatter.input).")
    if not dest:
        _die("scatter needs a target filename (--into or yuj.yaml scatter.into).")
    header = scfg.get("header")
    try:
        items = read_items(src)
    except OSError as exc:
        _die(f"could not read scatter input {src!r}: {exc}")
    drop = set(read_items(exclude)) if exclude else None
    results = scatter_fleet(
        fleet,
        items,
        remote_dir=config.remote_dir,
        filename=str(dest),
        header=str(header) if header else None,
        exclude=drop,
        timeout=timeout,
    )
    total = sum(r.count for r in results.values() if r.ok)
    console.print(
        f"scattered [bold]{total:,}[/bold] of {len(items):,} items "
        f"across {sum(1 for r in results.values() if r.ok)} host(s)"
    )
    _print_op_table(
        "scatter",
        [
            (name, r.ok, r.error or f"{r.count:,} items -> {dest}")
            for name, r in sorted(results.items())
        ],
    )


@app.command()
def authorize(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    key: Annotated[
        str | None,
        typer.Option("--key", help="Public key to install (else authorize.key)."),
    ] = None,
    generate: Annotated[
        str | None,
        typer.Option(
            "--generate",
            help="Create a passphraseless keypair at this path first, then install it.",
        ),
    ] = None,
    timeout: Annotated[float, typer.Option(help="Per-host op timeout (s).")] = 30.0,
) -> None:
    """Install an SSH public key on every host so future logins are key-based.

    Uses each host's *current* auth (password or an existing key/agent) once to
    append the key to its ``~/.ssh/authorized_keys``. Idempotent. After this,
    point ``key_path`` at the private key in ``fleet.csv`` and drop passwords.
    """
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    acfg = config.authorize
    try:
        if generate:
            pubkey = generate_keypair(generate, comment="yuj-fleet")
            console.print(f"generated keypair: [bold]{generate}[/bold] (+ .pub)")
        else:
            key_path = key or acfg.get("key")
            if not key_path:
                _die("authorize needs a key (--key, --generate, or authorize.key).")
            pubkey = read_public_key(str(key_path))
    except YujError as exc:
        _die(str(exc))
    results = authorize_fleet(fleet, pubkey, timeout=timeout)
    _print_op_table(
        "authorize",
        [
            (
                name,
                r.ok,
                r.error or ("already authorized" if r.already else "key installed"),
            )
            for name, r in sorted(results.items())
        ],
    )


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
    from_tarball: Annotated[
        str | None,
        typer.Option(
            "--from-tarball",
            help="Use a pre-staged installer/env tarball on each host instead of curl.",
        ),
    ] = None,
    check: Annotated[
        bool, typer.Option("--check", help="Dry-run: report, install nothing.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Re-run even if bootstrapped (re-installs deps; never deletes).",
        ),
    ] = False,
    max_workers: Annotated[
        int, typer.Option("--max-workers", help="Per-fleet concurrency cap.")
    ] = 4,
    timeout: Annotated[float, typer.Option(help="Per-host timeout (s).")] = 1800.0,
) -> None:
    """Install an env manager (+ extras) on every host, from a bare login shell."""
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    bcfg = config.bootstrap
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
            remote_dir=config.remote_dir,
            from_tarball=from_tarball or bcfg.get("from_tarball"),
            check=check,
            force=force,
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
def usable(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    names_only: Annotated[
        bool,
        typer.Option(
            "--names-only", help="Print just usable host names, one per line."
        ),
    ] = False,
    timeout: Annotated[float, typer.Option(help="Per-host probe timeout (s).")] = 20.0,
) -> None:
    """List hosts you can send work to now: reachable, not do_not_use, no owner."""
    fleet, config = _load(fleet_path)
    # Probe do_not_use hosts too, so the summary can report them as held back;
    # the .usable property still keeps them out of the ready list.
    fleet = _select_hosts(fleet, hosts, allow_do_not_use=True)
    statuses = probe_fleet(
        fleet, results_glob=config.results_glob, job=config.job, timeout=timeout
    )
    ready = [s for s in statuses if s.usable]
    if names_only:
        for s in ready:
            console.print(s.name)
        return
    if ready:
        console.print(status_table(ready, title="yuj usable"))
    console.print(usable_summary(statuses))


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
def storage(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    work_dir: Annotated[
        str | None,
        typer.Option(
            "--work-dir",
            help="Path whose free space to report (else remote_dir). Use ~ if the "
            "deploy dir doesn't exist yet.",
        ),
    ] = None,
    timeout: Annotated[float, typer.Option(help="Per-host probe timeout (s).")] = 20.0,
) -> None:
    """Show free space where the job writes (★), plus every partition on each host."""
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    target = work_dir or config.remote_dir
    stores = storage_fleet(fleet, work_dir=target, timeout=timeout)
    console.print(storage_table(stores))
    console.print(storage_summary(stores))


@app.command("exec")
def exec_cmd(
    command: Annotated[
        str, typer.Argument(help="Shell command to run on every host (quote it).")
    ],
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    include_down: Annotated[
        bool,
        typer.Option("--include-down", help="Also target hosts flagged do_not_use."),
    ] = False,
    timeout: Annotated[
        float, typer.Option(help="Per-host command timeout (s).")
    ] = 60.0,
    raw: Annotated[
        bool,
        typer.Option(
            "--raw", help="Print each host's full stdout/stderr, not a table."
        ),
    ] = False,
) -> None:
    """Run a shell command on every host in parallel.

    Runs in each host's default shell (quote the command). Targets usable hosts
    by default; ``--include-down`` also includes hosts flagged do_not_use.
    """
    fleet = _load(fleet_path)[0]
    selected = _select_hosts(fleet, hosts, allow_do_not_use=include_down)
    results = exec_fleet(selected, command, timeout=timeout)
    console.print(exec_raw(results) if raw else exec_table(results))
    console.print(exec_summary(results))


@app.command()
def rescue(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    attempts: Annotated[
        int, typer.Option(help="Max connection attempts per host.")
    ] = DEFAULT_ATTEMPTS,
    interval: Annotated[
        float, typer.Option(help="Seconds to wait between attempts.")
    ] = DEFAULT_INTERVAL,
    connect_timeout: Annotated[
        int, typer.Option(help="Per-attempt SSH connect timeout (s).")
    ] = DEFAULT_CONNECT_TIMEOUT,
    keep_cron: Annotated[
        bool,
        typer.Option(
            "--keep-cron", help="Don't strip the relaunch cron while rescuing."
        ),
    ] = False,
    pattern: Annotated[
        str | None,
        typer.Option(
            "--pattern",
            help="Comma-separated process names to kill "
            "(default: all the SSH user's processes).",
        ),
    ] = None,
) -> None:
    """Revive OOM-melted hosts: retry transient sshd windows, kill the orphans."""
    fleet = _load(fleet_path)[0]
    pats = tuple(p.strip() for p in pattern.split(",") if p.strip()) if pattern else ()
    skipped: list[tuple[str, bool, str]] = []
    if hosts and hosts.strip().lower() != "all":
        targets = _select_hosts(fleet, hosts, allow_do_not_use=True)
    else:
        diagnoses = diagnose_fleet(fleet)
        skipped = [
            (d.name, True, "healthy, skipped") for d in diagnoses if d.status == "ok"
        ]
        bad = [d.name for d in diagnoses if d.status != "ok"]
        if not bad:
            console.print("[green]all hosts reachable — nothing to rescue[/green]")
            return
        targets = fleet.select(bad)
    results = rescue_fleet(
        targets,
        attempts=attempts,
        interval=interval,
        connect_timeout=connect_timeout,
        strip_cron=not keep_cron,
        pattern=pats,
    )
    rows = skipped + [
        (
            r.host,
            r.rescued,
            f"load {r.load_before}→{r.load_after} in {r.attempts} attempt(s)"
            if r.rescued
            else (r.error or "failed"),
        )
        for r in results
    ]
    _print_op_table("rescue", sorted(rows))


@app.command()
def decommission(
    host: Annotated[
        str | None,
        typer.Argument(help="Host name, or 'all' for every usable host."),
    ] = None,
    fleet_path: _FleetOpt = None,
    all_hosts: Annotated[
        bool,
        typer.Option("--all", help="Decommission every usable host in the fleet."),
    ] = False,
    at: Annotated[
        str | None,
        typer.Option("--at", help='Schedule (e.g. "+90 seconds", "9am tomorrow").'),
    ] = None,
    remove_dir: Annotated[
        bool, typer.Option("--remove-dir", help="Also delete the deploy directory.")
    ] = False,
    timeout: Annotated[float, typer.Option(help="Per-host op timeout (s).")] = 60.0,
) -> None:
    """Politely tear down the yuj job on HOST, or every usable host with --all."""
    fleet, config = _load(fleet_path)
    if host is not None and host.strip().lower() == "all":
        all_hosts, host = True, None
    if all_hosts and host is not None:
        _die("pass either a HOST or --all, not both")
    if all_hosts:
        targets = fleet.usable
    elif host is None:
        _die("pass a HOST (or --all / 'all' for the whole fleet)")
    else:
        try:
            targets = Fleet((fleet.get(host),))
        except YujError as exc:
            _die(str(exc))
    cfg = _teardown_config(config)

    def teardown(h: Host) -> DecommissionResult:
        transport = make_transport(h)
        if at is not None:
            return _schedule_decommission(
                transport, cfg, at, remove_dir=remove_dir, timeout=timeout
            )
        return _decommission(transport, cfg, remove_dir=remove_dir, timeout=timeout)

    results = map_fleet(targets, teardown, max_workers=8)
    failed = 0
    for name in targets.names:
        result = results[name]
        if result.scheduled:
            console.print(f"[green]scheduled[/green] decommission of {name} at {at}")
        elif result.ok:
            console.print(
                f"[green]decommissioned[/green] {name}: cron removed, "
                f"{result.processes_remaining} processes remaining"
            )
        else:
            failed += 1
            console.print(f"[red]failed[/red] {name}: {result.error}")
    if failed:
        raise typer.Exit(1)


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
    output_dir = config.output_dir
    source = f"{config.remote_dir}/{output_dir}" if output_dir else config.remote_dir
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


def _stop_fleet(
    fleet: Fleet, config: ProjectConfig, *, timeout: float = 30.0
) -> dict[str, str]:
    """Set the stop sentinel on each host; return per-host failures."""
    cfg = _teardown_config(config)

    def _one(host: Host) -> str | None:
        try:
            supervise_stop(make_transport(host), cfg, timeout=timeout)
        except YujError as exc:
            return str(exc)
        return None

    results = map_fleet(fleet, _one, max_workers=8)
    return {name: error for name, error in results.items() if error}


@app.command()
def rebalance(
    fleet_path: _FleetOpt = None,
    hosts: _HostsOpt = None,
    input_path: Annotated[
        str | None,
        typer.Option("--input", help="Work list (else scatter.input/input_file)."),
    ] = None,
    into: Annotated[
        str | None,
        typer.Option("--into", help="Per-host slice filename (else scatter.into)."),
    ] = None,
    dest: Annotated[
        str,
        typer.Option("--dest", "-d", help="Local results dir to read done-set from."),
    ] = "results",
    by: Annotated[
        str | None,
        typer.Option(
            "--by",
            help=f"Weight the re-split by live capacity: {'/'.join(DIMENSIONS)} "
            "(default: static fleet weights).",
        ),
    ] = None,
    watch: Annotated[
        float | None,
        typer.Option(
            "--watch", help="Loop every N seconds until done (default: one tick)."
        ),
    ] = None,
    restart: Annotated[
        bool,
        typer.Option(
            "--restart",
            help="Re-submit each tick so hosts read the new slice now. Kicks "
            "in-progress downloads; use only when the remaining tail is cheap.",
        ),
    ] = False,
    max_ticks: Annotated[
        int,
        typer.Option("--max-ticks", help="Safety cap on --watch loop iterations."),
    ] = 200,
    timeout: Annotated[float, typer.Option(help="Per-host op timeout (s).")] = 300.0,
) -> None:
    """Pull results, re-scatter unfinished items, and optionally loop."""
    fleet, config = _load(fleet_path)
    fleet = _select_hosts(fleet, hosts)
    if watch is not None and watch <= 0:
        _die("--watch must be positive.")
    if max_ticks < 1:
        _die("--max-ticks must be positive.")
    src = input_path or config.input_source
    dest_name = into or config.scatter.get("into") or config.input_file
    if not src:
        _die("rebalance needs a work list (--input or yuj.yaml scatter.input).")
    if not dest_name:
        _die("rebalance needs a target filename (--into or yuj.yaml scatter.into).")
    if not config.output_dir:
        _die("rebalance needs output_dir in yuj.yaml to locate results.")
    try:
        worklist = read_items(src)
    except OSError as exc:
        _die(f"could not read work list {src!r}: {exc}")
    header = config.scatter.get("header")

    tick_no = 0
    while True:
        tick_no += 1
        target = _smart_reweight(fleet, config, by, timeout=timeout) if by else fleet
        tick = rebalance_once(
            target,
            worklist,
            remote_dir=config.remote_dir,
            output_dir=config.output_dir,
            into=str(dest_name),
            dest_dir=dest,
            output_suffix=config.output_suffix,
            header=str(header) if header else None,
            scatter_timeout=timeout,
            pull_fleet=fleet,
        )
        console.print(
            f"[bold]rebalance tick {tick_no}[/bold]: "
            f"done [green]{tick.done:,}[/green]/{tick.total:,}  "
            f"remaining [bold]{tick.remaining:,}[/bold]  "
            f"(pulled {tick.pulled_hosts} host(s), "
            f"scattered {tick.scattered:,} across {tick.scatter_hosts})"
        )
        if tick.scatter_errors:
            joined = ", ".join(
                f"{n}: {e}" for n, e in sorted(tick.scatter_errors.items())
            )
            console.print(f"[yellow]scatter errors: {joined}[/yellow]")
        if tick.complete:
            stop_errors = _stop_fleet(fleet, config)
            if stop_errors:
                joined = ", ".join(f"{n}: {e}" for n, e in sorted(stop_errors.items()))
                console.print(f"[yellow]stop errors: {joined}[/yellow]")
            else:
                console.print(
                    "[green]all items done; stop sentinel set fleet-wide[/green]"
                )
            break
        if restart:
            _do_submit(target, config, start=True, timeout=timeout, canary=False)
        if not watch:
            break
        if tick_no >= max_ticks:
            console.print(
                f"[yellow]hit --max-ticks {max_ticks}; "
                f"{tick.remaining:,} still remaining[/yellow]"
            )
            break
        time.sleep(watch)


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
    no_canary: _NoCanaryOpt = False,
    canary_timeout: _CanaryTimeoutOpt = DEFAULT_TIMEOUT_S,
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
    _do_submit(
        fleet,
        config,
        start=not no_start,
        timeout=timeout,
        canary=not no_canary,
        canary_timeout=canary_timeout,
    )


if __name__ == "__main__":  # pragma: no cover
    app()
