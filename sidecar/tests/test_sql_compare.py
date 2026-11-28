"""Editor parity S34: Compare data on the SQL page (sql_compare.py, verify.py's row diff). Real worker
processes over local Delta folders: A and B are each run as the page runs them, then compared on this PC."""

from __future__ import annotations

import csv

import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import sql_compare, sql_exec, verify
from fabric_desktop.service import Service
from tests.test_sql_exec import limits, wait


@pytest.fixture
def lake(tmp_path):
    write_deltalake(str(tmp_path / "lake" / "orders"), pa.table({
        "order_id": [1, 2, 3, 4, 5], "amount": [10.0, 20.0, 30.0, 40.0, 50.0],
        "status": ["open", "shipped", "shipped", "open", "=cmd|' /C calc'!A0"]}))
    write_deltalake(str(tmp_path / "lake" / "orders_rebuilt"), pa.table({
        "order_id": [1, 2, 3, 4, 6], "amount": [10.004, 21.0, 30.0, None, 60.0],
        "status": ["open", "shipped", "cancelled", "open", "open"]}))
    return {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Test"}


@pytest.fixture
def runs(tmp_path):
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "TOKEN", limits=limits(),
                                     downloads=lambda: tmp_path / "Downloads", test_lake=tmp_path / "lake")
    yield manager
    manager.shutdown()


def run(runs, lake, query, **kw):
    status = wait(runs, runs.start(query, lake, dialect="duckdb", **kw)["id"])
    assert status["state"] == "completed", status["error"]
    return status["id"]


def pair(runs, lake, a="SELECT * FROM orders", b="SELECT * FROM orders_rebuilt"):
    return run(runs, lake, a), run(runs, lake, b)


def test_rows_match_on_the_key_and_each_kind_is_counted(runs, lake):
    a, b = pair(runs, lake)
    out = sql_compare.compare(runs, a, b, keys=["order_id"])
    assert out["result"] == "differs" and out["rows"] == {"a": 5, "b": 5}
    # 1 same; 2 amount, 3 status and 4 amount (NULL) changed; 5 only in A; 6 only in B. 10.004 isn't 10 with no tolerance.
    assert out["counts"] == {"same": 0, "only_a": 1, "only_b": 1, "changed": 4}
    assert set(out["changed_columns"]) == {"amount", "status"}
    changed = {e["key"]["order_id"]: e for e in out["examples"] if e["status"] == "changed"}
    assert changed[3]["columns"] == {"status": {"a": "shipped", "b": "cancelled"}}
    assert changed[3]["same"]["amount"] == {"a": 30.0, "b": 30.0}, "Show all columns has the columns that are the same"
    assert changed[4]["columns"]["amount"] == {"a": 40.0, "b": None}, "a NULL equals only another NULL"
    assert {e["status"] for e in out["examples"]} == {"changed", "only_a", "only_b"}
    assert [c["name"] for c in out["common"]] == ["order_id", "amount", "status"]
    assert [c["number"] for c in out["common"]] == [True, True, False]


def test_a_tolerance_makes_close_numbers_the_same(runs, lake):
    a, b = pair(runs, lake)
    out = sql_compare.compare(runs, a, b, keys=["order_id"], tolerance={"amount": 0.01})
    assert out["counts"] == {"same": 1, "only_a": 1, "only_b": 1, "changed": 3}
    keys = [e["key"]["order_id"] for e in out["examples"] if e["status"] == "changed"]
    assert keys == [2, 3, 4]


def test_the_tiles_list_one_kind_a_page_at_a_time(runs, lake):
    a, b = pair(runs, lake)
    only_b = sql_compare.compare(runs, a, b, keys=["order_id"], status="only_b")
    assert [e["key"] for e in only_b["examples"]] == [{"order_id": 6}] and only_b["listed"] == 1
    changed = sql_compare.compare(runs, a, b, keys=["order_id"], status="changed", offset=2)
    assert [e["key"]["order_id"] for e in changed["examples"]] == [3, 4] and changed["listed"] == 4
    assert sql_compare.compare(runs, a, b, keys=["order_id"])["listed"] == 6


