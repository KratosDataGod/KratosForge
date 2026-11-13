"""Mutation testing batch 3: pins for the shared JSON file writer (jsonfile.py) that its own tests left
open. See docs/quality/mutation-testing.md."""

from __future__ import annotations

import logging

import pytest

from fabric_desktop import jsonfile


def test_json_is_written_with_two_space_indents(tmp_path):
    f = tmp_path / "a.json"
    jsonfile.write_json(f, {"k": [1]})
    assert f.read_text(encoding="utf-8") == '{\n  "k": [\n    1\n  ]\n}'


def test_text_and_bytes_make_missing_folders_and_reuse_existing_ones(tmp_path):
    t = tmp_path / "x" / "y" / "a.txt"
    jsonfile.write_text(t, "one")
    jsonfile.write_text(t.with_name("b.txt"), "two")  # the folder exists now
    b = tmp_path / "p" / "q" / "a.bin"
    jsonfile.write_bytes(b, b"\x00\x01")
    jsonfile.write_bytes(b.with_name("b.bin"), b"\x02")
    assert t.read_text(encoding="utf-8") == "one" and b.read_bytes() == b"\x00\x01"
    assert b.with_name("b.bin").read_bytes() == b"\x02"


def test_bytes_that_cant_be_swapped_in_raise_and_leave_no_temporary_file(tmp_path, monkeypatch):
    def refuse(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(jsonfile, "replace", refuse)
    target = tmp_path / "pic.png"
    with pytest.raises(PermissionError, match="in use"):
        jsonfile.write_bytes(target, b"img")
    assert list(tmp_path.iterdir()) == []


def test_a_missing_file_reads_as_the_default(tmp_path):
    default = {"d": 1}
    assert jsonfile.read_json(tmp_path / "none.json", default) is default


def test_pruning_set_aside_copies_without_wal_or_shm_logs_nothing(tmp_path, caplog):
    f = tmp_path / "s.json"
    for _ in range(jsonfile.KEEP_SET_ASIDE + 2):
        f.write_text("{torn", encoding="utf-8")
        jsonfile.set_aside(f)
    copies = list(tmp_path.glob("s.json.corrupt-*"))
    assert len(copies) == jsonfile.KEEP_SET_ASIDE
    caplog.set_level(logging.INFO, logger="fabric_desktop.jsonfile")
    f.write_text("{torn", encoding="utf-8")
    jsonfile.set_aside(f)
    assert not [r for r in caplog.records if "Couldn't remove" in r.getMessage()]
