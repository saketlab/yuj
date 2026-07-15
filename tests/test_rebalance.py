"""Tests for the rebalance tick (done-set derivation + remainder scatter)."""

from __future__ import annotations

from yuj.fleet import Fleet, Host
from yuj.pull import PullResult
from yuj.rebalance import (
    RebalanceTick,
    done_from_results,
    rebalance_once,
    remaining_items,
)
from yuj.scatter import ScatterResult


def _fleet(*names: str) -> Fleet:
    return Fleet(tuple(Host(name=n, ip="1.1.1.1", user="u") for n in names))


class TestDoneFromResults:
    def test_matches_by_output_presence(self, tmp_path) -> None:
        (tmp_path / "GSE1.json").write_text("{}")
        (tmp_path / "GSE3.json").write_text("{}")
        done = done_from_results(
            tmp_path, ["GSE1", "GSE2", "GSE3"], output_suffix=".json"
        )
        assert done == {"GSE1", "GSE3"}

    def test_only_worklist_items_count(self, tmp_path) -> None:
        # a stray result not in the work list must not shrink the pool
        (tmp_path / "STRAY.json").write_text("{}")
        assert done_from_results(tmp_path, ["GSE1"], output_suffix=".json") == set()

    def test_no_suffix(self, tmp_path) -> None:
        (tmp_path / "a").write_text("x")
        assert done_from_results(tmp_path, ["a", "b"]) == {"a"}

    def test_missing_dir_is_nothing_done(self, tmp_path) -> None:
        assert done_from_results(tmp_path / "nope", ["a"]) == set()


class TestRemainingItems:
    def test_order_preserving_and_dedup(self) -> None:
        assert remaining_items(["a", "b", "a", "c"], {"b"}) == ["a", "c"]

    def test_all_done(self) -> None:
        assert remaining_items(["a", "b"], {"a", "b"}) == []


class TestRebalanceOnce:
    def test_pulls_derives_done_and_scatters_remainder(
        self, tmp_path, monkeypatch
    ) -> None:
        (tmp_path / "GSE1.json").write_text("{}")
        (tmp_path / "GSE2.json").write_text("{}")
        captured: dict[str, object] = {}

        def fake_pull(fleet, **_kw):
            return {h.name: PullResult(h.name, True, tmp_path) for h in fleet.hosts}

        def fake_scatter(fleet, items, **_kw):
            captured["items"] = list(items)
            return {"a": ScatterResult("a", len(items), True)}

        monkeypatch.setattr("yuj.rebalance.pull_once", fake_pull)
        monkeypatch.setattr("yuj.rebalance.scatter_fleet", fake_scatter)

        tick = rebalance_once(
            _fleet("a"),
            ["GSE1", "GSE2", "GSE3"],
            remote_dir="yuj-run",
            output_dir="results",
            into="accessions.txt",
            dest_dir=tmp_path,
            output_suffix=".json",
        )
        assert isinstance(tick, RebalanceTick)
        assert captured["items"] == ["GSE3"]
        assert tick.total == 3
        assert tick.done == 2
        assert tick.remaining == 1
        assert tick.pulled_hosts == 1
        assert tick.scattered == 1
        assert not tick.complete

    def test_can_pull_from_more_hosts_than_it_scatters_to(
        self, tmp_path, monkeypatch
    ) -> None:
        (tmp_path / "done.json").write_text("{}")
        captured: dict[str, object] = {}

        def fake_pull(fleet, **_kw):
            captured["pulled"] = tuple(h.name for h in fleet.hosts)
            return {h.name: PullResult(h.name, True, tmp_path) for h in fleet.hosts}

        def fake_scatter(fleet, items, **_kw):
            captured["scattered_hosts"] = tuple(h.name for h in fleet.hosts)
            captured["items"] = list(items)
            return {
                h.name: ScatterResult(h.name, len(items), True) for h in fleet.hosts
            }

        monkeypatch.setattr("yuj.rebalance.pull_once", fake_pull)
        monkeypatch.setattr("yuj.rebalance.scatter_fleet", fake_scatter)

        tick = rebalance_once(
            _fleet("fast"),
            ["done", "todo"],
            remote_dir="yuj-run",
            output_dir="results",
            into="accessions.txt",
            dest_dir=tmp_path,
            output_suffix=".json",
            pull_fleet=_fleet("fast", "dropped"),
        )
        assert captured["pulled"] == ("fast", "dropped")
        assert captured["scattered_hosts"] == ("fast",)
        assert captured["items"] == ["todo"]
        assert tick.done == 1
        assert tick.remaining == 1

    def test_complete_when_all_done(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "a.json").write_text("{}")
        monkeypatch.setattr(
            "yuj.rebalance.pull_once",
            lambda fleet, **_k: {
                h.name: PullResult(h.name, True, tmp_path) for h in fleet.hosts
            },
        )
        monkeypatch.setattr(
            "yuj.rebalance.scatter_fleet",
            lambda fleet, items, **_k: {"a": ScatterResult("a", len(items), True)},
        )
        tick = rebalance_once(
            _fleet("a"),
            ["a"],
            remote_dir="yuj-run",
            output_dir="results",
            into="accessions.txt",
            dest_dir=tmp_path,
            output_suffix=".json",
        )
        assert tick.remaining == 0
        assert tick.complete

    def test_scatter_errors_surface(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "yuj.rebalance.pull_once",
            lambda fleet, **_k: {"a": PullResult("a", False, tmp_path, "down")},
        )
        monkeypatch.setattr(
            "yuj.rebalance.scatter_fleet",
            lambda fleet, items, **_k: {"a": ScatterResult("a", 0, False, "boom")},
        )
        tick = rebalance_once(
            _fleet("a"),
            ["x"],
            remote_dir="yuj-run",
            output_dir="results",
            into="accessions.txt",
            dest_dir=tmp_path,
        )
        assert tick.scatter_errors == {"a": "boom"}
        assert tick.pulled_hosts == 0
