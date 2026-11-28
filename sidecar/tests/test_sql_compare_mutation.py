"""Mutation tests for Compare data on the SQL page (editor parity S34): sql_compare.py and the options it
asks of verify.compare (repeated_keys, status, full, export). Each test pins one behaviour a one-token
change would break. No worker processes: a fake ``runs`` hands back small pyarrow tables."""

from __future__ import annotations

import csv
from decimal import Decimal

import pyarrow as pa
import pytest

from fabric_desktop import sql_compare, verify


class Runs:
    """sql_exec.SqlExecutions as sql_compare uses it: table(run_id) and _downloads()."""

    def __init__(self, folder=None, **tables):
        self.tables, self.folder = tables, folder

    def table(self, run_id):
        return {"id": run_id}, self.tables[run_id]

    def _downloads(self):
        return self.folder


def runs_of(a, b, folder=None):
    return Runs(folder, a=pa.table(a), b=pa.table(b))


A = {"id": [1, 2, 3, 4], "amount": [10.0, 20.0, 30.0, 40.0], "s": ["p", "q", "r", "s"]}
B = {"id": [1, 2, 3, 5], "amount": [10.0, 20.5, 30.0, 50.0], "s": ["p", "q", "R", "t"]}


# ---- _rules


def test_up_to_max_keys_are_accepted_and_one_more_is_refused():
    names = [f"k{i}" for i in range(sql_compare.MAX_KEYS)]
    assert sql_compare._rules(names, None)["keys"] == names
    with pytest.raises(ValueError, match=f"^Match rows on up to {sql_compare.MAX_KEYS} columns, each named.$"):
        sql_compare._rules([*names, "extra"], None)


@pytest.mark.parametrize("keys", [[""], ["  "], [1], ("id",), "id", {"id": 1}])
def test_a_key_is_a_list_of_named_columns(keys):
    with pytest.raises(ValueError, match="^Match rows on up to"):
        sql_compare._rules(keys, None)


def test_no_key_is_an_empty_key_and_names_are_trimmed():
    assert sql_compare._rules(None, None)["keys"] == []
    assert sql_compare._rules([], None)["keys"] == []
    assert sql_compare._rules([" id ", "b"], None)["keys"] == ["id", "b"]


@pytest.mark.parametrize("keys", [["id", "id"], ["id", "ID"], ["id", " id"]])
def test_a_key_given_twice_is_refused_whatever_its_case_or_spaces(keys):
    with pytest.raises(ValueError, match="^A column is in the key twice.$"):
        sql_compare._rules(keys, None)


def test_rules_compare_exactly_with_a_page_of_examples():
    r = sql_compare._rules(["id"], None)
    assert r == {"keys": ["id"], "abs": 0, "rel": 0, "columns": {}, "examples": sql_compare.PAGE_ROWS}
    assert verify.rules(r)["rel"] == 0.0, "no default relative tolerance: other columns must be equal"


def test_a_tolerance_is_an_abs_override_for_its_trimmed_column():
    r = sql_compare._rules(["id"], {" amount ": 0.01, "n": 2, "t": "0.5"})
    assert r["columns"] == {"amount": {"abs": 0.01}, "n": {"abs": 2}, "t": {"abs": "0.5"}}


@pytest.mark.parametrize("value", [True, False, None, [0.1], {"abs": 1}])
def test_a_tolerance_that_is_not_a_number_is_refused(value):
    with pytest.raises(ValueError, match="^The tolerance for amount is a number 0 or more, like 0.01.$"):
        sql_compare._rules([], {"amount": value})


@pytest.mark.parametrize("name", ["", "  ", 1])
def test_a_tolerance_names_its_column(name):
    with pytest.raises(ValueError, match="^A tolerance names its column.$"):
        sql_compare._rules([], {name: 0.1})


