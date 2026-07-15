"""Tests for cost-aware (LPT) splitting -- balancing work, not item count."""

from __future__ import annotations

import pytest

from yuj.exceptions import SplitError
from yuj.rates import makespan
from yuj.split import redistribute, weighted_split


def _flatten(assignment: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for items in assignment.values():
        out.extend(items)
    return out


class TestCostAwareSplit:
    def test_no_item_lost_or_duplicated(self) -> None:
        items = [f"i{n}" for n in range(53)]
        result = weighted_split(items, {"a": 1, "b": 2}, cost=lambda i: len(i))
        flat = _flatten(result)
        assert sorted(flat) == sorted(items)
        assert len(flat) == len(set(flat)) == 53

    def test_balances_work_not_count(self) -> None:
        items = ["fat"] + [f"thin{n}" for n in range(10)]
        cost = lambda i: 100 if i == "fat" else 10  # noqa: E731
        result = weighted_split(items, {"a": 1, "b": 1}, cost=cost)
        load_a = sum(cost(i) for i in result["a"])
        load_b = sum(cost(i) for i in result["b"])
        assert abs(load_a - load_b) <= 10

    def test_heavy_items_go_to_the_faster_host(self) -> None:
        items = ["monster", "a", "b", "c", "d"]
        cost = lambda i: 500 if i == "monster" else 10  # noqa: E731
        result = weighted_split(items, {"blackwell": 50, "tiny": 1}, cost=cost)
        assert "monster" in result["blackwell"]
        assert "monster" not in result["tiny"]

    def test_load_is_proportional_to_weight(self) -> None:
        items = [f"i{n}" for n in range(300)]
        cost = lambda i: 1  # noqa: E731
        result = weighted_split(items, {"big": 3, "small": 1}, cost=cost)
        assert len(result["big"]) == pytest.approx(225, abs=2)
        assert len(result["small"]) == pytest.approx(75, abs=2)

    def test_zero_weight_host_is_drained(self) -> None:
        result = weighted_split(
            ["a", "b", "c"], {"live": 1, "off": 0}, cost=lambda i: 1
        )
        assert result["off"] == []
        assert sorted(result["live"]) == ["a", "b", "c"]

    def test_all_zero_weights_raises(self) -> None:
        with pytest.raises(SplitError):
            weighted_split(["a"], {"x": 0, "y": 0}, cost=lambda i: 1)

    def test_empty_items_yields_empty_slices(self) -> None:
        result = weighted_split([], {"a": 1, "b": 1}, cost=lambda i: 1)
        assert result == {"a": [], "b": []}

    def test_deterministic(self) -> None:
        items = [f"i{n}" for n in range(50)]
        cost = lambda i: int(i[1:]) % 7 + 1  # noqa: E731
        first = weighted_split(items, {"a": 2, "b": 1}, cost=cost)
        second = weighted_split(items, {"a": 2, "b": 1}, cost=cost)
        assert first == second

    def test_cost_is_evaluated_once_per_item(self) -> None:
        items = [f"i{n}" for n in range(8)]
        calls: list[str] = []

        def cost(item: str) -> float:
            calls.append(item)
            return float(int(item[1:]) + 1)

        weighted_split(items, {"a": 1, "b": 1}, cost=cost)
        assert calls == items

    @pytest.mark.parametrize("bad", [-1.0, float("inf"), float("nan")])
    def test_rejects_invalid_cost(self, bad: float) -> None:
        with pytest.raises(SplitError, match="finite non-negative"):
            weighted_split(["x"], {"a": 1}, cost=lambda _i: bad)

    def test_beats_count_split_on_makespan(self) -> None:
        items = [f"i{n}" for n in range(200)]
        cost = lambda i: 500 if int(i[1:]) % 20 == 0 else 5  # noqa: E731
        rates = {"a": 10.0, "b": 5.0, "c": 1.0}

        by_count = weighted_split(items, rates)
        by_cost = weighted_split(items, rates, cost=cost)
        assert makespan(by_cost, rates, cost) < makespan(by_count, rates, cost)


class TestRedistributeWithCost:
    def test_forwards_cost_to_the_split(self) -> None:
        current = {"slow": ["monster", "x"], "fast": ["y"]}
        cost = lambda i: 500 if i == "monster" else 1  # noqa: E731
        result = redistribute(
            current,
            {"slow": 1, "fast": 100},
            is_done=lambda i: False,
            trim_only=False,
            cost=cost,
        )
        assert "monster" in result["fast"]

    def test_done_items_are_dropped(self) -> None:
        current = {"a": ["done", "todo"]}
        result = redistribute(
            current,
            {"a": 1},
            is_done=lambda i: i == "done",
            trim_only=False,
            cost=lambda i: 1,
        )
        assert result["a"] == ["todo"]

    def test_trim_only_ignores_cost_and_moves_nothing(self) -> None:
        current = {"a": ["keep", "done"], "b": ["also"]}
        result = redistribute(
            current,
            {"a": 1, "b": 1},
            is_done=lambda i: i == "done",
            trim_only=True,
            cost=lambda i: 999,
        )
        assert result == {"a": ["keep"], "b": ["also"]}
