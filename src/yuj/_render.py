"""Rich table rendering for ``yuj status`` and ``yuj diagnose``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from rich.table import Table
from rich.text import Text

from yuj.exec_cmd import ExecResult
from yuj.status import DEFAULT_STALL_MIN, Diagnosis, HostStatus
from yuj.storage import HostStorage

_DIAGNOSIS_STYLE = {
    "ok": ("✓ ok", "bold green"),
    "auth_refused": ("auth refused", "bold red"),
    "banner_fail": ("banner fail (fail2ban?)", "magenta"),
    "sshd_down": ("sshd down", "yellow"),
    "net_down": ("net down", "bold bright_black"),
    "unknown": ("unknown", "dim"),
}

_STATE_STYLE = {
    "producing": ("● producing", "bold green"),
    "stalled": ("● stalled", "bold red"),
    "dead": ("✖ dead", "bold red"),
    "idle": ("○ idle", "yellow"),
    "down": ("● down", "bold bright_black"),
    "excluded": ("⊘ excluded", "dim"),
}


def _state_text(status: HostStatus, stall_threshold_min: int) -> Text:
    """Colour-coded health verdict."""
    label, style = _STATE_STYLE[status.state(stall_threshold_min)]
    return Text(label, style=style)


def _load_text(status: HostStatus) -> Text:
    """Colour load relative to core count: green idle-ish, red oversubscribed."""
    if status.load1 is None:
        return Text("-", style="dim")
    ratio = status.load1 / status.nproc if status.nproc else status.load1
    style = "green" if ratio < 0.7 else "yellow" if ratio < 1.2 else "red"
    return Text(f"{status.load1:.2f}", style=style)


def _fmt_age(minutes: int | None) -> Text:
    """Human-friendly age of the newest output (m / h / d)."""
    if minutes is None:
        return Text("-", style="dim")
    if minutes < 90:
        return Text(f"{minutes}m")
    if minutes < 60 * 36:
        return Text(f"{minutes // 60}h", style="yellow")
    return Text(f"{minutes // 1440}d", style="red")


def _cpu_text(status: HostStatus) -> str:
    cores = f"{status.nproc}c" if status.nproc is not None else "?"
    if status.cpu_model:
        return f"{cores} · {status.cpu_model}"
    return cores


def _progress(statuses: Sequence[HostStatus], total_items: int) -> tuple[int, int]:
    """Return ``(done, pct)`` from output counts against ``total_items``."""
    done = sum(s.n_outputs or 0 for s in statuses)
    pct = int(done / total_items * 100) if total_items else 0
    return done, pct


_BLOCKS = " ▏▎▍▌▋▊▉"


def _bar(fraction: float, width: int = 22) -> str:
    """tqdm-style progress bar, e.g. ``████▌      `` for ~20%."""
    fraction = max(0.0, min(1.0, fraction))
    full, rem = divmod(round(fraction * width * 8), 8)
    bar = "█" * full
    if full < width:
        bar += _BLOCKS[rem]
    return bar.ljust(width)


def status_table(
    statuses: Sequence[HostStatus],
    *,
    title: str = "yuj fleet",
    stall_threshold_min: int = DEFAULT_STALL_MIN,
    total_items: int | None = None,
    eta: str | None = None,
    host_eta: Mapping[str, str] | None = None,
) -> Table:
    """Build the fleet status table from probe results."""
    if total_items is not None:
        done, pct = _progress(statuses, total_items)
        frac = done / total_items if total_items else 0.0
        title = f"{title}  {pct:3d}%|{_bar(frac)}| {done}/{total_items}"
        if eta:
            title += f" · ETA {eta}"
    table = Table(title=title, header_style="bold cyan", expand=False)
    table.add_column("host", style="bold")
    table.add_column("ip", style="dim")
    table.add_column("state")
    table.add_column("cpu")
    table.add_column("gpu")
    table.add_column("mem", justify="right")
    table.add_column("load", justify="right")
    table.add_column("outputs", justify="right")
    table.add_column("age", justify="right")
    table.add_column("eta", justify="right")
    table.add_column("owner", justify="center")

    for status in statuses:
        owner = (
            Text("⚠ " + status.console_user, style="bold magenta")
            if status.owner_present and status.console_user
            else Text("-", style="dim")
        )
        table.add_row(
            status.name,
            status.ip,
            _state_text(status, stall_threshold_min),
            _cpu_text(status),
            status.gpu or Text("-", style="dim"),
            f"{status.mem_gb}G" if status.mem_gb is not None else "-",
            _load_text(status),
            "-" if status.n_outputs is None else str(status.n_outputs),
            _fmt_age(status.newest_age_min),
            (host_eta or {}).get(status.name) or Text("-", style="dim"),
            owner,
        )
    return table


def diagnosis_table(
    diagnoses: Sequence[Diagnosis], *, title: str = "yuj diagnose"
) -> Table:
    """Build a fail2ban-aware diagnosis table from classification results."""
    table = Table(title=title, header_style="bold cyan")
    table.add_column("host", style="bold")
    table.add_column("ip", style="dim")
    table.add_column("status")
    table.add_column("detail", style="dim")
    for d in diagnoses:
        label, style = _DIAGNOSIS_STYLE.get(d.status, (d.status, "dim"))
        table.add_row(d.name, d.ip, Text(label, style=style), d.detail)
    return table


def _fmt_kb(kb: int | None) -> str:
    """Human-friendly size from KiB blocks (K/M/G/T)."""
    if kb is None:
        return "-"
    size = float(kb)
    for unit in ("K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return (
                f"{size:.0f}{unit}"
                if size >= 10 or unit == "K"
                else f"{size:.1f}{unit}"
            )
        size /= 1024
    return f"{size:.0f}T"


def _work_row(store: HostStorage) -> Text:
    """Header cell: free space at the work dir, green if known, yellow if not."""
    if store.work_avail_kb is not None:
        return Text(f"{_fmt_kb(store.work_avail_kb)} free at {store.work_dir}", "green")
    return Text(f"? free at {store.work_dir or '?'} (path missing?)", "yellow")


def storage_summary(stores: Sequence[HostStorage]) -> str:
    """Total free space at the work dirs, and which hosts were left out of it."""
    total = sum(s.work_avail_kb or 0 for s in stores if s.work_avail_kb is not None)
    counted = [s for s in stores if s.work_avail_kb is not None]
    unknown = [s.name for s in stores if s.reachable and s.work_avail_kb is None]
    down = [s.name for s in stores if not s.reachable]
    line = f"{_fmt_kb(total)} free across {len(counted)} host(s)"
    if unknown:
        line += f"; work dir missing on {', '.join(unknown)}"
    if down:
        line += f"; unreachable: {', '.join(down)}"
    return line


def storage_table(
    stores: Sequence[HostStorage], *, title: str = "yuj storage"
) -> Table:
    """One row per (host, partition); ★ marks the partition the job writes into."""
    table = Table(title=title, header_style="bold cyan")
    table.add_column("host", style="bold")
    table.add_column("filesystem", style="dim")
    table.add_column("mount")
    table.add_column("size", justify="right")
    table.add_column("avail", justify="right")
    table.add_column("use%", justify="right")
    table.add_column("work", justify="center")
    for store in stores:
        if not store.reachable:
            detail = (store.error or "unreachable").splitlines()[0][:60]
            table.add_row(store.name, Text(detail, style="red"), "", "", "", "", "")
            continue
        table.add_row(
            Text(store.name, style="bold"), _work_row(store), "", "", "", "", ""
        )
        for part in store.partitions:
            is_work = store.is_work_partition(part)
            style = "green" if is_work else "dim"
            table.add_row(
                "",
                part.filesystem,
                part.mount,
                _fmt_kb(part.size_kb),
                Text(_fmt_kb(part.avail_kb), style=style),
                f"{part.use_pct}%",
                Text("★", style="bold green") if is_work else "",
            )
    return table


def usable_summary(statuses: Sequence[HostStatus]) -> str:
    """One line: how many hosts are usable now, and why the rest are held back."""
    usable = [s.name for s in statuses if s.usable]
    excluded = [s.name for s in statuses if s.excluded]
    owned = [s.name for s in statuses if s.owner_present and not s.excluded]
    down = [s.name for s in statuses if not s.reachable and not s.excluded]
    line = f"{len(usable)} of {len(statuses)} host(s) usable now"
    if down:
        line += f"; down: {', '.join(down)}"
    if owned:
        line += f"; owner present: {', '.join(owned)}"
    if excluded:
        line += f"; do_not_use: {', '.join(excluded)}"
    return line


def summary_line(
    statuses: Sequence[HostStatus],
    *,
    stall_threshold_min: int = DEFAULT_STALL_MIN,
    total_items: int | None = None,
) -> str:
    """A compact one-line summary for logs and non-TTY output."""
    n_hosts = len(statuses)
    up = sum(1 for s in statuses if s.reachable)
    producing = sum(1 for s in statuses if s.state(stall_threshold_min) == "producing")
    stalled = sum(1 for s in statuses if s.state(stall_threshold_min) == "stalled")
    dead = sum(1 for s in statuses if s.state(stall_threshold_min) == "dead")
    owners = sum(1 for s in statuses if s.owner_present)
    outputs = sum(s.n_outputs or 0 for s in statuses)
    if total_items is not None:
        _, pct = _progress(statuses, total_items)
        out_str = f"{outputs}/{total_items} ({pct}%)"
    else:
        out_str = str(outputs)
    dead_str = f" · {dead} dead" if dead else ""
    return (
        f"{up}/{n_hosts} up · {producing} producing · {stalled} stalled{dead_str}"
        f" · {out_str} outputs · {owners} with owner present"
    )


def _exec_preview(result: ExecResult, width: int = 90) -> str:
    """One-line preview: last non-empty stdout line, else stderr, else the error."""
    if result.error:
        return result.error
    for stream in (result.stdout, result.stderr):
        lines = [ln for ln in stream.splitlines() if ln.strip()]
        if lines:
            text = lines[-1].strip()
            return text if len(text) <= width else text[: width - 1] + "…"
    return ""


def exec_table(results: Sequence[ExecResult], *, title: str = "yuj exec") -> Table:
    """One row per host: name, exit code, one-line output preview."""
    table = Table(title=title, header_style="bold cyan")
    table.add_column("host", style="bold")
    table.add_column("exit", justify="right")
    table.add_column("output")
    for r in results:
        if not r.reachable:
            code, style = "unreachable", "red"
        elif r.ok:
            code, style = "0", "green"
        else:
            code, style = str(r.returncode), "yellow"
        table.add_row(r.name, f"[{style}]{code}[/{style}]", _exec_preview(r))
    return table


def exec_raw(results: Sequence[ExecResult]) -> str:
    """Per-host block with the full stdout/stderr and exit code."""
    blocks: list[str] = []
    for r in results:
        lines = [f"[bold]===== {r.name} ({r.ip}) =====[/bold]"]
        if r.error:
            lines.append(f"[red]{r.error}[/red]")
        else:
            if r.stdout.strip():
                lines.append(r.stdout.rstrip())
            if r.stderr.strip():
                lines.append(f"[yellow]{r.stderr.rstrip()}[/yellow]")
            lines.append(f"[dim]exit {r.returncode}[/dim]")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def exec_summary(results: Sequence[ExecResult]) -> str:
    """One-line tally: ok / nonzero-exit / unreachable."""
    ok = sum(1 for r in results if r.ok)
    down = sum(1 for r in results if not r.reachable)
    failed = len(results) - ok - down
    return f"{ok} ok · {failed} nonzero · {down} unreachable  (of {len(results)})"
