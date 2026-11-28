"""sql_compare: the rules' refusals, the checksum-only compare of big results (compare and export), and an
export that fails before its file is written. Two in-memory results stand in for the SQL page's runs."""

from __future__ import annotations

import errno

import pyarrow as pa
import pytest

from fabric_desktop import sql_compare


class Runs:
    def __init__(self, tmp_path, tables):
        self.tables, self.folder = tables, tmp_path / "Downloads"

    def table(self, run_id):
        return {"id": run_id}, self.tables[run_id]

    def _downloads(self):
        return self.folder


A = pa.table({"id": [1, 2, 3], "amount": [1.0, 2.0, 3.0]})
B = pa.table({"id": [1, 2, 4], "amount": [1.0, 2.5, 4.0]})


@pytest.fixture()
def runs(tmp_path):
    return Runs(tmp_path, {"a": A, "b": B})


@pytest.mark.parametrize("tolerance, words", [
    ([("amount", 0.01)], "A tolerance is a number column"),
    ({f"c{i}": 1 for i in range(sql_compare.MAX_TOLERANCES + 1)}, "for up to 50 columns"),
    ({"  ": 0.1}, "A tolerance names its column."),
    ({1: 0.1}, "A tolerance names its column."),
    ({"amount": True}, "The tolerance for amount is a number 0 or more"),
    ({"amount": None}, "The tolerance for amount is a number 0 or more"),
])
def test_tolerances_must_be_named_number_columns(runs, tolerance, words):
    with pytest.raises(ValueError, match=words):
        sql_compare.compare(runs, "a", "b", keys=["id"], tolerance=tolerance)


def test_a_tolerance_given_as_text_is_taken():
    assert sql_compare._rules(None, {" amount ": "0.5"})["columns"] == {"amount": {"abs": "0.5"}}


@pytest.fixture()
def checksum_only(monkeypatch):
    real = sql_compare._rules
    monkeypatch.setattr(sql_compare, "_rules", lambda keys, tolerance: real(keys, tolerance) | {"limit_rows": 2})


def test_big_results_are_compared_by_checksum_only(runs, checksum_only):
    out = sql_compare.compare(runs, "a", "b", keys=["id"])
    assert out["level"] == "fingerprint" and out["method"] == "checksum" and out["result"] == "differs"
    assert out["counts"] is None and out["examples"] == []
    assert out["rows"] == {"a": 3, "b": 3}
    same = sql_compare.compare(Runs(runs.folder.parent, {"a": A, "b": A}), "a", "b")
    assert same["result"] == "same" and same["counts"] is None


def test_big_results_cant_be_exported_and_leave_no_file(runs, checksum_only):
    with pytest.raises(ValueError, match="too many rows to list the rows that differ"):
        sql_compare.export(runs, "a", "b", keys=["id"], name="Orders")
    assert list(runs.folder.iterdir()) == []


def test_an_export_whose_path_is_too_long_says_so(runs, monkeypatch):
    def fails(*a, **k):
        raise OSError(errno.ENAMETOOLONG, "File name too long")
    too_long = OSError("The exported file's path is too long")
    monkeypatch.setattr(sql_compare.verify, "compare", fails)
    monkeypatch.setattr(sql_compare.jsonfile, "path_too_long", lambda exc, path, doing: too_long)
    with pytest.raises(OSError) as caught:
        sql_compare.export(runs, "a", "b", keys=["id"])
    assert caught.value is too_long
    assert list(runs.folder.iterdir()) == []  # the reserved name is given back; no .partial was made


def test_discard_skips_files_that_arent_there(tmp_path):
    there = tmp_path / "x.csv"
    there.write_text("x")
    sql_compare._discard(tmp_path / "missing.partial", there)
    assert not there.exists()
