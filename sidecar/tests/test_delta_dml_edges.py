"""MERGE, UPDATE and DELETE on a Delta table (delta_dml.py), the paths the main tests don't reach:
statements refused before anything is written, ON clauses that aren't plain key equalities, a column
neither side has, and WHEN NOT MATCHED BY SOURCE THEN UPDATE. On a Delta table in tmp_path."""

from __future__ import annotations

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_dml as D


@pytest.mark.parametrize("sql, needle", [
    ("UPDATE t SET a = 1 FROM s WHERE t.id = s.id", "UPDATE ... FROM isn't supported"),
    ("DELETE FROM t USING s WHERE t.id = s.id", "DELETE ... USING isn't supported"),
    ("UPDATE c.db.t SET a = 1", "c.db.t: name the table as schema.table or table."),
    ("UPDATE t SET a", "a isn't column = value."),
    ("MERGE INTO t USING (SELECT 1 AS id) ON t.id = id WHEN MATCHED THEN DELETE", "Give the MERGE source an alias"),
    ("MERGE INTO t USING s WHEN MATCHED THEN DELETE", "MERGE INTO needs ON"),
    ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN DELETE", "no row in the table to delete"),
    ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN UPDATE SET a = 1", "no row in the table to update"),
    ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN UPDATE SET *",
     "UPDATE SET * goes with WHEN MATCHED"),
    ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN INSERT *", "INSERT goes with WHEN NOT MATCHED"),
    ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT (a, b) VALUES (s.a)",
     "needs as many values as columns"),
    ("UPDATE t SET a = (", "Could not read the statement"),
])
def test_statements_delta_cant_run_are_refused_before_anything_is_written(sql, needle):
    with pytest.raises(ValueError, match=needle.replace(".", r"\.").replace("*", r"\*").replace("(", r"\(")):
        D.parse(sql)


def test_a_statement_that_isnt_a_change_is_none():
    assert D.parse("SELECT 1") is None
    assert D.parse_tree(D.sqlglot.parse_one("SELECT 1")) is None


@pytest.mark.parametrize("on", ["t.id > s.id", "t.id = s.id AND t.region = 'EU'", "t.id = t.parent"])
def test_an_on_clause_that_isnt_only_key_equalities_has_no_keys(on):
    change = D.parse(f"MERGE INTO t USING s ON {on} WHEN MATCHED THEN DELETE")
    assert D.equi_keys(change) is None


def test_key_equalities_in_brackets_are_keys():
    change = D.parse("MERGE INTO t USING s ON (t.id = s.id AND (t.k = s.key)) WHEN MATCHED THEN DELETE")
    assert sorted(D.equi_keys(change)) == [("id", "id"), ("k", "key")]


@pytest.fixture
def table(tmp_path):
    path = tmp_path / "customers"
    write_deltalake(str(path), pa.table({"id": [1, 2, 3], "spend": [10.0, 20.0, 30.0]}))
    return DeltaTable(str(path))


def test_a_column_neither_side_has_is_named_with_both_sides_columns(table):
    change = D.parse("MERGE INTO customers t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET spend = s.total")
    source = pa.table({"id": [1], "amount": [5.0]})
    with pytest.raises(ValueError, match="There is no column total in s. customers has id, spend; "
                                         "the source has id, amount."):
        D.apply(change, table, source, ["id", "amount"])
    assert table.version() == 0


def test_rows_the_source_no_longer_has_can_be_updated(table):
    change = D.parse("MERGE INTO customers t USING s ON t.id = s.id "
                     "WHEN MATCHED THEN UPDATE SET spend = s.spend "
                     "WHEN NOT MATCHED BY SOURCE THEN UPDATE SET spend = 0")
    out = D.apply(change, table, pa.table({"id": [1], "spend": [11.0]}), ["id", "spend"])
    assert out["rows_updated"] == 3
    got = DeltaTable(table.table_uri).to_pyarrow_table().sort_by("id")
    assert got.column("spend").to_pylist() == [11.0, 0.0, 0.0]


def test_a_cast_arrow_doesnt_make_is_left_to_delta(monkeypatch):
    class Values:
        type = pa.string()

        def cast(self, want, safe=True):
            raise pa.ArrowNotImplementedError("no cast")

    assert D._check_values("t", "c", Values(), pa.int64()) is None
