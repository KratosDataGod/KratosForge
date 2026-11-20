"""Databricks SQL statements in a DuckDB engine cell: each answers as Databricks' SQL reference documents (the page
is named on each test), or is refused by name so nothing runs with another answer."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture()
def cell():
    session = local_sql.DuckDBSession(SessionConfig("nb"))

    def run(code, columns=False):
        result = session.execute(CellRequest(code, "sql", "databricks"))
        output = result.outputs[-1] if result.outputs else {}
        if result.status != "ok":
            return "error: " + str(output.get("evalue") or output.get("text"))
        return (output.get("columns"), output.get("rows")) if columns else output.get("rows")
    yield run
    session.close()


def _databricks_stops_too(answer, what):
    # a statement Databricks stops too: no warehouse step, since running it there won't help
    assert answer.startswith(f"error: {what}, so nothing ran. Databricks stops it too, "), answer
    assert "Databricks SQL warehouse" not in answer


def _refused(answer, what):
    assert answer.startswith(f"error: {what} can't run on this PC the way it runs in Databricks, because "), answer
    assert ", so nothing ran. " in answer
    assert "Run the cell on a Databricks SQL warehouse, or " in answer


_SOURCE = ("CREATE TABLE t (id INT, v STRING); INSERT INTO t VALUES (3, 'c'), (4, 'd'); "
           "CREATE TABLE s (id INT, w STRING); INSERT INTO s VALUES (3, 'C'), (3, 'CC'), (4, 'D')")


# https://docs.databricks.com/aws/en/sql/language-manual/delta-merge-into: more than one source row matching the
# same target row fails with DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE, except when the matches are
# deleted unconditionally; after 15.4 LTS the WHEN MATCHED conditions count, not only ON
@pytest.mark.parametrize("merge", [
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.w",
    "MERGE INTO t AS x USING s AS y ON x.id = y.id WHEN MATCHED AND y.w LIKE 'C%' THEN DELETE",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED AND s.w = 'C' THEN DELETE WHEN MATCHED THEN UPDATE SET v = s.w",
    ("MERGE INTO t USING (SELECT id, w AS v FROM s) AS src ON t.id = src.id WHEN MATCHED THEN UPDATE SET * "
     "WHEN NOT MATCHED THEN INSERT *"),
    "WITH src AS (SELECT * FROM s) MERGE INTO t USING src ON t.id = src.id WHEN MATCHED THEN UPDATE SET v = src.w",
])
def test_merge_stops_when_two_source_rows_match_one_target_row(cell, merge):
    cell(_SOURCE)
    answer = cell(merge)
    assert answer.startswith("error: "), answer
    assert answer.startswith("error: [DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE] "), answer
    assert cell("SELECT * FROM t ORDER BY id") == [[3, "c"], [4, "d"]]  # nothing changed


def test_merge_runs_when_its_conditions_leave_one_match(cell):
    cell(_SOURCE)
    assert cell("MERGE INTO t AS x USING s AS y ON x.id = y.id WHEN MATCHED AND y.w <> 'CC' THEN UPDATE SET v = y.w"
                ) == [[2]]
    assert cell("SELECT * FROM t ORDER BY id") == [[3, "C"], [4, "D"]]
    assert cell("MERGE INTO t USING (SELECT 4 AS id, 'q' AS w UNION ALL SELECT 5, 'n') s ON t.id = s.id "
                "WHEN MATCHED THEN UPDATE SET v = s.w WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.w)") == [[2]]
    assert cell("SELECT * FROM t ORDER BY id") == [[3, "C"], [4, "q"], [5, "n"]]


def test_an_unconditional_delete_with_two_matches_never_answers_otherwise(cell):
    # Databricks deletes the row; DuckDB 2.0's own MERGE stops it, which is no other answer
    cell(_SOURCE)
    answer = cell("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE")
    if isinstance(answer, str):
        assert answer.startswith("error: "), answer
        assert cell("SELECT count(*) FROM t") == [[2]]
    else:
        assert cell("SELECT * FROM t") == []


# https://docs.databricks.com/aws/en/sql/language-manual/delta-update: UPDATE has no FROM clause ("UPDATE ... FROM
# ... JOIN" isn't supported; use MERGE INTO)
def test_update_from_is_refused_as_databricks_has_no_such_clause(cell):
    cell(_SOURCE)
    _databricks_stops_too(cell("UPDATE t SET v = s.w FROM s WHERE t.id = s.id"), "UPDATE ... FROM isn't Databricks SQL")
    assert cell("SELECT * FROM t ORDER BY id") == [[3, "c"], [4, "d"]]
    assert cell("UPDATE t SET v = 'x' WHERE id IN (SELECT id FROM s WHERE w = 'D')") == [[1]]
    assert cell("DELETE FROM t WHERE id IN (SELECT id FROM s WHERE w = 'C')") == [[1]]
    assert cell("SELECT * FROM t") == [[4, "x"]]


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-dml-insert-into: INSERT OVERWRITE truncates
# the table (or the partition) first; REPLACE WHERE deletes the rows matching the condition and fails when a row the
# query returns doesn't match it. DuckDB has neither, so both are refused by name.
@pytest.mark.parametrize("insert, what", [
    ("INSERT OVERWRITE t VALUES (9, 'z')", "INSERT OVERWRITE"),
    ("INSERT OVERWRITE TABLE t SELECT * FROM s", "INSERT OVERWRITE"),
    ("INSERT INTO t REPLACE WHERE id = 3 SELECT 3, 'r'", r"INSERT INTO ... REPLACE WHERE"),
])
def test_insert_overwrite_and_replace_where_are_refused(cell, insert, what):
    cell(_SOURCE)
    _refused(cell(insert), what)
    assert cell("SELECT * FROM t ORDER BY id") == [[3, "c"], [4, "d"]]


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-describe-table: col_name, data_type,
# comment, with Databricks' type names (int, string) and NULL for no comment
def test_describe_table_answers_with_databricks_columns(cell):
    assert cell("CREATE TABLE customer (cust_id INT, name STRING COMMENT 'Short name', state STRING, amount DECIMAL(10,2), "
         "ts TIMESTAMP, ntz TIMESTAMP_NTZ, n BIGINT, ok BOOLEAN, d DATE, x DOUBLE)") == []
    rows = [["cust_id", "int", None], ["name", "string", "Short name"], ["state", "string", None],
            ["amount", "decimal(10,2)", None], ["ts", "timestamp", None], ["ntz", "timestamp_ntz", None],
            ["n", "bigint", None], ["ok", "boolean", None], ["d", "date", None], ["x", "double", None]]
    assert cell("DESCRIBE TABLE customer", columns=True) == (["col_name", "data_type", "comment"], rows)
    assert cell("DESC customer") == rows
    assert cell("DESCRIBE main.customer") == rows


def test_describe_stops_for_a_missing_table_or_a_type_it_cant_name(cell):
    answer = cell("DESCRIBE TABLE nowhere")
    assert answer.startswith("error: ") and "TABLE_OR_VIEW_NOT_FOUND" in answer, answer
    cell("CREATE TABLE nested (a ARRAY<INT>)")
    answer = cell("DESCRIBE nested")
    assert answer.startswith("error: ") and "DESCRIBE TABLE of this column's type can't run on this PC" in answer
    _refused(cell("CREATE TABLE p (a INT); DESCRIBE TABLE EXTENDED p"), "This DESCRIBE")


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-alter-table-manage-column: ADD COLUMNS
# and DROP COLUMNS take a list; added columns go at the end and are NULL for rows already there; a DEFAULT can't be
# given when adding a column to a Delta table (SET DEFAULT afterwards changes new rows only)
def test_add_and_drop_columns_take_a_list(cell):
    cell("CREATE TABLE t (id INT, v STRING); INSERT INTO t VALUES (1, 'a')")
    assert cell("ALTER TABLE t ADD COLUMNS (x INT COMMENT 'the x', y STRING)") == []
    assert cell("SELECT * FROM t", columns=True) == (["id", "v", "x", "y"], [[1, "a", None, None]])
    assert cell("DESCRIBE TABLE t")[2] == ["x", "int", "the x"]
    assert cell("ALTER TABLE t DROP COLUMNS (x, y)") == []
    assert cell("SELECT * FROM t", columns=True) == (["id", "v"], [[1, "a"]])


def test_set_default_changes_new_rows_only_and_add_column_default_is_refused(cell):
    cell("CREATE TABLE t (id INT, v STRING); INSERT INTO t VALUES (1, 'a')")
    _databricks_stops_too(cell("ALTER TABLE t ADD COLUMN w INT DEFAULT 5"),
                          "ALTER TABLE ... ADD COLUMN ... DEFAULT can't run here")
    _refused(cell("ALTER TABLE t ADD COLUMN w INT FIRST"), r"ALTER TABLE ... ADD COLUMN ... FIRST or AFTER")
    _refused(cell("ALTER TABLE t ADD COLUMN w INT AFTER id"), r"ALTER TABLE ... ADD COLUMN ... FIRST or AFTER")
    assert cell("ALTER TABLE t ADD COLUMN w INT; ALTER TABLE t ALTER COLUMN w SET DEFAULT 5") == []
    cell("INSERT INTO t (id, v) VALUES (2, 'b')")
    assert cell("SELECT * FROM t ORDER BY id") == [[1, "a", None], [2, "b", 5]]
    cell("ALTER TABLE t ALTER COLUMN w DROP DEFAULT; INSERT INTO t (id, v) VALUES (3, 'c')")
    assert cell("SELECT w FROM t ORDER BY id") == [[None], [5], [None]]


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-declare-variable: a query reads a
# session variable by its name (or session.name), which DuckDB can't, so DECLARE and SET VARIABLE are refused
@pytest.mark.parametrize("statement, what", [
    ("DECLARE VARIABLE myvar INT DEFAULT 5", "DECLARE VARIABLE"),
    ("DECLARE OR REPLACE myvar STRING", "DECLARE VARIABLE"),
    ("SET VAR myvar = 7", "SET VARIABLE"),
    ("SET VARIABLE myvar = (SELECT 1)", "SET VARIABLE"),
])
def test_session_variables_are_refused(cell, statement, what):
    _refused(cell(statement), what)


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-cache-cache-table (Apache Spark's
# cache, Databricks Runtime), https://docs.databricks.com/aws/en/sql/language-manual/delta-optimize and
# https://docs.databricks.com/sql/language-manual/delta-vacuum.html (a Delta table's files)
@pytest.mark.parametrize("statement, what", [
    ("CACHE TABLE t", "CACHE TABLE"),
    ("CACHE TABLE c AS SELECT * FROM t", "CACHE TABLE"),
    ("UNCACHE TABLE t", "UNCACHE TABLE"),
    ("OPTIMIZE t", "OPTIMIZE"),
    ("OPTIMIZE t ZORDER BY (id)", "OPTIMIZE"),
    ("VACUUM t", "VACUUM"),
    ("VACUUM t RETAIN 168 HOURS", "VACUUM"),
])
def test_cache_and_delta_file_maintenance_are_refused_by_name(cell, statement, what):
    cell("CREATE TABLE t (id INT)")
    _refused(cell(statement), what)


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-setops: INTERSECT before UNION
# and EXCEPT; EXCEPT ALL removes one match for each row of the right side; MINUS is EXCEPT
def test_set_operators_answer_as_databricks(cell):
    cell("CREATE TEMPORARY VIEW number1(c) AS VALUES (3), (1), (2), (2), (3), (4); "
         "CREATE TEMPORARY VIEW number2(c) AS VALUES (5), (1), (1), (2)")
    assert cell("SELECT c FROM number1 EXCEPT SELECT c FROM number2 ORDER BY c") == [[3], [4]]
    assert cell("SELECT c FROM number1 MINUS SELECT c FROM number2 ORDER BY c") == [[3], [4]]
    assert cell("(SELECT c FROM number1) INTERSECT (SELECT c FROM number2) ORDER BY c") == [[1], [2]]
    assert cell("(SELECT c FROM number1) UNION (SELECT c FROM number2) ORDER BY c") == [[1], [2], [3], [4], [5]]
    assert sorted(cell("SELECT c FROM number1 UNION ALL (SELECT c FROM number2)")) == [
        [1], [1], [1], [2], [2], [2], [3], [3], [4], [5]]
    assert sorted(cell("SELECT c FROM number1 EXCEPT ALL (SELECT c FROM number2)")) == [[2], [3], [3], [4]]
    assert sorted(cell("SELECT c FROM number1 MINUS ALL (SELECT c FROM number2)")) == [[2], [3], [3], [4]]
    assert sorted(cell("SELECT 1 UNION ALL SELECT 2 INTERSECT SELECT 2")) == [[1], [2]]  # INTERSECT first
    assert cell("SELECT c FROM number1 UNION SELECT c, c FROM number2").startswith("error: ")


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-groupby (GROUP BY ALL groups by
# the select-list expressions with no aggregate, a global aggregate when there are none) and
# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-orderby (ORDER BY ALL sorts by
# every select-list expression in order; ASC puts NULLs first, DESC last)
def test_group_by_all_and_order_by_all(cell):
    cell("CREATE TEMP VIEW dealer (id, city, car_model, quantity) AS VALUES (100, 'Fremont', 'Honda Civic', 10), "
         "(100, 'Fremont', 'Honda Accord', 15), (100, 'Fremont', 'Honda CRV', 7), (200, 'Dublin', 'Honda Civic', 20), "
         "(200, 'Dublin', 'Honda Accord', 10), (200, 'Dublin', 'Honda CRV', 3), (300, 'San Jose', 'Honda Civic', 5), "
         "(300, 'San Jose', 'Honda Accord', 8)")
    assert sorted(cell("SELECT car_model, count(DISTINCT city) AS count FROM dealer GROUP BY ALL")) == [
        ["Honda Accord", 3], ["Honda CRV", 2], ["Honda Civic", 3]]
    assert cell("SELECT count(*) FROM dealer WHERE id = 1 GROUP BY ALL") == [[0]]
    cell("CREATE TABLE person (id INT, name STRING, age INT); INSERT INTO person VALUES (100, 'John', 30), "
         "(200, 'Mary', NULL), (300, 'Mike', 80), (400, 'Jerry', NULL), (500, 'Dan', 50)")
    assert cell("SELECT * FROM person ORDER BY ALL ASC") == [
        [100, "John", 30], [200, "Mary", None], [300, "Mike", 80], [400, "Jerry", None], [500, "Dan", 50]]
    assert cell("SELECT age, id FROM person ORDER BY ALL") == [[None, 200], [None, 400], [30, 100], [50, 500],
                                                                 [80, 300]]
    assert cell("SELECT age, id FROM person ORDER BY ALL DESC") == [[80, 300], [50, 500], [30, 100], [None, 400],
                                                                      [None, 200]]


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-name-resolution: a later select-list expression can
# use an earlier alias, and a table's own column of that name wins over the alias
def test_lateral_column_aliases(cell):
    assert cell("SELECT c1 AS a, a + c1 FROM VALUES(2, 3) AS T(c1, a)") == [[2, 5]]
    assert cell("SELECT c1 AS a, a + c1 FROM VALUES(2) AS T(c1)") == [[2, 4]]


# https://docs.databricks.com/aws/en/sql/language-manual/functions/table_changes: a Delta table's change data feed,
# which a DuckDB session's tables don't have
def test_table_changes_is_refused_in_a_duckdb_session(cell):
    cell("CREATE TABLE t (id INT)")
    answer = cell("SELECT * FROM table_changes('t', 0)")
    _refused(answer, "table_changes")
    assert "readChangeFeed" in answer
