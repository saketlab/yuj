"""Tests for the self-contained HTML dashboard renderer."""

from __future__ import annotations

from yuj._html import status_html
from yuj.status import HostStatus


def _statuses() -> list[HostStatus]:
    return [
        HostStatus(
            name="prod",
            ip="1.1.1.1",
            reachable=True,
            gpu="A40 45G",
            nproc=8,
            mem_gb=31,
            load1=2.0,
            n_outputs=120,
            newest_age_min=4,
        ),
        HostStatus(
            name="stale",
            ip="2.2.2.2",
            reachable=True,
            nproc=8,
            n_outputs=50,
            newest_age_min=300,
            watchdog_running=True,
        ),
        HostStatus(name="down", ip="3.3.3.3", reachable=False, error="banned"),
        HostStatus(
            name="owned",
            ip="4.4.4.4",
            reachable=True,
            nproc=4,
            n_outputs=5,
            newest_age_min=2,
            console_user="alice",
        ),
    ]


def test_renders_self_contained_page() -> None:
    html = status_html(_statuses())
    assert html.startswith("<!doctype html>")
    assert "<style>" in html  # CSS is inline, no external assets
    assert "http" not in html.split("<style>")[1].split("</style>")[0]  # no remote CSS


def test_shows_each_host_and_its_state() -> None:
    html = status_html(_statuses(), stall_threshold_min=90)
    for name in ("prod", "stale", "down", "owned"):
        assert name in html
    assert "producing" in html
    assert "stalled" in html
    assert "down" in html
    assert "A40 45G" in html
    assert "alice" in html  # owner-present warning


def test_progress_and_total() -> None:
    html = status_html(_statuses(), total_items=1000)
    assert "175 / 1,000" in html  # 120 + 50 + 5 outputs
    assert "17%" in html


def test_window_eta_and_refresh_are_optional() -> None:
    plain = status_html(_statuses())
    assert "http-equiv" not in plain  # no meta-refresh when refresh_secs is None
    rich = status_html(
        _statuses(),
        refresh_secs=30,
        window_label="19:00-09:30",
        stops_in="4h12m",
        eta="~3.1d at 412/hr",
    )
    assert 'http-equiv="refresh" content="30"' in rich
    assert "19:00-09:30" in rich
    assert "4h12m" in rich
    assert "~3.1d at 412/hr" in rich


def test_escapes_untrusted_fields() -> None:
    evil = HostStatus(name="<script>x</script>", ip="1", reachable=True, gpu="<b>g</b>")
    html = status_html([evil])
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
