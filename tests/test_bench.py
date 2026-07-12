"""Tests for host benchmarking, parsing, and smart-scatter weight derivation."""

from __future__ import annotations

from yuj.bench import (
    DEFAULT_URL,
    HostBench,
    _bench_command,
    parse_bench,
    weights_for,
)
from yuj.fleet import Host

HOST = Host(name="box", ip="10.0.0.1", user="u", password="p")

BLOCK = (
    "Last login: yesterday\n"
    "YUJBENCH\n"
    "cores=32\n"
    "memgb=251\n"
    "gpuvram=90\n"
    "gpucount=2\n"
    "gpuname=NVIDIA A100-SXM4-80GB\n"
    "diskgb=1800\n"
    "download=118000000\n"  # bytes/s
    "YUJEND\n"
)


class TestParseBench:
    def test_parses_all_fields(self) -> None:
        b = parse_bench(BLOCK, HOST)
        assert b.reachable
        assert b.cores == 32
        assert b.mem_gb == 251
        assert b.gpu_vram_gb == 90
        assert b.gpu_count == 2
        assert b.gpu_name == "A100-SXM4-80GB"
        assert b.disk_gb == 1800
        assert b.download_mbps == 118.0

    def test_missing_gpu_is_none(self) -> None:
        b = parse_bench("YUJBENCH\ncores=8\nmemgb=15\nYUJEND\n", HOST)
        assert b.gpu_vram_gb is None
        assert b.gpu_count == 0
        assert b.download_mbps is None

    def test_zero_download_is_none(self) -> None:
        b = parse_bench("YUJBENCH\ndownload=0.000\nYUJEND\n", HOST)
        assert b.download_mbps is None


class TestBenchCommand:
    def test_download_line_present_only_when_requested(self) -> None:
        with_dl = _bench_command("~", DEFAULT_URL, 20, download=True)
        without = _bench_command("~", DEFAULT_URL, 20, download=False)
        assert "curl" in with_dl and "speed_download" in with_dl
        assert "-fsSL" in with_dl  # L follows redirects, else times a 3xx body
        assert "curl" not in without

    def test_url_is_shell_quoted(self) -> None:
        cmd = _bench_command("~", "http://x/f;rm -rf /", 5, download=True)
        assert "rm -rf /'" in cmd or "'http://x/f;rm -rf /'" in cmd
        assert "; rm -rf /" not in cmd.replace("'http://x/f;rm -rf /'", "")


def _cores(name: str, n: int | None, reachable: bool = True) -> HostBench:
    return HostBench(name=name, ip="1.1.1.1", reachable=reachable, cores=n)


def _dl(name: str, mbps: float | None) -> HostBench:
    return HostBench(name=name, ip="1.1.1.1", reachable=True, download_mbps=mbps)


class TestWeightsFor:
    def test_weight_tracks_metric(self) -> None:
        weights, dropped = weights_for([_cores("a", 32), _cores("b", 8)], "cores")
        assert weights == {"a": 32.0, "b": 8.0}
        assert dropped == []

    def test_unreachable_drained_silently(self) -> None:
        benches = [_cores("a", 32), _cores("b", None, reachable=False)]
        weights, dropped = weights_for(benches, "cores")
        assert weights["b"] == 0.0
        assert dropped == []

    def test_reachable_but_no_metric_is_dropped(self) -> None:
        weights, dropped = weights_for([_dl("a", 100.0), _dl("b", None)], "download")
        assert weights == {"a": 100.0, "b": 0.0}
        assert dropped == ["b"]
