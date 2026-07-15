"""Observed per-host throughput for live rebalance weighting."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from yuj.exceptions import SplitError

# Long enough for slow hosts to finish several items in normal runs.
DEFAULT_WINDOW_S = 90.0 * 60.0


def observed_rates(
    assignment: Mapping[str, Sequence[str]],
    *,
    done_at: Callable[[str], float | None],
    cost: Callable[[str], float],
    now: float,
    window_s: float = DEFAULT_WINDOW_S,
    floor: float = 1.0,
) -> dict[str, float]:
    """Measure each host's throughput in cost-units per hour over the last window.

    Args:
        assignment: Which items each host owns.
        done_at: Item -> completion timestamp (epoch seconds), or None if not done.
            Typically the mtime of the item's output artefact.
        cost: Item -> cost, in whatever unit the caller balances on (samples, bytes,
            rows). The returned rates are in that unit per hour.
        now: Current epoch seconds. Passed in rather than read, so callers can
            measure reproducibly and tests need no clock patching.
        window_s: Look back this far. Too short and hosts working a long item read
            as zero; see DEFAULT_WINDOW_S.
        floor: Minimum rate assigned to a host that completed nothing in the window.
            Keeps a host that is merely slow (or mid-item) from being scored 0 and
            starved, while still ranking it far below its peers. Must be > 0.

    Returns:
        Host -> rate in cost-units/hour. Every host in ``assignment`` appears.

    Raises:
        SplitError: If ``window_s`` or ``floor`` is not positive.
    """
    if window_s <= 0:
        raise SplitError(f"window_s must be positive, got {window_s}")
    if floor <= 0:
        raise SplitError(
            f"floor must be positive, got {floor}",
            hint="a zero floor drains any host that completed nothing in the window",
        )

    cutoff = now - window_s
    hours = window_s / 3600.0
    rates: dict[str, float] = {}
    for host, items in assignment.items():
        completed = 0.0
        for item in items:
            stamp = done_at(item)
            if stamp is not None and stamp > cutoff:
                completed += cost(item)
        rates[host] = max(completed / hours, floor)
    return rates


def makespan(
    assignment: Mapping[str, Sequence[str]],
    rates: Mapping[str, float],
    cost: Callable[[str], float],
) -> float:
    """Return the slowest host's completion time in hours."""
    worst = 0.0
    for host, items in assignment.items():
        rate = rates.get(host, 0.0)
        load = sum(cost(item) for item in items)
        if load == 0:
            continue
        if rate <= 0:
            return float("inf")
        worst = max(worst, load / rate)
    return worst