def test_up_to_max_tolerances_are_accepted_and_one_more_is_refused():
    many = {f"c{i}": 1 for i in range(sql_compare.MAX_TOLERANCES)}
    assert len(sql_compare._rules([], many)["columns"]) == sql_compare.MAX_TOLERANCES
    with pytest.raises(ValueError, match=f"for up to {sql_compare.MAX_TOLERANCES} columns"):
        sql_compare._rules([], many | {"one_more": 1})
    with pytest.raises(ValueError, match="^A tolerance is a number column"):
        sql_compare._rules([], [("amount", 1)])


@pytest.mark.parametrize("value, message", [(-0.01, "0 or more"), (float("inf"), "0 or more"), (float("nan"), "0 or more"),
                                            ("abc", "is a number")])
def test_a_negative_or_endless_tolerance_is_refused_when_compared(value, message):
    runs = runs_of(A, B)
    with pytest.raises(ValueError, match=message):
        sql_compare.compare(runs, "a", "b", keys=["id"], tolerance={"amount": value})


def test_a_zero_tolerance_is_allowed_and_changes_nothing():
    out = sql_compare.compare(runs_of(A, B), "a", "b", keys=["id"], tolerance={"amount": 0})
    assert out["counts"]["changed"] == 2


# ---- _check_tolerances


def test_a_tolerance_is_for_a_number_column_in_both_a_and_b():
    ta = pa.table({"id": [1], "n": [1.0], "i": [1], "d": pa.array([Decimal("1.0")], pa.decimal128(5, 1)), "s": ["x"]})
    tb = pa.table({"ID": [1], "N": [1.0], "I": [1], "D": pa.array([Decimal("1.0")], pa.decimal128(5, 1)), "s": ["x"],
                   "only_b": [1.0]})
    sql_compare._check_tolerances(sql_compare._rules(["id"], {"n": 1, "I": 1, "d": 1}), ta, tb)  # case doesn't matter
    for name in ("s", "only_b", "nope"):
        with pytest.raises(ValueError, match=f"^A tolerance is for a number column in both A and B, and {name} isn't one.$"):
            sql_compare._check_tolerances(sql_compare._rules(["id"], {name: 1}), ta, tb)


def test_a_number_on_one_side_only_takes_no_tolerance():
    ta, tb = pa.table({"id": [1], "n": [1.0]}), pa.table({"id": [1], "n": ["1.0"]})
    with pytest.raises(ValueError, match="n isn't one"):
        sql_compare._check_tolerances(sql_compare._rules(["id"], {"n": 1}), ta, tb)
    with pytest.raises(ValueError, match="n isn't one"):
        sql_compare._check_tolerances(sql_compare._rules(["id"], {"n": 1}), tb, ta)


def test_a_key_column_takes_no_tolerance_whatever_its_case():
    t = pa.table({"id": [1], "n": [1.0]})
    with pytest.raises(ValueError, match="^ID matches rows, so it can't have a tolerance.$"):
        sql_compare._check_tolerances(sql_compare._rules(["id"], {"ID": 1}), t, t)


def test_a_tolerance_on_whole_numbers_makes_close_ones_the_same():
    # The page offers a tolerance on an integer column (common[].number), so it must apply there too.
    runs = runs_of({"id": [1, 2, 3], "q": [5, 7, 10]}, {"id": [1, 2, 3], "q": [6, 9, 10]})
    assert sql_compare.compare(runs, "a", "b", keys=["id"])["counts"]["changed"] == 2
    out = sql_compare.compare(runs, "a", "b", keys=["id"], tolerance={"q": 1})
    assert out["counts"] == {"same": 2, "only_a": 0, "only_b": 0, "changed": 1}
    assert out["examples"][0]["columns"] == {"q": {"a": 7, "b": 9}}
    assert sql_compare.compare(runs, "a", "b", keys=["id"], tolerance={"Q": 2})["result"] == "same"


