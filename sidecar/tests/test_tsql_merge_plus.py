"""WR-267: ``+`` in a local T-SQL MERGE follows T-SQL's types, on a database table (CREATE TABLE, changed by DuckDB)
and on one of the project's Delta tables (USING DELTA, changed by delta-rs), as it does in a SELECT or an UPDATE.

- Text + text joins the strings, in SET values, INSERT values, WHEN conditions and ON. DuckDB and delta-rs had no
  ``+`` for text, so the MERGE stopped with their binder words.
- A number outranks text: '10' + 2 is 12, and text that isn't a number is T-SQL's conversion error.
- NULL + text is NULL.
- A source whose columns are known only when the MERGE runs (a #temp table the script makes, WR-257) gets the same
  rules then. A ``+`` whose types DuckDB can't read even then is refused by name, not with DuckDB's words.
"""

from __future__ import annotations

import pytest
import sqlglot

from fabric_desktop import delta_dml
from fabric_desktop.localdata import LocalData, tsql

START = [[1, "a"], [2, "b"], [3, "c"]]
TABLES = ["dbo.tgt", "dbo.dt"]  # a database table, and one of the project's Delta tables


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Merge plus")["id"])
    rows = "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, v)"
    e.execute(f"CREATE TABLE dbo.tgt AS {rows}")
    e.execute(f"CREATE TABLE dbo.dt USING DELTA AS {rows}")
    e.execute("CREATE TABLE dbo.src AS SELECT * FROM (VALUES (2, 'B', '20'), (4, 'D', '40')) v(id, v, n)")
    yield e
    local.close_all()


def table(engine, name):
    return [list(r) for r in engine.execute(f"SELECT id, v FROM {name} ORDER BY id")["rows"]]


def run(engine, name, sql):
    engine.execute(sql.format(t=name), dialect="tsql")
    return table(engine, name)


def refused(engine, name, sql):
    with pytest.raises(Exception) as caught:
        engine.execute(sql.format(t=name), dialect="tsql")
    assert table(engine, name) == START  # a refused MERGE changes nothing
    return str(caught.value)


MERGE = "MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
TEMP = "SELECT * INTO #s FROM dbo.src; MERGE {t} AS t USING #s AS s ON t.id = s.id "


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("head", [MERGE, TEMP], ids=["known", "temp"])
def test_text_plus_text_joins_in_set_and_insert(engine, name, head):
    assert run(engine, name, head + "WHEN MATCHED THEN UPDATE SET v = s.v + 'z' + t.v "
                                   "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, 'n' + s.v);") \
        == [[1, "a"], [2, "Bzb"], [3, "c"], [4, "nD"]]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("head", [MERGE, TEMP], ids=["known", "temp"])
def test_text_plus_text_joins_in_when_conditions(engine, name, head):
    sql = head + ("WHEN MATCHED AND s.v + t.v = 'Bb' THEN UPDATE SET v = 'm' "
                  "WHEN NOT MATCHED AND s.v + '' = 'D' THEN INSERT (id, v) VALUES (s.id, 'i') "
                  "WHEN NOT MATCHED BY SOURCE AND t.v + '' = 'a' THEN UPDATE SET v = v + '-gone';")
    assert run(engine, name, sql) == [[1, "a-gone"], [2, "m"], [3, "c"], [4, "i"]]


