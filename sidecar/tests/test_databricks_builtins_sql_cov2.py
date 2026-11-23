"""Databricks SQL on this PC (databricks_builtins_sql) at the edges the rest of the suite leaves untaken: a
vector norm's degree written as text that isn't a number, argument counts refused by name, and table
statements with the column constraints and MERGE clauses that need nothing written for them."""

from __future__ import annotations

import pytest
from sqlglot import parse_one

from fabric_desktop.databricks_builtins_sql import rewrite
from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def written(sql: str) -> str:
    return rewrite(parse_one(sql, read="databricks")).sql("duckdb")


@pytest.fixture()
def cell():
    session = local_sql.DuckDBSession(SessionConfig("nb"))

    def run(code):
        result = session.execute(CellRequest(code, "sql", "databricks"))
        output = result.outputs[-1] if result.outputs else {}
        if result.status != "ok":
            return "error: " + str(output.get("evalue") or output.get("text"))
        return output.get("rows")

    yield run
    session.close()


def test_a_vector_norm_degree_written_as_text_that_isnt_a_number_is_refused():
    with pytest.raises(
        ValueError,
        match=r"^vector_norm\(\) takes degree 1\.0, 2\.0 or float\('inf'\), as in Databricks "
        r"\(INVALID_VECTOR_NORM_DEGREE\), not 'two'\.$",
    ):
        written("SELECT vector_norm(array(3.0, 4.0), 'two')")


def test_time_trunc_with_a_unit_from_a_column_is_refused():
    with pytest.raises(
        ValueError,
        match=r"^time_trunc\(\) can't run on this PC the way it runs in Databricks, so "
        r"nothing ran: its unit isn't a constant string here\.",
    ):
        written("SELECT time_trunc(u, t) FROM x")


def test_try_make_timestamp_with_four_arguments_is_refused_by_count():
    with pytest.raises(
        ValueError,
        match=r"^try_make_timestamp\(\) takes year, month, day, hour, minute and second, or "
        r"a date and a time, and a timezone, as in Databricks; not 4 arguments\.$",
    ):
        written("SELECT try_make_timestamp(1, 2, 3, 4)")


def test_timediff_without_a_unit_is_refused():
    with pytest.raises(
        ValueError,
        match=r"^timediff\(\) takes a unit first, as in timediff\(HOUR, start, end\)\.$",
    ):
        written("SELECT timediff(a, b)")


def test_an_ip_function_databricks_doesnt_have_is_left_as_written():
    assert written("SELECT try_ip_foo('1')") == "SELECT TRY_IP_FOO('1')"


def test_variant_get_as_a_decimal_with_no_size_is_decimal_10_0():
    assert written("SELECT variant_get(parse_json('1'), '$', 'DECIMAL')").endswith(
        "AS DECIMAL(10, 0))"
    )


def test_current_timezone_as_the_whole_expression_is_the_sessions_setting():
    assert (
        rewrite(parse_one("current_timezone()", read="databricks")).sql("duckdb")
        == "CURRENT_SETTING('TimeZone')"
    )


def test_a_table_with_not_null_comment_default_and_identity_columns(cell):
    assert (
        cell(
            "CREATE TABLE t (a INT NOT NULL COMMENT 'the a', b INT DEFAULT 5, "
            "c BIGINT GENERATED ALWAYS AS IDENTITY)"
        )
        == []
    )
    assert cell("ALTER TABLE t ADD COLUMN d INT") == []
    assert cell("ALTER TABLE t DROP COLUMNS (d)") == []
    assert cell("ALTER TABLE t ADD COLUMN d INT") == []
    assert cell("ALTER TABLE t DROP COLUMN d") == []
    assert cell("INSERT INTO t (a) VALUES (1)") == [[1]]
    assert cell("SELECT * FROM t") == [[1, 5, 1]]
    assert cell("INSERT INTO t (a) VALUES (NULL)").startswith("error: ")
    assert cell("CREATE TABLE s AS SELECT 1 AS a") == [[1]]
    assert cell("MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN DELETE") == [[1]]
    assert cell("SELECT count(*) FROM t") == [[0]]
    assert cell("DROP TABLE IF EXISTS t") == []
    assert cell(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 't'"
    ) == [[0]]
