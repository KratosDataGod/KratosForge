"""WR-705: local T-SQL control flow, as a Fabric Warehouse runs it.

These tests cover:
- IF and ELSE, with a statement or a BEGIN ... END block, nested, and IF EXISTS;
- WHILE with BREAK and CONTINUE, and BREAK or CONTINUE outside a loop (Msg 135, 136);
- RETURN, which ends the batch, and the next batch after GO, which still runs;
- a condition that isn't true or false (Msg 4145);
- Delta writes inside a loop, through the engine's usual path, and a failure midway that says what already changed;
- a WHILE with no end, stopped by the time limit or by Stop, with no cap on how many times it goes round;
- the results: the last SELECT's rows, as the local engine returns one result.
"""

from __future__ import annotations

import re
import threading
import time

import pytest

from fabric_desktop.localdata import LocalData, QueryCancelled


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Control")["id"])
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, name)")
    e.execute("CREATE TABLE dbo.dt USING DELTA AS SELECT * FROM (VALUES (1, 10), (2, 20), (3, 30)) v(id, v)",
              dialect="duckdb")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def messages(engine, sql):
    return engine.execute(sql, dialect="tsql")["messages"]


def refused(engine, sql, words):
    with pytest.raises(ValueError, match=re.escape(words)):
        engine.execute(sql, dialect="tsql")


@pytest.mark.parametrize("x, want", [(5, ["big", "really"]), (1, ["small"])])
def test_if_else_with_a_block(engine, x, want):
    sql = f"DECLARE @x INT = {x}\nIF @x > 3\nBEGIN\n  PRINT 'big'\n  PRINT 'really'\nEND\nELSE\n  PRINT 'small'"
    assert messages(engine, sql) == want


def test_if_without_else_that_isnt_true_runs_nothing(engine):
    assert messages(engine, "IF 1 = 2 PRINT 'no'") == []


def test_if_with_null_is_not_true(engine):
    assert messages(engine, "DECLARE @x INT; IF @x = 1 PRINT 'a' ELSE PRINT 'b'") == ["b"]


def test_if_exists(engine):
    assert messages(engine, "IF EXISTS (SELECT 1 FROM dbo.t WHERE id = 2) PRINT 'yes' ELSE PRINT 'no'") == ["yes"]
    assert messages(engine, "IF NOT EXISTS (SELECT 1 FROM dbo.t WHERE id = 9) PRINT 'none'") == ["none"]


def test_nested_if(engine):
    sql = "DECLARE @a INT = 1, @b INT = 2\nIF @a = 1\n  IF @b = 3 PRINT 'x'\n  ELSE PRINT 'y'\nELSE PRINT 'z'"
    assert messages(engine, sql) == ["y"]


def test_if_that_chooses_a_statement_on_a_table(engine):
    assert rows(engine, "DECLARE @n INT = 2\nIF @n > 1\n  SELECT name FROM dbo.t WHERE id = @n\n"
                        "ELSE\n  SELECT 'none' AS name") == [["b"]]


def test_a_condition_that_isnt_true_or_false_is_msg_4145(engine):
    refused(engine, "DECLARE @x INT = 1; IF @x PRINT 'a'", "Msg 4145, Level 15: An expression of non-boolean type "
                                                            "specified in a context where a condition is expected")


def test_while_with_break_and_continue(engine):
    sql = ("DECLARE @i INT = 0, @s INT = 0\nWHILE @i < 5\nBEGIN\n  SET @i += 1\n  IF @i = 2 CONTINUE\n"
           "  IF @i = 4 BREAK\n  SET @s = @s + @i\nEND\nSELECT @i AS i, @s AS s")
    assert rows(engine, sql) == [[4, 4]]


def test_while_true_ends_by_break(engine):
    assert rows(engine, "DECLARE @w INT = 0; WHILE 1 = 1 BEGIN SET @w += 1; IF @w > 50 BREAK; END; "
                        "SELECT @w AS w") == [[51]]


def test_break_leaves_only_the_inner_loop(engine):
    sql = ("DECLARE @i INT = 0, @n INT = 0\nWHILE @i < 3\nBEGIN\n  SET @i += 1\n  DECLARE @j INT = 0\n"
           "  SET @j = 0\n  WHILE 1 = 1\n  BEGIN\n    SET @j += 1\n    SET @n += 1\n    IF @j = 2 BREAK\n  END\n"
           "END\nSELECT @i AS i, @n AS n")
    assert rows(engine, sql) == [[3, 6]]


def test_a_declare_in_a_loop_is_declared_once(engine):
    """In T-SQL a DECLARE in a loop body isn't run again: its value stays from one time round to the next."""
    sql = ("DECLARE @i INT = 0\nWHILE @i < 3\nBEGIN\n  DECLARE @k INT\n  SET @k = ISNULL(@k, 0) + 1\n  SET @i += 1\n"
           "END\nSELECT @k AS k")
    assert rows(engine, sql) == [[3]]


def test_a_while_that_doesnt_run(engine):
    assert rows(engine, "DECLARE @i INT = 9; WHILE @i < 3 SET @i += 1; SELECT @i AS i") == [[9]]