def test_whole_numbers_without_a_tolerance_of_their_own_stay_exact_for_verifys_other_callers():
    big = 10**12
    a, b = pa.table({"id": [1], "q": [big]}), pa.table({"id": [1], "q": [big + 1]})
    assert verify.compare(a, b, {"keys": ["id"]})["result"] == "differs", "rel 1e-9 never applies to whole numbers"
    assert verify.compare(a, b, {"keys": ["id"], "columns": {"q": {"abs": 1}}})["result"] == "match"
    assert verify.compare(a, b, {"keys": ["id"], "columns": {"q": {"abs": 0, "rel": 0}}})["result"] == "differs"


# ---- status names


def test_the_page_names_map_to_verifys_and_back():
    assert sql_compare._STATUS == {"only_a": "only_pc", "only_b": "only_fabric", "changed": "changed"}
    assert sql_compare._BACK == {"only_pc": "only_a", "only_fabric": "only_b", "changed": "changed"}


@pytest.mark.parametrize("status, keys", [("only_a", [2, 4]), ("only_b", [5]), ("changed", [2, 3])])
def test_each_tile_lists_its_own_kind_on_the_side_it_names(status, keys):
    a = {"id": [1, 2, 3, 4], "v": [1, 2, 3, 4]}
    b = {"id": [1, 3, 5], "v": [1, 30, 5]}
    if status == "changed":
        a, b = {"id": [1, 2, 3], "v": [1, 2, 3]}, {"id": [1, 2, 3], "v": [1, 20, 30]}
    out = sql_compare.compare(runs_of(a, b), "a", "b", keys=["id"], status=status)
    assert [e["key"]["id"] for e in out["examples"]] == keys
    assert {e["status"] for e in out["examples"]} == {status}
    assert out["status"] == status and out["listed"] == len(keys)


def test_a_whole_row_example_says_which_side_and_how_many_times():
    out = sql_compare.compare(runs_of({"v": [1, 1, 1, 2]}, {"v": [1, 3]}), "a", "b")
    assert out["examples"] == [{"status": "only_a", "row": {"v": 1}, "times": 2},
                               {"status": "only_a", "row": {"v": 2}, "times": 1},
                               {"status": "only_b", "row": {"v": 3}, "times": 1}]
    only_b = sql_compare.compare(runs_of({"v": [1, 1, 1, 2]}, {"v": [1, 3]}), "a", "b", status="only_b")
    assert only_b["examples"] == [{"status": "only_b", "row": {"v": 3}, "times": 1}] and only_b["listed"] == 1


def test_a_changed_example_gives_both_sides_as_a_and_b():
    out = sql_compare.compare(runs_of(A, B), "a", "b", keys=["id"], status="changed")
    two = out["examples"][0]
    assert two == {"status": "changed", "key": {"id": 2}, "columns": {"amount": {"a": 20.0, "b": 20.5}},
                   "same": {"s": {"a": "q", "b": "q"}}}


def test_an_unknown_status_is_refused_before_anything_is_read():
    class Never:
        def table(self, run_id):
            raise AssertionError("read")

    for status in ("only_pc", "only_fabric", "", "Changed"):
        with pytest.raises(ValueError, match="^status is one of only_a, only_b, changed.$"):
            sql_compare.compare(Never(), "a", "b", status=status)


# ---- counts


def test_same_is_the_rows_of_a_less_only_a_and_changed_when_keyed():
    out = sql_compare.compare(runs_of(A, B), "a", "b", keys=["id"])
    # A: 4 rows. 4 only in A; 2 and 3 changed; 1 same. B's 5 is only in B and isn't one of A's rows.
    assert out["counts"] == {"same": 1, "only_a": 1, "only_b": 1, "changed": 2}
    assert out["rows"] == {"a": 4, "b": 4}


def test_same_is_the_rows_of_a_less_only_a_on_whole_rows():
    out = sql_compare.compare(runs_of({"v": [1, 1, 2, 3, 4]}, {"v": [1, 2, 2, 5]}), "a", "b")
    assert out["counts"] == {"same": 2, "only_a": 3, "only_b": 2, "changed": None}


