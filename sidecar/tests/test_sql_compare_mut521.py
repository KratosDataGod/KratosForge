"""Mutation batch 521: sql_compare.py, Compare data on the SQL page. The two finished runs are stood in for by
tables held in memory (the module only asks ``runs.table(id)`` for each side), so no worker process starts.
Each test pins a behavior a surviving mutant changed."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_desktop import sql_compare, verify


class Runs:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, run_id):
        return {"id": run_id}, self.tables[run_id]


def keyed(n, start=0, amount=1.0):
    return pa.table({"id": list(range(start, start + n)), "amount": [amount] * n})


def test_up_to_eight_key_columns_are_allowed_and_a_ninth_is_refused():
    assert sql_compare._rules([f"k{n}" for n in range(8)], None)["keys"] == [f"k{n}" for n in range(8)]
    with pytest.raises(ValueError, match="Match rows on up to 8 columns, each named"):
        sql_compare._rules([f"k{n}" for n in range(9)], None)


def test_up_to_fifty_tolerances_are_allowed_and_a_fifty_first_is_refused():
    fifty = {f"c{n}": 0.1 for n in range(50)}
    assert len(sql_compare._rules([], fifty)["columns"]) == 50
    with pytest.raises(ValueError, match=r"for up to 50 columns\.$"):
        sql_compare._rules([], fifty | {"c50": 0.1})


@pytest.mark.parametrize("tolerance", [[("amount", 0.01)], "amount=0.01", 0.01])
def test_a_tolerance_that_isnt_a_mapping_is_refused_with_an_example(tolerance):
    with pytest.raises(ValueError, match=r'like \{"amount": 0\.01\}'):
        sql_compare._rules([], tolerance)


def test_a_page_lists_fifty_differing_rows():
    runs = Runs(a=keyed(60), b=keyed(60, amount=2.0))
    out = sql_compare.compare(runs, "a", "b", keys=["id"])
    assert out["counts"]["changed"] == 60 and out["listed"] == 60
    assert len(out["examples"]) == 50
    assert sql_compare._rules(None, None)["examples"] == 50


def test_tables_too_large_to_list_rows_report_no_counts_and_no_examples(monkeypatch):
    real = verify.compare
    monkeypatch.setattr(sql_compare.verify, "compare", lambda ta, tb, rules, *a, **kw: real(ta, tb, rules | {"limit_rows": 2}, *a, **kw))
    runs = Runs(a=keyed(5), b=keyed(5, amount=2.0))
    out = sql_compare.compare(runs, "a", "b", keys=["id"])
    assert out["level"] == "fingerprint" and out["result"] == "differs"
    assert out["counts"] is None and out["examples"] == []
    assert "listed" not in out and "changed_columns" not in out
    assert out["rows"] == {"a": 5, "b": 5} and out["runs"] == {"a": "a", "b": "b"}


@pytest.mark.parametrize("keys", ["id", ("id",), ["id", " "], ["id", 3]])
def test_keys_are_a_list_of_named_columns(keys):
    with pytest.raises(ValueError, match="each named"):
        sql_compare._rules(keys, None)


@pytest.mark.parametrize("tolerance", [{"": 0.1}, {"  ": 0.1}, {1: 0.1}])
def test_a_tolerance_names_its_column(tolerance):
    with pytest.raises(ValueError, match="^A tolerance names its column.$"):
        sql_compare._rules([], tolerance)


@pytest.mark.parametrize("value", [True, False, None, [0.1], {"abs": 0.1}])
def test_a_tolerance_is_a_number(value):
    with pytest.raises(ValueError, match="^The tolerance for amount is a number 0 or more, like 0.01.$"):
        sql_compare._rules([], {"amount": value})


def test_a_tolerance_given_as_text_is_passed_on_with_the_column_name_trimmed():
    assert sql_compare._rules([" id "], {" amount ": "0.5"}) == {
        "keys": ["id"], "abs": 0, "rel": 0, "columns": {"amount": {"abs": "0.5"}}, "examples": 50}


def test_the_keys_and_the_seconds_it_took_are_reported(monkeypatch):
    clock = iter([100.0, 101.23456])
    monkeypatch.setattr(sql_compare.time, "monotonic", lambda: next(clock))
    out = sql_compare.compare(Runs(a=keyed(3), b=keyed(3)), "a", "b", keys=["id"])
    assert out["result"] == "same" and out["keys"] == ["id"] and out["seconds"] == 1.23


def test_rows_compared_whole_count_only_a_and_only_b_and_list_no_changed_rows():
    a = pa.table({"id": [1, 2, 3, 4], "v": ["x", "y", "z", "w"]})
    b = pa.table({"id": [1, 2, 9], "v": ["x", "y", "q"]})
    runs = Runs(a=a, b=b)
    out = sql_compare.compare(runs, "a", "b")
    assert out["counts"] == {"same": 2, "only_a": 2, "only_b": 1, "changed": None}
    assert out["listed"] == 3
    whole = [e for e in out["examples"] if e["status"] == "only_a"]
    assert {e["row"]["id"] for e in whole} == {3, 4} and all(e["times"] == 1 for e in whole)
    assert sql_compare.compare(runs, "a", "b", status="changed")["listed"] == 0
    assert sql_compare.compare(runs, "a", "b", status="only_b")["listed"] == 1


class Exports(Runs):
    def __init__(self, folder, **tables):
        super().__init__(**tables)
        self.folder = folder

    def _downloads(self):
        return self.folder


def test_an_export_makes_its_folder_and_counts_every_row_it_writes(tmp_path):
    runs = Exports(tmp_path / "missing" / "Downloads", a=keyed(3), b=keyed(4, amount=2.0))
    out = sql_compare.export(runs, "a", "b", keys=["id"], name="orders")
    assert out["rows"] == 4 and out["notes"] == []
    assert out["name"].startswith("orders compare ") and out["name"].endswith(".csv")
    assert (tmp_path / "missing" / "Downloads" / out["name"]).is_file()


def test_an_export_that_protects_a_cell_says_so_without_offering_raw_values(tmp_path):
    from fabric_desktop import csv_cells
    a = pa.table({"id": [1], "v": ["=1+1"]})
    b = pa.table({"id": [1], "v": ["plain"]})
    out = sql_compare.export(Exports(tmp_path, a=a, b=b), "a", "b", keys=["id"])
    assert out["notes"] == [csv_cells.note(1, can_keep_raw=False)]


def test_an_export_of_tables_too_large_to_list_rows_is_refused_and_leaves_no_file(tmp_path, monkeypatch):
    real = verify.compare
    monkeypatch.setattr(sql_compare.verify, "compare", lambda ta, tb, rules, *a, **kw: real(ta, tb, rules | {"limit_rows": 2}, *a, **kw))
    with pytest.raises(ValueError, match="too many rows to list the rows that differ"):
        sql_compare.export(Exports(tmp_path, a=keyed(5), b=keyed(5, amount=2.0)), "a", "b", keys=["id"])
    assert list(tmp_path.iterdir()) == []


def test_an_export_whose_path_is_too_long_raises_the_plain_error(tmp_path, monkeypatch):
    plain = OSError("The path is too long for Windows here.")

    def fail(_src, _dst):
        raise OSError(206, "The filename or extension is too long")

    monkeypatch.setattr(sql_compare.jsonfile, "replace", fail)
    monkeypatch.setattr(sql_compare.jsonfile, "path_too_long", lambda exc, path, doing: plain if doing == "exported" else None)
    with pytest.raises(OSError) as got:
        sql_compare.export(Exports(tmp_path, a=keyed(1), b=keyed(1, amount=2.0)), "a", "b", keys=["id"])
    assert got.value is plain
    assert list(tmp_path.iterdir()) == []
