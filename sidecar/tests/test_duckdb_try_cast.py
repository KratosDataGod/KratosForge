"""TRY_CAST over a file's column gives NULL for a value that doesn't fit, on DuckDB 1.x and 2.0 (#1341).

DuckDB 2.0's type_pushdown optimizer pushes the cast into the file scan as a strict type, so the
query failed instead. Every connection that runs a person's SQL turns it off (duckdb_rows.keep_try_cast).
"""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import spark_local, sql
from fabric_desktop.duckdb_rows import keep_try_cast
from fabric_desktop.jobs import Job, Target, run_local
from fabric_desktop.localdata import LocalData


@pytest.fixture
def csv(tmp_path):
    (tmp_path / "data").mkdir()
    path = tmp_path / "data" / "p.csv"
    path.write_bytes(b"id,amount\n1,2.50\n2,oops\n")
    return path.as_posix()


def query(path):
    return f"SELECT id, TRY_CAST(amount AS DOUBLE) AS amount FROM read_csv('{path}', header = true) ORDER BY id"


def test_a_connection_keeps_try_cast(csv):
    con = keep_try_cast(duckdb.connect())
    assert con.execute(query(csv)).fetchall() == [(1, 2.5), (2, None)]


def test_spark_sql_keeps_try_cast(csv):
    assert [tuple(r) for r in spark_local.session().sql(query(csv)).collect()] == [(1, 2.5), (2, None)]


def test_sql_run_keeps_try_cast(csv):
    assert sql.run(query(csv), dialect="duckdb").to_pylist() == [{"id": 1, "amount": 2.5}, {"id": 2, "amount": None}]


def test_a_local_data_project_keeps_try_cast(tmp_path, csv):
    local = LocalData(tmp_path / "app" / "local-data")
    try:
        pid = local.create("Casts")["id"]
        local.add_source(pid, str(tmp_path / "data"))  # the folder holding p.csv, read as the table dbo.p
        rows = local.engine(pid).execute("SELECT id, TRY_CAST(amount AS DOUBLE) FROM dbo.p ORDER BY id")["rows"]
        assert rows == [[1, 2.5], [2, None]]
    finally:
        local.close_all()


def test_a_local_duckdb_job_keeps_try_cast(tmp_path, csv):
    job = Job(name="casts", engine="duckdb", base_dir=tmp_path, target=Target("ws", "lh", "casts"), sql=query(csv))
    assert run_local(job).to_pylist() == [{"id": 1, "amount": 2.5}, {"id": 2, "amount": None}]
