"""Mutation batch 150: what a warehouse user sees from local T-SQL's hints, labels and statistics (tsql_hints.py).

Each test runs T-SQL through the local engine and checks the rows and notes it gives, or the exact words it stops
with. A refused statement changes nothing.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Hints")["id"])
    e.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, s)")
    yield e
    data.close_all()


def run(engine, sql):
    result = engine.execute(sql, dialect="tsql")
    return result["rows"], result["translation"]["notes"]


def refused(engine, sql):
    with pytest.raises(ValueError) as caught:
        engine.execute(sql, dialect="tsql")
    assert engine.execute("SELECT id, s FROM t ORDER BY id")["rows"] == [[1, "a"], [2, "b"], [3, "c"]]
    return str(caught.value)


def test_a_batch_that_cant_be_tokenized_says_so(engine):
    # the hints pass leaves a batch it can't read to the parser, which names what it can't read
    assert refused(engine, "SELECT 'OPTION FROM t").startswith("Could not read the query as T-SQL: Error tokenizing")


def test_print_before_a_labelled_query_is_refused_as_print(engine):
    # PRINT is a command token like UPDATE STATISTICS, but isn't read as a statistics statement
    text = refused(engine, "PRINT 'hi'; SELECT id FROM t OPTION (LABEL = 'x')")
    assert text.startswith("The local engine doesn't run T-SQL PRINT yet.")


def test_a_table_called_option_is_still_a_table(engine):
    # [option] followed by a column list reads like OPTION (...), but has no hint in it, so it is left as written
    engine.execute('CREATE TABLE "option" (id INT)')
    assert run(engine, "INSERT INTO [option] (id) VALUES (5)") == ([[1]], [])
    assert run(engine, "SELECT id FROM [option]") == ([[5]], [])


@pytest.mark.parametrize("option", ["FORCE DISTRIBUTED PLAN, LABEL = 'nightly'", "FORCE ORDER, FORCE SINGLE NODE PLAN"])
def test_a_hint_sqlglot_cant_read_beside_another_option_keeps_both(engine, option):
    rows, notes = run(engine, f"SELECT id FROM t ORDER BY id OPTION ({option})")
    assert rows == [[1], [2], [3]] and len(notes) == 2


_NOT_YET = "This T-SQL can't be run on the local engine: "
_SHAPE = _NOT_YET + "CREATE STATISTICS is read here as CREATE STATISTICS s ON dbo.t (c)."


@pytest.mark.parametrize("sql, says", [
    ("CREATE STATISTICS st t (id)", _SHAPE),  # no ON
    ("CREATE STATISTICS st", _SHAPE),  # ends before ON
    ("CREATE STATISTICS st ON t", _SHAPE),  # ends before the column
    ("CREATE STATISTICS st ON t id", _SHAPE),  # the column not in brackets
    ("UPDATE STATISTICS", _NOT_YET + "this statistics statement isn't written as T-SQL has it"),
    ("CREATE STATISTICS", _NOT_YET + "this statistics statement isn't written as T-SQL has it"),
    ("DROP STATISTICS", _NOT_YET + "this statistics statement isn't written as T-SQL has it"),
    ("DROP STATISTICS t.st x", _NOT_YET + "DROP STATISTICS is followed by x, which isn't read here."),
])
def test_a_statistics_statement_not_written_as_t_sql_has_it_is_refused_with_the_shape(engine, sql, says):
    assert refused(engine, sql).startswith(says)


@pytest.mark.parametrize("sql", ["CREATE STATISTICS st ON t (id) WITH SAMPLE 5", "UPDATE STATISTICS t WITH SAMPLE 5",
                                 "UPDATE STATISTICS t WITH SAMPLE", "UPDATE STATISTICS t WITH SAMPLE 5; SELECT 1",
                                 "UPDATE STATISTICS t WITH SAMPLE x PERCENT"])
def test_a_sample_without_a_number_and_percent_or_rows_is_refused_by_name(engine, sql):
    assert refused(engine, sql).startswith(_NOT_YET + "SAMPLE in a statistics statement needs a number")


@pytest.mark.parametrize("sql", ["SELECT id FROM t OPTION (LABEL = 'x'", "SELECT id FROM t OPTION (FORCE ORDER"])
def test_an_option_without_its_closing_bracket_stops_with_the_parsers_words(engine, sql):
    assert refused(engine, sql).startswith("Could not read the query as T-SQL: Expecting ).")



@pytest.mark.parametrize("word", ["create", "update", "drop", "option"])
def test_a_column_named_like_a_hint_word_at_the_end_of_the_batch_runs(engine, word):
    # the batch says STATISTICS, so the hints pass reads it, and its last word starts a statement or an OPTION
    # with nothing after it
    assert run(engine, f"SELECT 'STATISTICS' AS [{word}]") == ([["STATISTICS"]], [])


@pytest.mark.parametrize("hint, word", [("INDEX(ix)", "INDEX"), ("FORCESEEK", "FORCESEEK")])
def test_an_index_hint_is_refused_by_its_own_name(engine, hint, word):
    assert refused(engine, f"SELECT id FROM t WITH ({hint})") == (
        _NOT_YET + f"the table hint {word} isn't carried yet. A Fabric Warehouse has no indexes to name, and "
        "Microsoft's pages don't list table hints for it. Remove the hint.")


_PLANS = ("changes how a warehouse plans the query, not its rows. The local engine plans on its own, so it's left "
          "out of the run here.")


@pytest.mark.parametrize("hint, named", [
    ("FORCE SINGLE NODE PLAN", "FORCE SINGLE NODE PLAN"),
    ("DISABLE_RESULT_SET_CACHE", "DISABLE_RESULT_SET_CACHE"),
    ("USE HINT ('ASSUME_MIN_SELECTIVITY_FOR_FILTER_ESTIMATES')", "USE HINT"),
])
def test_a_hint_sqlglot_cant_read_is_named_in_its_note(engine, hint, named):
    rows, notes = run(engine, f"SELECT id FROM t ORDER BY id OPTION ({hint})")
    assert rows == [[1], [2], [3]] and notes == [f"OPTION ({named}) {_PLANS}"]


def test_a_one_word_hint_beside_one_sqlglot_cant_read_is_still_refused_by_name(engine):
    assert refused(engine, "SELECT id FROM t OPTION (RECOMPILE, FORCE SINGLE NODE PLAN)").startswith(
        _NOT_YET + "OPTION (RECOMPILE) isn't carried yet.")
