"""Weighted work splitting (largest-remainder) with resume and redistribution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from yuj.exceptions import SplitError

# An assignment maps each host name to the ordered list of items it owns.
Assignment = dict[str, list[str]]


@dataclass(frozen=True)
class _Quota:
    """Internal: a host's name, its integer item count, and the remainder used
    for largest-remainder tie-breaking."""

    name: str
    count: int
    remainder: float


def weighted_split(
    items: Sequence[str],
    weights: Mapping[str, float],
) -> Assignment:
    """Split ``items`` across hosts proportionally to ``weights``.

    Uses the largest-remainder (Hamilton) method: each host gets
    ``floor(weight_share * n)`` items, then the leftover items go one each to the
    hosts with the largest fractional remainders. Counts therefore sum to exactly
    ``len(items)`` with no item dropped or duplicated. Allocation is deterministic
    for a given ``(items, weights)`` and follows the iteration order of
    ``weights`` for tie-breaks.

    Args:
        items: The ordered batch to divide. Duplicates are preserved as-is.
        weights: Per-host weight. A weight of 0 drains that host (it gets nothing).

    Returns:
        An :data:`Assignment`: every host in ``weights`` appears as a key.

    Raises:
        SplitError: If there are no hosts, any weight is negative, or the total
            weight is zero while there are items to place.
    """
    if not weights:
        raise SplitError("cannot split work: no hosts given")
    if any(w < 0 for w in weights.values()):
        raise SplitError("cannot split work: negative weight")

    names = list(weights)
    assignment: Assignment = {name: [] for name in names}
    n = len(items)
    if n == 0:
        return assignment

    total = sum(weights.values())
    if total <= 0:
        raise SplitError(
            "cannot split work: total weight is zero",
            hint="give at least one host a positive weight",
        )

    quotas = _largest_remainder(names, weights, total, n)
    cursor = 0
    for quota in quotas:
        assignment[quota.name] = list(items[cursor : cursor + quota.count])
        cursor += quota.count
    return assignment


def chunked(items: Sequence[str], chunk_size: int) -> list[list[str]]:
    """Split ``items`` into consecutive chunks of at most ``chunk_size``.

    This mirrors the per-host chunking the on-target work loop uses (process a
    chunk, mark it done, move on) so the controller and worker agree on bounds.
    """
    if chunk_size <= 0:
        raise SplitError(f"chunk_size must be positive, got {chunk_size}")
    return [list(items[i : i + chunk_size]) for i in range(0, len(items), chunk_size)]


def pending_items(
    items: Sequence[str],
    is_done: Callable[[str], bool],
) -> list[str]:
    """Return the items for which ``is_done`` is False, preserving order.

    ``is_done`` is typically ``lambda item: output_path(item).exists()``: resume
    by checking for the output file, never by trusting a job log.
    """
    return [item for item in items if not is_done(item)]


def redistribute(
    current: Mapping[str, Sequence[str]],
    weights: Mapping[str, float],
    is_done: Callable[[str], bool],
    *,
    trim_only: bool,
) -> Assignment:
    """Rebalance remaining work across the fleet.

    Args:
        current: The existing per-host assignment.
        weights: Target weights for the (possibly changed) set of hosts.
        is_done: Predicate marking already-completed items, dropped everywhere.
        trim_only: If True, only remove completed items from each host's current
            list, so no item moves between hosts and no shared payload needs to be
            pushed anywhere (the light path). If False, pool every remaining item
            and re-split by ``weights`` (the heavy path: moved items imply pushing
            the recipient any shared payload they lack).

    Returns:
        A new :data:`Assignment` over the hosts named in ``weights``.

    Raises:
        SplitError: If ``trim_only`` is True but ``current`` references a host not
            present in ``weights`` (an ambiguous, lossy request).
    """
    if trim_only:
        unknown = set(current) - set(weights)
        if unknown:
            raise SplitError(
                f"trim-only cannot drop hosts {sorted(unknown)} "
                "without moving their work",
                hint="use trim_only=False to re-pool and redistribute remaining items",
            )
        return {
            name: [item for item in current.get(name, ()) if not is_done(item)]
            for name in weights
        }

    pooled: list[str] = []
    seen: set[str] = set()
    for host_items in current.values():
        for item in host_items:
            if item not in seen and not is_done(item):
                seen.add(item)
                pooled.append(item)
    return weighted_split(pooled, weights)


def _largest_remainder(
    names: Sequence[str],
    weights: Mapping[str, float],
    total: float,
    n: int,
) -> list[_Quota]:
    quotas: list[_Quota] = []
    for name in names:
        exact = weights[name] / total * n
        floor = int(exact)
        quotas.append(_Quota(name=name, count=floor, remainder=exact - floor))

    allocated = sum(q.count for q in quotas)
    leftover = n - allocated
    # Hand out leftover items to the largest remainders; ties broken by the
    # original order in ``names`` (quotas is built in names order, so index i is
    # that original position).
    order = sorted(
        range(len(quotas)),
        key=lambda i: (-quotas[i].remainder, i),
    )
    for i in order[:leftover]:
        q = quotas[i]
        quotas[i] = _Quota(name=q.name, count=q.count + 1, remainder=q.remainder)
    return quotas