def test_the_same_rows_say_same_with_the_count(runs, lake):
    a, b = pair(runs, lake, b="SELECT * FROM orders ORDER BY order_id DESC")
    out = sql_compare.compare(runs, a, b, keys=["order_id"])
    assert out["result"] == "same" and out["counts"] == {"same": 5, "only_a": 0, "only_b": 0, "changed": 0}
    assert out["examples"] == []


def test_a_repeated_key_stops_the_compare_and_names_the_key(runs, lake):
    a = run(runs, lake, "SELECT * FROM orders UNION ALL SELECT * FROM orders WHERE order_id = 2")
    b = run(runs, lake, "SELECT * FROM orders_rebuilt")
    out = sql_compare.compare(runs, a, b, keys=["order_id"])
    assert "counts" not in out and "The key (order_id) is repeated in A" in out["stopped"]
    assert out["rows"] == {"a": 6, "b": 5}, "the pickers still get the columns"


def test_no_key_compares_whole_rows_and_counts_only_a_and_only_b(runs, lake):
    a, b = pair(runs, lake)
    out = sql_compare.compare(runs, a, b)
    assert out["method"] == "whole rows" and out["counts"]["changed"] is None
    assert out["counts"]["only_a"] == 5 and out["counts"]["only_b"] == 5 and out["counts"]["same"] == 0  # 10.004 isn't 10
    assert all("row" in e and e["status"] in ("only_a", "only_b") for e in out["examples"])
    assert sql_compare.compare(runs, a, b, status="changed")["examples"] == []


def test_decimals_compare_digit_for_digit_with_no_tolerance(runs, lake):
    a = run(runs, lake, "SELECT 1 AS id, CAST(1.50 AS DECIMAL(10,2)) AS v")
    b = run(runs, lake, "SELECT 1 AS id, CAST(1.5000 AS DECIMAL(18,4)) AS v")
    assert sql_compare.compare(runs, a, b, keys=["id"])["result"] == "same"
    c = run(runs, lake, "SELECT 1 AS id, CAST(1.5001 AS DECIMAL(18,4)) AS v")
    assert sql_compare.compare(runs, a, c, keys=["id"])["counts"]["changed"] == 1


def test_columns_only_on_one_side_make_it_differ_and_are_named(runs, lake):
    a, b = pair(runs, lake, b="SELECT order_id, amount, status, 1 AS extra FROM orders")
    out = sql_compare.compare(runs, a, b, keys=["order_id"])
    assert out["result"] == "differs" and out["columns"]["only_b"] == ["extra"]
    assert out["counts"]["changed"] == 0


@pytest.mark.parametrize("kw, message", [
    ({"keys": ["nope"]}, "isn't in both A and B"),
    ({"keys": "order_id"}, "Match rows on up to"),
    ({"keys": ["order_id", "ORDER_ID"]}, "in the key twice"),
    ({"keys": ["order_id"], "tolerance": {"status": 1}}, "status isn't one"),
    ({"keys": ["order_id"], "tolerance": {"order_id": 1}}, "matches rows"),
    ({"keys": ["order_id"], "tolerance": {"amount": -1}}, "0 or more"),
    ({"keys": ["order_id"], "tolerance": {"amount": True}}, "is a number"),
    ({"keys": ["order_id"], "status": "both"}, "status is one of"),
    ({"keys": ["order_id"], "offset": -1}, "offset"),
])
def test_bad_choices_are_refused_with_what_to_do(runs, lake, kw, message):
    a, b = pair(runs, lake)
    with pytest.raises(ValueError, match=message):
        sql_compare.compare(runs, a, b, **kw)


