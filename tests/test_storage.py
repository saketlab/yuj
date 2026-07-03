"""Tests for disk probing and YUJDISK block parsing."""

from __future__ import annotations

import pytest

from yuj.exceptions import YujError
from yuj.fleet import Host
from yuj.storage import _disk_command, parse_storage

HOST = Host(name="box", ip="10.0.0.1", user="u", password="p")

BLOCK = (
    "Last login: yesterday\n"
    "YUJDISK\n"
    "work=/home/u/yuj-work\n"
    "workavail=52428800\n"
    "workdev=/dev/sda1\n"
    "part=/dev/sda1|103212320|52428800|49%|/home\n"
    "part=tmpfs|8192000|8000000|3%|/run\n"
    "YUJEND\n"
)


class TestParseStorage:
    def test_parses_work_and_partitions(self) -> None:
        s = parse_storage(BLOCK, HOST)
        assert s.reachable is True
        assert s.work_dir == "/home/u/yuj-work"
        assert s.work_avail_kb == 52428800
        assert s.work_dev == "/dev/sda1"
        assert len(s.partitions) == 2

    def test_partition_fields(self) -> None:
        home, _run = parse_storage(BLOCK, HOST).partitions
        assert home.filesystem == "/dev/sda1"
        assert home.avail_kb == 52428800
        assert home.use_pct == 49
        assert home.mount == "/home"

    def test_work_partition_matched_by_device(self) -> None:
        s = parse_storage(BLOCK, HOST)
        home, run = s.partitions
        assert s.is_work_partition(home) is True
        assert s.is_work_partition(run) is False

    def test_ignores_malformed_part(self) -> None:
        block = "YUJDISK\npart=bad|only|three\nYUJEND\n"
        assert parse_storage(block, HOST).partitions == ()

    def test_missing_end_marker_raises(self) -> None:
        with pytest.raises(YujError):
            parse_storage("YUJDISK\nwork=~\npart=x|1|1|0%|/\n", HOST)

    def test_absent_work_dir_leaves_avail_none(self) -> None:
        # df of a missing path prints nothing, so no workavail/workdev lines appear.
        block = "YUJDISK\nwork=~/missing\npart=/dev/sda1|10|5|50%|/\nYUJEND\n"
        s = parse_storage(block, HOST)
        assert s.work_avail_kb is None
        assert s.work_dev is None
        assert s.is_work_partition(s.partitions[0]) is False


class TestDiskCommand:
    def test_rejects_unsafe_work_dir(self) -> None:
        with pytest.raises(YujError):
            _disk_command("~; rm -rf /")

    def test_emits_markers_and_workdev(self) -> None:
        cmd = _disk_command("~")
        assert "YUJDISK" in cmd and "YUJEND" in cmd and "df -Pk" in cmd
        assert "workdev=" in cmd