def test_changed_is_none_on_whole_rows_and_a_number_when_keyed():
    whole = sql_compare.compare(runs_of(A, A), "a", "b")
    assert whole["counts"]["changed"] is None and whole["method"] == "whole rows"
    keyed = sql_compare.compare(runs_of(A, A), "a", "b", keys=["id"])
    assert keyed["counts"]["changed"] == 0 and keyed["method"] == "keyed"
    assert keyed["result"] == whole["result"] == "same"


def test_listed_is_every_differing_row_or_the_kind_asked_for():
    runs = runs_of(A, B)
    assert sql_compare.compare(runs, "a", "b", keys=["id"])["listed"] == 4  # 1 + 1 + 2, never same's 1
    assert sql_compare.compare(runs, "a", "b", keys=["id"], status="only_a")["listed"] == 1
    assert sql_compare.compare(runs, "a", "b", keys=["id"], status="changed")["listed"] == 2
    assert sql_compare.compare(runs, "a", "b")["listed"] == 6  # whole rows: 3 only in A and 3 only in B
    assert sql_compare.compare(runs, "a", "b", status="changed")["listed"] == 0, "None counts as 0, not an error"
    assert sql_compare.compare(runs, "a", "b", status="only_b")["listed"] == 3


def test_listed_on_whole_rows_counts_the_lines_the_pages_hold_not_the_rows():
    # A has 1 three times and B none: one line, "only in A x3". The tile counts 3 rows; the pages hold 1 line,
    # so the page offers no Next page that would be empty.
    out = sql_compare.compare(runs_of({"v": [1, 1, 1, 2]}, {"v": [2, 3]}), "a", "b")
    assert out["counts"]["only_a"] == 3 and out["counts"]["only_b"] == 1
    assert len(out["examples"]) == 2 and out["listed"] == 2
    assert sql_compare.compare(runs_of({"v": [1, 1, 1, 2]}, {"v": [2, 3]}), "a", "b", status="only_a")["listed"] == 1
    # No column in both: the row counts differ, but there's no row to list.
    none = sql_compare.compare(runs_of({"x": [1, 2, 3]}, {"y": [1]}), "a", "b")
    assert none["counts"]["only_a"] == 2 and none["examples"] == [] and none["listed"] == 0


def test_verify_counts_the_distinct_rows_of_each_kind_on_whole_rows():
    out = verify.compare(pa.table({"v": [1, 1, 1, 2, 4]}), pa.table({"v": [2, 3, 3, 5]}))
    assert out["counts"] == {"only_pc": 4, "only_fabric": 3}
    assert out["distinct"] == {"only_pc": 2, "only_fabric": 2}
    assert "distinct" not in verify.compare(pa.table({"k": [1]}), pa.table({"k": [2]}), {"keys": ["k"]})


def test_a_page_starts_at_the_offset():
    a = {"id": list(range(120)), "v": [0] * 120}
    b = {"id": list(range(120)), "v": [1] * 120}
    runs = runs_of(a, b)
    first = sql_compare.compare(runs, "a", "b", keys=["id"])
    assert len(first["examples"]) == sql_compare.PAGE_ROWS and first["offset"] == 0
    last = sql_compare.compare(runs, "a", "b", keys=["id"], offset=100)
    assert [e["key"]["id"] for e in last["examples"]] == list(range(100, 120)) and last["offset"] == 100
    assert last["listed"] == 120


# ---- export


def test_export_names_where_then_the_key_then_a_and_b_values():
    schema = pa.schema([("status", pa.string()), ("id", pa.int64()), ("A: v", pa.int64()), ("B: v", pa.int64())])
    assert sql_compare._export_schema(schema, True).names == ["Where", "id", "A: v", "B: v"]


def test_export_names_where_and_times_then_the_row_with_no_key():
    schema = pa.schema([("status", pa.string()), ("times", pa.int64()), ("a", pa.int64()), ("b", pa.string())])
    assert sql_compare._export_schema(schema, False).names == ["Where", "Times", "a", "b"]