def test_many_times_round(engine):
    assert rows(engine, "DECLARE @i INT = 0; WHILE @i < 300 SET @i += 1; SELECT @i AS i") == [[300]]


def test_break_and_continue_outside_a_loop(engine):
    refused(engine, "BREAK", "Msg 135, Level 15: Cannot use a BREAK statement outside the scope of a WHILE statement.")
    refused(engine, "IF 1 = 1 CONTINUE", "Msg 136, Level 15: Cannot use a CONTINUE statement outside the scope of a "
                                         "WHILE statement.")


def test_return_ends_the_batch_and_the_next_batch_runs(engine):
    assert messages(engine, "PRINT 'a'\nRETURN\nPRINT 'b'\nGO\nPRINT 'c'") == ["a", "c"]


def test_return_with_a_value_is_msg_178(engine):
    refused(engine, "RETURN 1", "Msg 178, Level 15:")


def test_an_empty_block_is_a_syntax_error(engine):
    with pytest.raises(ValueError, match="incorrect syntax|Incorrect syntax"):
        engine.execute("IF 1 = 1 BEGIN END", dialect="tsql")


def test_the_result_is_the_last_select(engine):
    result = engine.execute("SELECT 1 AS a; DECLARE @x INT = 2; SELECT @x AS b", dialect="tsql")
    assert result["rows"] == [[2]] and [c["name"] for c in result["columns"]] == ["b"]


def test_each_statement_is_listed_with_how_many_times_it_ran(engine):
    result = engine.execute("DECLARE @i INT = 0\nWHILE @i < 3\n  SET @i += 1\nPRINT 'done'", dialect="tsql")
    ran = [(s["statement"], s["type"], s["runs"]) for s in result["statements"]]
    assert ran == [(1, "declare", 1), (2, "while", 1), (3, "set", 3), (4, "print", 1)]


def test_delta_updates_in_a_loop(engine):
    sql = ("DECLARE @i INT = 1\nWHILE @i <= 3\nBEGIN\n  UPDATE dbo.dt SET v = v + 1 WHERE id = @i\n  SET @i += 1\n"
           "END\nSELECT id, v FROM dbo.dt ORDER BY id")
    assert rows(engine, sql) == [[1, 11], [2, 21], [3, 31]]


def test_a_failure_midway_says_what_already_changed(engine):
    sql = ("DECLARE @i INT = 1\nWHILE @i <= 3\nBEGIN\n  UPDATE dbo.dt SET v = v + 1 WHERE id = @i\n"
           "  IF @i = 2 SELECT 1 / 0\n  SET @i += 1\nEND")
    with pytest.raises(ValueError) as caught:
        engine.execute(sql, dialect="tsql")
    said = str(caught.value)
    assert "Msg 8134, Level 16: Divide by zero error encountered." in said
    assert "line 5" in said
    assert "Before it, the UPDATE on line 4 (2 times) ran, and what they changed is kept." in said
    assert rows(engine, "SELECT id, v FROM dbo.dt ORDER BY id") == [[1, 11], [2, 21], [3, 30]]


def test_a_failure_before_any_change_says_so(engine):
    with pytest.raises(ValueError, match=re.escape("Nothing before it changed data.")):
        engine.execute("DECLARE @x INT = 1; SELECT 1 / 0", dialect="tsql")


def test_a_database_table_insert_then_failure(engine):
    with pytest.raises(ValueError, match=re.escape("Before it, the INSERT on line 1 ran, and what it changed is kept.")):
        engine.execute("DECLARE @x INT = 1; INSERT INTO dbo.t VALUES (10, 'q'); SELECT 1 / 0", dialect="tsql")
    assert rows(engine, "SELECT COUNT(*) AS n FROM dbo.t") == [[4]]


def test_a_script_is_not_a_delta_write_a_change_or_a_plan(engine):
    script = "DECLARE @x INT = 1; UPDATE dbo.dt SET v = @x WHERE id = 1"
    for call in (lambda: engine.explain(script, dialect="tsql"),
                 lambda: engine.change_delta(script, dialect="tsql"),
                 lambda: engine.write_delta("dbo.made", "DECLARE @x INT = 1; SELECT @x AS x", dialect="tsql")):
        with pytest.raises(ValueError, match="runs only as a whole query"):
            call()
    assert rows(engine, "SELECT v FROM dbo.dt WHERE id = 1") == [[10]]


def test_an_endless_while_stops_at_the_time_limit(engine):
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        engine.execute("DECLARE @i INT = 0; WHILE 1 = 1 SET @i += 1", dialect="tsql", timeout=1.5)
    assert time.monotonic() - start < 30
    assert rows(engine, "SELECT 42 AS n") == [[42]]


def test_an_endless_while_stops_when_cancelled(engine):
    failed: list[BaseException] = []

    def run():
        try:
            engine.execute("DECLARE @i INT = 0; WHILE 1 = 1 SET @i += 1", dialect="tsql", query_id="loop")
        except BaseException as exc:  # noqa: BLE001 - the test reads what it was
            failed.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 20
    while not engine.cancel("loop") and time.monotonic() < deadline:
        time.sleep(0.05)
    worker.join(30)
    assert not worker.is_alive()
    assert failed and isinstance(failed[0], QueryCancelled)
