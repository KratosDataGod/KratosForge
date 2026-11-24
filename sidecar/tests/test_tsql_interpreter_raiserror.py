"""WR-705: RAISERROR with severity 11 to 18 doesn't end a batch, as in a Fabric Warehouse.

Outside TRY the error goes to the answer's ``messages``, as a warehouse shows it ("Msg 50000, Level N, State S,
Line L" and the text on the next line), in order with PRINT's lines, and the next statement runs. Inside TRY it
still goes to CATCH, also from a TRY that a BREAK or RETURN left before. THROW, and any other error, still ends
the script.
"""

from __future__ import annotations

import re

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Raiserror")["id"])
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) v(id, name)")
    yield e
    local.close_all()


def run(engine, sql):
    result = engine.execute(sql, dialect="tsql")
    return [list(r) for r in result["rows"]], result["messages"]


def test_the_script_goes_on_after_it(engine):
    assert run(engine, "RAISERROR('bad', 16, 1)\nSELECT 1 AS one") == \
        ([[1]], ["Msg 50000, Level 16, State 1, Line 1\nbad"])


@pytest.mark.parametrize("severity", [11, 14, 16, 18])
def test_each_severity_from_11_to_18(engine, severity):
    _rows, said = run(engine, f"RAISERROR('x', {severity}, 3)")
    assert said == [f"Msg 50000, Level {severity}, State 3, Line 1\nx"]


def test_its_line_is_the_line_in_the_batch(engine):
    _rows, said = run(engine, "SELECT 1 AS a\nGO\nPRINT 'p'\n\nRAISERROR('on %s', 16, 1, 'four')")
    assert said == ["p", "Msg 50000, Level 16, State 1, Line 3\non four"]


def test_in_order_with_print(engine):
    _rows, said = run(engine, "PRINT 'a'; RAISERROR('b', 16, 1); PRINT 'c'; RAISERROR('d', 10, 1)")
    assert said == ["a", "Msg 50000, Level 16, State 1, Line 1\nb", "c", "d"]


def test_changes_after_it_are_made(engine):
    rows, _said = run(engine, "RAISERROR('warn', 16, 1); UPDATE dbo.t SET name = 'z' WHERE id = 1; "
                              "SELECT name FROM dbo.t WHERE id = 1")
    assert rows == [["z"]]


def test_inside_try_it_goes_to_catch(engine):
    rows, said = run(engine, "BEGIN TRY RAISERROR('bad', 16, 4); PRINT 'not here' END TRY "
                             "BEGIN CATCH SELECT ERROR_NUMBER() AS n, ERROR_STATE() AS s, ERROR_MESSAGE() AS m END CATCH")
    assert rows == [[50000, 4, "bad"]] and said == []


def test_in_catch_outside_any_try_it_goes_on(engine):
    rows, said = run(engine, "BEGIN TRY SELECT 1 / 0 END TRY BEGIN CATCH RAISERROR('again', 16, 1) END CATCH; "
                             "SELECT 2 AS two")
    assert rows == [[2]] and said == ["Msg 50000, Level 16, State 1, Line 1\nagain"]


def test_in_catch_inside_an_outer_try_it_goes_to_the_outer_catch(engine):
    sql = ("BEGIN TRY\n  BEGIN TRY SELECT 1 / 0 END TRY\n  BEGIN CATCH RAISERROR('inner', 16, 1) END CATCH\nEND TRY\n"
           "BEGIN CATCH\n  SELECT ERROR_MESSAGE() AS m\nEND CATCH")
    assert run(engine, sql) == ([["inner"]], [])


def test_after_a_break_left_a_try_it_is_outside_try_again(engine):
    sql = ("DECLARE @i INT = 0\nWHILE 1 = 1\nBEGIN\n  BEGIN TRY\n    SET @i += 1\n    IF @i = 2 BREAK\n  END TRY\n"
           "  BEGIN CATCH\n    PRINT 'caught'\n  END CATCH\nEND\nRAISERROR('after', 16, 1)\nSELECT @i AS i")
    assert run(engine, sql) == ([[2]], ["Msg 50000, Level 16, State 1, Line 12\nafter"])


def test_in_a_loop_each_time_round(engine):
    _rows, said = run(engine, "DECLARE @i INT = 0; WHILE @i < 2 BEGIN SET @i += 1; RAISERROR('n %d', 16, 1, @i) END")
    assert said == ["Msg 50000, Level 16, State 1, Line 1\nn 1", "Msg 50000, Level 16, State 1, Line 1\nn 2"]


def test_throw_still_ends_the_script(engine):
    with pytest.raises(ValueError, match=re.escape("Msg 50001, Level 16: stop")):
        engine.execute("RAISERROR('goes on', 16, 1); THROW 50001, 'stop', 1; SELECT 1", dialect="tsql")


def test_a_severity_over_18_still_ends_the_script(engine):
    with pytest.raises(ValueError, match="Msg 2754"):
        engine.execute("RAISERROR('x', 19, 1); SELECT 1", dialect="tsql")


def test_execute_reader_carries_it(engine):
    with engine.execute_reader("RAISERROR('r', 16, 1); SELECT id FROM dbo.t ORDER BY id", dialect="tsql") as stream:
        assert stream.read_all().to_pylist() == [{"id": 1}, {"id": 2}]
        assert stream.messages == ["Msg 50000, Level 16, State 1, Line 1\nr"]


@pytest.mark.parametrize("sql, line", [
    ("BEGIN TRY SELECT 1 / 0 END TRY BEGIN CATCH SELECT ERROR_LINE() AS l END CATCH", 1),
    ("\nBEGIN TRY SELECT 1 / 0 END TRY BEGIN CATCH SELECT ERROR_LINE() AS l END CATCH", 2),
    ("SELECT 1 AS a\nGO\nBEGIN TRY SELECT 1 / 0 END TRY BEGIN CATCH SELECT ERROR_LINE() AS l END CATCH", 1),
    ("SELECT 1 AS a\r\nGO\r\n\r\nBEGIN TRY SELECT 1 / 0 END TRY BEGIN CATCH SELECT ERROR_LINE() AS l END CATCH", 2),
])
def test_error_line_counts_from_the_batchs_first_line(engine, sql, line):
    """The line after GO is line 1 of the next batch (it was counted as line 2)."""
    assert run(engine, sql)[0] == [[line]]
