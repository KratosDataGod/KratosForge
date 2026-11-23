"""Snowflake SQL statements on this PC (WR-313): each answers as the cited Snowflake page documents, or is
refused by name (SFN-026 to SFN-030) with one next step. The pages were read on 2026-11-19; no Snowflake
account was used. The parity cases in snowflake_sql_parity.py run the documented examples end to end."""

from __future__ import annotations

import re

import duckdb
import pytest

from fabric_desktop import snowflake_sql as s
from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


def _rows(code: str) -> list[tuple]:
    t = s.translate(code)
    assert t.runnable, t.first_refusal()
    con = duckdb.connect()
    cur = con.execute(t.duckdb_sql)
    return cur.fetchall()


@pytest.mark.parametrize(("code", "rule", "says"), [
    # https://docs.snowflake.com/en/sql-reference/sql/insert-multi-table
    ("INSERT ALL INTO t1 INTO t2 SELECT 1", "SFN-026", "INSERT ALL"),
    ("INSERT FIRST WHEN n > 1 THEN INTO t1 ELSE INTO t2 SELECT 1 AS n", "SFN-026", "INSERT FIRST"),
    # https://docs.snowflake.com/en/sql-reference/sql/create-table ("Column names, types, defaults, and
    # constraints are copied to the new table")
    ("CREATE TABLE u LIKE t", "SFN-027", "Write the column definitions out"),
    # https://docs.snowflake.com/en/sql-reference/sql/truncate-table (TRUNCATE [ TABLE ] [ IF EXISTS ] <name>)
    ("TRUNCATE TABLE IF EXISTS t", "SFN-028", "Leave out IF EXISTS"),
    # https://docs.snowflake.com/en/sql-reference/sql/alter-table (several ADD COLUMN, DROP COLUMN a, b; SWAP WITH)
    ("ALTER TABLE t ADD COLUMN a INT, b INT", "SFN-029", "Write one ALTER TABLE for each column"),
    ("ALTER TABLE t DROP COLUMN a, b", "SFN-029", "Write one ALTER TABLE for each column"),
    ("ALTER TABLE t SWAP WITH u", "SFN-029", "RENAME TO"),
    # https://docs.snowflake.com/en/sql-reference/transactions ("DDL statements implicitly commit active
    # transactions")
    ("BEGIN; INSERT INTO t VALUES (1); CREATE TABLE x (b INT); ROLLBACK", "SFN-030", "Put COMMIT before"),
    ("BEGIN TRANSACTION; ALTER TABLE t ADD COLUMN c INT; COMMIT", "SFN-030", "Put COMMIT before"),
    ("START TRANSACTION; DROP TABLE t; COMMIT", "SFN-030", "Put COMMIT before"),
])
def test_a_statement_this_pc_would_answer_differently_is_refused_by_name(code, rule, says):
    t = s.translate(code)
    assert not t.runnable
    assert [n.id for r in t.statements for n in r.notes if r.verdict == "not_translatable"] == [rule]
    assert says in t.first_refusal()
    note = next(n for r in t.statements for n in r.notes if n.id == rule)
    assert ":" not in note.reason and "—" not in note.reason  # plain sentences, no colon reveal


def test_ddl_outside_a_transaction_still_runs():
    # https://docs.snowflake.com/en/sql-reference/transactions: after COMMIT no transaction is open
    assert _rows("CREATE TABLE t (a INT); BEGIN; INSERT INTO t VALUES (1); COMMIT; CREATE TABLE x (b INT); "
                 "SELECT count(*) FROM t") == [(1,)]


def test_start_transaction_is_begin():
    # https://docs.snowflake.com/en/sql-reference/sql/begin ("START TRANSACTION is a synonym for BEGIN")
    t = s.translate("START TRANSACTION")
    assert t.runnable and t.duckdb_sql == "BEGIN"
    assert _rows("CREATE TABLE t (a INT); START TRANSACTION; INSERT INTO t VALUES (1); ROLLBACK; "
                 "SELECT count(*) FROM t") == [(0,)]


def test_a_column_named_start_is_still_a_column():
    assert s.translate("SELECT start AS transaction FROM t").duckdb_sql.startswith("SELECT")


def test_merge_update_set_may_name_the_target_column():
    # https://docs.snowflake.com/en/sql-reference/sql/merge ("Perform a basic merge that updates values")
    sql = s.translate("MERGE INTO tt AS x USING ss ON x.id = ss.id WHEN MATCHED THEN UPDATE SET x.v = ss.v, "
                      "tt.w = ss.w").duckdb_sql
    assert "SET v = ss.v, w = ss.w" in sql


