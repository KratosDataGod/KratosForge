"""WR-231: local T-SQL's hints, query labels and statistics statements as a Fabric Warehouse has them.

Each one is honoured (it changes no rows, and the result's notes say it was left out of the run) or refused by
name. A hint is never dropped without a note, and a refused statement changes nothing.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql, tsql_hints


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


def refused(engine, sql, says):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    text = str(caught.value)
    assert says in text and "Unsupported query option" not in text and "doesn't run T-SQL" not in text, text
    assert engine.execute("SELECT id, s FROM t ORDER BY id")["rows"] == [[1, "a"], [2, "b"], [3, "c"]]
    return text


# ---- OPTION (LABEL = ...)

@pytest.mark.parametrize("label", ["'nightly load'", "N'nächtlich'", "'it''s'", "'OPTION (MAXDOP 1)'",
                                   "'\x01kf-statistics:ZZ'"])  # a typed label that looks like the local mark
def test_a_label_changes_nothing_and_says_so(engine, label):
    rows, notes = run(engine, f"SELECT id FROM t WHERE id < 3 ORDER BY id OPTION (LABEL = {label})")
    assert rows == [[1], [2]] and notes == [tsql_hints.LABEL_NOTE]


def test_a_label_on_a_change_runs_the_change(engine):
    run(engine, "UPDATE t SET s = 'z' WHERE id = 2 OPTION (LABEL = 'fix b')")
    assert engine.execute("SELECT s FROM t WHERE id = 2")["rows"] == [["z"]]


# ---- the query hints Microsoft lists for a warehouse

@pytest.mark.parametrize("hint", ["HASH GROUP", "ORDER GROUP", "FORCE ORDER", "FORCE SINGLE NODE PLAN",
                                  "FORCE DISTRIBUTED PLAN", "DISABLE_RESULT_SET_CACHE",
                                  "USE HINT ('ASSUME_MIN_SELECTIVITY_FOR_FILTER_ESTIMATES')",
                                  ("USE HINT ('ASSUME_FULL_INDEPENDENCE_FOR_FILTER_ESTIMATES', "
                                   "'ASSUME_JOIN_PREDICATE_DEPENDS_ON_FILTERS')")])
def test_a_planning_hint_a_warehouse_takes_changes_no_rows_and_says_so(engine, hint):
    rows, notes = run(engine, f"SELECT id, COUNT(*) FROM t GROUP BY id ORDER BY id OPTION ({hint})")
    assert rows == [[1, 1], [2, 1], [3, 1]]
    assert len(notes) == 1 and "changes how a warehouse plans the query, not its rows" in notes[0]


@pytest.mark.parametrize("hint", ["MERGE UNION", "HASH UNION", "CONCAT UNION"])
def test_a_union_hint_changes_no_rows(engine, hint):
    rows, notes = run(engine, f"SELECT id FROM t WHERE id = 1 UNION SELECT id FROM t WHERE id = 3 OPTION ({hint})")
    assert sorted(rows) == [[1], [3]] and f"OPTION ({hint})" in notes[0]


def test_several_hints_and_a_label_each_get_a_note(engine):
    rows, notes = run(engine, "SELECT id FROM t ORDER BY id OPTION (LABEL = 'x', FORCE ORDER, HASH GROUP)")
    assert rows == [[1], [2], [3]] and len(notes) == 3 and notes[0] == tsql_hints.LABEL_NOTE


@pytest.mark.parametrize("hint, says", [
    ("MAXDOP 1", "OPTION (MAXDOP) isn't carried yet"),
    ("RECOMPILE", "OPTION (RECOMPILE) isn't carried yet"),
    ("LOOP JOIN", "OPTION (LOOP JOIN) isn't carried yet"),
    ("FAST 10", "OPTION (FAST) isn't carried yet"),
    ("USE HINT ('ENABLE_PARALLEL_PLAN_PREFERENCE')", "USE HINT ('ENABLE_PARALLEL_PLAN_PREFERENCE') isn't carried"),
    ("LABEL = 'x', MAXDOP 1", "OPTION (MAXDOP) isn't carried yet"),
])
def test_a_hint_microsoft_doesnt_list_for_a_warehouse_is_refused_by_name(engine, hint, says):
    refused(engine, f"SELECT id FROM t OPTION ({hint})", says)
    refused(engine, f"UPDATE t SET s = 'z' OPTION ({hint})", says)


def test_option_words_in_a_string_or_comment_are_left_alone(engine):
    rows, notes = run(engine, "SELECT 'OPTION (FORCE SINGLE NODE PLAN)' AS x /* OPTION (MAXDOP 1) */")
    assert rows == [["OPTION (FORCE SINGLE NODE PLAN)"]] and notes == []


# ---- join and table hints

@pytest.mark.parametrize("join", ["INNER HASH JOIN", "INNER LOOP JOIN", "INNER MERGE JOIN", "LEFT HASH JOIN"])
def test_a_join_hint_changes_no_rows_and_says_so(engine, join):
    rows, notes = run(engine, f"SELECT a.id FROM t a {join} t b ON a.id = b.id ORDER BY a.id")
    assert rows == [[1], [2], [3]] and len(notes) == 1 and "join hint" in notes[0]


def test_a_join_hint_a_warehouse_doesnt_list_is_refused_by_name(engine):
    refused(engine, "SELECT t.id FROM t INNER REMOTE JOIN t u ON t.id = u.id", "the join hint REMOTE JOIN isn't carried")


@pytest.mark.parametrize("sql", ["SELECT id FROM t WITH (NOLOCK) ORDER BY id", "SELECT id FROM t (NOLOCK) ORDER BY id",
                                 "SELECT id FROM t AS x WITH (READUNCOMMITTED) ORDER BY id",
                                 "SELECT id FROM t WITH (READPAST, ROWLOCK) ORDER BY id",
                                 "SELECT id FROM t WITH (HOLDLOCK) ORDER BY id"])
def test_a_locking_hint_reads_the_same_rows_and_says_so(engine, sql):
    rows, notes = run(engine, sql)
    assert rows == [[1], [2], [3]] and notes and all("snapshot reads" in n for n in notes)


def test_a_locking_hint_on_a_change_runs_the_change_and_says_so(engine):
    rows, notes = run(engine, "UPDATE t WITH (TABLOCK) SET s = 'z' WHERE id = 2")
    assert engine.execute("SELECT s FROM t WHERE id = 2")["rows"] == [["z"]]
    assert notes == [tsql_hints.LOCK_NOTE.format(hint="WITH (TABLOCK)")]


@pytest.mark.parametrize("hint", ["INDEX(ix)", "INDEX(1)", "FORCESEEK", "FORCESCAN", "NOEXPAND"])
def test_an_index_hint_is_refused_by_name(engine, hint):
    refused(engine, f"SELECT id FROM t WITH ({hint})", "isn't carried yet. A Fabric Warehouse has no indexes")


def test_no_hint_is_ever_left_out_without_a_note():
    for sql in ("SELECT id FROM t WITH (NOLOCK)", "SELECT a.id FROM t a INNER HASH JOIN t b ON a.id = b.id",
                "SELECT id FROM t OPTION (FORCE ORDER)", "SELECT id FROM t OPTION (LABEL = 'x')"):
        texts, notes = tsql.translate(sql)
        assert notes and "NOLOCK" not in texts[0] and "HASH" not in texts[0] and "OPTION" not in texts[0], sql


# ---- statistics

@pytest.mark.parametrize("sql, verb", [
    ("CREATE STATISTICS st ON t (id)", "CREATE"),
    ("CREATE STATISTICS st ON dbo.t (id) WITH FULLSCAN", "CREATE"),
    ("CREATE STATISTICS [st 2] ON [dbo].[t] ([s]) WITH SAMPLE 50 PERCENT", "CREATE"),
    ("UPDATE STATISTICS t", "UPDATE"),
    ("UPDATE STATISTICS dbo.t (st)", "UPDATE"),
    ("UPDATE STATISTICS t st WITH FULLSCAN", "UPDATE"),
    ("UPDATE STATISTICS t (st) WITH SAMPLE 20 PERCENT", "UPDATE"),
    ("DROP STATISTICS t.st", "DROP"),
    ("DROP STATISTICS dbo.t.st, dbo.t.st2", "DROP"),
])
def test_a_statistics_statement_a_warehouse_takes_changes_nothing_and_says_so(engine, sql, verb):
    rows, notes = run(engine, sql)
    assert rows == [] and notes == [tsql_hints._STATS_NOTE[verb]]
    assert engine.execute("SELECT id, s FROM t ORDER BY id")["rows"] == [[1, "a"], [2, "b"], [3, "c"]]


def test_statistics_beside_other_statements_leave_them_to_run(engine):
    rows, notes = run(engine, "CREATE STATISTICS st ON t (id); UPDATE STATISTICS t\nSELECT COUNT(*) FROM t")
    assert rows == [[3]] and len(notes) == 2


@pytest.mark.parametrize("sql", ["CREATE STATISTICS st ON missing (id)", "CREATE STATISTICS st ON t (nope)",
                                 "UPDATE STATISTICS missing", "DROP STATISTICS missing.st"])
def test_statistics_on_a_table_or_column_that_isnt_there_is_an_error(engine, sql):
    with pytest.raises(Exception, match="missing|nope"):
        engine.execute(sql, dialect="tsql")


def test_a_table_called_statistics_is_still_a_table(engine):
    engine.execute("CREATE TABLE statistics (x INT)")
    run(engine, "INSERT INTO statistics VALUES (1)")
    assert run(engine, "SELECT x FROM statistics") == ([[1]], [])
    for sql in ("UPDATE statistics SET x = 2", "UPDATE dbo.statistics SET x = 2"):
        assert tsql_hints.take(sql, tsql.KfTSQL().tokenize) == sql


@pytest.mark.parametrize("sql, says", [
    ("CREATE STATISTICS st ON t (id, s)", "Multi-column statistics aren't supported in a Fabric Warehouse"),
    ("CREATE STATISTICS st ON t (id) WHERE id > 1", "filtered statistics"),
    ("CREATE STATISTICS st ON t (id) WITH NORECOMPUTE", "the statistics option NORECOMPUTE isn't carried yet"),
    ("UPDATE STATISTICS t WITH RESAMPLE", "the statistics option RESAMPLE isn't carried yet"),
    ("DROP STATISTICS st", "DROP STATISTICS names table.statistic"),
])
def test_what_a_warehouse_doesnt_take_is_refused_by_name(engine, sql, says):
    refused(engine, sql, says)
