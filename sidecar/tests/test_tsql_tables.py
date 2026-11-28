"""WR-215: local T-SQL temp tables and table creation follow a Fabric Warehouse, or stop by name.

What Fabric Warehouse documents (Microsoft Learn, read 2026-11-17: identity 2026-08-26, temp-tables 2026-07-27,
tsql-surface-area 2026-09-29):
- #temp tables last for the session, and ``WITH (DISTRIBUTION = ROUND_ROBIN)`` makes one stored as Parquet;
  ``##global`` temp tables aren't supported.
- IDENTITY is BIGINT only, with no seed or increment.
- TRUNCATE TABLE is supported.

T-SQL keeps ``VARCHAR(n)`` and ``VARBINARY(n)`` to n bytes and stops a longer value with "String or binary data
would be truncated". The local engine is DuckDB, which ignores the length.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData

TRUNCATED = "String or binary data would be truncated in table 'dbo.t', column '{}'."


@pytest.fixture()
def local(tmp_path):
    store = LocalData(tmp_path / "app" / "local-data")
    yield store
    store.close_all()


@pytest.fixture()
def engine(local):
    e = local.engine(local.create("Tables")["id"])
    e.execute("CREATE TABLE dbo.t (id INT NOT NULL, v VARCHAR(5) NULL, c CHAR(2), b VARBINARY(2), m VARCHAR(MAX))",
              dialect="tsql")
    return e


def run(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def test_values_that_fit_the_declared_length_are_kept(engine):
    assert run(engine, "INSERT INTO dbo.t VALUES (1, 'abcde', 'ab', 0x0102, REPLICATE(CAST('x' AS VARCHAR(MAX)), 9000)), "
                       "(2, N'éé', NULL, NULL, NULL), (3, NULL, NULL, NULL, NULL)") == [[3]]
    assert run(engine, "SELECT id, v, c, LEN(m) FROM dbo.t ORDER BY id") == [[1, "abcde", "ab", 9000],
                                                                              [2, "éé", None, None],
                                                                              [3, None, None, None]]


@pytest.mark.parametrize(("sql", "column"), [
    ("INSERT INTO dbo.t VALUES (1, 'abcdef', NULL, NULL, NULL)", "v"),
    ("INSERT INTO dbo.t VALUES (1, N'ééé', NULL, NULL, NULL)", "v"),  # 3 characters, 6 bytes in UTF-8
    ("INSERT INTO dbo.t VALUES (1, NULL, 'abc', NULL, NULL)", "c"),
    ("INSERT INTO dbo.t VALUES (1, NULL, NULL, 0x010203, NULL)", "b"),
    ("INSERT INTO dbo.t (id, v) SELECT 1, 'abcdefgh'", "v"),
])
def test_a_value_longer_than_its_column_stops_with_tsqls_words(engine, sql, column):
    with pytest.raises(Exception, match=TRUNCATED.format(column).replace(".", r"\.")):
        engine.execute(sql, dialect="tsql")
    assert run(engine, "SELECT COUNT(*) FROM dbo.t") == [[0]]


def test_an_update_past_the_length_stops_and_keeps_the_old_value(engine):
    run(engine, "INSERT INTO dbo.t VALUES (1, 'abc', NULL, NULL, NULL)")
    with pytest.raises(Exception, match=TRUNCATED.format("v")):
        engine.execute("UPDATE dbo.t SET v = CONCAT(v, 'xyz') WHERE id = 1", dialect="tsql")
    assert run(engine, "SELECT v FROM dbo.t") == [["abc"]]


def test_trailing_blanks_past_the_length_pass_as_in_tsql(engine):
    # T-SQL drops blanks past a VARCHAR or CHAR length without an error; bytes past a VARBINARY length still stop.
    assert run(engine, "INSERT INTO dbo.t (id, v, c) VALUES (1, 'abcde   ', 'ab ')") == [[1]]
    assert run(engine, "SELECT LEN(v), LEN(c) FROM dbo.t") == [[5, 2]]
    with pytest.raises(Exception, match=TRUNCATED.format("v")):
        engine.execute("INSERT INTO dbo.t (id, v) VALUES (2, 'abcdef ')", dialect="tsql")


def test_only_spaces_pass_past_the_length(engine):
    # A no-break space isn't a trailing blank in T-SQL: 'abcde' + NCHAR(160) is 7 bytes, too long for VARCHAR(5).
    with pytest.raises(Exception, match=TRUNCATED.format("v")):
        engine.execute("INSERT INTO dbo.t (id, v) VALUES (1, N'abcde\u00a0')", dialect="tsql")


def test_dbcc_inside_a_string_is_just_text(engine):
    assert run(engine, "SELECT 'x;\n DBCC'") == [["x;\n DBCC"]]


def test_alter_table_adds_a_column_without_a_length(engine):
    run(engine, "ALTER TABLE dbo.t ADD n INT")
    run(engine, "ALTER TABLE dbo.t ADD w VARCHAR(MAX)")
    assert run(engine, "INSERT INTO dbo.t (id, n, w) VALUES (1, 2, 'abc')") == [[1]]


def test_not_null_is_kept(engine):
    with pytest.raises(Exception, match="NOT NULL constraint failed"):
        engine.execute("INSERT INTO dbo.t (id) VALUES (NULL)", dialect="tsql")


def test_a_temp_table_lasts_for_the_session(tmp_path):
    local = LocalData(tmp_path / "temp" / "local-data")
    project = local.create("Temp")["id"]
    e = local.engine(project)
    e.execute("CREATE TABLE #t (id INT) WITH (DISTRIBUTION = ROUND_ROBIN)", dialect="tsql")
    e.execute("INSERT INTO #t VALUES (1), (2)", dialect="tsql")
    assert run(e, "SELECT COUNT(*) FROM #t") == [[2]]
    e.execute("DROP TABLE #t", dialect="tsql")
    with pytest.raises(Exception, match=r"Invalid object name '#t'\."):
        e.execute("SELECT * FROM #t", dialect="tsql")
    e.execute("CREATE TABLE #u (id INT)", dialect="tsql")
    local.close_all()
    reopened = LocalData(tmp_path / "temp" / "local-data")
    with pytest.raises(Exception, match=r"Invalid object name '#u'\."):
        reopened.engine(project).execute("SELECT * FROM #u", dialect="tsql")
    reopened.close_all()


def test_drop_table_if_exists_and_truncate(engine):
    assert run(engine, "DROP TABLE IF EXISTS dbo.nothere") == []
    run(engine, "INSERT INTO dbo.t (id) VALUES (1), (2)")
    engine.execute("TRUNCATE TABLE dbo.t", dialect="tsql")
    assert run(engine, "SELECT COUNT(*) FROM dbo.t") == [[0]]
    engine.execute("DROP TABLE IF EXISTS dbo.t", dialect="tsql")
    with pytest.raises(Exception, match=r"Invalid object name 'dbo\.t'\."):
        engine.execute("SELECT * FROM dbo.t", dialect="tsql")


def test_select_into_and_ctas_make_tables_with_the_selects_types(engine):
    engine.execute("SELECT 1 AS a, 'xy' AS b, CAST(1.5 AS DECIMAL(5, 2)) AS c, 1.5 AS d INTO dbo.si", dialect="tsql")
    engine.execute("CREATE TABLE dbo.ct AS SELECT a, b, c, d FROM dbo.si", dialect="tsql")
    for table in ("si", "ct"):
        assert run(engine, f"SELECT column_name, data_type FROM INFORMATION_SCHEMA.COLUMNS WHERE table_name = '{table}' "
                           "ORDER BY ordinal_position") == [["a", "INTEGER"], ["b", "VARCHAR"],
                                                            ["c", "DECIMAL(5,2)"], ["d", "DECIMAL(2,1)"]]


@pytest.mark.parametrize(("sql", "words"), [
    ("CREATE TABLE ##g (id INT)", r"global temporary tables \(##g\) aren't supported in a Fabric Warehouse either"),
    ("SELECT * FROM ##g", r"global temporary tables \(##g\) aren't supported in a Fabric Warehouse either"),
    ("CREATE TABLE dbo.x (id INT) WITH (DISTRIBUTION = HASH(id))",
     r"WITH \(DISTRIBUTION = \.\.\.\) is only for #temp tables in a Fabric Warehouse"),
    ("CREATE TABLE dbo.i (id BIGINT IDENTITY, v INT)", (r"IDENTITY isn't mapped yet \(column id\)\. A Fabric Warehouse "
                                                       "takes only BIGINT IDENTITY, with no seed or increment")),
    ("CREATE TABLE dbo.i (id INT IDENTITY(1, 1), v INT)", r"IDENTITY isn't mapped yet \(column id\)"),
    ("DBCC CHECKIDENT ('dbo.i', RESEED)", "DBCC isn't mapped yet"),
    ("SET IDENTITY_INSERT dbo.i ON", "SET IDENTITY_INSERT isn't mapped yet"),
    ("SELECT 1;\n  dbcc SHOW_STATISTICS ('dbo.t', s)", "DBCC isn't mapped yet"),
    ("ALTER TABLE dbo.t ADD w VARCHAR(2)", r"ALTER TABLE \.\.\. ADD of a column with a length \(w\) isn't mapped yet"),
    ("ALTER TABLE dbo.t ADD x INT, w VARBINARY(4)", r"ADD of a column with a length \(w\)"),
])
def test_what_isnt_mapped_is_refused_by_name(engine, sql, words):
    with pytest.raises(ValueError, match="This T-SQL can't be run on the local engine|The local engine doesn't run"):
        engine.execute(sql, dialect="tsql")
    with pytest.raises(ValueError, match=words):
        engine.execute(sql, dialect="tsql")


def test_the_length_check_is_written_into_the_table():
    from fabric_desktop.localdata.tsql import translate
    texts, _ = translate("CREATE TABLE dbo.t (v VARCHAR(5), b VARBINARY(2), m VARCHAR(MAX))")
    assert texts == [("CREATE TABLE dbo.t (v TEXT(5) CHECK (CASE WHEN STRLEN(RTRIM(v, ' ')) > 5 THEN ERROR('String or binary data "
                     "would be truncated in table ''dbo.t'', column ''v''.') ELSE TRUE END), b BLOB CHECK (CASE WHEN "
                     "OCTET_LENGTH(b) > 2 THEN ERROR('String or binary data would be truncated in table ''dbo.t'', "
                     "column ''b''.') ELSE TRUE END), m TEXT)")]
