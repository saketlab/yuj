"""Split a batch across the fleet by weight and place each host's slice."""

from __future__ import annotations

import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.split import Assignment, weighted_split
from yuj.transport import Transport, make_transport


@dataclass(frozen=True)
class ScatterResult:
    """Outcome of placing one host's slice."""

    host: str
    count: int
    ok: bool
    error: str | None = None


def read_items(path: str | Path) -> list[str]:
    """Read a work list: first whitespace/comma field per non-blank line.

    A leading ``accession``/``item``/``id`` header line is skipped. Blank lines
    and ``#`` comments are ignored. Order is preserved.
    """
    text = Path(path).read_text(encoding="utf-8")
    items: list[str] = []
    for lineno, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        field = line.split(",")[0].split()[0].strip()
        if lineno == 0 and field.lower() in {"accession", "item", "id", "items"}:
            continue
        items.append(field)
    return items


def plan_scatter(
    fleet: Fleet, items: list[str], *, exclude: set[str] | None = None
) -> Assignment:
    """Weighted-split ``items`` (minus ``exclude``) across ``fleet.usable``.

    Returns an :class:`~yuj.split.Assignment` mapping every usable host to its
    ordered slice. Excluded items (e.g. already-done) are dropped before the
    split so capacity is not wasted re-placing them.
    """
    usable = fleet.usable
    if not usable:
        raise YujError("no usable hosts to scatter to (all do_not_use?)")
    pool = items if not exclude else [i for i in items if i not in exclude]
    weights = {h.name: h.weight for h in usable}
    return weighted_split(pool, weights)


def scatter_host(
    transport: Transport,
    items: list[str],
    *,
    remote_dir: str,
    filename: str,
    header: str | None = None,
    timeout: float = 300.0,
) -> ScatterResult:
    """Write ``items`` to ``remote_dir/filename`` on one host (atomic-ish)."""
    host = transport.host.name
    body = ""
    if header:
        body += header + "\n"
    body += "".join(f"{item}\n" for item in items)
    rel = Path(filename)
    remote = remote_dir.rstrip("/")
    subdir = rel.parent.as_posix()
    remote_target = remote if subdir == "." else f"{remote}/{subdir}"
    try:
        mk = transport.run(f"mkdir -p {shlex.quote(remote_target)}", timeout=timeout)
        if not mk.ok:
            return ScatterResult(host, 0, False, f"mkdir failed: {mk.stderr.strip()}")
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / rel.name
            local.write_text(body, encoding="utf-8")
            put = transport.put(str(local), remote_target + "/", timeout=timeout)
        if not put.ok:
            return ScatterResult(host, 0, False, put.stderr.strip())
    except YujError as exc:
        return ScatterResult(host, 0, False, str(exc))
    return ScatterResult(host, len(items), True)


def scatter_fleet(
    fleet: Fleet,
    items: list[str],
    *,
    remote_dir: str,
    filename: str,
    header: str | None = None,
    exclude: set[str] | None = None,
    connect_timeout: int = 20,
    timeout: float = 300.0,
    max_workers: int = 8,
) -> dict[str, ScatterResult]:
    """Split ``items`` by weight and write each host its slice, in parallel."""
    assignment = plan_scatter(fleet, items, exclude=exclude)
    usable = fleet.usable

    def _one(host: Host) -> ScatterResult:
        return scatter_host(
            make_transport(host, connect_timeout=connect_timeout),
            assignment.get(host.name, []),
            remote_dir=remote_dir,
            filename=filename,
            header=header,
            timeout=timeout,
        )

    return map_fleet(usable, _one, max_workers=max_workers)
