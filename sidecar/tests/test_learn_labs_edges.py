"""Learn labs: an exercise with no hints, values the UI shows as text, an answer that tries to end
the lab's transaction, and a check kind this app doesn't know."""

import datetime as dt
import decimal

import duckdb

from fabric_desktop.learn import labs


def test_an_exercise_with_no_hints_says_so(monkeypatch):
    monkeypatch.setattr(labs.content, "get", lambda module_id: {"id": module_id, "exercises": [{"id": "e1"}]})
    assert labs.hint("m", "e1", 2) == {"level": 0, "of": 0, "text": "This exercise has no hints."}


def test_dates_times_and_other_values_become_text_the_ui_can_show():
    assert labs._cell(decimal.Decimal("1.5")) == 1.5
    assert labs._cell(dt.date(2026, 1, 2)) == "2026-01-02"
    assert labs._cell(dt.time(7, 30)) == "07:30:00"
    assert labs._cell(b"\x01") == "b'\\x01'"


def test_an_answer_that_ends_the_transaction_is_not_run_for_a_table_check():
    con = duckdb.connect()
    out = labs._run(con, {"kind": "table_shape", "table": "t"}, "CREATE TABLE t(a INT); COMMIT;", "", {}, "m", [])
    assert out["passed"] is False and out["detail"].startswith("Your SQL wasn't run: a lab answer can't use BEGIN")
    assert con.execute("select count(*) from information_schema.tables where table_name = 't'").fetchone() == (0,)


def test_a_check_kind_this_app_does_not_know_fails_with_a_reason():
    out = labs._run(duckdb.connect(), {"kind": "explain_plan"}, "select 1", "", {}, "m", [])
    assert out == {"kind": "explain_plan", "passed": False, "detail": "This app can't check explain_plan yet."}
