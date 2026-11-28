"""runlog.py paths that mutation testing (batch 72, docs/quality/mutation-testing.md) showed no test
pinned: the default sizes, a log in a folder that doesn't exist yet, the exact trim edge, how much a
trim keeps and that the newest row is always kept."""

from __future__ import annotations

import json
import os

import pytest

from fabric_desktop import runlog

ROW = len(json.dumps({"i": "000"}) + "\n")  # every row below is this many characters, as the trim counts them
DISK = ROW - 1 + len(os.linesep)  # and this many bytes on disk, where Windows writes \r\n


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def rows(path):
    return [json.loads(line)["i"] for line in path.read_text(encoding="utf-8").splitlines()]


def fill(path, n, **kw):
    for i in range(n):
        runlog.append(path, {"i": f"{i:03d}"}, **kw)


def test_the_default_sizes_are_8_mb_and_ten_thousand_rows():
    assert runlog.TRIM_BYTES == 8 * 1024 * 1024 == 8388608
    assert runlog.KEEP_ROWS == 10_000
    defaults = runlog.append.__defaults__
    assert defaults == (8388608, 10_000, None)


def test_a_log_in_folders_that_dont_exist_yet_is_created(tmp_path):
    path = tmp_path / "a" / "b" / "c" / "history.jsonl"
    runlog.append(path, {"i": "000"})
    assert rows(path) == ["000"]


def test_a_file_exactly_at_the_trim_size_is_left_alone(tmp_path):
    path = tmp_path / "h.jsonl"
    fill(path, 4, trim_bytes=4 * DISK)  # 4 rows is exactly the trigger, not over it
    assert rows(path) == ["000", "001", "002", "003"]


def test_one_row_over_the_trim_size_keeps_half_of_it_by_default(tmp_path):
    path = tmp_path / "h.jsonl"
    fill(path, 9, trim_bytes=8 * DISK)  # 9 rows passes 8: keep 4 rows' worth (half), the newest
    assert rows(path) == ["005", "006", "007", "008"]


def test_rows_that_add_up_to_exactly_keep_bytes_are_all_kept(tmp_path):
    path = tmp_path / "h.jsonl"
    fill(path, 6, trim_bytes=5 * DISK, keep_bytes=3 * ROW)
    assert rows(path) == ["003", "004", "005"]


def test_one_byte_less_than_three_rows_keeps_two(tmp_path):
    path = tmp_path / "h.jsonl"
    fill(path, 6, trim_bytes=5 * DISK, keep_bytes=3 * ROW - 1)
    assert rows(path) == ["004", "005"]


def test_the_row_count_limit_wins_when_it_is_smaller(tmp_path):
    path = tmp_path / "h.jsonl"
    fill(path, 6, trim_bytes=5 * DISK, keep_rows=2, keep_bytes=100 * ROW)
    assert rows(path) == ["004", "005"]


def test_the_newest_row_is_kept_even_when_it_alone_is_over_keep_bytes(tmp_path):
    path = tmp_path / "h.jsonl"
    fill(path, 3, trim_bytes=10 * DISK)
    runlog.append(path, {"i": "x" * 200}, trim_bytes=2 * DISK, keep_bytes=ROW)
    assert rows(path) == ["x" * 200]

