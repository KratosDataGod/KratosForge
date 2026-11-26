"""T-SQL on the local engine (localdata/tsql.py) at edges the rest of the suite leaves untaken: CHAR with more
than one argument, deep nesting, a column named like a routine keyword, text against numbers of two types, a
nested UNION in brackets, ORDER BY keys that aren't text or name a window, a DISTINCT * that can't be expanded,
JSON paths that don't read, and the splitter's helpers. Each test checks the SQL written, the answer, or the
refusal."""

from __future__ import annotations

import pytest
from sqlglot import exp

from fabric_desktop.localdata import LocalData, tsql
from fabric_desktop.localdata.tsql import translate

COLUMNS = {"dbo": {"t": {"id": "INTEGER", "s": "VARCHAR", "d": "DECIMAL(10,2)", "j": "VARCHAR", "f": "DOUBLE"},
                   "u": {"id": "INTEGER", "s": "VARCHAR"}}}


def one(sql: str) -> tuple[str, list[str]]:
    texts, notes = translate(sql, COLUMNS)
    assert len(texts) == 1
    return texts[0], notes


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("TSQL")["id"])
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 'a  '), (2, 'b')) v(id, s)")
    e.execute("CREATE TABLE dbo.u AS SELECT * FROM (VALUES (3, ' 1'), (4, '2')) v(id, s)")
    return e


