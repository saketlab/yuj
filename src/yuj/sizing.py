"""Decide how many workers a host can take, from its free RAM, cores and VRAM."""

from __future__ import annotations

from dataclasses import dataclass

from yuj.exceptions import YujError
from yuj.status import Gpu

# available RAM counts reclaimable cache and peak sits above steady state
RAM_SAFETY = 0.8


def courteous_gpus(
    gpus: tuple[Gpu, ...] | list[Gpu], me: str, own_marker: str = ""
) -> tuple[int, ...]:
    """Indices of the cards nobody else is computing on.

    Mirrored by ``gpu_courtesy_list`` in ``templates/watchdog.sh.j2``, which the
    watchdog re-runs every tick on the host; keep the two in step. The shell
    side compares users only: ``own_marker`` is a ``sizing:`` key, so it reaches
    ``--autotune`` on the controller but not the watchdog.
    """
    return tuple(g.index for g in gpus if not g.shared_with_others(me, own_marker))


@dataclass(frozen=True)
class WorkerProfile:
    """Per worker cost. Measure once per job; defaults suit a local ollama."""

    ram_gb: float = 7.0
    vram_mb: int = 6_000
    cores: int = 8
    max_per_gpu: int = 8
    gpu_reserve_mb: int = 1_000
    require_gpu: bool = False
    own_marker: str = ""

    def __post_init__(self) -> None:
        for name in ("ram_gb", "vram_mb", "cores", "max_per_gpu"):
            if getattr(self, name) <= 0:
                raise YujError(
                    f"sizing.{name} must be > 0, got {getattr(self, name)!r}",
                    hint="measure one worker's peak cost and set it in yuj.yaml",
                )
        if self.gpu_reserve_mb < 0:
            raise YujError("sizing.gpu_reserve_mb must be >= 0")


@dataclass(frozen=True)
class SizingPlan:
    """How many workers to run on one host, and on which cards."""

    gpu_indices: tuple[int, ...]
    workers_per_gpu: tuple[int, ...]
    skipped_gpus: tuple[int, ...]
    limited_by: str

    @property
    def total_workers(self) -> int:
        return sum(self.workers_per_gpu)

    @property
    def usable(self) -> bool:
        return self.total_workers > 0

    @property
    def gpus_env(self) -> str:
        """Value for GPUS, e.g. ``"0 2"``. Empty when CPU-only."""
        return " ".join(str(i) for i in self.gpu_indices)

    @property
    def workers_per_gpu_env(self) -> str:
        """Value for WORKERS_PER_GPU, e.g. ``"4 3"``. Empty when CPU-only."""
        return " ".join(str(w) for w in self.workers_per_gpu)


def plan_workers(
    gpus: tuple[Gpu, ...] | list[Gpu],
    *,
    nproc: int,
    mem_avail_gb: float,
    me: str,
    profile: WorkerProfile | None = None,
    load1: float = 0.0,
) -> SizingPlan:
    """Workers a host can take, from free RAM, cores and VRAM; tightest cap wins.

    Measures headroom, so probing a busy host answers "how many more fit".
    ``total_workers == 0`` means do not run here; there is no floor of one.
    """
    prof = profile or WorkerProfile()
    mine = set(courteous_gpus(gpus, me, prof.own_marker))
    usable = [g for g in gpus if g.index in mine]
    skipped = tuple(g.index for g in gpus if g.index not in mine)

    by_ram = int(mem_avail_gb * RAM_SAFETY / prof.ram_gb)
    by_cores = int(max(0.0, nproc - max(0.0, load1)) // prof.cores)

    if not usable:
        if gpus:
            return SizingPlan((), (), skipped, "gpus-busy")
        if prof.require_gpu:
            return SizingPlan((), (), skipped, "no-gpu")
        if min(by_ram, by_cores) < 1:
            return SizingPlan((), (), skipped, "ram" if by_ram < 1 else "cores")
        # ~20x slower without a card, so one worker only
        return SizingPlan((), (1,), skipped, "cpu-only")

    per_card = [
        min(prof.max_per_gpu, max(0, g.free_mb - prof.gpu_reserve_mb) // prof.vram_mb)
        for g in usable
    ]
    caps = ((by_ram, "ram"), (by_cores, "cores"), (sum(per_card), "vram"))
    total, limited_by = min(caps, key=lambda t: t[0])
    if total <= 0:
        return SizingPlan((), (), tuple(g.index for g in gpus), limited_by)

    # largest card first; a 96 GB card should not get the 8 GB one's share
    alloc = [0] * len(usable)
    order = sorted(range(len(usable)), key=lambda i: -usable[i].free_mb)
    left = total
    # total <= sum(per_card), so max_per_gpu passes always suffice
    for i in order * prof.max_per_gpu:
        if not left:
            break
        if alloc[i] < per_card[i]:
            alloc[i] += 1
            left -= 1

    keep = [(g.index, n) for g, n in zip(usable, alloc, strict=True) if n]
    unused = tuple(g.index for g, n in zip(usable, alloc, strict=True) if not n)
    return SizingPlan(
        gpu_indices=tuple(i for i, _ in keep),
        workers_per_gpu=tuple(n for _, n in keep),
        skipped_gpus=tuple(sorted(skipped + unused)),
        limited_by=limited_by,
    )
