"""Rich table rendering for ``yuj status`` and ``yuj diagnose``."""

from __future__ import annotations

from collections.abc import Sequence

from rich.table import Table
from rich.text import Text

from yuj.status import DEFAULT_STALL_MIN, Diagnosis, HostStatus

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
    "idle": ("○ idle", "yellow"),
    "down": ("● down", "bold bright_black"),
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


def status_table(
    statuses: Sequence[HostStatus],
    *,
    title: str = "yuj fleet",
    stall_threshold_min: int = DEFAULT_STALL_MIN,
    total_items: int | None = None,
) -> Table:
    """Build the fleet status table from probe results."""
    if total_items is not None:
        done, pct = _progress(statuses, total_items)
        title = f"{title}: {done}/{total_items} ({pct}%)"
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
    owners = sum(1 for s in statuses if s.owner_present)
    outputs = sum(s.n_outputs or 0 for s in statuses)
    if total_items is not None:
        _, pct = _progress(statuses, total_items)
        out_str = f"{outputs}/{total_items} ({pct}%)"
    else:
        out_str = str(outputs)
    return (
        f"{up}/{n_hosts} up · {producing} producing · {stalled} stalled"
        f" · {out_str} outputs · {owners} with owner present"
    )
