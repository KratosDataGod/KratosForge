"""WR-269: a Fabric Warehouse has no DEFAULT constraints, so local T-SQL refuses DEFAULT by name.

Microsoft Learn, "Primary keys, foreign keys, and unique keys in Warehouse in Microsoft Fabric" (table-constraints):
"SQL analytics endpoint and Warehouse don't support default constraints at this time." The Fabric syntax of
CREATE TABLE gives a column only NULL, NOT NULL and COLLATE; the Fabric syntax of INSERT is
``VALUES ( { NULL | expression } )`` and of UPDATE ``SET column_name = { expression | NULL }``. DuckDB takes all of
them, so each ran here and would have stopped in a warehouse. DuckDB SQL in the same project keeps DEFAULT.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("No DEFAULT")["id"])
    e.execute("CREATE TABLE dbo.t (a INT, b INT)", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, 2)", dialect="tsql")
    e.execute("CREATE TABLE dbo.s (a INT, b INT)", dialect="tsql")
    yield e
    local.close_all()


NO_DEFAULTS = "a Fabric Warehouse has no DEFAULT constraints"

REFUSED = [  # (T-SQL, words the message must also hold: the thing named, and what to write instead)
    ("CREATE TABLE dbo.d (a INT DEFAULT 0)", ["CREATE TABLE", "column a DEFAULT 0", "NULL"]),
    ("CREATE TABLE dbo.d (a INT NOT NULL CONSTRAINT df_a DEFAULT (0), b INT)", ["CREATE TABLE", "column a", "NULL"]),
    ("CREATE TABLE dbo.d (a INT, d DATE DEFAULT GETDATE())", ["column d", "each INSERT"]),
    ("CREATE TABLE #d (a INT DEFAULT 0)", ["CREATE TABLE", "column a"]),
    ("ALTER TABLE dbo.t ADD c INT DEFAULT 0", ["ALTER TABLE ... ADD", "column c DEFAULT 0", "NULL"]),
    ("ALTER TABLE dbo.t ADD CONSTRAINT df_b DEFAULT 0 FOR b", ["ADD CONSTRAINT ... DEFAULT", "NULL"]),
    ("ALTER TABLE dbo.t ADD DEFAULT ('x') FOR b", ["ADD CONSTRAINT ... DEFAULT"]),
    ("INSERT INTO dbo.t VALUES (1, DEFAULT)", ["DEFAULT in VALUES", "Write NULL, or the value itself"]),
    ("INSERT INTO dbo.t (a, b) VALUES (1, 2), (3, default)", ["DEFAULT in VALUES"]),
    ("INSERT INTO dbo.t DEFAULT VALUES", ["DEFAULT VALUES", "NULL where a column has none"]),
    ("UPDATE dbo.t SET b = DEFAULT", ["SET b = DEFAULT", "Write NULL, or the value itself"]),
    ("UPDATE dbo.t SET a = 1, b = DEFAULT WHERE a = 1", ["SET b = DEFAULT"]),
    ("MERGE INTO dbo.t AS t USING dbo.s AS s ON t.a = s.a WHEN MATCHED THEN UPDATE SET b = DEFAULT;",
     ["SET b = DEFAULT"]),
    ("MERGE INTO dbo.t AS t USING dbo.s AS s ON t.a = s.a WHEN NOT MATCHED THEN INSERT (a, b) VALUES (s.a, DEFAULT);",
     ["DEFAULT in VALUES"]),
]


@pytest.mark.parametrize("sql, words", REFUSED, ids=[r[0][:60] for r in REFUSED])
def test_default_is_refused_by_name(engine, sql, words):
    with pytest.raises(Exception, match="local engine") as err:
        engine.execute(sql, dialect="tsql")
    message = str(err.value)
    assert NO_DEFAULTS in message
    for word in words:
        assert word in message, (word, message)
    assert "Catalog Error" not in message and "Binder Error" not in message


def test_nothing_changed(engine):
    """A refusal stops before anything runs: the table keeps its row and gets no column."""
    assert engine.execute("SELECT a, b FROM dbo.t", dialect="tsql")["rows"] == [[1, 2]]
    columns = engine.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 't' "
                             "ORDER BY ORDINAL_POSITION", dialect="tsql")["rows"]
    assert columns == [["a"], ["b"]]


def test_merge_default_values_keeps_its_own_message():
    with pytest.raises(ValueError, match="A Fabric Warehouse's MERGE doesn't take INSERT DEFAULT VALUES"):
        tsql.translate("MERGE INTO t USING s ON t.a = s.a WHEN NOT MATCHED THEN INSERT DEFAULT VALUES;")


@pytest.mark.parametrize("sql", [
    "SELECT [default] FROM t",
    "UPDATE t SET b = [DEFAULT]",
    "SELECT 'DEFAULT' AS v",
    "INSERT INTO t VALUES (1, NULL)",
    "SELECT 'ALTER TABLE t ADD DEFAULT 0 FOR b' AS v",
])
def test_a_quoted_default_is_a_name_or_text(sql):
    texts, _ = tsql.translate(sql)
    assert texts


def test_what_to_write_instead_runs(engine):
    engine.execute("CREATE TABLE dbo.w (a INT, b INT)", dialect="tsql")
    engine.execute("INSERT INTO dbo.w VALUES (1, NULL)", dialect="tsql")
    engine.execute("UPDATE dbo.w SET b = NULL WHERE a = 1", dialect="tsql")
    assert engine.execute("SELECT a, b FROM dbo.w", dialect="tsql")["rows"] == [[1, None]]


def test_duckdb_sql_keeps_default(engine):
    """Only T-SQL is a warehouse's language: DuckDB SQL in the same project still has DEFAULT."""
    engine.execute("CREATE TABLE dk (a INT, b INT DEFAULT 7)")
    engine.execute("INSERT INTO dk (a) VALUES (1)")
    engine.execute("INSERT INTO dk VALUES (2, DEFAULT)")
    engine.execute("INSERT INTO dk DEFAULT VALUES")
    engine.execute("UPDATE dk SET b = DEFAULT WHERE a = 1")
    rows = engine.execute("SELECT a, b FROM dk ORDER BY a NULLS LAST")["rows"]
    assert rows == [[1, 7], [2, 7], [None, 7]]