@pytest.mark.parametrize("name", TABLES)
def test_text_plus_text_joins_in_on(engine, name):
    sql = "MERGE {t} AS t USING dbo.src AS s ON t.v + 'x' = LOWER(s.v) + 'x' WHEN MATCHED THEN UPDATE SET v = 'm';"
    assert run(engine, name, sql) == [[1, "a"], [2, "m"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
def test_an_unqualified_column_is_typed_by_its_side(engine, name):
    sql = MERGE + "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (id, v + n);"  # the source's, there (WR-264)
    assert run(engine, name, sql) == [[1, "a"], [2, "b"], [3, "c"], [4, "D40"]]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("head", [MERGE, TEMP], ids=["known", "temp"])
def test_null_plus_text_is_null(engine, name, head):
    sql = head + "WHEN MATCHED THEN UPDATE SET v = NULL + s.v WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v + NULL);"
    assert run(engine, name, sql) == [[1, "a"], [2, None], [3, "c"], [4, None]]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("head", [MERGE, TEMP], ids=["known", "temp"])
def test_a_number_outranks_text(engine, name, head):
    sql = head + "WHEN MATCHED THEN UPDATE SET id = s.n + 1 WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id + '100', s.v);"
    assert run(engine, name, sql) == [[1, "a"], [3, "c"], [21, "b"], [104, "D"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_number_outranks_text_in_on(engine, name):
    sql = "MERGE {t} AS t USING dbo.src AS s ON t.id + '0' = s.id WHEN MATCHED THEN UPDATE SET v = 'm';"
    assert run(engine, name, sql) == [[1, "a"], [2, "m"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("head", [MERGE, TEMP], ids=["known", "temp"])
def test_text_that_isnt_a_number_is_a_conversion_error(engine, name, head):
    message = refused(engine, name, head + "WHEN MATCHED THEN UPDATE SET id = s.v + 1;")
    # SQL Server's Msg 245 with the value, as WR-247 (main) words the conversion error
    assert "Msg 245, Level 16: Conversion failed when converting the varchar value 'B' to data type int." in message
    assert "binder" not in message.lower() and "coerce" not in message.lower()


@pytest.mark.parametrize("name", TABLES)
def test_a_source_known_from_an_earlier_query(engine, name):
    engine.execute("SELECT * INTO #s FROM dbo.src", dialect="tsql")
    sql = "MERGE {t} AS t USING #s AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v + s.n;"
    assert run(engine, name, sql) == [[1, "a"], [2, "B20"], [3, "c"]]


def test_a_table_column_converted_from_text_runs_on_a_database_table(engine):
    sql = MERGE + "WHEN MATCHED THEN UPDATE SET id = t.id + s.n;"
    assert run(engine, "dbo.tgt", sql) == [[1, "a"], [3, "c"], [22, "b"]]


def test_a_table_column_converted_from_text_is_refused_by_name_on_a_delta_table(engine):
    # The checked conversion is the local engine's own (delta-rs can't run it), and it reads the table.
    message = refused(engine, "dbo.dt", MERGE + "WHEN MATCHED THEN UPDATE SET id = t.id + s.n;")
    assert "reads the table's columns" in message and "delta-rs" in message


def test_the_translation_types_a_merge_and_says_nothing_untyped():
    columns = {"dbo": {"t": {"id": "INTEGER", "v": "VARCHAR"}, "s": {"id": "INTEGER", "v": "VARCHAR"}}}
    texts, notes = tsql.translate("MERGE dbo.t AS t USING dbo.s AS s ON t.id = s.id "
                                  "WHEN MATCHED THEN UPDATE SET v = s.v + 'z';", columns)
    assert "s.v || 'z'" in texts[0]
    assert not [n for n in notes if "+" in n]


def test_a_plus_named_in_the_script_is_still_refused():
    with pytest.raises(ValueError, match="KF_TSQL_PLUS isn't mapped yet"):
        tsql.translate("SELECT kf_tsql_plus(1, 2)", {}, known={"ABS"})


def test_a_plus_duckdb_cant_type_when_it_runs_is_refused_by_name():
    change = delta_dml.parse_tree(sqlglot.parse_one(
        "MERGE INTO dbo.t AS t USING s AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = kf_tsql_plus(s.v, 'z')",
        read="duckdb"))
    with pytest.raises(ValueError) as caught:
        tsql.late_plus(change, lambda nodes: None)
    assert str(caught.value) == tsql.PLUS_UNTYPED.format(sql="s.v + 'z'")
    assert str(caught.value).startswith("Couldn't tell the types in s.v + 'z', so this MERGE isn't run.")
