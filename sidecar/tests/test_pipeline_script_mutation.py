"""Mutation batch 62: pipeline_script.py's helpers, lakehouse routing, refusal wording, cancel and paging.

A fake SQL runner stands in for the SQL page, so no query runs and nothing reaches a lakehouse."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import pipeline_script as ps
from fabric_desktop.orchestration.adapters import ActivityFailed
from fabric_desktop.orchestration.definition import Activity


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def activity(*scripts, name="Load", service=None):
    return Activity(name=name, type="Script", path="$.a[0]",
                    raw={"typeProperties": {"scripts": list(scripts)}, "linkedService": service or {"name": "Conn"}})


LAKEHOUSE = {"name": "LH", "properties": {"type": "Lakehouse", "typeProperties": {"workspaceId": "w"}}}


class Runs:
    def __init__(self, states=(), pages=()):
        self.states, self.pages, self.cancelled, self.asked = list(states), list(pages), [], []

    def start(self, text, *args, **kwargs):
        if kwargs.get("dry_run"):
            return {}
        return {"id": "r1", **self.states.pop(0)}

    def status(self, run_id):
        return self.states.pop(0)

    def cancel(self, run_id):
        self.cancelled.append(run_id)

    def page(self, run_id, offset, limit):
        self.asked.append((offset, limit))
        return {"rows": self.pages.pop(0) if self.pages else []}


def scripts(runs=None, projects=None):
    data = SimpleNamespace(svc=SimpleNamespace(sql_runs=runs or Runs()), lakehouse_reads=True, workspace_id="ws")
    return ps.LocalScripts(data, projects)


# ---- helpers


@pytest.mark.parametrize("value, expr", [
    ("@pipeline().x", True), ("@@literal", False), ("plain", False), (" @x", False), ("", False),
    ({"type": "Expression", "value": "@x"}, True), ({"type": "expression"}, False), ({"value": "@x"}, False),
    (None, False)])
def test_expr_knows_an_expression_from_text(value, expr):
    assert ps._expr(value) is expr


@pytest.mark.parametrize("block, text", [
    ({"text": "SELECT 1"}, "SELECT 1"), ({"text": {"type": "Expression", "value": "@x"}}, "@x"),
    ({"text": {"type": "Other", "value": "@x"}}, {"type": "Other", "value": "@x"}), ("SELECT 1", None)])
def test_text_unwraps_only_an_expression(block, text):
    assert ps._text(block) == text


def test_projects_keep_only_text_names_and_text_projects():
    s = scripts(projects={"Load": "proj", "Other": 3, 4: "x", "Conn": None})
    assert s.projects == {"Load": "proj"}


# ---- which runs read a lakehouse


def test_on_lakehouse_is_true_for_a_lakehouse_connection_without_a_project():
    assert scripts().on_lakehouse(activity(service=LAKEHOUSE)) is True


def test_on_lakehouse_is_false_when_a_project_is_named_for_the_activity_or_its_connection():
    assert scripts(projects={"Load": "p"}).on_lakehouse(activity(service=LAKEHOUSE)) is False
    assert scripts(projects={"LH": "p"}).on_lakehouse(activity(service=LAKEHOUSE)) is False


@pytest.mark.parametrize("kind", ["DataWarehouse", "lakehouse", None])
def test_on_lakehouse_is_false_for_other_connections(kind):
    service = {"name": "X", "properties": {"type": kind}}
    assert scripts().on_lakehouse(activity(service=service)) is False


# ---- before the run


def test_a_file_read_names_the_statement_it_is_in():
    s = scripts(projects={})
    problems = s.check(activity({"type": "Query", "text": "SELECT 1; SELECT * FROM read_csv('c:/x.csv')"},
                                service=LAKEHOUSE))
    assert problems[0]["code"] == "script_refused" and "script 1, statement 2," in problems[0]["message"]


# ---- running


def test_a_person_cancelling_a_running_script_fails_it_as_cancelled():
    runs = Runs([{"state": "running"}])
    with pytest.raises(ActivityFailed, match="^Cancelled.$") as raised:
        scripts(runs, {"Load": "proj"}).run(activity(), {"scripts": [{"text": "SELECT 1"}]}, lambda: True, None)
    assert raised.value.error["errorCode"] == "LocalCancelled" and runs.cancelled == ["r1"]


def test_a_script_runs_on_until_the_clock_passes_the_deadline(monkeypatch):
    # At exactly the deadline the script keeps running; only a later reading ends it.
    clock = iter([100.0, 100.5])
    monkeypatch.setattr(ps, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda s: None))
    monkeypatch.setattr(ps, "awake_monotonic", lambda: next(clock))  # the deadline's clock
    runs = Runs([{"state": "running"}, {"state": "running"}, {"state": "running"}])
    with pytest.raises(ActivityFailed, match="policy timeout") as raised:
        scripts(runs, {"Load": "proj"}).run(activity(), {"scripts": [{"text": "SELECT 1"}]}, lambda: False, 100.0)
    assert raised.value.error["errorCode"] == "LocalActivityTimeout" and runs.cancelled == ["r1"]
    assert runs.states == [{"state": "running"}]   # one poll happened at the deadline itself


@pytest.mark.parametrize("state, words, code", [
    ({"state": "failed", "refused": {"message": "DROP needs a confirm"}, "error": "e"}, "DROP needs a confirm",
     "LocalScriptRefused"),
    ({"state": "failed", "error": "Binder Error: no table t"}, "Binder Error: no table t", "LocalScriptFailed"),
    ({"state": "failed", "refused": {}, "error": "boom"}, "boom", "LocalScriptFailed"),
    ({"state": "failed"}, "failed", "LocalScriptFailed")])
def test_a_failed_script_says_why(state, words, code):
    with pytest.raises(ActivityFailed) as raised:
        scripts(Runs([state]), {"Load": "proj"}).run(activity(), {"scripts": [{"text": "SELECT 1"}]},
                                                      lambda: False, None)
    assert str(raised.value) == f"Load: script 1: {words}" and raised.value.error["errorCode"] == code


def test_later_pages_ask_only_for_the_rows_still_wanted():
    first = [[n] for n in range(3000)]
    runs = Runs([{"state": "completed", "columns": [{"name": "n"}], "rows": 6000}], pages=[first, first])
    out = scripts(runs, {"Load": "proj"}).run(activity(), {"scripts": [{"text": "SELECT n"}]}, lambda: False, None)
    assert runs.asked == [(0, 5000), (3000, 2000)]
    assert out["resultSets"][0]["rowCount"] == 5000 and out["outputTruncated"] is True


# ---- every message names the script it is about (the second here, so its number is 2)


class Plans(Runs):
    """A dry run answers with ``plan`` for the script whose text is ``bad``."""

    def __init__(self, bad, plan, **kw):
        super().__init__(**kw)
        self.bad, self.plan = bad, plan

    def start(self, text, *args, **kwargs):
        if kwargs.get("dry_run"):
            return self.plan if text == self.bad else {}
        return super().start(text, *args, **kwargs)


OK = {"type": "Query", "text": "SELECT 1"}


def test_a_script_with_parameters_is_named_by_its_number():
    p = scripts(projects={"Load": "proj"}).check(activity(OK, {"text": "SELECT 2", "parameters": [{"name": "x"}]}))
    assert p[0]["code"] == "script_parameters" and p[0]["message"].startswith("Load: script 2 has parameters")


def test_an_unreadable_lakehouse_script_is_named_by_its_number():
    p = scripts(projects={}).check(activity(OK, {"text": "SELECT * FROM t FOR XML PATH"}, service=LAKEHOUSE))
    assert p[0]["code"] == "script_unreadable" and p[0]["message"].startswith("Load: script 2 can't be read: ")


def test_a_file_read_in_a_later_script_is_named_by_its_number():
    p = scripts(projects={}).check(activity(OK, {"text": "SELECT * FROM read_csv('x.csv')"}, service=LAKEHOUSE))
    assert p[0]["message"].startswith("Load: script 2, statement 1, ")


def test_a_script_needing_a_confirm_is_named_by_its_number():
    s = scripts(Plans("DROP TABLE t", {"confirm_required": True}), {"Load": "proj"})
    p = s.check(activity(OK, {"text": "DROP TABLE t"}))
    assert p[0]["code"] == "script_needs_confirm" and p[0]["message"].startswith("Load: script 2 removes a table")


def test_a_refused_script_says_its_number_statement_label_and_reason():
    s = scripts(Plans("X", {"refused": {"statement": 3, "label": "COPY", "reason": "writes files"}}), {"Load": "proj"})
    assert s.check(activity(OK, {"text": "X"}))[0]["message"] == \
        "Load: script 2, statement 3, COPY (writes files). Give a mock for it."


def test_a_refused_script_without_a_label_or_reason_still_reads():
    s = scripts(Plans("X", {"refused": {"statement": 1}}), {"Load": "proj"})
    assert s.check(activity(OK, {"text": "X"}))[0]["message"] == \
        "Load: script 2, statement 1, is refused (not allowed here). Give a mock for it."


def test_a_connection_with_no_type_is_called_a_connection():
    p = scripts(projects={}).check(activity(OK, service={"name": "X"}))
    assert p[0]["code"] == "script_connection" and p[0]["message"].startswith("Load runs on a connection; ")


def test_a_connection_type_is_named():
    p = scripts(projects={}).check(activity(OK, service={"name": "X", "properties": {"type": "SqlServer"}}))
    assert p[0]["message"].startswith("Load runs on SqlServer; ")


@pytest.mark.parametrize("state, words", [
    ({"state": "timed_out"}, "Load: script 2 reached its time limit."),
    ({"state": "cancelled"}, "Load: script 2 was stopped."),
    ({"state": "failed", "error": "boom"}, "Load: script 2: boom")])
def test_a_stopped_or_failed_later_script_is_named_by_its_number(state, words):
    runs = Runs([{"state": "completed"}, state])
    with pytest.raises(ActivityFailed) as raised:
        scripts(runs, {"Load": "proj"}).run(activity(), {"scripts": [OK, {"text": "SELECT 2"}]}, lambda: False, None)
    assert str(raised.value) == words


def test_lakehouse_notes_are_logged_under_their_scripts_number():
    runs = Runs([{"state": "completed"}, {"state": "completed"}])
    s = scripts(runs, {})
    s.data._ref = lambda a, props: (None, None, {"workspace_id": "w", "lakehouse_id": "l", "name": "LH"})
    out = s.run(activity(service=LAKEHOUSE), {"scripts": [OK, {"text": "SELECT a / b FROM t"}]}, lambda: False, None)
    assert out["outputLogs"].startswith("Script 2: Couldn't tell the types in a / b")
    assert "Script 1" not in out["outputLogs"]


# ---- rows


def test_a_page_longer_than_asked_is_cut_to_the_limit():
    runs = Runs([{"state": "completed", "columns": [{"name": "n"}], "rows": 6000}], pages=[[[n] for n in range(6000)]])
    out = scripts(runs, {"Load": "proj"}).run(activity(), {"scripts": [OK]}, lambda: False, None)
    assert out["resultSets"][0]["rowCount"] == 5000 and out["resultSets"][0]["rows"][-1] == {"n": 4999}


def test_a_row_that_doesnt_match_the_columns_fails_loudly():
    runs = Runs([{"state": "completed", "columns": [{"name": "n"}], "rows": 1}], pages=[[[1, 2]]])
    with pytest.raises(ValueError):
        scripts(runs, {"Load": "proj"}).run(activity(), {"scripts": [OK]}, lambda: False, None)
