"""Tests for the rich status-table rendering helpers."""

from __future__ import annotations

from rich.console import Console

from yuj._render import _bar, status_table, summary_line, usable_summary
from yuj.status import HostStatus


def test_usable_property_and_summary() -> None:
    hosts = [
        HostStatus(name="ready", ip="1", reachable=True),
        HostStatus(name="owned", ip="2", reachable=True, console_user="alice"),
        HostStatus(name="down", ip="3", reachable=False, error="banned"),
        HostStatus(name="skip", ip="4", reachable=True, excluded=True),
    ]
    assert [h.name for h in hosts if h.usable] == ["ready"]
    line = usable_summary(hosts)
    assert "1 of 4 host(s) usable" in line
    assert "down: down" in line
    assert "owner present: owned" in line
    assert "do_not_use: skip" in line


def test_bar_endpoints_and_width() -> None:
    assert _bar(0.0) == " " * 22
    assert _bar(1.0) == "█" * 22
    assert _bar(2.0) == "█" * 22  # clamped
    assert all(len(_bar(f / 10)) == 22 for f in range(11))
    assert _bar(0.5).startswith("█") and _bar(0.5).endswith(" ")  # partway


def _statuses() -> list[HostStatus]:
    return [
        HostStatus(
            name="prod",
            ip="1",
            reachable=True,
            cpu_model="Xeon Gold 6248",
            nproc=8,
            mem_gb=31,
            gpu="A40 45G",
            load1=2.0,
            n_outputs=120,
            newest_age_min=4,
        ),
        HostStatus(
            name="stale",
            ip="2",
            reachable=True,
            nproc=8,
            n_outputs=50,
            newest_age_min=300,
            watchdog_running=True,
        ),
        HostStatus(name="idle-host", ip="3", reachable=True),
        HostStatus(name="down", ip="4", reachable=False, error="banned"),
        HostStatus(
            name="owned",
            ip="5",
            reachable=True,
            nproc=4,
            n_outputs=5,
            newest_age_min=2,
            console_user="alice",
        ),
    ]


def _render(table: object) -> str:
    console = Console(width=160, record=True)
    console.print(table)
    return console.export_text()


def test_status_table_has_a_row_per_host() -> None:
    assert status_table(_statuses()).row_count == 5


def test_status_table_renders_states_and_columns() -> None:
    text = _render(status_table(_statuses(), stall_threshold_min=90))
    assert "producing" in text
    assert "stalled" in text
    assert "idle" in text
    assert "down" in text
    assert "A40 45G" in text  # gpu column
    assert "Xeon Gold 6248" in text  # cpu column
    assert "alice" in text  # owner-present flag


def test_status_table_title() -> None:
    assert status_table(_statuses(), title="my lab").title == "my lab"


def test_status_table_title_shows_eta() -> None:
    table = status_table(_statuses(), total_items=1000, eta="~2.5h at 800/hr")
    title = str(table.title)
    assert "175/1000" in title
    assert "ETA ~2.5h at 800/hr" in title


def test_status_table_eta_ignored_without_total() -> None:
    # No total to measure against -> no progress, no ETA in the title.
    title = str(status_table(_statuses(), eta="~2.5h at 800/hr").title)
    assert "ETA" not in title


def test_summary_line_counts() -> None:
    line = summary_line(_statuses(), stall_threshold_min=90)
    assert "4/5 up" in line
    assert "2 producing" in line  # prod + owned (recent output)
    assert "1 stalled" in line  # stale
    assert "175 outputs" in line  # 120 + 50 + 5
    assert "1 with owner present" in line


def test_summary_line_empty() -> None:
    assert "0/0 up" in summary_line([])


def test_load_without_nproc_renders() -> None:
    # A host reporting load but not core count still renders a load cell.
    status = HostStatus(name="h", ip="1", reachable=True, load1=1.5, newest_age_min=2)
    text = _render(status_table([status]))
    assert "1.50" in text


def test_stall_threshold_changes_classification() -> None:
    # The "stale" host (age 300) becomes "producing" under a very high threshold.
    high = summary_line(_statuses(), stall_threshold_min=600)
    assert "3 producing" in high
    assert "0 stalled" in high
