"""T-SQL on the local engine (localdata/tsql.py) at the edges the rest of the suite leaves untaken: a
batch that can't be tokenized, view and table statements written another way, and the refusals that
name what can't run. Each test checks the answer the local engine gives, the SQL written or the refusal."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("TSQL")["id"])
    e.execute(
        "CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 2.50), (2, 4.00)) v(id, price)"
    )
    return e


def tsql(engine, text):
    return engine.execute(text, dialect="tsql")


def test_a_batch_that_cant_be_tokenized_is_reported_once_by_the_parser():
    # Each check that reads tokens (OUTPUT, JSON, SET, AUTHORIZATION, TIMESTAMP ...) leaves it to the parser.
    with pytest.raises(
        ValueError, match=r"^Could not read the query as T-SQL: Error tokenizing"
    ):
        translate("SELECT 'OUTPUT JSON SET AUTHORIZATION TIMESTAMP COPY DBCC GRANT")


def test_a_procedure_with_return_is_refused_as_a_procedure_not_as_return():
    with pytest.raises(ValueError, match=r"CREATE PROCEDURE p isn't mapped yet"):
        translate("CREATE PROCEDURE p AS SELECT 1; RETURN")


def test_alter_view_with_a_union_replaces_the_view(engine):
    tsql(engine, "CREATE VIEW dbo.v AS SELECT 1 AS n")
    tsql(engine, "ALTER VIEW dbo.v AS SELECT 1 AS n UNION ALL SELECT 2")
    assert tsql(engine, "SELECT n FROM dbo.v ORDER BY n")["rows"] == [[1], [2]]


def test_create_view_with_schemabinding_is_refused_by_name():
    with pytest.raises(
        ValueError, match=r"CREATE VIEW \.\.\. WITH SCHEMABINDING isn't mapped yet"
    ):
        translate("CREATE VIEW v WITH SCHEMABINDING AS SELECT 1")


def test_delete_from_a_join_deletes_the_matching_rows(engine):
    tsql(engine, "CREATE TABLE dbo.s (id INT)")
    tsql(engine, "INSERT INTO dbo.s VALUES (2)")
    tsql(engine, "DELETE t FROM dbo.t AS t JOIN dbo.s AS s ON t.id = s.id")
    assert tsql(engine, "SELECT id FROM dbo.t")["rows"] == [[1]]


def test_isjson_with_three_arguments_is_refused():
    with pytest.raises(
        ValueError, match=r"^ISJSON takes the text, and optionally VALUE"
    ):
        translate("SELECT ISJSON('{}', VALUE, 1)")


def test_string_split_with_one_argument_is_refused():
    with pytest.raises(
        ValueError, match=r"^STRING_SPLIT takes text, a separator of one character"
    ):
        translate("SELECT * FROM STRING_SPLIT('a,b')")


def test_a_windowed_decimal_average_is_decimal_38_6(engine):
    r = tsql(engine, "SELECT AVG(price) OVER () AS a FROM dbo.t ORDER BY id")
    assert r["rows"] == [["3.250000"], ["3.250000"]]


def test_a_windowed_decimal_sum_keeps_the_scale(engine):
    r = tsql(engine, "SELECT SUM(price) OVER () AS s FROM dbo.t ORDER BY id")
    assert r["rows"] == [["6.50"], ["6.50"]]


def test_decimal_with_no_size_divides_at_scale_three(engine):
    out, _ = translate("SELECT CAST(1.5 AS DECIMAL) / 2")
    assert "DECIMAL(21, 6)" in out[0]


def test_a_percentile_over_a_window_orders_by_its_value(engine):
    r = tsql(
        engine,
        "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY id) OVER () AS m FROM dbo.t ORDER BY id",
    )
    assert r["rows"] == [[1.5], [1.5]]


def test_a_division_by_a_random_number_isnt_worked_out_exactly():
    out, _ = translate("SELECT 1.0 / RAND()")
    assert (
        "(1.0) / (RANDOM())" in out[0] and "Divide by zero error encountered." in out[0]
    )


def test_text_plus_a_float_reads_the_text_as_a_float(engine):
    assert tsql(engine, "SELECT ' ' + 1.5e0 AS a, '2' + 1.5e0 AS b")["rows"] == [
        [1.5, 3.5]
    ]


def test_a_temp_table_is_made_temporary(engine):
    tsql(engine, "CREATE TABLE #t (a INT)")
    tsql(engine, "INSERT INTO #t VALUES (1)")
    assert tsql(engine, "SELECT a FROM #t")["rows"] == [[1]]


def test_two_decimal_averages_note_floating_point_once():
    out, notes = translate("SELECT AVG(1.50) + AVG(2.50)")
    assert out == [
        "SELECT CAST(CAST(AVG(1.50) AS DECIMAL(38, 6)) + CAST(AVG(2.50) AS DECIMAL(38, 6)) AS DECIMAL(38, 6))"
    ]
    assert len(notes) == 1 and notes[0].startswith("A decimal AVG")


def test_a_float_average_stays_as_it_is_and_a_windowed_integer_sum_stays_an_int(engine):
    assert translate("SELECT AVG(CAST(1 AS FLOAT))")[0] == [
        "SELECT AVG(CAST(1 AS DOUBLE))"
    ]
    # WR-247: T-SQL's SUM of an int is an int, so a total past it is error 8115
    assert translate("SELECT SUM(1) OVER ()")[0] == [(
        "SELECT LIST_TRANSFORM([SUM(1) OVER ()], LAMBDA kf_value : CASE WHEN NOT kf_value IS NULL AND "
        "TRY_CAST(kf_value AS INT) IS NULL THEN CAST(ERROR('Arithmetic overflow error converting expression to data "
        "type int.') AS INT) ELSE CAST(kf_value AS INT) END)[1]")]
    assert tsql(engine, "SELECT AVG(CAST(id AS FLOAT)) AS a FROM dbo.t")["rows"] == [
        [1.5]
    ]


def test_a_decimal_plus_a_windowed_integer_sum_is_a_decimal(engine):
    assert translate("SELECT 1.5 + SUM(2) OVER ()")[0] == [(
        "SELECT CAST(1.5 + LIST_TRANSFORM([SUM(2) OVER ()], LAMBDA kf_value : CASE WHEN NOT kf_value IS NULL AND "
        "TRY_CAST(kf_value AS INT) IS NULL THEN CAST(ERROR('Arithmetic overflow error converting expression to data "
        "type int.') AS INT) ELSE CAST(kf_value AS INT) END)[1] AS DECIMAL(21, 1))")]  # WR-247: SUM of an int is an int


def test_text_divided_by_text_and_text_modulo_text_are_left_to_duckdb():
    assert translate("SELECT '6' / '2'")[0] == ["SELECT '6' / '2'"]
    assert translate("SELECT '5' % '2'")[0] == ["SELECT '5' % '2'"]


def test_update_from_a_join_on_the_target_updates_the_matching_rows(engine):
    tsql(engine, "CREATE TABLE dbo.s (id INT)")
    tsql(engine, "INSERT INTO dbo.s VALUES (2)")
    tsql(engine, "UPDATE dbo.t SET price = 9 FROM dbo.t JOIN dbo.s ON t.id = s.id")
    assert tsql(engine, "SELECT id, price FROM dbo.t ORDER BY id")["rows"] == [
        [1, "2.50"],
        [2, "9.00"],
    ]


def test_text_plus_a_date_is_left_to_duckdb():
    # WR-275: CAST(1 AS DATE) is T-SQL's "Explicit conversion from data type int to date is not allowed."
    assert translate("SELECT '1' + CAST('2024-01-01' AS DATE)")[0] == [
        "SELECT '1' + CAST('2024-01-01' AS DATE)"
    ]


def test_isnull_of_a_varchar_max_keeps_the_whole_second_value(engine):
    assert translate("SELECT ISNULL(CAST(NULL AS VARCHAR(MAX)), 'abcd')")[0] == [
        "SELECT COALESCE(CAST(NULL AS TEXT), 'abcd')"
    ]
    assert tsql(engine, "SELECT ISNULL(CAST(NULL AS VARCHAR(MAX)), 'abcd') AS v")[
        "rows"
    ] == [["abcd"]]


def test_view_metadata_is_refused_by_name():
    with pytest.raises(
        ValueError, match=r"CREATE VIEW \.\.\. WITH VIEW_METADATA isn't mapped yet"
    ):
        translate("CREATE VIEW v WITH VIEW_METADATA AS SELECT 1")


def test_text_in_a_subquery_is_compared_with_tsqls_padding():  # WR-246: 'a' IN (SELECT 'a ') is true in T-SQL
    out, notes = translate("SELECT 1 WHERE 'a' IN (SELECT 'a ')")
    assert out == ["SELECT 1 WHERE 'a' IN (SELECT 'a')"]


def test_a_table_with_a_check_constraint_keeps_it(engine):
    tsql(engine, "CREATE TABLE dbo.c (a INT, CONSTRAINT positive CHECK (a > 0))")
    tsql(engine, "INSERT INTO dbo.c VALUES (1)")
    with pytest.raises(Exception, match="positive|CHECK|Constraint"):
        tsql(engine, "INSERT INTO dbo.c VALUES (-1)")
    assert tsql(engine, "SELECT a FROM dbo.c")["rows"] == [[1]]


def test_format_of_a_value_whose_type_cant_be_told_is_refused():
    with pytest.raises(
        ValueError,
        match=r"FORMAT needs to know its value's type, and here it can't be told",
    ):
        translate("SELECT FORMAT(x, 'N2') FROM t")


def test_a_decimal_divided_by_a_random_decimal_is_worked_out_in_floating_point():
    out, notes = translate("SELECT 1.5 / CAST(RAND() AS DECIMAL(10,2))")
    assert out == [
        "SELECT CAST(1.5 / CAST(RANDOM() AS DECIMAL(10, 2)) AS DECIMAL(15, 12))"
    ]
    assert notes and notes[0].startswith("A decimal AVG, or decimal arithmetic")


def test_a_time_and_a_datetime2_with_no_fraction_as_text_have_whole_seconds(engine):
    r = tsql(
        engine,
        "SELECT CAST(CAST('10:00:00' AS TIME(0)) AS VARCHAR(20)) AS t, "
        "CAST(CAST('2020-01-01 10:00:00' AS DATETIME2(0)) AS VARCHAR(30)) AS d",
    )
    assert r["rows"] == [["10:00:00", "2020-01-01 10:00:00"]]


def test_text_plus_a_decimal_reads_the_text_as_that_decimal(engine):
    assert translate("SELECT '1' + CAST(1.5 AS DECIMAL(5,1))")[0] == [
        "SELECT CAST('1' AS DECIMAL(5, 1)) + CAST(1.5 AS DECIMAL(5, 1))"
    ]
    assert tsql(engine, "SELECT '1' + CAST(1.5 AS DECIMAL(5,1)) AS v")["rows"] == [
        ["2.5"]
    ]
