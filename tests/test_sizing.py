"""Sizing cases taken from hosts that actually misbehaved."""

from __future__ import annotations

import pytest

from yuj.exceptions import YujError
from yuj.sizing import WorkerProfile, plan_workers
from yuj.status import Gpu

ME = "me"


def a6000(index: int, used: int = 0, users: tuple[str, ...] = ()) -> Gpu:
    """A 48 GB A6000, optionally already busy with someone else's work."""
    return Gpu(index, "A6000", 49_140, used, users)


def test_cores_bind_when_ram_is_plentiful() -> None:
    # meherangarh: 3x A6000, 96 cores, ~880 GB free. Cores are the ceiling.
    plan = plan_workers(
        [a6000(0), a6000(1), a6000(2)], nproc=96, mem_avail_gb=880, me=ME
    )
    assert plan.total_workers == 12
    assert plan.limited_by == "cores"
    assert plan.gpus_env == "0 1 2"
    assert plan.workers_per_gpu_env == "4 4 4"


def test_card_with_another_user_is_left_alone() -> None:
    gpus = [a6000(0), a6000(1, 7_170, ("bgoswami",)), a6000(2)]
    plan = plan_workers(gpus, nproc=96, mem_avail_gb=880, me=ME)
    assert plan.gpus_env == "0 2"
    assert 1 in plan.skipped_gpus


def test_gpu_host_with_every_card_busy_is_skipped_not_run_on_cpu() -> None:
    # Falling back to a CPU worker here would eat the owner's cores while their
    # GPU job runs, for ~1/20th the throughput. Leave the box alone.
    gpus = [a6000(0, 8_000, ("someone",)), a6000(1, 8_000, ("someone",))]
    plan = plan_workers(gpus, nproc=96, mem_avail_gb=880, me=ME)
    assert plan.total_workers == 0
    assert plan.limited_by == "gpus-busy"
    assert plan.skipped_gpus == (0, 1)


def test_our_own_processes_do_not_count_as_foreign() -> None:
    gpus = [a6000(0, 22_330, (ME,)), a6000(1)]
    plan = plan_workers(gpus, nproc=96, mem_avail_gb=880, me=ME)
    assert plan.gpus_env == "0 1"
    assert plan.skipped_gpus == ()


def test_ram_binds_on_a_fat_gpu_thin_ram_box() -> None:
    # pasteur: 192 cores and 3 cards, but only ~100 GB free. 18 workers OOM-killed it.
    plan = plan_workers(
        [a6000(0), a6000(1), a6000(2)], nproc=192, mem_avail_gb=100, me=ME
    )
    assert plan.total_workers == 11
    assert plan.limited_by == "ram"


def test_no_gpu_falls_back_to_one_cpu_worker() -> None:
    plan = plan_workers([], nproc=64, mem_avail_gb=250, me=ME)
    assert plan.total_workers == 1
    assert plan.limited_by == "cpu-only"
    assert plan.gpus_env == ""


def test_gpu_only_job_skips_a_gpuless_host() -> None:
    plan = plan_workers(
        [], nproc=64, mem_avail_gb=250, me=ME, profile=WorkerProfile(require_gpu=True)
    )
    assert not plan.usable
    assert plan.limited_by == "no-gpu"


def test_exhausted_resource_yields_zero_workers_not_one() -> None:
    # The floor of 1 was the original defect: a host with no headroom was still
    # handed a worker, which is exactly how the 251GB box got OOM-killed.
    starved = plan_workers([a6000(0)], nproc=96, mem_avail_gb=1, me=ME)
    assert starved.total_workers == 0 and starved.limited_by == "ram"

    few_cores = plan_workers([a6000(0)], nproc=4, mem_avail_gb=100, me=ME)
    assert few_cores.total_workers == 0 and few_cores.limited_by == "cores"

    full_card = plan_workers([a6000(0, 49_140)], nproc=96, mem_avail_gb=100, me=ME)
    assert full_card.total_workers == 0 and full_card.limited_by == "vram"


def test_live_load_eats_the_core_budget() -> None:
    # Same box, same cores: already-busy hosts must not be handed a full share.
    idle = plan_workers([a6000(0)], nproc=96, mem_avail_gb=880, me=ME, load1=0)
    busy = plan_workers([a6000(0)], nproc=96, mem_avail_gb=880, me=ME, load1=90)
    assert busy.total_workers < idle.total_workers
    swamped = plan_workers([a6000(0)], nproc=96, mem_avail_gb=880, me=ME, load1=856)
    assert swamped.total_workers == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ram_gb": 0},
        {"cores": 0},
        {"vram_mb": -1},
        {"max_per_gpu": 0},
        {"gpu_reserve_mb": -1},
    ],
)
def test_nonpositive_costs_are_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(YujError):
        WorkerProfile(**kwargs)  # type: ignore[arg-type]


def test_our_own_stale_run_counts_as_foreign_with_a_marker() -> None:
    stale = Gpu(0, "A6000", 49_140, 22_000, (ME,), ("ollama runner --old",))
    mine = Gpu(1, "A6000", 49_140, 22_000, (ME,), ("ollama runner --job=abc",))
    plan = plan_workers(
        [stale, mine],
        nproc=96,
        mem_avail_gb=880,
        me=ME,
        profile=WorkerProfile(own_marker="--job=abc"),
    )
    assert plan.gpus_env == "1"
    assert plan.skipped_gpus == (0,)


def test_gpu_reserve_is_held_back() -> None:
    card = Gpu(0, "A6000", 49_140, 0)
    tight = plan_workers(
        [card],
        nproc=96,
        mem_avail_gb=880,
        me=ME,
        profile=WorkerProfile(vram_mb=20_000, gpu_reserve_mb=20_000),
    )
    assert tight.total_workers == 1  # 49140-20000 = 29140 -> one 20GB worker


def test_bigger_card_gets_the_larger_share() -> None:
    small = Gpu(0, "RTX 3050", 8_192, 0)
    big = Gpu(1, "Blackwell", 97_887, 0)
    plan = plan_workers([small, big], nproc=96, mem_avail_gb=880, me=ME)
    per = dict(zip(plan.gpu_indices, plan.workers_per_gpu, strict=True))
    assert per[1] > per[0]


def test_vram_caps_a_small_card() -> None:
    # An 8 GB card fits one 6 GB worker, no matter how much RAM the host has.
    plan = plan_workers(
        [Gpu(0, "RTX 3050", 8_192, 0)], nproc=96, mem_avail_gb=880, me=ME
    )
    assert plan.total_workers == 1
    assert plan.limited_by == "vram"
    assert plan.gpus_env == "0"