def test_only_a_query_that_reads_and_has_one_result_can_be_compared(runs, lake):
    a = run(runs, lake, "SELECT * FROM orders")
    script = run(runs, lake, "", statements=["SELECT 1 AS x", "SELECT 2 AS x"])
    with pytest.raises(ValueError, match="B isn't one"):
        sql_compare.compare(runs, a, script)
    with pytest.raises(ValueError, match="Run A before comparing"):
        sql_compare.compare(runs, "", a)


def test_export_writes_every_differing_row_to_a_new_csv_with_formulas_protected(runs, lake, tmp_path):
    a, b = pair(runs, lake)
    out = sql_compare.export(runs, a, b, keys=["order_id"], name="orders")
    assert out["rows"] == 6 and out["name"].startswith("orders compare ") and out["name"].endswith(".csv")
    with open(out["path"], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == ["Where", "order_id", "A: amount", "B: amount", "A: status", "B: status"]
    assert sorted(r["Where"] for r in rows) == ["changed"] * 4 + ["only_a", "only_b"], "as the page says it, not verify's names"
    five = next(r for r in rows if r["order_id"] == "5")
    assert five["A: status"].startswith("'="), "a cell that starts like a formula is protected"
    assert out["notes"], "the export says it protected a cell"
    again = sql_compare.export(runs, a, b, keys=["order_id"], name="orders")
    assert again["path"] != out["path"], "never over an earlier file"
    assert not list((tmp_path / "Downloads").glob("*.partial"))


def test_export_with_nothing_different_leaves_no_file(runs, lake, tmp_path):
    a, b = pair(runs, lake, b="SELECT * FROM orders")
    with pytest.raises(ValueError, match="No rows differ"):
        sql_compare.export(runs, a, b, keys=["order_id"])
    assert not list((tmp_path / "Downloads").iterdir())


def test_a_cleaned_up_result_asks_to_run_again(monkeypatch):
    class Gone:
        def table(self, run_id):
            raise sql_exec.RerunNeeded("This result was cleaned up. Run the query again to compare it.")

    monkeypatch.setattr(Service, "sql_runs", Gone())
    service = Service.__new__(Service)
    assert service.sql_compare("a", "b") == {"rerun_needed": True,
                                             "message": "This result was cleaned up. Run the query again to compare them."}
    assert service.sql_compare_export("a", "b")["rerun_needed"] is True


def test_verify_keeps_its_old_answers_for_its_other_callers():
    a = pa.table({"id": [1, 1, 2], "v": [1, 1, 2]})
    b = pa.table({"id": [1, 2], "v": [1, 2]})
    out = verify.compare(a, b, {"keys": ["id"]})
    assert out["duplicates"] == {"pc": 1, "fabric": 0} and out["method"] == "whole rows"
    with pytest.raises(verify.RepeatedKey):
        verify.compare(a, b, {"keys": ["id"]}, repeated_keys="stop")


def test_export_columns_never_clash_with_the_tables_own(runs, lake):
    # No key: the table's own Where and Times columns get _2 after the export's.
    a = run(runs, lake, "SELECT 1 AS \"Where\", 2 AS times")
    b = run(runs, lake, "SELECT 1 AS \"Where\", 3 AS times")
    with open(sql_compare.export(runs, a, b)["path"], newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["Where", "Times", "Where_2", "times_2"]
    assert sorted(r[0] for r in rows[1:]) == ["only_a", "only_b"]
    # A key: a column named "A: v" stays apart from v's A value, and from each other.
    a = run(runs, lake, "SELECT 1 AS id, 1 AS v, 5 AS \"A: v\"")
    b = run(runs, lake, "SELECT 1 AS id, 2 AS v, 6 AS \"A: v\"")
    with open(sql_compare.export(runs, a, b, keys=["id"])["path"], newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == ["Where", "id", "A: v", "B: v", "A: A: v", "B: A: v"]
    assert len(set(h.lower() for h in header)) == len(header)
