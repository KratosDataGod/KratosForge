"""academy/workspace.py at its edges (mutation testing, batch 393).

test_academy_validators.py refuses paths, links and big files by the kind of answer. These pin the limits
(each at its value, with small stand-ins where a real one would be hundreds of megabytes), every refusal's
words, and the Windows link count read through an open handle, which runs here with ``_WINDOWS`` set.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from fabric_desktop.academy import workspace as ws

MB = 1024 * 1024


@pytest.fixture
def work(tmp_path: Path) -> Path:
    folder = ws.workspace_dir(tmp_path, "dp-700", "E37")
    folder.mkdir(parents=True)
    return folder


def symlink_or_skip(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links need a privilege on this PC")


def test_the_limits():
    assert (ws.MAX_FILE_BYTES, ws.MAX_JSON_BYTES, ws.MAX_FILES, ws.MAX_ROWS) == (256 * MB, 4 * MB, 20_000, 5_000_000)


# ---- links


def test_a_reparse_point_is_a_link_only_with_the_name_surrogate_bit():
    regular = stat.S_IFREG | 0o644
    assert ws.is_link(SimpleNamespace(st_mode=regular, st_reparse_tag=1)) is False
    assert ws.is_link(SimpleNamespace(st_mode=regular, st_reparse_tag=0xA000000C)) is True   # a symbolic link's tag
    assert ws.is_link(SimpleNamespace(st_mode=regular, st_reparse_tag=0x9000001A)) is False  # a cloud file in place


def test_off_windows_the_link_count_comes_from_lstat_without_opening_the_file(work, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows reads it through a handle")
    f = work / "a.txt"
    f.write_text("x", encoding="utf-8")

    def no_open(*a, **k):
        raise OSError(13, "not allowed")
    monkeypatch.setattr(ws.os, "open", no_open)
    assert ws.links(f, os.lstat(f)) == 1


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(ws, "_WINDOWS", True)
    return monkeypatch


def test_on_windows_the_link_count_is_read_through_a_read_only_handle(work, windows):
    f = work / "a.txt"
    f.write_text("x", encoding="utf-8")
    flags = []
    real = os.open
    windows.setattr(ws.os, "open", lambda p, fl, *a: flags.append(fl) or real(p, fl, *a))
    assert ws.links(f, os.lstat(f)) == 1
    assert flags and flags[0] & (os.O_WRONLY | os.O_RDWR) == 0


def test_on_windows_a_file_swapped_between_the_two_looks_counts_as_linked(work, windows):
    a, b = work / "a.txt", work / "b.txt"
    a.write_text("x", encoding="utf-8")
    b.write_text("y", encoding="utf-8")
    assert ws.links(a, os.lstat(b)) == 2


def test_on_windows_a_file_that_cant_be_opened_is_refused(work, windows):
    with pytest.raises(ws.Refused, match="gone.txt can't be read: "):
        ws.links(work / "gone.txt", os.lstat(work))


# ---- names and paths


def test_a_workspace_reached_through_a_linked_pack_folder_is_refused(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "E37").mkdir(parents=True)
    (tmp_path / "academy").mkdir()
    symlink_or_skip(elsewhere, tmp_path / "academy" / "dp-700")
    with pytest.raises(ws.Refused, match="Not an Academy exercise."):
        ws.workspace_dir(tmp_path, "dp-700", "E37")


@pytest.mark.parametrize("rel", [None, "", "a\x00b", "x" * 401])
def test_a_name_that_isnt_a_plain_short_text_is_refused(work, rel):
    with pytest.raises(ws.Refused, match="Not a file name in the exercise."):
        ws.inside(work, rel, must_exist=False)


def test_a_name_of_400_characters_is_fine(work):
    rel = "./" * 199 + "ab"
    assert len(rel) == 400 and ws.inside(work, rel, must_exist=False) == work / "ab"


@pytest.mark.parametrize("rel", ["./", "a/../x"])
def test_a_name_with_no_parts_or_a_parent_step_is_refused_even_when_it_neednt_exist(work, rel):
    with pytest.raises(ws.Refused, match="isn't inside the exercise folder."):
        ws.inside(work, rel, must_exist=False)


def test_a_missing_workspace_says_to_start_again(tmp_path):
    with pytest.raises(ws.Refused, match="The exercise folder isn't there; start the exercise again."):
        ws.inside(tmp_path / "nope", "a.txt")


def test_a_path_through_a_file_cant_be_read(work):
    (work / "f.txt").write_text("x", encoding="utf-8")
    # Windows reports a path through a file as not found; POSIX says it's not a folder.
    msg = "f.txt/x isn't there." if os.name == "nt" else "f.txt/x can't be read: "
    with pytest.raises(ws.Refused, match=re.escape(msg)):
        ws.inside(work, "f.txt/x")


# ---- reading files


def test_read_bytes_reads_up_to_its_limit_and_says_the_limit_in_mb(work):
    f = work / "big.bin"
    f.write_bytes(b"x" * (2 * MB))
    assert len(ws.read_bytes(work, "big.bin", 2 * MB)) == 2 * MB
    f.write_bytes(b"x" * (2 * MB + 1))
    with pytest.raises(ws.Refused, match="big.bin is bigger than 2 MB; an exercise answer is smaller."):
        ws.read_bytes(work, "big.bin", 2 * MB)


def test_read_bytes_needs_a_file(work):
    (work / "d").mkdir()
    with pytest.raises(ws.Refused, match="d isn't a file."):
        ws.read_bytes(work, "d")


def test_bad_json_says_its_line(work):
    (work / "s.json").write_text('{\n"a": }', encoding="utf-8")
    with pytest.raises(ws.Refused, match=r"s.json isn't valid JSON \(line 2\)."):
        ws.read_json(work, "s.json")


def test_files_under_needs_a_folder(work):
    (work / "f.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ws.Refused, match="f.txt isn't a folder."):
        ws.files_under(work, "f.txt")


def test_files_under_refuses_a_link_inside(work):
    (work / "d").mkdir()
    (work / "target.txt").write_text("x", encoding="utf-8")
    symlink_or_skip(work / "target.txt", work / "d" / "link.txt")
    with pytest.raises(ws.Refused, match="d/link.txt is a link"):
        ws.files_under(work, "d")


def test_files_under_refuses_a_hard_link_inside(work):
    (work / "d").mkdir()
    (work / "d" / "a.txt").write_text("x", encoding="utf-8")
    try:
        os.link(work / "d" / "a.txt", work / "d" / "b.txt")
    except OSError:
        pytest.skip("hard links aren't available here")
    with pytest.raises(ws.Refused, match=r"d/a.txt is a hard link|d/b.txt is a hard link"):
        ws.files_under(work, "d")


def test_files_under_allows_max_files_and_refuses_one_more(work, monkeypatch):
    monkeypatch.setattr(ws, "MAX_FILES", 2)
    (work / "d").mkdir()
    for n in (1, 2):
        (work / "d" / f"{n}.txt").write_text("x", encoding="utf-8")
    assert ws.files_under(work, "d") == ["d/1.txt", "d/2.txt"]
    (work / "d" / "3.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ws.Refused, match="d has more than 2 files; an exercise answer has fewer."):
        ws.files_under(work, "d")


# ---- load_table


def parquet(path: Path, rows: int) -> None:
    con = duckdb.connect()
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT range AS x FROM range({rows})) TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()


def test_a_folder_of_parquet_files_loads(work):
    parquet(work / "t" / "part-0.parquet", 2)
    parquet(work / "t" / "part-1.parquet", 1)
    con = ws.connect(work)
    assert ws.load_table(con, work, "t", "t") == 3


def test_a_folder_without_parquet_files_is_refused(work):
    (work / "t").mkdir()
    (work / "t" / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ws.Refused, match="t has no .parquet files."):
        ws.load_table(ws.connect(work), work, "t", "t")


def test_load_table_allows_max_rows(work):
    parquet(work / "t.parquet", 3)
    assert ws.load_table(ws.connect(work), work, "t.parquet", "t", max_rows=3) == 3


def test_load_table_refuses_a_file_past_its_size_and_says_it_in_mb(work, monkeypatch):
    f = work / "t.parquet"
    parquet(f, 3)
    size = f.stat().st_size
    monkeypatch.setattr(ws, "MAX_FILE_BYTES", size)
    assert ws.load_table(ws.connect(work), work, "t.parquet", "t") == 3
    monkeypatch.setattr(ws, "MAX_FILE_BYTES", 2 * MB)
    f.write_bytes(f.read_bytes() + b"\0" * (2 * MB))
    with pytest.raises(ws.Refused, match="t.parquet is bigger than 2 MB; an exercise answer is smaller."):
        ws.load_table(ws.connect(work), work, "t.parquet", "t")