def test_export_gives_a_clashing_column_2():
    keyed = pa.schema([("status", pa.string()), ("WHERE", pa.int64()), ("A: v", pa.int64()), ("B: v", pa.int64())])
    assert sql_compare._export_schema(keyed, True).names == ["Where", "WHERE_2", "A: v", "B: v"]
    whole = pa.schema([("status", pa.string()), ("times", pa.int64()), ("times", pa.int64()), ("where", pa.int64())])
    assert sql_compare._export_schema(whole, False).names == ["Where", "Times", "times_2", "where_2"]
    assert sql_compare._export_schema(whole, False).types == whole.types, "names change, types don't"


def test_where_says_the_page_names_and_leaves_any_other_value_alone():
    column = pa.array(["only_pc", "only_fabric", "changed", "only_pc_x", "x_only_fabric", "ONLY_PC", None])
    assert sql_compare._where(column).to_pylist() == ["only_a", "only_b", "changed", "only_pc_x", "x_only_fabric",
                                                      "ONLY_PC", None]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_export_writes_where_as_the_page_says_it(tmp_path):
    out = sql_compare.export(runs_of(A, B, tmp_path), "a", "b", keys=["id"], name="t")
    rows = read_csv(out["path"])
    assert rows[0] == ["Where", "id", "A: amount", "B: amount", "A: s", "B: s"]
    assert [r[:2] for r in rows[1:]] == [["changed", "2"], ["changed", "3"], ["only_a", "4"], ["only_b", "5"]]
    assert out["rows"] == 4 and out["notes"] == []


def test_export_with_no_key_writes_times(tmp_path):
    out = sql_compare.export(runs_of({"v": [1, 1, 1]}, {"v": [2]}, tmp_path), "a", "b")
    assert read_csv(out["path"]) == [["Where", "Times", "v"], ["only_a", "3", "1"], ["only_b", "1", "2"]]
    assert out["rows"] == 2


def test_export_refuses_when_nothing_differs_and_leaves_no_file(tmp_path):
    for keys in (["id"], None):
        with pytest.raises(ValueError, match="^No rows differ, so there are none to export.$"):
            sql_compare.export(runs_of(A, A, tmp_path), "a", "b", keys=keys)
    assert list(tmp_path.iterdir()) == []


def test_export_refuses_a_checksum_only_compare_and_leaves_no_file(tmp_path, monkeypatch):
    rules = sql_compare._rules
    monkeypatch.setattr(sql_compare, "_rules", lambda keys, tolerance: rules(keys, tolerance) | {"limit_rows": 2})
    with pytest.raises(ValueError, match="^A or B has too many rows to list the rows that differ"):
        sql_compare.export(runs_of(A, B, tmp_path), "a", "b", keys=["id"])
    assert list(tmp_path.iterdir()) == []
    out = sql_compare.compare(runs_of(A, B), "a", "b", keys=["id"])
    assert out["level"] == "fingerprint" and out["counts"] is None and out["examples"] == []
    assert out["result"] == "differs" and "listed" not in out


def test_a_checksum_only_compare_still_says_same():
    out = sql_compare._shape(verify.compare(pa.table(A), pa.table(A), {"limit_rows": 2}), pa.table(A), pa.table(A),
                             {}, {}, None, 0.0)
    assert out["result"] == "same" and out["level"] == "fingerprint"


# ---- verify.compare's options


def keyed_pair():
    a = pa.table({"k": [1, 2, 3, 4], "v": [1, 2, 3, 4], "w": ["x", "x", "x", "x"]})
    b = pa.table({"k": [1, 3, 4, 5], "v": [1, 30, 4, 5], "w": ["x", "x", "y", "x"]})
    return a, b


@pytest.mark.parametrize("status, expected", [
    (None, [(2, "only_pc"), (3, "changed"), (4, "changed"), (5, "only_fabric")]),
    ("only_pc", [(2, "only_pc")]), ("only_fabric", [(5, "only_fabric")]),
    ("changed", [(3, "changed"), (4, "changed")])])
