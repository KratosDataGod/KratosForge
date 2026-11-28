"""Mutation batch 413: academy/workspace.py. Tests for the survivors of its first score (see
docs/quality/mutation-testing.md)."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from fabric_desktop.academy import workspace as ws


def test_the_caps_are_what_the_docs_say():
    assert ws.MAX_FILE_BYTES == 256 * 1024 * 1024
    assert ws.MAX_JSON_BYTES == 4 * 1024 * 1024
    assert ws.MAX_FILES == 20_000
    assert ws.MAX_ROWS == 5_000_000
    assert ws._WINDOWS is (os.name == "nt")


def test_a_name_surrogate_reparse_point_is_a_link_and_another_reparse_point_isnt():
    plain = os.stat_result((0o100644,) + (0,) * 9)
    assert ws.is_link(SimpleNamespace(st_mode=plain.st_mode, st_reparse_tag=0xA000000C))   # IO_REPARSE_TAG_SYMLINK
    assert ws.is_link(SimpleNamespace(st_mode=plain.st_mode, st_reparse_tag=0x20000000))
    assert not ws.is_link(SimpleNamespace(st_mode=plain.st_mode, st_reparse_tag=0x9000001A))  # a OneDrive placeholder
    assert not ws.is_link(SimpleNamespace(st_mode=plain.st_mode, st_reparse_tag=0x80000017))  # a compressed (WOF) file


def test_a_json_file_of_exactly_4_mb_is_read_and_one_byte_more_isnt(tmp_path):
    body = b'"' + b"x" * (ws.MAX_JSON_BYTES - 2) + b'"'
    (tmp_path / "s.json").write_bytes(body)
    assert len(ws.read_json(tmp_path, "s.json")) == ws.MAX_JSON_BYTES - 2
    (tmp_path / "s.json").write_bytes(body + b" ")
    with pytest.raises(ws.Refused, match="bigger than 4 MB"):
        ws.read_json(tmp_path, "s.json")


def test_on_windows_a_file_swapped_between_the_two_looks_counts_as_linked(tmp_path, monkeypatch):
    f = tmp_path / "a.csv"
    f.write_bytes(b"x\n")
    st = os.lstat(f)
    monkeypatch.setattr(ws, "_WINDOWS", True)
    real = os.fstat
    monkeypatch.setattr(ws.os, "fstat", lambda fd: SimpleNamespace(st_ino=st.st_ino + 1, st_dev=st.st_dev,
                                                                    st_nlink=real(fd).st_nlink))
    assert ws.links(f, st) == 2


def test_on_windows_the_same_file_gives_its_own_link_count(tmp_path, monkeypatch):
    f = tmp_path / "a.csv"
    f.write_bytes(b"x\n")
    st = os.lstat(f)
    monkeypatch.setattr(ws, "_WINDOWS", True)
    monkeypatch.setattr(ws.os, "fstat", lambda fd: SimpleNamespace(st_ino=st.st_ino, st_dev=st.st_dev, st_nlink=1))
    assert ws.links(f, st) == 1


def test_a_pack_folder_that_links_elsewhere_isnt_an_exercise(tmp_path):
    (tmp_path / "academy").mkdir()
    (tmp_path / "elsewhere" / "E1").mkdir(parents=True)
    try:
        os.symlink(tmp_path / "elsewhere", tmp_path / "academy" / "p1", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account can't make symbolic links")
    with pytest.raises(ws.Refused, match="Not an Academy exercise"):
        ws.workspace_dir(tmp_path, "p1", "E1")
    assert ws.workspace_dir(tmp_path, "p2", "E1") == (tmp_path / "academy" / "p2" / "E1").resolve()


@pytest.mark.parametrize("rel", ["", "a\x00b", "a" * 401, None, 7])
def test_a_name_that_isnt_a_file_name_is_refused(tmp_path, rel):
    with pytest.raises(ws.Refused, match="Not a file name in the exercise"):
        ws._relative_parts(rel)


def test_a_name_of_400_characters_is_allowed():
    assert ws._relative_parts("a" * 400) == ["a" * 400]


@pytest.mark.parametrize("rel", ["./", "a/../b", ".."])
def test_a_name_with_no_parts_or_a_parent_step_is_refused(rel):
    with pytest.raises(ws.Refused, match="isn't inside the exercise folder"):
        ws._relative_parts(rel)


def test_reading_a_folder_as_a_file_is_refused(tmp_path):
    (tmp_path / "out").mkdir()
    with pytest.raises(ws.Refused, match="out isn't a file"):
        ws.read_bytes(tmp_path, "out")


def test_listing_a_file_as_a_folder_is_refused(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"x\n")
    with pytest.raises(ws.Refused, match="a.csv isn't a folder"):
        ws.files_under(tmp_path, "a.csv")


def test_a_folder_with_no_files_of_the_format_is_refused(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "a.csv").write_bytes(b"x\n1\n")
    con = ws.connect(tmp_path)
    try:
        with pytest.raises(ws.Refused, match="out has no .parquet files"):
            ws.load_table(con, tmp_path, "out", "t")
        assert ws.load_table(con, tmp_path, "out", "t", "csv") == 1
    finally:
        con.close()


def test_on_windows_a_file_that_cant_be_opened_is_refused(tmp_path, monkeypatch):
    f = tmp_path / "a.csv"
    f.write_bytes(b"x\n")
    st = os.lstat(f)
    monkeypatch.setattr(ws, "_WINDOWS", True)

    def denied(*_a, **_k):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(ws.os, "open", denied)
    with pytest.raises(ws.Refused, match="a.csv can't be read: Access is denied"):
        ws.links(f, st)


def test_a_missing_exercise_folder_says_start_again(tmp_path):
    with pytest.raises(ws.Refused, match="isn't there; start the exercise again"):
        ws.inside(tmp_path / "gone", "a.csv")


def test_a_path_through_a_file_is_refused(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"x\n")
    with pytest.raises(ws.Refused, match="a.csv/x"):
        ws.inside(tmp_path, "a.csv/x")


def test_a_link_inside_a_listed_folder_is_refused(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "real.csv").write_bytes(b"x\n")
    try:
        os.symlink(tmp_path / "real.csv", tmp_path / "out" / "l.csv")
    except (OSError, NotImplementedError):
        pytest.skip("this account can't make symbolic links")
    with pytest.raises(ws.Refused, match="out/l.csv is a link"):
        ws.files_under(tmp_path, "out")


def test_a_folder_of_exactly_max_files_is_listed_and_one_more_isnt(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "MAX_FILES", 3)
    (tmp_path / "out").mkdir()
    for i in range(3):
        (tmp_path / "out" / f"{i}.csv").write_bytes(b"x\n")
    assert len(ws.files_under(tmp_path, "out")) == 3
    (tmp_path / "out" / "3.csv").write_bytes(b"x\n")
    with pytest.raises(ws.Refused, match="out has more than 3 files"):
        ws.files_under(tmp_path, "out")


def test_a_file_of_exactly_the_limit_is_read_and_the_message_names_the_limit_in_mb(tmp_path):
    limit = 3 * 1024 * 1024
    (tmp_path / "a.bin").write_bytes(b"x" * limit)
    assert len(ws.read_bytes(tmp_path, "a.bin", limit)) == limit
    (tmp_path / "a.bin").write_bytes(b"x" * (limit + 1))
    with pytest.raises(ws.Refused, match=r"^a.bin is bigger than 3 MB; an exercise answer is smaller\.$"):
        ws.read_bytes(tmp_path, "a.bin", limit)


def test_a_table_file_over_256_mb_is_refused_before_it_is_read(tmp_path):
    with open(tmp_path / "big.csv", "wb") as fh:
        fh.truncate(ws.MAX_FILE_BYTES + 1)   # sparse: nothing is written
    con = ws.connect(tmp_path)
    try:
        with pytest.raises(ws.Refused, match=r"^big.csv is bigger than 256 MB; an exercise answer is smaller\.$"):
            ws.load_table(con, tmp_path, "big.csv", "t", "csv")
    finally:
        con.close()


def test_a_table_file_of_exactly_the_size_limit_is_read(tmp_path, monkeypatch):
    (tmp_path / "a.csv").write_bytes(b"x\n1\n2\n")
    monkeypatch.setattr(ws, "MAX_FILE_BYTES", 6)
    con = ws.connect(tmp_path)
    try:
        assert ws.load_table(con, tmp_path, "a.csv", "t", "csv") == 2
        monkeypatch.setattr(ws, "MAX_FILE_BYTES", 5)
        with pytest.raises(ws.Refused, match="bigger than"):
            ws.load_table(con, tmp_path, "a.csv", "t", "csv")
    finally:
        con.close()


def test_a_table_of_exactly_max_rows_loads_and_one_more_doesnt(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"x\n1\n2\n")
    con = ws.connect(tmp_path)
    try:
        assert ws.load_table(con, tmp_path, "a.csv", "t", "csv", max_rows=2) == 2
        with pytest.raises(ws.Refused, match="a.csv has more than 1 rows"):
            ws.load_table(con, tmp_path, "a.csv", "t", "csv", max_rows=1)
    finally:
        con.close()
