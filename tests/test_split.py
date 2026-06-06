"""Tests for weighted work splitting, chunking, resume, and redistribution."""

from __future__ import annotations

import pytest

from yuj.exceptions import SplitError
from yuj.split import chunked, pending_items, redistribute, weighted_split


def _flatten(assignment: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for items in assignment.values():
        out.extend(items)
    return out


class TestWeightedSplit:
    def test_equal_weights_partition_exactly(self) -> None:
        items = [f"item{i}" for i in range(10)]
        result = weighted_split(items, {"a": 1, "b": 1})
        assert len(result["a"]) == 5
        assert len(result["b"]) == 5

    def test_no_item_lost_or_duplicated(self) -> None:
        items = [f"i{i}" for i in range(97)]
        result = weighted_split(items, {"a": 1, "b": 2, "c": 3})
        flat = _flatten(result)
        assert sorted(flat) == sorted(items)
        assert len(flat) == len(set(flat)) == 97

    def test_proportional_to_weight(self) -> None:
        items = [str(i) for i in range(100)]
        result = weighted_split(items, {"big": 3, "small": 1})
        assert len(result["big"]) == 75
        assert len(result["small"]) == 25

    def test_largest_remainder_distributes_leftovers(self) -> None:
        # 10 items, three equal hosts -> 4/3/3 (leftover goes to earliest in order).
        result = weighted_split([str(i) for i in range(10)], {"a": 1, "b": 1, "c": 1})
        counts = sorted(len(v) for v in result.values())
        assert counts == [3, 3, 4]
        assert sum(counts) == 10

    def test_zero_weight_host_gets_nothing(self) -> None:
        result = weighted_split([str(i) for i in range(10)], {"on": 1, "drained": 0})
        assert len(result["drained"]) == 0
        assert len(result["on"]) == 10

    def test_every_host_is_a_key_even_when_empty(self) -> None:
        result = weighted_split(["only"], {"a": 1, "b": 1, "c": 1})
        assert set(result) == {"a", "b", "c"}
        assert sum(len(v) for v in result.values()) == 1

    def test_empty_items_returns_empty_lists(self) -> None:
        result = weighted_split([], {"a": 1, "b": 1})
        assert result == {"a": [], "b": []}

    def test_more_hosts_than_items(self) -> None:
        result = weighted_split(["x"], {"a": 1, "b": 1})
        assert sorted(len(v) for v in result.values()) == [0, 1]

    def test_preserves_order_within_and_across_hosts(self) -> None:
        items = [str(i) for i in range(6)]
        result = weighted_split(items, {"a": 1, "b": 1})
        # Contiguous slices preserve original order.
        assert result["a"] == ["0", "1", "2"]
        assert result["b"] == ["3", "4", "5"]

    def test_deterministic(self) -> None:
        items = [str(i) for i in range(53)]
        weights = {"a": 2, "b": 3, "c": 5}
        assert weighted_split(items, weights) == weighted_split(items, weights)

    def test_no_hosts_raises(self) -> None:
        with pytest.raises(SplitError, match="no hosts"):
            weighted_split(["a"], {})

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(SplitError, match="negative weight"):
            weighted_split(["a"], {"x": -1})

    def test_zero_total_weight_with_items_raises(self) -> None:
        with pytest.raises(SplitError, match="total weight is zero"):
            weighted_split(["a", "b"], {"x": 0, "y": 0})

    def test_zero_total_weight_no_items_is_ok(self) -> None:
        assert weighted_split([], {"x": 0}) == {"x": []}


class TestChunked:
    def test_even_split(self) -> None:
        assert chunked(["a", "b", "c", "d"], 2) == [["a", "b"], ["c", "d"]]

    def test_uneven_last_chunk_shorter(self) -> None:
        assert chunked(["a", "b", "c"], 2) == [["a", "b"], ["c"]]

    def test_chunk_larger_than_input(self) -> None:
        assert chunked(["a", "b"], 10) == [["a", "b"]]

    def test_empty(self) -> None:
        assert chunked([], 5) == []

    def test_nonpositive_chunk_size_raises(self) -> None:
        with pytest.raises(SplitError, match="chunk_size must be positive"):
            chunked(["a"], 0)


class TestPendingItems:
    def test_filters_done(self) -> None:
        done = {"a", "c"}
        result = pending_items(["a", "b", "c", "d"], lambda i: i in done)
        assert result == ["b", "d"]

    def test_preserves_order(self) -> None:
        result = pending_items(["3", "1", "2"], lambda i: False)
        assert result == ["3", "1", "2"]

    def test_all_done(self) -> None:
        assert pending_items(["a", "b"], lambda i: True) == []


class TestRedistribute:
    def test_trim_only_drops_done_no_movement(self) -> None:
        current = {"a": ["1", "2", "3"], "b": ["4", "5"]}
        done = {"2", "4"}
        result = redistribute(
            current, {"a": 1, "b": 1}, lambda i: i in done, trim_only=True
        )
        assert result == {"a": ["1", "3"], "b": ["5"]}

    def test_trim_only_rejects_dropping_a_host(self) -> None:
        current = {"a": ["1"], "gone": ["2"]}
        with pytest.raises(SplitError, match="cannot drop hosts"):
            redistribute(current, {"a": 1}, lambda i: False, trim_only=True)

    def test_heavy_repools_and_resplits(self) -> None:
        current = {"a": ["1", "2", "3", "4"], "b": []}
        # Re-pool remaining across a new host set, weighted.
        result = redistribute(
            current, {"a": 1, "b": 1}, lambda i: False, trim_only=False
        )
        assert sorted(_flatten(result)) == ["1", "2", "3", "4"]
        assert len(result["a"]) == 2
        assert len(result["b"]) == 2

    def test_heavy_drops_done_before_resplit(self) -> None:
        current = {"a": ["1", "2"], "b": ["3", "4"]}
        done = {"1", "3"}
        result = redistribute(
            current, {"a": 1, "b": 1}, lambda i: i in done, trim_only=False
        )
        assert sorted(_flatten(result)) == ["2", "4"]

    def test_heavy_dedupes_items_seen_on_multiple_hosts(self) -> None:
        # Same item assigned to two hosts (a redistribution race) is pooled once.
        current = {"a": ["dup", "x"], "b": ["dup", "y"]}
        result = redistribute(
            current, {"a": 1, "b": 1}, lambda i: False, trim_only=False
        )
        flat = _flatten(result)
        assert flat.count("dup") == 1
        assert sorted(flat) == ["dup", "x", "y"]

    def test_heavy_can_move_work_to_new_host(self) -> None:
        current = {"a": ["1", "2", "3", "4"]}
        result = redistribute(
            current, {"a": 1, "new": 1}, lambda i: False, trim_only=False
        )
        assert len(result["new"]) == 2
        assert sorted(_flatten(result)) == ["1", "2", "3", "4"]
