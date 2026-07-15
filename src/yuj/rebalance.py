"""Pull outputs and scatter unfinished work back across the fleet."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from yuj.fleet import Fleet
from yuj.pull import DEFAULT_PULL_TIMEOUT, pull_once
from yuj.scatter import scatter_fleet


def done_from_results(
    results_dir: str | Path, items: Iterable[str], *, output_suffix: str = ""
) -> set[str]:
    """Return work items whose output file exists in ``results_dir``."""
    root = Path(results_dir)
    if not root.is_dir():
        return set()
    suffix = output_suffix or ""
    by_output = {f"{item}{suffix}": item for item in items}
    if not by_output:
        return set()
    done: set[str] = set()
    for path in root.iterdir():
        if path.is_file() and path.name in by_output:
            done.add(by_output[path.name])
    return done


def remaining_items(items: Iterable[str], done: set[str]) -> list[str]:
    """Items not in ``done``, order-preserving and de-duplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in done or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


@dataclass(frozen=True)
class RebalanceTick:
    """Outcome of one pull -> derive-done -> scatter-remaining cycle."""

    total: int
    done: int
    remaining: int
    pulled_hosts: int
    scattered: int
    scatter_hosts: int
    scatter_errors: dict[str, str]

    @property
    def complete(self) -> bool:
        """True when nothing is left to do."""
        return self.remaining == 0


def rebalance_once(
    fleet: Fleet,
    worklist: list[str],
    *,
    remote_dir: str,
    output_dir: str | None,
    into: str,
    dest_dir: str | Path,
    output_suffix: str = "",
    header: str | None = None,
    connect_timeout: int = 20,
    pull_timeout: float = DEFAULT_PULL_TIMEOUT,
    scatter_timeout: float = 300.0,
    max_workers: int = 8,
    pull_fleet: Fleet | None = None,
) -> RebalanceTick:
    """One tick: pull results, derive done items, and scatter the rest."""
    source = f"{remote_dir}/{output_dir}" if output_dir else remote_dir
    pulls = pull_once(
        pull_fleet or fleet,
        remote_dir=source,
        dest_dir=dest_dir,
        per_host_subdir=False,
        connect_timeout=connect_timeout,
        timeout=pull_timeout,
        max_workers=max_workers,
    )
    pulled_hosts = sum(1 for r in pulls.values() if r.ok)

    done = done_from_results(dest_dir, worklist, output_suffix=output_suffix)
    remaining = remaining_items(worklist, done)
    total = len(set(worklist))

    if remaining:
        scatters = scatter_fleet(
            fleet,
            remaining,
            remote_dir=remote_dir,
            filename=into,
            header=header,
            connect_timeout=connect_timeout,
            timeout=scatter_timeout,
            max_workers=max_workers,
        )
    else:
        scatters = {}
    scattered = sum(r.count for r in scatters.values() if r.ok)
    scatter_hosts = sum(1 for r in scatters.values() if r.ok)
    errors = {n: r.error for n, r in scatters.items() if not r.ok and r.error}

    return RebalanceTick(
        total=total,
        done=len(done),
        remaining=len(remaining),
        pulled_hosts=pulled_hosts,
        scattered=scattered,
        scatter_hosts=scatter_hosts,
        scatter_errors=errors,
    )
