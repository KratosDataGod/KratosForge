"""A local data script whose earlier statement creates a project Delta table and whose later statements write
it (WR-143): each later statement is read as a write to that Delta table, in DuckDB SQL and Spark SQL. Local only."""

from __future__ import annotations

import pytest


@pytest.fixture
def engine(tmp_path):
    from fabric_desktop.localdata import LocalData
    data = LocalData(tmp_path / "local-data")
    yield data.engine(data.create("Script writes")["id"])
    data.close_all()


def _rows(engine, query: str) -> list[tuple]:
    out = engine.execute(query)
    return sorted(tuple(r) if not isinstance(r, dict) else tuple(r.values()) for r in out["rows"])


def test_create_then_insert_overwrite_runs_as_one_duckdb_script(engine):
    engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id, 'a' AS v;\n"
                   "INSERT INTO t SELECT 2, 'b';\n"
                   "INSERT OVERWRITE t SELECT * FROM t WHERE id = 2;")
    assert _rows(engine, "SELECT id, v FROM t") == [(2, "b")]


def test_create_then_overwrite_runs_as_one_spark_script(engine):
    engine.execute("CREATE TABLE s USING DELTA AS SELECT 1 AS id;\n"
                   "INSERT INTO TABLE s SELECT 5;\n"
                   "INSERT OVERWRITE TABLE s SELECT id * 10 FROM s;", dialect="spark")
    assert _rows(engine, "SELECT id FROM s") == [(10,), (50,)]


def test_create_then_update_and_delete_run_as_one_script(engine):
    engine.execute("CREATE TABLE u USING DELTA AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) x(id, v);\n"
                   "UPDATE u SET v = 'z' WHERE id = 1;\n"
                   "DELETE FROM u WHERE id = 3;")
    assert _rows(engine, "SELECT id, v FROM u") == [(1, "z"), (2, "b")]


def test_a_plain_table_made_earlier_in_the_script_is_still_duckdb_s(engine):
    engine.execute("CREATE TABLE plain AS SELECT 1 AS id;\nINSERT INTO plain SELECT 2;")
    assert _rows(engine, "SELECT id FROM plain") == [(1,), (2,)]
