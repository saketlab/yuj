"""Tests for weighted per-host scatter (pure-Python parts)."""

from __future__ import annotations

import pytest

from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host
from yuj.scatter import plan_scatter, read_items, scatter_host


def _fleet(*specs: tuple[str, float, bool]) -> Fleet:
    return Fleet(
        tuple(
            Host(name=n, ip="1.1.1.1", user="u", weight=w, do_not_use=dnu)
            for n, w, dnu in specs
        )
    )


class TestReadItems:
    def test_plain_lines(self, tmp_path) -> None:
        p = tmp_path / "items.txt"
        p.write_text("a\nb\nc\n")
        assert read_items(p) == ["a", "b", "c"]

    def test_skips_header_and_comments(self, tmp_path) -> None:
        p = tmp_path / "acc.csv"
        p.write_text("accession\nGSE1\n# note\n\nGSE2\n")
        assert read_items(p) == ["GSE1", "GSE2"]

    def test_first_field_only(self, tmp_path) -> None:
        p = tmp_path / "acc.csv"
        p.write_text("GSE1,foo,bar\nGSE2\t99\n")
        assert read_items(p) == ["GSE1", "GSE2"]


class TestPlanScatter:
    def test_weighted_counts(self) -> None:
        fleet = _fleet(("a", 2.0, False), ("b", 1.0, False))
        items = [f"i{i}" for i in range(30)]
        plan = plan_scatter(fleet, items)
        assert len(plan["a"]) == 20
        assert len(plan["b"]) == 10
        assert sorted(plan["a"] + plan["b"]) == sorted(items)

    def test_excludes_drop_before_split(self) -> None:
        fleet = _fleet(("a", 1.0, False), ("b", 1.0, False))
        items = ["x1", "x2", "x3", "x4"]
        plan = plan_scatter(fleet, items, exclude={"x1", "x2"})
        placed = plan["a"] + plan["b"]
        assert sorted(placed) == ["x3", "x4"]

    def test_skips_do_not_use(self) -> None:
        fleet = _fleet(("a", 1.0, False), ("dead", 1.0, True))
        plan = plan_scatter(fleet, ["i1", "i2"])
        assert "dead" not in plan
        assert sorted(plan["a"]) == ["i1", "i2"]

    def test_no_usable_hosts_raises(self) -> None:
        fleet = _fleet(("dead", 1.0, True))
        with pytest.raises(YujError):
            plan_scatter(fleet, ["i1"])

    def test_zero_weight_host_gets_nothing(self) -> None:
        fleet = _fleet(("a", 1.0, False), ("idle", 0.0, False))
        plan = plan_scatter(fleet, ["i1", "i2", "i3"])
        assert plan["idle"] == []
        assert len(plan["a"]) == 3


class TestScatterHostBody:
    """scatter_host builds the file body; verify header/content via a fake transport."""

    def test_writes_header_and_items(self, tmp_path, monkeypatch) -> None:
        captured: dict[str, str] = {}

        class FakeResult:
            ok = True
            stderr = ""

        class FakeTransport:
            def __init__(self) -> None:
                self.host = Host(name="h", ip="1.1.1.1", user="u")

            def run(self, *_a, **_k):  # type: ignore[no-untyped-def]
                return FakeResult()

            def put(self, local, _dest, **_k):  # type: ignore[no-untyped-def]
                captured["body"] = open(local).read()  # noqa: SIM115
                return FakeResult()

        res = scatter_host(
            FakeTransport(),  # type: ignore[arg-type]
            ["GSE1", "GSE2"],
            remote_dir="yuj-run",
            filename="accessions.csv",
            header="accession",
        )
        assert res.ok and res.count == 2
        assert captured["body"] == "accession\nGSE1\nGSE2\n"

    def test_no_header(self, tmp_path) -> None:
        captured: dict[str, str] = {}

        class FakeResult:
            ok = True
            stderr = ""

        class FakeTransport:
            host = Host(name="h", ip="1.1.1.1", user="u")

            def run(self, *_a, **_k):  # type: ignore[no-untyped-def]
                return FakeResult()

            def put(self, local, _dest, **_k):  # type: ignore[no-untyped-def]
                captured["body"] = open(local).read()  # noqa: SIM115
                return FakeResult()

        scatter_host(
            FakeTransport(),  # type: ignore[arg-type]
            ["a"],
            remote_dir="d",
            filename="f.txt",
        )
        assert captured["body"] == "a\n"

    def test_filename_with_subdir_mkdirs_and_places(self) -> None:
        captured: dict[str, str] = {}

        class FakeResult:
            ok = True
            stderr = ""

        class FakeTransport:
            host = Host(name="h", ip="1.1.1.1", user="u")

            def run(self, cmd, **_k):  # type: ignore[no-untyped-def]
                captured["mkdir"] = cmd
                return FakeResult()

            def put(self, local, dest, **_k):  # type: ignore[no-untyped-def]
                captured["dest"] = dest
                captured["body"] = open(local).read()  # noqa: SIM115
                return FakeResult()

        res = scatter_host(
            FakeTransport(),  # type: ignore[arg-type]
            ["a", "b"],
            remote_dir="yuj-run",
            filename="sub/dir/slice.txt",
        )
        assert res.ok and res.count == 2
        assert "yuj-run/sub/dir" in captured["mkdir"]  # parent created remotely
        assert captured["dest"] == "yuj-run/sub/dir/"
        assert captured["body"] == "a\nb\n"