def test_keys_are_not_enforced_but_a_primary_key_is_not_null():
    # https://docs.snowflake.com/en/sql-reference/constraints-overview ("Snowflake doesn't enforce PRIMARY KEY,
    # UNIQUE, and FOREIGN KEY constraints on standard tables"; "A PRIMARY KEY constraint implies that the
    # column is both NOT NULL and UNIQUE"; "NOT NULL and CHECK constraints are enforced")
    sql = s.translate("CREATE TABLE t (a INT PRIMARY KEY, b INT UNIQUE, c INT REFERENCES p (x), d INT, "
                      "CONSTRAINT pk PRIMARY KEY (a, d), UNIQUE (b), FOREIGN KEY (c) REFERENCES p (x), "
                      "CHECK (a > 0))").duckdb_sql
    # the names stored in upper case, as Snowflake stores unquoted ones (SFX-072)
    assert sql == 'CREATE TABLE "T" ("A" INT NOT NULL, "B" INT, "C" INT, "D" INT NOT NULL, CHECK (a > 0))'
    assert _rows("CREATE TABLE t (a INT PRIMARY KEY, b VARCHAR UNIQUE); INSERT INTO t VALUES (1, 'x'), (1, 'x'); "
                 "SELECT count(*) FROM t") == [(2,)]
    with pytest.raises(duckdb.ConstraintException):
        _rows("CREATE TABLE t (a INT PRIMARY KEY); INSERT INTO t VALUES (NULL)")
    with pytest.raises(duckdb.ConstraintException):
        _rows("CREATE TABLE t (a INT CHECK (a > 0)); INSERT INTO t VALUES (0)")


def test_transient_and_cluster_by_are_dropped():
    # https://docs.snowflake.com/en/sql-reference/sql/create-table (TRANSIENT: lower data protection; CLUSTER BY:
    # a clustering key)
    assert s.translate("CREATE TRANSIENT TABLE t (a INT) CLUSTER BY (a)").duckdb_sql == 'CREATE TABLE "T" ("A" INT)'


@pytest.mark.parametrize(("column", "start", "step"), [
    ("id INT AUTOINCREMENT", 1, 1), ("id INT IDENTITY", 1, 1), ("id INT IDENTITY(10, 5)", 10, 5),
    ("id NUMBER AUTOINCREMENT START 10 INCREMENT 5 NOORDER", 10, 5), ("id INT AUTOINCREMENT ORDER", 1, 1),
    ("id INT IDENTITY START 100 INCREMENT -1", 100, -1)])
def test_an_autoincrement_column_numbers_from_its_start_by_its_step(column, start, step):
    # https://docs.snowflake.com/en/sql-reference/sql/create-table ("AUTOINCREMENT and IDENTITY are synonymous";
    # "The default value for both the start value and the step/increment value is 1")
    code = f"CREATE TABLE t ({column}, v INT); INSERT INTO t (v) VALUES (1), (2), (3); SELECT id FROM t ORDER BY v"
    ids = [r[0] for r in _rows(code)]
    assert len(set(ids)) == 3 and all((i - start) % step == 0 and (i - start) // step >= 0 for i in ids)
    statement = s.translate(code).statements[0].duckdb_sql or ""
    assert re.match(r"CREATE SEQUENCE __kf_identity_[0-9a-f]{32} START WITH ", statement)


def test_an_autoincrement_start_that_isnt_a_number_is_refused():
    t = s.translate("CREATE TABLE t (id INT IDENTITY(x, 1))")
    assert not t.runnable and "whole numbers" in t.first_refusal()


def test_describe_and_show_say_where_to_look_instead():
    assert "information_schema.columns" in s.translate("DESCRIBE TABLE t").first_refusal()
    assert "information_schema.tables" in s.translate("SHOW TABLES").first_refusal()


def test_an_autoincrement_table_runs_and_plans_in_a_session():
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    try:
        code = "CREATE TABLE t (id INT AUTOINCREMENT, v INT)"
        assert "Plan of the DuckDB translation" in session.plan(code, "snowflake")
        assert session.execute(CellRequest(code, "sql", "snowflake")).status == "ok"
        assert session.execute(CellRequest("INSERT INTO t (v) VALUES (5), (6)", "sql", "snowflake")).status == "ok"
        r = session.execute(CellRequest("SELECT sum(id) AS ids FROM t", "sql", "snowflake"))
        assert r.status == "ok" and r.outputs[-1]["rows"] == [["3"]]
    finally:
        session.close()


def test_ddl_in_a_transaction_runs_nothing_in_a_session():
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    try:
        session.execute(CellRequest("CREATE TABLE t (a INT)", "sql", "snowflake"))
        r = session.execute(CellRequest("BEGIN; INSERT INTO t VALUES (1); CREATE TABLE x (b INT); ROLLBACK",
                                        "sql", "snowflake"))
        assert r.status == "error" and r.outputs[-1]["evalue"].endswith("Nothing in the cell ran.")
        assert "inside a transaction" in r.outputs[-1]["evalue"]
        assert session.execute(CellRequest("SELECT count(*) AS n FROM t", "sql", "snowflake")).outputs[-1]["rows"] \
            == [[0]]
    finally:
        session.close()


@pytest.mark.parametrize("step", ["99999999999999999999", "9" * 5000])
def test_an_identity_step_too_big_for_a_bigint_is_refused(step):
    translation = s.translate(f"CREATE TABLE t (id INT IDENTITY(1, {step}))")
    assert not translation.runnable
    assert "fit in a BIGINT" in translation.first_refusal()


def test_a_hybrid_table_which_enforces_its_keys_is_not_made_here():
    # hybrid tables enforce PRIMARY KEY, UNIQUE and FOREIGN KEY (create-hybrid-table), so the keys must not be dropped
    assert not s.translate("CREATE HYBRID TABLE h (id INT PRIMARY KEY, v INT)").runnable
