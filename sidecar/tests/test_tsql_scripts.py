"""T-SQL scripts on the local engine (WR-214): what a script does that isn't one statement.

- Variables, control flow, cursors and transactions are refused by name, never left to DuckDB's syntax error.
- ``GO`` splits batches; ``GO n`` is refused by name.
- An error part way through a script names the statement and says what ran before it.
- ``UPDATE ... SET`` and the WHERE of an UPDATE or DELETE follow T-SQL's types: ``v + 'x'`` joins text (it was a
  DuckDB binder error), and text compares without trailing spaces."""
from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate

LATER = "The local engine doesn't run T-SQL {}"


@pytest.fixture()
def local(tmp_path):
    store = LocalData(tmp_path / "app" / "local-data")
    yield store
    store.close_all()


@pytest.fixture()
def engine(local):
    e = local.engine(local.create("Scripts")["id"])
    e.execute("CREATE TABLE dbo.t (id INT, v VARCHAR(20), n INT)", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, 'a', 10), (2, 'b  ', 20)", dialect="tsql")
    return e


def run(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


@pytest.mark.parametrize(("sql", "what"), [
    ("RETURN", "RETURN"),
    ("SELECT 1\nRETURN", "RETURN"),
    ("BREAK", "BREAK"),
    ("CONTINUE", "CONTINUE"),
    ("GOTO done", "GOTO"),
    ("done:\nSELECT 1", r"labels \(done:\)"),
    ("THROW 50000, 'Stop', 1", "THROW"),
    ("SELECT 1;\nTHROW 50000, 'Stop', 1", "THROW"),
    ("WAITFOR DELAY '00:00:01'", r"WAITFOR yet\. Run it in a Fabric Warehouse\.$"),
    ("CHECKPOINT", "CHECKPOINT"),
    ("OPEN c", r"cursors \(OPEN\) yet\. Run it in a Fabric Warehouse\.$"),
    ("CLOSE c", r"cursors \(CLOSE\)"),
    ("DEALLOCATE c", r"cursors \(DEALLOCATE\)"),
    ("SAVE TRANSACTION s", r"transactions \(SAVE TRANSACTION\)"),
    ("SAVE TRAN s", r"transactions \(SAVE TRANSACTION\)"),
    ("SET TRANSACTION ISOLATION LEVEL SNAPSHOT", "SET TRANSACTION"),
    ("DECLARE @x INT = 1", r"variables \(DECLARE\)"),
    ("SELECT * FROM dbo.t WHERE id = @x", r"variables \(@x\)"),
    ("IF 1 = 1 SELECT 1", "IF blocks"),
    ("WHILE 1 = 0 SELECT 1", "WHILE loops"),
    ("BEGIN TRY SELECT 1 END TRY BEGIN CATCH SELECT 2 END CATCH", "BEGIN"),
    ("BEGIN TRANSACTION", r"transactions \(BEGIN TRAN\)"),
])
def test_control_flow_is_refused_by_name(engine, sql, what):
    with pytest.raises(ValueError, match=LATER.format(what)):
        engine.execute(sql, dialect="tsql")


def test_reserved_words_quoted_or_after_a_dot_or_at_are_names(engine):
    run(engine, "CREATE TABLE dbo.w ([open] INT, [return] INT, throw INT)")
    assert run(engine, "INSERT INTO dbo.w VALUES (1, 2, 3)") == [[1]]
    assert run(engine, "SELECT w.[open], [return], throw FROM dbo.w AS w") == [[1, 2, 3]]
    with pytest.raises(ValueError, match="variables"):
        engine.execute("DECLARE @return INT", dialect="tsql")


def test_a_procedure_keeps_its_own_refusal():
    with pytest.raises(ValueError, match="CREATE FUNCTION dbo.f isn.t mapped yet. Procedures and user-defined functions take parameters"):
        translate("CREATE FUNCTION dbo.f() RETURNS INT AS BEGIN RETURN 1 END")


def test_go_splits_batches_and_go_n_is_refused(engine):
    assert run(engine, "INSERT INTO dbo.t VALUES (3, 'c', 30)\nGO\nSELECT COUNT(*) FROM dbo.t\ngo -- done\n") == [[3]]
    with pytest.raises(ValueError, match=r"GO 2 \(run the batch 2 times\) isn't mapped yet"):
        engine.execute("INSERT INTO dbo.t VALUES (3, 'c', 30)\nGO 2", dialect="tsql")
    assert run(engine, "SELECT COUNT(*) FROM dbo.t") == [[3]]


def test_only_session_options_says_theres_nothing_to_run(engine):
    with pytest.raises(ValueError, match="There's nothing to run: SET NOCOUNT"):
        engine.execute("SET NOCOUNT ON", dialect="tsql")


def test_an_error_part_way_names_the_statement_and_keeps_what_ran(engine):
    with pytest.raises(Exception, match=r"Statement 2 of 3 \(SELECT\) failed: .*Divide by zero error encountered\. "
                                        r"Statement 1 ran, and what it changed is kept; the last statement didn't run\."):
        engine.execute("INSERT INTO dbo.t VALUES (3, 'c', 30); SELECT 1 / 0; INSERT INTO dbo.t VALUES (4, 'd', 40)",
                       dialect="tsql")
    assert run(engine, "SELECT id FROM dbo.t ORDER BY id") == [[1], [2], [3]]


def test_an_error_in_the_first_or_last_statement(engine):
    with pytest.raises(Exception, match=r"(?s)Statement 1 of 3 \(SELECT\) failed: .*nope.* Nothing before it ran; the 2 "
                                        r"after it didn't run\."):
        engine.execute("SELECT * FROM nope; SELECT 1; SELECT 2", dialect="tsql")
    with pytest.raises(Exception, match=r"Statement 3 of 3 \(UPDATE\) failed: .* Statements 1 to 2 ran, and what they "
                                        r"changed is kept\.$"):
        engine.execute("UPDATE dbo.t SET n = 0 WHERE id = 1; SELECT 1; UPDATE dbo.t SET n = n / 0", dialect="tsql")
    assert run(engine, "SELECT n FROM dbo.t ORDER BY id") == [[0], [20]]


def test_one_statement_keeps_its_own_error(engine):
    with pytest.raises(Exception) as err:
        engine.execute("SELECT 1 / 0", dialect="tsql")
    assert "Statement" not in str(err.value)


def test_update_set_adds_text_as_tsql_does(engine):
    assert run(engine, "UPDATE dbo.t SET v = v + 'x' WHERE id = 1") == [[1]]
    assert run(engine, "UPDATE dbo.t SET v = '>' + v, n = n / 3 WHERE id = 1") == [[1]]
    assert run(engine, "SELECT v, n FROM dbo.t WHERE id = 1") == [[">ax", 3]]


def test_update_and_delete_compare_text_without_trailing_spaces(engine):
    assert run(engine, "UPDATE dbo.t SET n = n + 1 WHERE v = 'b'") == [[1]]  # 'b  ' = 'b' in T-SQL
    assert run(engine, "SELECT n FROM dbo.t WHERE id = 2") == [[21]]
    assert run(engine, "DELETE FROM dbo.t WHERE v IN ('b')") == [[1]]
    assert run(engine, "SELECT id FROM dbo.t") == [[1]]


def test_update_from_another_table_is_typed(engine):
    columns = {"dbo": {"t": {"id": "INTEGER", "v": "VARCHAR"}, "s": {"id": "INTEGER", "w": "VARCHAR"}}}
    texts, notes = translate("UPDATE x SET v = x.v + s.w FROM dbo.t AS x JOIN dbo.s AS s ON s.w = x.v "
                             "WHERE x.id = 1", columns)
    assert texts == [("UPDATE dbo.t AS x SET v = x.v || s.w FROM dbo.s AS s WHERE (RTRIM(s.w, ' ') = RTRIM(x.v, ' ')) "
                      "AND (x.id = 1)")]
    assert notes == []
    run(engine, "CREATE TABLE dbo.s (id INT, w VARCHAR(5))")
    run(engine, "INSERT INTO dbo.s VALUES (1, 'z'), (2, 'y')")
    assert run(engine, "UPDATE x SET v = x.v + s.w FROM dbo.t AS x JOIN dbo.s AS s ON s.id = x.id "
                       "WHERE x.id = 1") == [[1]]
    assert run(engine, "SELECT v FROM dbo.t ORDER BY id") == [["az"], ["b  "]]


def test_the_set_side_is_never_read_as_a_comparison():
    texts, notes = translate("UPDATE dbo.t SET v = 'a  ' WHERE v = 'a'", {"dbo": {"t": {"v": "VARCHAR"}}})
    assert texts[0].startswith("UPDATE dbo.t SET v = 'a  ' WHERE")
    assert notes == []


NBSP, IDEO = "\u00a0", "\u3000"  # whitespace T-SQL's trailing-space rules don't take off; DuckDB's RTRIM(x) did


@pytest.fixture()
def spaced(engine):
    run(engine, "DELETE FROM dbo.t")
    run(engine, f"INSERT INTO dbo.t VALUES (1, 'a', 1), (2, 'a ', 2), (3, N'a{NBSP}', 3), (4, N'a{IDEO}', 4)")
    run(engine, "CREATE TABLE dbo.s (w VARCHAR(5))")
    run(engine, "INSERT INTO dbo.s VALUES ('a')")
    return engine


@pytest.mark.parametrize("where", ["v IN ('a', 'b')", "v LIKE 'a'", "v = 'a'", "v <= 'a'",
                                   "EXISTS (SELECT 1 FROM dbo.s WHERE s.w = t.v)"])
def test_only_spaces_are_trailing_spaces_in_a_write(spaced, where):
    assert run(spaced, f"DELETE FROM dbo.t WHERE {where}") == [[2]]
    assert run(spaced, "SELECT id FROM dbo.t ORDER BY id") == [[3], [4]]


def test_only_spaces_are_trailing_spaces_in_a_join_and_rtrim_and_len(spaced):
    assert run(spaced, "SELECT t.id FROM dbo.t JOIN dbo.s ON s.w = t.v ORDER BY 1") == [[1], [2]]
    assert run(spaced, "SELECT id, LEN(v), LEN(RTRIM(v)), LEN(TRIM(v)), LEN(LTRIM(v)) FROM dbo.t ORDER BY id") == [
        [1, 1, 1, 1, 1], [2, 1, 1, 1, 1], [3, 2, 2, 2, 2], [4, 2, 2, 2, 2]]
