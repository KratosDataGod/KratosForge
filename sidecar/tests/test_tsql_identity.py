"""WR-261: IDENTITY columns as a Fabric Warehouse has them.

Microsoft Learn (Fabric Warehouse, IDENTITY columns and T-SQL surface area): IDENTITY is BIGINT only, with no seed
or increment, unique but not always in order, with gaps. SET IDENTITY_INSERT is supported, with DBCC CHECKIDENT
RESEED. SELECT ... INTO and CREATE TABLE ... AS SELECT carry IDENTITY. @@IDENTITY, IDENT_CURRENT, SEQUENCE and
ALTER TABLE ... ADD of an IDENTITY column aren't supported.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate
from fabric_desktop.localdata.tsql_identity import SETUP, from_default


@pytest.fixture()
def local(tmp_path):
    store = LocalData(tmp_path / "app" / "local-data")
    yield store
    store.close_all()


@pytest.fixture()
def engine(local):
    e = local.engine(local.create("Identity")["id"])
    e.execute("CREATE TABLE dbo.t (id BIGINT IDENTITY, v INT)", dialect="tsql")
    return e


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


# A BIGINT IDENTITY column numbers the rows

def test_new_rows_are_numbered(engine):
    rows(engine, "INSERT INTO dbo.t (v) VALUES (10), (20)")
    rows(engine, "INSERT INTO dbo.t (v) SELECT 30")
    got = rows(engine, "SELECT id, v FROM dbo.t ORDER BY v")
    assert [v for _, v in got] == [10, 20, 30]
    ids = [i for i, _ in got]
    assert len(set(ids)) == 3 and all(isinstance(i, int) for i in ids)


def test_the_column_is_bigint_and_not_null(engine):
    assert rows(engine, "SELECT DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 't' "
                        "AND COLUMN_NAME = 'id'") == [["BIGINT", "NO"]]


def test_an_insert_with_no_column_list_fills_the_other_columns(engine):
    rows(engine, "INSERT INTO dbo.t VALUES (7)")
    assert rows(engine, "SELECT v FROM dbo.t WHERE id IS NOT NULL") == [[7]]


def test_one_script_can_make_the_table_and_fill_it(local):
    e = local.engine(local.create("Script")["id"])
    assert rows(e, "CREATE TABLE dbo.s (id BIGINT IDENTITY NOT NULL, name VARCHAR(10));\n"
                   "INSERT INTO dbo.s VALUES ('a');\nINSERT INTO dbo.s (name) VALUES ('b');\n"
                   "SELECT COUNT(DISTINCT id), COUNT(*) FROM dbo.s") == [[2, 2]]


def test_a_temp_table_takes_identity(engine):
    assert rows(engine, "CREATE TABLE #n (id BIGINT IDENTITY, v INT); INSERT INTO #n (v) VALUES (1), (2);\n"
                        "SELECT COUNT(DISTINCT id) FROM #n") == [[2]]


def test_numbers_last_when_the_project_is_opened_again(tmp_path):
    store = LocalData(tmp_path / "ld")
    project = store.create("Reopen")["id"]
    e = store.engine(project)
    rows(e, "CREATE TABLE dbo.t (id BIGINT IDENTITY, v INT); INSERT INTO dbo.t (v) VALUES (1), (2)")
    store.close_all()
    store = LocalData(tmp_path / "ld")
    e = store.engine(project)
    rows(e, "INSERT INTO dbo.t (v) VALUES (3)")
    assert rows(e, "SELECT COUNT(DISTINCT id), COUNT(*) FROM dbo.t") == [[3, 3]]
    store.close_all()


def test_merge_inserts_get_numbers_and_an_insert_with_no_list_fills_the_rest(engine):
    rows(engine, "INSERT INTO dbo.t (v) VALUES (1)")
    rows(engine, "MERGE INTO dbo.t AS t USING (SELECT 1 AS v UNION ALL SELECT 2) AS s ON t.v = s.v "
                 "WHEN NOT MATCHED THEN INSERT VALUES (s.v);")
    assert rows(engine, "SELECT COUNT(DISTINCT id), COUNT(*) FROM dbo.t") == [[2, 2]]


# An explicit value: SQL Server's errors, and SET IDENTITY_INSERT

def test_an_explicit_value_is_sql_servers_error_544(engine):
    with pytest.raises(ValueError, match=r"^Msg 544, Level 16: Cannot insert explicit value for identity column in "
                                         r"table 't' when IDENTITY_INSERT is set to OFF\.$"):
        engine.execute("INSERT INTO dbo.t (id, v) VALUES (5, 1)", dialect="tsql")
    assert rows(engine, "SELECT COUNT(*) FROM dbo.t") == [[0]]


def test_an_explicit_value_from_a_merge_is_error_544(engine):
    with pytest.raises(ValueError, match=r"Msg 544, Level 16: Cannot insert explicit value for identity column"):
        engine.execute("MERGE INTO dbo.t AS t USING (SELECT 5 AS id, 1 AS v) AS s ON t.id = s.id "
                       "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);", dialect="tsql")


def test_identity_insert_on_takes_explicit_values(engine):
    assert rows(engine, "SET IDENTITY_INSERT dbo.t ON;\nINSERT INTO dbo.t (id, v) VALUES (100, 1), (200, 2);\n"
                        "SET IDENTITY_INSERT dbo.t OFF;\nINSERT INTO dbo.t (v) VALUES (3);\n"
                        "SELECT id, v FROM dbo.t WHERE id >= 100 ORDER BY id") == [[100, 1], [200, 2]]
    assert rows(engine, "SELECT COUNT(*) FROM dbo.t") == [[3]]


def test_identity_insert_off_again_refuses_explicit_values(engine):
    with pytest.raises(ValueError, match=r"Msg 544, Level 16"):
        engine.execute("SET IDENTITY_INSERT dbo.t ON; SET IDENTITY_INSERT [dbo].[t] OFF;\n"
                       "INSERT INTO dbo.t (id, v) VALUES (5, 1)", dialect="tsql")


def test_identity_insert_lasts_for_the_run(engine):
    rows(engine, "SET IDENTITY_INSERT dbo.t ON; INSERT INTO dbo.t (id, v) VALUES (9, 9)")
    with pytest.raises(ValueError, match=r"Msg 544, Level 16"):
        engine.execute("INSERT INTO dbo.t (id, v) VALUES (10, 10)", dialect="tsql")


def test_identity_insert_on_needs_a_column_list_error_8101(engine):
    with pytest.raises(ValueError, match=r"^Msg 8101, Level 16: An explicit value for the identity column in table "
                                         r"'t' can only be specified when a column list is used and IDENTITY_INSERT "
                                         r"is ON\.$"):
        engine.execute("SET IDENTITY_INSERT dbo.t ON; INSERT INTO dbo.t VALUES (5, 1)", dialect="tsql")


def test_identity_insert_is_on_for_one_table_at_a_time(engine):
    with pytest.raises(ValueError, match=r"IDENTITY_INSERT is already ON for table 'dbo\.t'\. Cannot perform SET "
                                         r"operation for table 'dbo\.u'\."):
        engine.execute("CREATE TABLE dbo.u (id BIGINT IDENTITY, v INT);\nSET IDENTITY_INSERT dbo.t ON;\n"
                       "SET IDENTITY_INSERT dbo.u ON", dialect="tsql")


def test_identity_insert_on_a_table_without_identity_is_error_8106(engine):
    rows(engine, "CREATE TABLE dbo.p (a INT)")
    with pytest.raises(ValueError, match=r"^Msg 8106, Level 16: Table 'dbo\.p' does not have the identity property\. "
                                         r"Cannot perform SET operation\.$"):
        engine.execute("SET IDENTITY_INSERT dbo.p ON", dialect="tsql")


def test_identity_insert_on_a_missing_table_is_error_1088(engine):
    with pytest.raises(ValueError, match=r'^Msg 1088, Level 16: Cannot find the object "nothere" because it does not '
                                         r'exist or you do not have permissions\.$'):
        engine.execute("SET IDENTITY_INSERT dbo.nothere ON", dialect="tsql")


def test_updating_the_column_is_error_8102(engine):
    rows(engine, "INSERT INTO dbo.t (v) VALUES (1)")
    with pytest.raises(ValueError, match=r"^Msg 8102, Level 16: Cannot update identity column 'id'\.$"):
        engine.execute("UPDATE dbo.t SET id = 5", dialect="tsql")


# SELECT ... INTO and CREATE TABLE ... AS SELECT carry IDENTITY

@pytest.mark.parametrize("make", ["SELECT * INTO dbo.n FROM dbo.t", "CREATE TABLE dbo.n AS SELECT id, v FROM dbo.t",
                                  "SELECT id AS k, v INTO dbo.n FROM dbo.t WHERE v > 0"])
def test_select_into_carries_identity(engine, make):
    rows(engine, "INSERT INTO dbo.t (v) VALUES (1), (2)")
    rows(engine, make)
    rows(engine, "INSERT INTO dbo.n (v) VALUES (3)")
    column = "k" if " AS k" in make else "id"
    assert rows(engine, f"SELECT COUNT(DISTINCT {column}), COUNT(*), COUNT({column}) FROM dbo.n") == [[3, 3, 3]]
    with pytest.raises(ValueError, match=r"Msg 544, Level 16: Cannot insert explicit value for identity column in "
                                         r"table 'n'"):
        engine.execute(f"INSERT INTO dbo.n ({column}, v) VALUES (1, 1)", dialect="tsql")


def test_select_into_returns_its_row_count(engine):
    rows(engine, "INSERT INTO dbo.t (v) VALUES (1), (2)")
    assert rows(engine, "SELECT * INTO dbo.n FROM dbo.t") == [[2]]


@pytest.mark.parametrize("make", ["SELECT id + 0 AS id, v INTO dbo.n FROM dbo.t",
                                  "SELECT t.id, t.v INTO dbo.n FROM dbo.t AS t JOIN dbo.t AS u ON u.id = t.id",
                                  "SELECT MAX(id) AS id, v INTO dbo.n FROM dbo.t GROUP BY v",
                                  "SELECT id, id AS again, v INTO dbo.n FROM dbo.t"])
def test_select_into_does_not_carry_identity_where_sql_server_does_not(engine, make):
    rows(engine, "INSERT INTO dbo.t (v) VALUES (1)")
    rows(engine, make)
    rows(engine, "INSERT INTO dbo.n (id, v) VALUES (42, 2)" if "again" not in make
         else "INSERT INTO dbo.n (id, again, v) VALUES (42, 42, 2)")
    assert rows(engine, "SELECT COUNT(*) FROM dbo.n WHERE id = 42") == [[1]]


def test_select_into_a_project_table_from_a_temp_table_says_identity_is_not_carried(engine):
    _, notes = translate("CREATE TABLE #s (id BIGINT IDENTITY, v INT); SELECT * INTO dbo.n FROM #s")
    assert any("IDENTITY isn't carried here from a #temp table into a project table" in n for n in notes)


# Refused by name

@pytest.mark.parametrize(("sql", "words"), [
    ("CREATE TABLE dbo.x (id BIGINT IDENTITY(1, 1), v INT)",
     (r"IDENTITY with a seed or increment \(IDENTITY\(1, 1\), column id\) isn't supported in a Fabric Warehouse\. "
      r"Write BIGINT IDENTITY, with neither\.")),
    ("CREATE TABLE dbo.x (id BIGINT IDENTITY(100, 5))", r"IDENTITY with a seed or increment \(IDENTITY\(100, 5\)"),
    ("CREATE TABLE dbo.x (id INT IDENTITY, v INT)",
     r"IDENTITY on column id, which isn't BIGINT, isn't supported in a Fabric Warehouse"),
    ("CREATE TABLE dbo.x (id DECIMAL(18, 0) IDENTITY)", r"IDENTITY on column id, which isn't BIGINT"),
    ("CREATE SEQUENCE dbo.s START WITH 1", (r"SEQUENCE objects aren't supported in a Fabric Warehouse\. Use a "
                                            r"BIGINT IDENTITY column")),
    ("DROP SEQUENCE dbo.s", "SEQUENCE objects aren't supported"),
    ("ALTER SEQUENCE dbo.s RESTART", "SEQUENCE objects aren't supported"),
    ("SELECT NEXT VALUE FOR dbo.s", "SEQUENCE objects aren't supported"),
    ("SELECT @@IDENTITY", r"@@IDENTITY isn't available in a Fabric Warehouse"),
    ("INSERT INTO dbo.t (v) VALUES (1); SELECT @@identity", r"@@IDENTITY isn't available"),
    ("SELECT IDENT_CURRENT('dbo.t')", r"IDENT_CURRENT isn't available in a Fabric Warehouse"),
    ("ALTER TABLE dbo.t ADD n BIGINT IDENTITY",
     (r"ALTER TABLE \.\.\. ADD of an IDENTITY column \(n\) isn't supported in a Fabric Warehouse\. Declare it in "
      r"CREATE TABLE\.")),
])
def test_what_a_warehouse_does_not_have_is_refused_by_name(engine, sql, words):
    with pytest.raises(ValueError, match=words):
        engine.execute(sql, dialect="tsql")


def test_two_identity_columns_are_error_2744(engine):
    with pytest.raises(ValueError, match=r"^Msg 2744, Level 16: Multiple identity columns specified for table 'x'\. "
                                         r"Only one identity column per table is allowed\.$"):
        engine.execute("CREATE TABLE dbo.x (a BIGINT IDENTITY, b BIGINT IDENTITY)", dialect="tsql")


# The translation

def test_the_sequence_is_made_before_the_table_and_counts_as_no_statement():
    texts, _ = translate("CREATE TABLE dbo.t (id BIGINT IDENTITY, v INT)")
    assert texts == [SETUP + 'CREATE SEQUENCE IF NOT EXISTS "dbo"."__identity_t_id"',
                     'CREATE TABLE dbo.t (id BIGINT DEFAULT NEXTVAL(\'"dbo"."__identity_t_id"\') NOT NULL, v INT)']


def test_a_script_error_counts_the_users_statements_only(engine):
    with pytest.raises(Exception, match=r"Statement 2 of 2 \(SELECT\) failed"):
        engine.execute("CREATE TABLE dbo.y (id BIGINT IDENTITY, v INT); SELECT nothere FROM dbo.y", dialect="tsql")


@pytest.mark.parametrize(("default", "sequence"), [
    ("nextval('dbo.\"__identity_t_id\"')", 'dbo."__identity_t_id"'),
    ("nextval('temp.main.\"__identity_#t_id\"')", 'temp.main."__identity_#t_id"'),
    ("nextval('dbo.myseq')", None), (None, None), ("42", None),
    ("nextval('dbo.\"__identity_t_id\"')\n", 'dbo."__identity_t_id"'),
])
def test_an_identity_default_is_told_from_another(default, sequence):
    assert from_default(default) == sequence