def rows(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"]


# CHAR takes one argument: sqlglot kept the second but wrote only the first, so CHAR(65, 66) ran as 'A'.

def test_char_with_two_arguments_is_refused_as_tsql_refuses_it():
    with pytest.raises(ValueError, match=r"The char function requires 1 argument\(s\)"):
        translate("SELECT CHAR(65, 66)")


def test_char_with_two_arguments_runs_nothing_on_the_engine(engine):
    with pytest.raises(ValueError, match=r"requires 1 argument"):
        engine.execute("SELECT CHAR(65, 66) AS c", dialect="tsql")
    assert rows(engine, "SELECT CHAR(65) AS c") == [["A"]]


def test_a_sum_nested_past_pythons_recursion_is_refused_as_too_deep():
    with pytest.raises(ValueError, match="nests calls or brackets too deeply"):
        translate("SELECT " + "1+" * 3000 + "1")


def test_a_column_named_create_before_proc_is_read_as_a_column_not_a_routine():
    # The control-flow check leaves a CREATE PROC to the routine refusal; here t.create is a column.
    text, notes = one("SELECT t.create proc FROM dbo.t")
    assert text.replace('"create"', "create") == "SELECT t.create AS proc FROM dbo.t" and notes == []  # newer sqlglot quotes it


def test_a_column_named_create_reaches_the_engine_as_a_column(engine):
    with pytest.raises(Exception, match="Invalid column name 'create'"):
        engine.execute("SELECT t.create proc FROM dbo.t", dialect="tsql")


def test_coalesce_of_text_and_a_decimal_is_left_to_duckdb():
    # Only text mixed with integers, bits or one date and time type is converted by precedence here.
    assert one("SELECT COALESCE(s, d) FROM dbo.t") == ("SELECT COALESCE(s, d) FROM dbo.t", [])


def test_a_union_in_brackets_takes_its_types_from_the_outermost_union(engine):
    text, notes = one("SELECT id FROM dbo.t UNION (SELECT s FROM dbo.u UNION SELECT s FROM dbo.u)")
    assert text.startswith("SELECT id FROM dbo.t UNION (SELECT LIST_TRANSFORM([CAST(s AS TEXT)]")
    assert text.count("to data type int.") == 2  # each inner leaf is converted to int
    assert any("GROUP BY, DISTINCT, UNION" in n for n in notes)
    # ' 1' and '2' are the ints 1 and 2, the same rows as dbo.t's ids.
    assert rows(engine, "SELECT id FROM dbo.t UNION (SELECT s FROM dbo.u UNION SELECT s FROM dbo.u) ORDER BY 1") \
        == [[1], [2]]


def test_between_with_text_and_a_value_of_unknown_type_is_noted():
    text, notes = one("SELECT id FROM dbo.t WHERE s BETWEEN 'a' AND x")
    assert text == "SELECT id FROM dbo.t WHERE s BETWEEN 'a' AND x"
    assert notes == [tsql._PADDED_NOTE]


@pytest.mark.parametrize("listed", ["'1', 2.5", "'1', CAST(2 AS BIGINT)", "'1', f"])
def test_text_in_a_list_with_numbers_of_another_type_is_left_to_duckdb(listed):
    sql = f"SELECT id FROM dbo.t WHERE id IN ({listed})"
    text, notes = one(sql)
    assert "LIST_TRANSFORM" not in text and notes == []


def test_text_in_a_list_with_ints_only_is_converted_to_int():
    text, _ = one("SELECT id FROM dbo.t WHERE id IN ('1', 2)")
    assert "to data type int." in text


def test_a_simple_case_with_a_non_text_arm_keeps_its_value_untrimmed():
    assert one("SELECT CASE s WHEN 'a' THEN 1 WHEN id THEN 2 END FROM dbo.t")[0] == \
        "SELECT CASE s WHEN 'a' THEN 1 WHEN id THEN 2 END FROM dbo.t"
    assert one("SELECT CASE s WHEN 'a' THEN 1 WHEN 'b' THEN 2 END FROM dbo.t")[0] == \
        "SELECT CASE RTRIM(s, ' ') WHEN 'a' THEN 1 WHEN 'b' THEN 2 END FROM dbo.t"


def test_nullif_is_compared_through_rtrim_only_for_text_read_once():
    assert one("SELECT NULLIF(s, 'a') FROM dbo.t")[0] == "SELECT CASE WHEN RTRIM(s, ' ') = 'a' THEN NULL ELSE s END FROM dbo.t"
    assert one("SELECT NULLIF(id, 1) FROM dbo.t")[0] == "SELECT NULLIF(id, 1) FROM dbo.t"
    # NEWID() would be drawn twice in the CASE: it stays one NULLIF.
    assert one("SELECT NULLIF(NEWID(), s) FROM dbo.t")[0] == "SELECT NULLIF(UUID(), s) FROM dbo.t"


def test_nullif_ignores_trailing_spaces_on_the_engine(engine):
    assert rows(engine, "SELECT NULLIF(s, 'a') AS v FROM dbo.t ORDER BY id") == [[None], ["b"]]


@pytest.mark.parametrize("key", ["'k'", "5"])
def test_an_order_by_constant_or_a_position_past_the_columns_is_left_as_written(key):
    assert one(f"SELECT s, id FROM dbo.t ORDER BY {key}, id")[0] == \
        f"SELECT s, id FROM dbo.t ORDER BY {key} NULLS FIRST, id NULLS FIRST"


def test_an_order_by_alias_of_a_window_is_not_trimmed():
    assert one("SELECT ROW_NUMBER() OVER (ORDER BY s) AS r, s FROM dbo.t ORDER BY r, s")[0] == \
        "SELECT ROW_NUMBER() OVER (ORDER BY RTRIM(s, ' ') NULLS FIRST) AS r, s FROM dbo.t ORDER BY r NULLS FIRST, " \
        "s NULLS FIRST"


def test_an_order_by_alias_is_trimmed_only_when_its_column_is_text(engine):
    assert one("SELECT id AS x, s FROM dbo.t ORDER BY x, s")[0] == \
        "SELECT id AS x, s FROM dbo.t ORDER BY x NULLS FIRST, s NULLS FIRST"
    assert one("SELECT s AS x, id FROM dbo.t ORDER BY x, id")[0] == \
        "SELECT s AS x, id FROM dbo.t ORDER BY RTRIM(s, ' ') NULLS FIRST, id NULLS FIRST"
    assert rows(engine, "SELECT s AS x, id FROM dbo.t ORDER BY x, id") == [["a  ", 1], ["b", 2]]


def test_a_negated_int_divided_by_a_float_keeps_the_float():
    assert one("SELECT -(id / f) FROM dbo.t")[0] == (
        "SELECT -(CASE WHEN (f) = 0 AND NOT (id) IS NULL THEN ERROR('Divide by zero error encountered.') "
        "ELSE (id) / (f) END) FROM dbo.t")


def test_a_distinct_star_that_cant_be_expanded_gives_no_grouping_note():
    # Two tables with one alias: qualify stops, and the * can't be told to hold text.
    assert one("SELECT DISTINCT * FROM dbo.t AS a, dbo.u AS a") == ("SELECT DISTINCT * FROM dbo.t AS a, dbo.u AS a", [])


@pytest.mark.parametrize("path", ['$["a', "$.a["])
def test_a_json_path_that_doesnt_read_is_refused_by_name(path):
    with pytest.raises(ValueError, match=r"isn't a JSON path\. A path starts with \$"):
        translate(f"SELECT JSON_VALUE(j, '{path}') FROM dbo.t", COLUMNS)


def test_json_extract_is_not_taken_for_json_query():
    assert one("SELECT JSON_EXTRACT(j, '$.a') FROM dbo.t") == ("SELECT j -> '$.a' FROM dbo.t", [])


def test_a_regexp_count_already_cast_to_int_isnt_cast_again():
    text, _ = one("SELECT CAST(REGEXP_COUNT(s, 'a') AS INT) FROM dbo.t")
    assert text == "SELECT CAST(CASE WHEN 'a' = '' THEN 0 ELSE LENGTH(REGEXP_EXTRACT_ALL(s, 'a')) END AS INT) FROM dbo.t"


# The splitter's and the refusal's helpers, at inputs a whole script can't give them.

def test_a_line_comment_left_open_at_the_end_of_a_batch_is_nothing_open():
    assert tsql._left_open("SELECT 1 -- no newline") is None
    assert tsql._left_open("SELECT 1 /* open") == "a comment"


def test_a_statement_that_cant_be_written_is_named_by_its_type():
    class Unwritable(exp.Select):
        def sql(self, *args, **kwargs):
            raise RuntimeError("can't write")

    assert tsql._statement_kind(Unwritable()) == "UNWRITABLE"
    assert tsql._statement_kind(exp.select("1")) == "SELECT"
