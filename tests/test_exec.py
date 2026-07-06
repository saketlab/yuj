"""Tests for `yuj exec` result logic and rendering."""

from __future__ import annotations

from yuj._render import _exec_preview, exec_summary, exec_table
from yuj.exec_cmd import ExecResult


def _r(
    name: str,
    *,
    reachable: bool = True,
    returncode: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    error: str | None = None,
) -> ExecResult:
    return ExecResult(
        name=name,
        ip="10.0.0.1",
        reachable=reachable,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        error=error,
    )


class TestOk:
    def test_zero_exit_reachable_is_ok(self) -> None:
        assert _r("a", returncode=0).ok is True

    def test_nonzero_exit_not_ok(self) -> None:
        assert _r("a", returncode=1).ok is False

    def test_unreachable_not_ok(self) -> None:
        assert _r("a", reachable=False, returncode=None, error="timeout").ok is False


class TestPreview:
    def test_last_nonempty_stdout_line(self) -> None:
        assert _exec_preview(_r("a", stdout="one\n\ntwo\n")) == "two"

    def test_falls_back_to_stderr(self) -> None:
        assert _exec_preview(_r("a", stdout="   \n", stderr="boom")) == "boom"

    def test_error_wins(self) -> None:
        assert _exec_preview(_r("a", reachable=False, error="no route")) == "no route"

    def test_truncates_long_line(self) -> None:
        out = _exec_preview(_r("a", stdout="x" * 200), width=10)
        assert out.endswith("…") and len(out) == 10


class TestSummary:
    def test_tally(self) -> None:
        results = [
            _r("a", returncode=0),
            _r("b", returncode=7),
            _r("c", reachable=False, returncode=None, error="down"),
        ]
        assert exec_summary(results) == "1 ok · 1 nonzero · 1 unreachable  (of 3)"


def test_exec_table_has_a_row_per_host() -> None:
    results = [_r("a", returncode=0), _r("b", reachable=False, returncode=None)]
    table = exec_table(results)
    assert table.row_count == 2
    assert [c.header for c in table.columns] == ["host", "exit", "output"]
