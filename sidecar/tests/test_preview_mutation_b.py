"""Mutation batch 517, a re-score of preview.py from batches 54 and 154 (docs/quality/mutation-testing.md): a JSON
file's preview, read by pyarrow only when its first 4,096 bytes start an object, and otherwise value by value,
with blank lines passed over and the first line that isn't JSON named."""

from __future__ import annotations

import pytest

from fabric_desktop import preview


def _preview(tmp_path, data: bytes):
    path = tmp_path / "a.jsonl"
    path.write_bytes(data)
    return preview.file_preview(path, "Files/a.jsonl")


def test_a_file_that_starts_with_an_object_is_read_by_pyarrow(tmp_path):
    out = _preview(tmp_path, b'{"t": "2020-01-01 00:00:00"}\n{"t": "2020-01-02 00:00:00"}\n')
    assert out["columns"] == [{"name": "t", "type": "timestamp[s]"}]  # pyarrow's own type, not json's string
    assert out["row_count"] == 2


def test_an_object_after_4096_bytes_of_space_isnt_given_to_pyarrow(tmp_path):
    out = _preview(tmp_path, b"\n" * 4096 + b'{"t": "2020-01-01 00:00:00"}\n')
    assert out["columns"] == [{"name": "t", "type": "string"}]
    assert out["rows"] == [["2020-01-01 00:00:00"]]


def test_values_a_line_pass_over_blank_lines(tmp_path):
    out = _preview(tmp_path, b"1\n\n2\n")
    assert out["rows"] == [[1], [2]] and out["row_count"] == 2


def test_a_first_line_that_isnt_json_is_the_files_error(tmp_path):
    with pytest.raises(ValueError) as caught:
        _preview(tmp_path, b"nope\n{}\n")
    assert str(caught.value) == "The file isn't JSON: Expecting value (line 1, column 1)."


def test_a_later_line_that_isnt_json_is_named_by_its_number(tmp_path):
    with pytest.raises(ValueError) as caught:
        _preview(tmp_path, b"1\n2\nnope\n")
    assert str(caught.value) == "Line 3 of the file isn't JSON: Expecting value (column 1)."
