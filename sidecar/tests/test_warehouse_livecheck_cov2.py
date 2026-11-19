"""The warehouse live check (warehouse_livecheck.py): the SQL guard's refusals of SQL it can't read, an empty
batch and ALTER of anything but a table; answers of every value kind; a case whose setup or statements fail
or whose rollback breaks; and the plan. The connection is a fake: nothing reaches a warehouse."""

import datetime as dt
import logging

from fabric_desktop import warehouse_livecheck as wl

S = "fdlt_abcdef12"


def test_the_guard_refuses_unreadable_sql_and_an_empty_batch():
    assert wl.statement_problem("SELECT (((", S).startswith("it can't be read: ")
    assert wl.statement_problem("", S) == "there's nothing to run"
    assert wl.statement_problem(";", S) == "there's nothing to run"


def test_the_guard_refuses_alter_of_anything_but_a_table():
    assert (
        wl.statement_problem(f"ALTER VIEW {S}.v AS SELECT 1", S)
        == "ALTER VIEW isn't allowed"
    )


def test_the_guard_passes_a_table_function_it_has_no_name_for():
    assert (
        wl.statement_problem(f"INSERT INTO {S}.t SELECT * FROM OPENJSON('[1]')", S)
        is None
    )
    assert wl.statement_problem(
        "INSERT INTO dbo.t SELECT * FROM OPENJSON('[1]')", S
    ) == (f"it names dbo.t, outside the scratch schema {S}")


def test_answers_of_each_kind():
    assert wl.answer(True) == "1" and wl.answer(False) == "0"
    assert wl.answer(dt.datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02 03:04:05"
    assert wl.answer(dt.date(2026, 1, 2)) == "2026-01-02"
    assert wl.answer(b"\x01\xab") == "0x01AB"
    assert wl.answer(bytearray(b"\xff")) == "0xFF"
    assert wl.answer(0.1 + 0.2) == "0.3"


def test_verdict_without_a_documented_answer_compares_local_and_warehouse():
    def result(local, warehouse):
        return wl.Result("c", "WR-1", "s", "t", "unverified", None, local, warehouse)

    assert result("1.50", "1.5").verdict == "local matches the warehouse"
    assert result("1", "2").verdict == "local differs"
    assert result("1", None).verdict == "not run"


class Cursor:
    def __init__(self, con):
        self.con = con

    def execute(self, sql):
        self.con.sent.append(sql)
        if sql in self.con.raise_on:
            raise RuntimeError(f"Msg 1: {sql} failed")

    def nextset(self):
        if self.con.nextset_errors:
            raise RuntimeError(self.con.nextset_errors.pop(0))
        return False

    def fetchone(self):
        return (42,)


class Connection:
    def __init__(self, raise_on=(), nextset_errors=(), rollback_fails=False):
        self.raise_on, self.nextset_errors, self.rollback_fails = (
            set(raise_on),
            list(nextset_errors),
            rollback_fails,
        )
        self.sent = []
        self.autocommit = False

    def cursor(self):
        return Cursor(self)

    def rollback(self):
        if self.rollback_fails:
            raise RuntimeError("connection is broken")


def check():
    return wl.WarehouseCheck("ws", "wh", None, None, "out", run_id="abcdef12")


def test_a_failed_setup_is_the_cases_answer():
    case = wl.Case(
        "c1", "WR-1", "s", "t", "SELECT 1", "1", setup=("CREATE TABLE {s}.t (x int)",)
    )
    con = Connection(nextset_errors=["Msg 2714: There is already an object named t"])
    notes = []
    assert (
        check()._one(con, case, notes)
        == "error: Msg 2714: There is already an object named t"
    )
    assert con.sent == [f"CREATE TABLE {S}.t (x int)"] and notes == []


def test_a_statement_that_fails_is_noted_and_the_query_still_answers(caplog):
    stmt = "INSERT INTO {s}.t VALUES (1)"
    case = wl.Case(
        "c2", "WR-1", "s", "t", "SELECT 1", "1", statements=(stmt,), autocommit=True
    )
    con = Connection(raise_on={stmt.replace("{s}", S)}, rollback_fails=True)
    notes = []
    with caplog.at_level(logging.WARNING, logger=wl.__name__):
        assert check()._one(con, case, notes) == "42"
    assert notes == [f"error: Msg 1: INSERT INTO {S}.t VALUES (1) failed"]
    assert con.autocommit is False
    assert "The rollback after case c2 failed: connection is broken" in caplog.text


def test_drain_stops_at_its_limit_of_errors():
    con = Connection(nextset_errors=["one", "two", "three"])
    errors = []
    wl._drain(Cursor(con), errors, limit=2)
    assert errors == ["error: one", "error: two"]
    assert con.nextset_errors == ["three"]


def test_plan_names_the_scratch_schema_and_where_results_go():
    plan = check().plan()
    assert len(plan) == 6
    assert plan[0].startswith("Read workspace ws and check it is marked for tests")
    assert f"Create the scratch schema {S} in the warehouse." in plan[2]
    assert plan[-1] == "Write the local, documented and warehouse answers to out."
