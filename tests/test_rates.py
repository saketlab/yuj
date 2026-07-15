"""Tests for observed-throughput weighting and makespan."""

from __future__ import annotations

import pytest

from yuj.exceptions import SplitError
from yuj.rates import makespan, observed_rates
from yuj.split import weighted_split

NOW = 1_000_000.0
HOUR = 3600.0


class TestObservedRates:
    def test_rate_is_cost_per_hour(self) -> None:
        rates = observed_rates(
            {"a": ["x", "y"]},
            done_at=lambda i: NOW - 60,
            cost=lambda i: 50,
            now=NOW,
            window_s=HOUR,
        )
        assert rates["a"] == pytest.approx(100.0)

    def test_items_outside_window_are_ignored(self) -> None:
        rates = observed_rates(
            {"a": ["old", "new"]},
            done_at=lambda i: NOW - 10 * HOUR if i == "old" else NOW - 60,
            cost=lambda i: 100,
            now=NOW,
            window_s=HOUR,
            floor=1.0,
        )
        assert rates["a"] == pytest.approx(100.0)

    def test_unfinished_items_are_ignored(self) -> None:
        rates = observed_rates(
            {"a": ["done", "pending"]},
            done_at=lambda i: NOW - 60 if i == "done" else None,
            cost=lambda i: 10,
            now=NOW,
            window_s=HOUR,
        )
        assert rates["a"] == pytest.approx(10.0)

    def test_idle_host_gets_floor_not_zero(self) -> None:
        rates = observed_rates(
            {"grinding": ["monster"]},
            done_at=lambda i: None,
            cost=lambda i: 500,
            now=NOW,
            window_s=HOUR,
            floor=7.0,
        )
        assert rates["grinding"] == 7.0

    def test_every_host_present_even_with_empty_slice(self) -> None:
        rates = observed_rates(
            {"a": ["x"], "drained": []},
            done_at=lambda i: NOW - 60,
            cost=lambda i: 10,
            now=NOW,
            window_s=HOUR,
            floor=2.0,
        )
        assert set(rates) == {"a", "drained"}
        assert rates["drained"] == 2.0

    def test_rejects_nonpositive_window(self) -> None:
        with pytest.raises(SplitError):
            observed_rates(
                {"a": []},
                done_at=lambda i: None,
                cost=lambda i: 1,
                now=NOW,
                window_s=0,
            )

    def test_rejects_nonpositive_floor(self) -> None:
        with pytest.raises(SplitError):
            observed_rates(
                {"a": []},
                done_at=lambda i: None,
                cost=lambda i: 1,
                now=NOW,
                floor=0,
            )


class TestMakespan:
    def test_is_the_slowest_host_not_the_mean(self) -> None:
        span = makespan(
            {"fast": ["a"], "slow": ["b"]},
            {"fast": 100.0, "slow": 10.0},
            cost=lambda i: 100,
        )
        assert span == pytest.approx(10.0)

    def test_empty_assignment_is_zero(self) -> None:
        assert makespan({"a": []}, {"a": 5.0}, cost=lambda i: 1) == 0.0

    def test_work_on_a_zero_rate_host_never_finishes(self) -> None:
        span = makespan({"dead": ["x"]}, {"dead": 0.0}, cost=lambda i: 1)
        assert span == float("inf")

    def test_zero_rate_host_with_no_work_is_fine(self) -> None:
        span = makespan(
            {"dead": [], "live": ["x"]},
            {"dead": 0.0, "live": 2.0},
            cost=lambda i: 4,
        )
        assert span == pytest.approx(2.0)


class TestRebalanceEndToEnd:
    def test_measured_rates_beat_stale_weights(self) -> None:
        items = [f"i{n}" for n in range(40)]
        cost = lambda i: 10  # noqa: E731 - uniform here; heterogeneity covered below

        stale = weighted_split(items, {"a": 1, "b": 1}, cost=cost)
        real = {"a": 100.0, "b": 10.0}
        assert makespan(stale, real, cost) == pytest.approx(20.0)

        measured = weighted_split(items, real, cost=cost)
        span = makespan(measured, real, cost)
        assert span == pytest.approx(3.7)
        assert span < makespan(stale, real, cost) / 5