def test_verify_status_lists_one_kind_when_keyed(status, expected):
    a, b = keyed_pair()
    out = verify.compare(a, b, {"keys": ["k"]}, status=status)
    assert [(e["key"]["k"], e["status"]) for e in out["examples"]] == expected
    assert out["counts"] == {"only_pc": 1, "only_fabric": 1, "changed": 2}, "status pages; it never changes the counts"


@pytest.mark.parametrize("status, expected", [
    (None, [(1, "only_pc", 2), (2, "only_fabric", 1), (3, "only_pc", 1)]),
    ("only_pc", [(1, "only_pc", 2), (3, "only_pc", 1)]), ("only_fabric", [(2, "only_fabric", 1)]), ("changed", [])])
def test_verify_status_lists_one_kind_on_whole_rows(status, expected):
    a, b = pa.table({"v": [1, 1, 1, 3]}), pa.table({"v": [1, 2]})
    out = verify.compare(a, b, status=status)
    assert [(e["row"]["v"], e["status"], e["times"]) for e in out["examples"]] == expected
    assert out["counts"] == {"only_pc": 3, "only_fabric": 1}


def test_verify_refuses_an_unknown_status():
    t = pa.table({"v": [1]})
    for status in ("only_a", "same", ""):
        with pytest.raises(ValueError, match="^status is one of only_pc, only_fabric, changed.$"):
            verify.compare(t, t, status=status)


def test_full_gives_the_columns_that_are_the_same_and_only_when_asked():
    a, b = keyed_pair()
    plain = verify.compare(a, b, {"keys": ["k"]}, status="changed")
    assert all("same" not in e for e in plain["examples"])
    full = verify.compare(a, b, {"keys": ["k"]}, status="changed", full=True)
    assert full["examples"] == [
        {"key": {"k": 3}, "status": "changed", "columns": {"v": {"pc": 3, "fabric": 30}}, "same": {"w": {"pc": "x", "fabric": "x"}}},
        {"key": {"k": 4}, "status": "changed", "columns": {"w": {"pc": "x", "fabric": "y"}}, "same": {"v": {"pc": 4, "fabric": 4}}}]


def test_repeated_keys_is_whole_rows_or_stop():
    t = pa.table({"k": [1], "v": [1]})
    for bad in ("", "Stop", "keyed", None):
        with pytest.raises(ValueError, match="^repeated_keys is whole_rows or stop.$"):
            verify.compare(t, t, {"keys": ["k"]}, repeated_keys=bad)
    assert verify.compare(t, t, {"keys": ["k"]}, repeated_keys="stop")["method"] == "keyed"
    assert verify.compare(t, t, {"keys": ["k"]}, repeated_keys="whole_rows")["method"] == "keyed"


@pytest.mark.parametrize("a, b, where", [([1, 1], [1], "A"), ([1], [1, 1], "B"), ([1, 1], [1, 1], "both sides")])
def test_stop_names_the_side_with_the_repeated_key(a, b, where):
    with pytest.raises(verify.RepeatedKey, match=f"is repeated in {where}, so"):
        verify.compare(pa.table({"k": a}), pa.table({"k": b}), {"keys": ["k"]}, repeated_keys="stop")


def test_a_repeated_key_matches_whole_rows_unless_told_to_stop():
    a, b = pa.table({"k": [1, 1], "v": [1, 2]}), pa.table({"k": [1, 1], "v": [1, 2]})
    out = verify.compare(a, b, {"keys": ["k"]})
    assert out["method"] == "whole rows" and out["result"] == "match" and out["duplicates"] == {"pc": 1, "fabric": 1}


def test_export_hands_over_every_differing_row_not_a_page():
    a = pa.table({"k": list(range(130)), "v": [0] * 130})
    b = pa.table({"k": list(range(130)), "v": [1] * 130})
    got = []
    out = verify.compare(a, b, {"keys": ["k"], "examples": 5}, export=lambda reader: got.append(reader.read_all()))
    assert len(out["examples"]) == 5 and len(got) == 1
    assert got[0].num_rows == 130 and got[0].column_names == ["status", "k", "A: v", "B: v"]
    assert set(got[0].column("status").to_pylist()) == {"changed"}
