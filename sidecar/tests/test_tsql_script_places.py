"""WR-256: a statement of a T-SQL script is named by its place in the user's own count, and a batch that can't be
read is named by its place and its lines.

WR-238 named a refused statement, but skipped SET NOCOUNT and the other harmless session settings when it counted,
so "SET NOCOUNT ON; SELECT 1; <refused>" said "Statement 2 of 2" where the user counts 3. The settings are still
skipped, but now they count, both for a refusal before anything runs and for an error part way through a script.
A batch between GO lines that can't be read at all is named "Batch 2 of 3 (lines 3 to 4)", in the user's lines.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql

ENDING = " None of the script ran, because every statement is checked before the first one runs."
DUPLICATE = "WITH a AS (SELECT 1 AS x), a AS (SELECT 2 AS x) SELECT x FROM a"


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    yield data.engine(data.create("Places")["id"])
    data.close_all()


def error(engine, sql):
    with pytest.raises(Exception) as caught:  # noqa: B017 - a run-time error keeps DuckDB's own error type
        engine.execute(sql, dialect="tsql")
    return str(caught.value)


# ---------------------------------------------------------------- refusals, before anything runs

@pytest.mark.parametrize("sql, place", [
    (f"SET NOCOUNT ON;\nSELECT 1;\n{DUPLICATE}", "Statement 3 of 3 (WITH)"),
    (f"SELECT 1;\n{DUPLICATE};\nSET NOCOUNT ON", "Statement 2 of 3 (WITH)"),
    (f"SET NOCOUNT ON;\nSET ANSI_NULLS ON;\n{DUPLICATE};\nSELECT 2", "Statement 3 of 4 (WITH)"),
    (f"SET NOCOUNT ON;\n{DUPLICATE}", "Statement 2 of 2 (WITH)"),
    (f"SET NOCOUNT ON\nGO\nSELECT 1\nGO\n{DUPLICATE}", "Statement 3 of 3 (WITH)"),
])
def test_a_refusal_counts_the_session_settings_as_the_user_does(engine, sql, place):
    assert error(engine, sql) == (f"{place} was refused: Msg 239, Level 16: Duplicate common table expression name "
                                  f"'a' was specified.{ENDING}")


def test_a_setting_the_local_engine_refuses_is_named_by_its_place_too(engine):
    text = error(engine, "SELECT 1;\nSET DATEFIRST 1;\nSELECT 2")
    assert text.startswith("Statement 2 of 3 (SET) was refused: The local engine counts weeks from Sunday"), text
    assert text.endswith(ENDING), text


def test_nothing_ran_when_a_statement_after_a_setting_is_refused(engine):
    text = error(engine, f"SET NOCOUNT ON;\nCREATE TABLE made AS SELECT 1 AS x;\n{DUPLICATE}")
    assert text.startswith("Statement 3 of 3 (WITH) was refused: Msg 239"), text
    assert engine.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'made'")["rows"] == [[0]]


# ---------------------------------------------------------------- errors part way through a script

@pytest.mark.parametrize("sql, words", [
    ("SET NOCOUNT ON;\nCREATE TABLE t (x INT);\nINSERT INTO t SELECT 1/0;\nSELECT 2",
     ("Statement 3 of 4 (INSERT) failed: Msg 8134, Level 16: Divide by zero error encountered. Statements 1 to 2 ran, "
      "and what they changed is kept; the last statement didn't run.")),
    ("CREATE TABLE t (x INT);\nINSERT INTO t SELECT 1/0;\nSET NOCOUNT ON",
     ("Statement 2 of 3 (INSERT) failed: Msg 8134, Level 16: Divide by zero error encountered. Statement 1 ran, and "
      "what it changed is kept; the last statement didn't run.")),
    ("SET NOCOUNT ON;\nSELECT 1/0",
     ("Statement 2 of 2 (SELECT) failed: Msg 8134, Level 16: Divide by zero error encountered. Statement 1 ran, and "
      "what it changed is kept.")),
    ("SELECT 1/0;\nSET NOCOUNT ON;\nSET ANSI_WARNINGS ON",
     ("Statement 1 of 3 (SELECT) failed: Msg 8134, Level 16: Divide by zero error encountered. Nothing before it ran; "
      "the 2 after it didn't run.")),
])
def test_an_error_part_way_counts_the_session_settings_too(engine, sql, words):
    assert error(engine, sql) == words


def test_what_ran_before_the_error_is_kept_with_the_users_count(engine):
    text = error(engine, "SET NOCOUNT ON;\nCREATE TABLE t (x INT);\nINSERT INTO t VALUES (1);\nGO\n"
                         "INSERT INTO t SELECT 1/0;\nINSERT INTO t VALUES (2)")
    assert text.startswith("Statement 4 of 5 (INSERT) failed: Msg 8134"), text
    assert "Statements 1 to 3 ran" in text and text.endswith("the last statement didn't run."), text
    assert engine.execute("SELECT x FROM t")["rows"] == [[1]]


# ---------------------------------------------------------------- the statement's kind, as the user wrote it

@pytest.mark.parametrize("sql, kind", [
    ("SELECT 1 AS x", "SELECT"),
    ("SELECT 1 AS x UNION SELECT 2", "SELECT"),
    ("WITH c AS (SELECT 1 AS x) SELECT x FROM c", "WITH"),
    ("WITH c AS (SELECT 1 AS x) INSERT INTO t SELECT x FROM c", "WITH"),
    ("INSERT INTO t VALUES (1)", "INSERT"),
    ("UPDATE t SET x = 2", "UPDATE"),
    ("DELETE FROM t", "DELETE"),
    ("MERGE INTO t USING s ON t.x = s.x WHEN MATCHED THEN DELETE;", "MERGE"),
    ("CREATE TABLE t (x INT)", "CREATE"),
    ("DROP TABLE t", "DROP"),
    ("TRUNCATE TABLE t", "TRUNCATE"),
    ("SET NOCOUNT ON", "SET"),
])
def test_a_statement_is_named_by_its_first_word(sql, kind):
    trees = [t for t in tsql._parse(sql) if not isinstance(t, tsql.exp.Semicolon)]
    assert [tsql._statement_kind(t) for t in trees] == [kind]


def test_a_with_that_inserts_is_named_with_when_it_fails_as_it_runs(engine):
    text = error(engine, "CREATE TABLE t (x INT);\nWITH c AS (SELECT 0 AS x) INSERT INTO t SELECT 1 / x FROM c")
    assert text.startswith("Statement 2 of 2 (WITH) failed: Msg 8134"), text


def test_a_select_into_is_named_select_as_the_user_wrote_it(engine):
    text = error(engine, "SET NOCOUNT ON;\nSELECT 1 / 0 AS x INTO made")
    assert text.startswith("Statement 2 of 2 (SELECT) failed: Msg 8134"), text


def test_the_script_says_each_statements_place_and_kind():
    script = tsql.translate_script("SET NOCOUNT ON;\nSELECT 1 AS a;\nSET ANSI_NULLS ON\nGO\n"
                                   "WITH c AS (SELECT 1 AS x) SELECT x FROM c")
    assert script.count == 4
    assert script.places == [1, 3] and script.kinds == ["SELECT", "WITH"] and len(script.texts) == 2
    assert tsql.translate("SET NOCOUNT ON;\nSELECT 1 AS a") == (script.texts[:1], [])


def test_the_translator_counts_the_settings_for_other_callers():
    with pytest.raises(tsql.InScript) as caught:
        tsql.translate("SET NOCOUNT ON;\nSELECT 1;\nSELECT 3 OPTION (MAXDOP 1)")
    assert (caught.value.index, caught.value.count, caught.value.kind) == (2, 3, "SELECT")
    assert caught.value.place == "Statement 3 of 3 (SELECT)"


# ---------------------------------------------------------------- batches that can't be read

def test_a_batch_that_cant_be_read_is_named_by_its_place_and_lines(engine):
    text = error(engine, "SELECT 1\nGO\nSELECT (\n  1 +\nGO\nSELECT 3")
    assert text.startswith("Batch 2 of 3 (lines 3 to 4) was refused: Could not read the query as T-SQL"), text
    assert text.endswith(ENDING), text


def test_batch_lines_with_crlf_endings_and_comments(engine):
    sql = "SELECT 1\r\nGO\r\n\r\n-- why\r\n/* a\r\n   b */\r\nSELECT (\r\n\r\nGO\r\nSELECT 3\r\n"
    text = error(engine, sql)
    assert text.startswith("Batch 2 of 3 (lines 4 to 7) was refused: "), text
    assert "Line 7" in text, text  # sqlglot's line is the user's line, not the batch's


def test_a_batch_of_one_line_says_line(engine):
    text = error(engine, "SELECT 1\nGO\nSELECT (")
    assert text.startswith("Batch 2 of 2 (line 3) was refused: "), text


def test_a_missing_semicolon_names_the_users_line(engine):
    text = error(engine, "SELECT 1\nGO\nSELECT 1\nSELECT 2\nGO")
    assert text.startswith("Batch 2 of 2 (lines 3 to 4) was refused: "), text
    assert "the SELECT on line 4 starts a new statement" in text, text


def test_a_batch_error_runs_nothing(engine):
    text = error(engine, "CREATE TABLE made AS SELECT 1 AS x\nGO\nSELECT (")
    assert text.startswith("Batch 2 of 2 (line 3) was refused: "), text
    assert engine.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'made'")["rows"] == [[0]]


def test_the_translator_says_which_batch_for_other_callers():
    with pytest.raises(tsql.InBatch) as caught:
        tsql.translate("SELECT 1\nGO\n\nSELECT (\nGO\nSELECT 3")
    assert (caught.value.index, caught.value.count, caught.value.first, caught.value.last) == (1, 3, 4, 4)
    assert caught.value.place == "Batch 2 of 3 (line 4)" and isinstance(caught.value, ValueError)


def test_batches_and_their_lines():
    assert tsql._batches("SELECT 1\nGO\n\n  SELECT 2\r\n  -- c\r\nGO\n") == [
        ("SELECT 1\n", 1, 1, 0), ("\n\n  SELECT 2\r\n  -- c\r\n", 4, 5, 1)]
    assert tsql.split_batches("SELECT 1\nGO\nSELECT 2") == ["SELECT 1\n", "\nSELECT 2"]


# ---------------------------------------------------------------- one statement keeps its plain message

@pytest.mark.parametrize("sql", [DUPLICATE, f"{DUPLICATE}\nGO", f"\n\n{DUPLICATE};"])
def test_a_script_of_one_statement_keeps_its_plain_refusal(engine, sql):
    assert error(engine, sql) == "Msg 239, Level 16: Duplicate common table expression name 'a' was specified."


def test_one_batch_that_cant_be_read_keeps_its_plain_message(engine):
    assert error(engine, "SELECT (").startswith("Could not read the query as T-SQL: ")


def test_one_statement_that_fails_as_it_runs_keeps_its_plain_error(engine):
    assert error(engine, "SELECT 1/0") == "Msg 8134, Level 16: Divide by zero error encountered."
