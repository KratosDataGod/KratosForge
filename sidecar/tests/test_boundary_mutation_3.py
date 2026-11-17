"""Mutation-testing follow-up, batch 39: boundary mutants (< against <=, > against >=) in older
modules. Each test pins a value exactly at a limit, where the two operators disagree."""

import datetime as dt

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop import risk

NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


# ------------------------------------------------------------------ risk.py


def test_a_run_that_ended_the_moment_it_started_took_zero_seconds():
    t = NOW.isoformat()
    assert risk._finished({"started_at": t, "ended_at": t}) == 0.0
    later = (NOW + dt.timedelta(seconds=5)).isoformat()
    assert risk._finished({"started_at": later, "ended_at": t}) is None  # ended before it started


def test_a_run_exactly_at_the_cu_hour_limit_doesnt_warn():
    m = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, 3600, source="export",
                collected_at=(NOW - dt.timedelta(minutes=5)).isoformat())
    facts = {"where": "fabric", "measured": [m], "estimated": []}
    assert risk.check_consumption(facts, risk.settings({"warn_run_cu_hours": 1}))["outcome"] == risk.PASS
    assert risk.check_consumption(facts, risk.settings({"warn_run_cu_hours": 0.999}))["outcome"] == risk.WARNING


@pytest.mark.parametrize("minutes", [0, 60])
def test_a_schedule_due_now_or_at_the_window_edge_warns(minutes):
    nxt = (NOW + dt.timedelta(minutes=minutes)).isoformat()
    out = risk.check_schedules({"schedules": [{"enabled": True, "where": "pc", "next_run": nxt}]}, risk.DEFAULTS, NOW)
    assert out["outcome"] == risk.WARNING


# ------------------------------------------------------------------ frames.py

import pyarrow as pa  # noqa: E402

from fabric_desktop import frames  # noqa: E402
from fabric_desktop.frames import Frames  # noqa: E402


def test_the_twentieth_result_still_keeps_the_first():
    f = Frames()
    ids = [f.keep(pa.table({"n": [i]})) for i in range(frames.MAX_FRAMES)]
    assert f.table(ids[0]).num_rows == 1  # reading it makes it the newest
    f.keep(pa.table({"n": [99]}))
    with pytest.raises(frames.FrameGone):  # the twenty-first drops the oldest, now the second
        f.table(ids[1])
    assert f.table(ids[0]).num_rows == 1


def test_a_sort_column_one_past_the_last_is_ignored():
    f = Frames()
    fid = f.keep(pa.table({"a": [3, 1, 2], "b": [1, 2, 3]}))
    assert f.page(fid, sort=2)["rows"] == [[3, 1], [1, 2], [2, 3]]
    assert f.page(fid, sort=1, desc=True)["rows"][0] == [2, 3]


def test_a_chart_column_one_past_the_last_is_refused_plainly():
    f = Frames()
    fid = f.keep(pa.table({"a": [1, 2], "b": [1, 2]}))
    with pytest.raises(ValueError, match="X axis column isn't in this result"):
        f.chart(fid, x=2, y=1)


def test_exactly_sixty_categories_arent_truncated():
    f = Frames()
    n = frames.CHART_CATEGORIES
    fid = f.keep(pa.table({"x": list(range(n)), "y": [1] * n}))
    assert f.chart(fid, "bar", x=0, y=1)["truncated"] is False
    fid = f.keep(pa.table({"x": list(range(n + 1)), "y": [1] * (n + 1)}))
    assert f.chart(fid, "bar", x=0, y=1)["truncated"] is True


def test_exactly_seven_series_have_no_other_slot():
    f = Frames()
    s = frames.CHART_SERIES
    fid = f.keep(pa.table({"x": [1] * s, "y": list(range(1, s + 1)), "s": [f"s{i}" for i in range(s)]}))
    names = [x["name"] for x in f.chart(fid, "bar", x=0, y=1, series=2)["series"]]
    assert len(names) == s and frames.OTHER not in names


def test_a_result_of_exactly_a_sheets_rows_goes_to_excel(tmp_path, monkeypatch):
    wrote = []
    monkeypatch.setattr(frames, "write_xlsx", lambda table, sink: wrote.append(table.num_rows))
    f = Frames()
    fid = f.keep(pa.table({"n": pa.array(range(frames.EXCEL_MAX_ROWS), pa.int32())}))
    target = tmp_path / "full.xlsx"
    target.write_bytes(b"")
    assert f.download(fid, "xlsx", str(target))["rows"] == frames.EXCEL_MAX_ROWS
    assert wrote == [frames.EXCEL_MAX_ROWS]


def test_excels_first_day_is_a_date_not_text():
    assert frames._xlsx_cell("A2", dt.datetime(1900, 1, 1)) == '<c r="A2" s="2"><v>1.0</v></c>'
    assert frames._xlsx_cell("A2", dt.date(1900, 1, 1)) == '<c r="A2" s="3"><v>1</v></c>'
    assert 'inlineStr' in frames._xlsx_cell("A2", dt.datetime(1899, 12, 31, 23, 59))


# ------------------------------------------------------------------ sqlcompat/practices.py

from fabric_desktop.sqlcompat import practices  # noqa: E402


def _ids(query):
    return [f["rule_id"] for f in practices.check(query)["findings"]]


@pytest.mark.parametrize("query, expected", [
    # a statement that stops right after the word each check looks past must be read, not fail
    ("SELECT", []), ("SELECT DISTINCT", []), ("SELECT TOP", ["SQ008"]), ("SELECT TOP (", ["SQ008"]),
    ("SELECT a INTO", []), ("SELECT a FROM dbo.t WHERE c LIKE", []),
    ("INSERT INTO dbo.t SELECT ROW_NUMBER", []), ("INSERT INTO dbo.t SELECT ROW_NUMBER(", []),
    ("INSERT INTO dbo.t SELECT ROW_NUMBER() OVER", []),
    ("INSERT INTO dbo.t SELECT ROW_NUMBER() OVER (ORDER BY a) AS", []),
    # an unclosed bracket ends at the statement's last token, which isn't read as a reference
    ("SELECT (SELECT max(b) FROM dbo.u WHERE u.k = t.k", []),
])
def test_a_statement_that_ends_at_a_boundary(query, expected):
    assert _ids(query) == expected


def test_a_like_after_an_in_list_is_still_in_the_where_clause():
    assert _ids("SELECT a FROM dbo.t WHERE b IN (1, 2) AND c LIKE '%x'") == ["SQ016"]


def test_a_wrapped_column_after_group_by_isnt_a_where_finding():
    assert _ids("SELECT a FROM dbo.t WHERE b = 1 GROUP BY a HAVING YEAR(d) = 2024") == []


# ------------------------------------------------------------------ sqlcompat/rules.py

from fabric_desktop.sqlcompat import analyze  # noqa: E402


def _found(sql, target):
    return [(d["rule"], sql[d["from"]:d["to"]]) for d in analyze(sql, target)["diagnostics"]]


# Most of these stop right after the token a check looks one or more tokens past: each must be read
# and reported, not fail. The last one has two foreign keys: the second reports from its own REFERENCES.
RULES_CASES = [
    ('warehouse', 'ALTER TABLE', [('SYN-007', 'TABLE')]),
    ('warehouse', 'CREATE TABLE', [('SYN-007', 'TABLE')]),
    ('warehouse', 'ALTER TABLE t ADD', []),
    ('warehouse', 'CREATE TABLE #', [('DDL-006', 'CREATE TABLE #'), ('SYN-007', '#')]),
    ('warehouse', 'SELECT * FROM t LIMIT', [('SYN-007', 'LIMIT')]),
    ('warehouse', 'CREATE TABLE t (a int, PRIMARY', [('SYN-007', 'PRIMARY')]),
    ('warehouse', 'UPDATE', [('DES-003', 'UPDATE'), ('SYN-007', 'UPDATE')]),
    ('warehouse', 'CREATE STATISTICS s ON', []),
    ('warehouse', 'ALTER TABLE t ADD CONSTRAINT c', [('SYN-007', 'c')]),
    ('warehouse', 'merge', [('DML-006', 'merge'), ('SYN-007', 'merge')]),
    ('warehouse', 'WITH a', [('SYN-007', 'a')]),
    ('warehouse', 'WITH a AS', [('SYN-007', 'AS')]),
    ('warehouse', 'CREATE TABLE t WITH', []),
    ('warehouse', 'ALTER TABLE t ADD a', []),
    ('warehouse', 'DROP TABLE', [('DES-001', 'DROP TABLE'), ('SYN-007', 'TABLE')]),
    ('spark', "SELECT split(a, '.'", [('SYN-007', "'.'")]),
    ('spark', 'SELECT [', [('SYN-007', '[')]),
    ('spark', 'SELECT [a', [('SYN-007', 'a')]),
    ('warehouse', 'CONVERT(string', [('SYN-009', 'string')]),
    ('warehouse', 'SELECT * FROM t OPTION', [('SYN-007', 'OPTION')]),
    ('warehouse', 'SELECT a FROM t WHERE b NOT IN (', [('SYN-007', '(')]),
    ('warehouse', 'CREATE TABLE t (a datetime2(7', [('SYN-007', 'datetime2')]),
    ('warehouse', 'ON SCHEMA::', [('SYN-007', 'ON')]),
    ('warehouse', 'CREATE TABLE t (a datetime', [('SYN-007', 'datetime')]),
    ('warehouse', 'WITH a AS NOT', [('SYN-007', 'NOT')]),
    ('warehouse', 'CREATE TABLE t (a varchar(max', [('SYN-007', 'varchar')]),
    ('warehouse', 'CREATE TABLE t (a int, CONSTRAINT f FOREIGN KEY (a) REFERENCES u (a), b int REFERENCES v (b))', [('DDL-004', 'FOREIGN KEY (a) REFERENCES'), ('DDL-004', 'REFERENCES')]),
]


@pytest.mark.parametrize("target, sql, expected", RULES_CASES)
def test_rules_at_a_boundary(target, sql, expected):
    assert _found(sql, target) == expected


# ------------------------------------------------------------------ mailbox.py

import json  # noqa: E402

from fabric_desktop import mailbox  # noqa: E402
from fabric_desktop.leases import Stored  # noqa: E402
from tests import test_mailbox_mutation as mbm  # noqa: E402

DAY = 24 * 3600


def _request(**extra):
    return {"contract": 1, "env": "dev", "entities": ["silver.orders"]} | extra


def _padded(request, size):
    data = json.dumps(request).encode()
    return data + b" " * (size - len(data))


def test_a_request_of_exactly_the_most_bytes_is_read():
    m = mbm.box(mbm.Control())
    request, reason = m._parse("dev", "r1", _padded(_request(), mailbox.MAX_REQUEST_BYTES))
    assert reason == "" and request["entities"] == ["silver.orders"]
    _, reason = m._parse("dev", "r1", _padded(_request(), mailbox.MAX_REQUEST_BYTES + 1))
    assert reason == f"the request is {mailbox.MAX_REQUEST_BYTES + 1} bytes; the most is {mailbox.MAX_REQUEST_BYTES}"


def test_a_submitter_name_of_exactly_two_hundred_characters_is_allowed():
    m = mbm.box(mbm.Control())
    _, reason = m._parse("dev", "r1", json.dumps(_request(submitted_by="a" * 200)).encode())
    assert reason == ""
    _, reason = m._parse("dev", "r1", json.dumps(_request(submitted_by="a" * 201)).encode())
    assert reason == "submitted_by is text of at most 200 characters"


@pytest.mark.parametrize("age, written", [(mailbox.WRITING_GRACE - 1, False), (mailbox.WRITING_GRACE, True)])
def test_an_empty_request_is_rejected_once_the_writing_grace_is_over(age, written):
    control = mbm.Control({"requests/r1.json": b""}, modified=mbm.NOW - age)
    mbm.box(control)._claim("dev", "r1")
    assert [s["state"] for s in mbm.statuses(control)] == (["rejected"] if written else [])


@pytest.mark.parametrize("age, written", [(mailbox.RUN_LOOKUP_GRACE - 1, False), (mailbox.RUN_LOOKUP_GRACE, True)])
def test_a_run_lookup_fabric_couldnt_answer_is_given_up_on_at_the_grace(age, written):
    def busy(env, item, run):
        raise mbm._status_error(503)

    request = _request(pipeline_run={"item_id": mbm.ITEM, "run_id": mbm.RUN})
    control = mbm.Control({"requests/r1.json": request}, modified=mbm.NOW - age)
    mbm.box(control, verify_run=busy, require_run=True)._claim("dev", "r1")
    assert [s["state"] for s in mbm.statuses(control)] == (["rejected"] if written else [])


def _queued():
    return {"id": "j1", "state": "queued", "attempts": 0}


def test_a_deadline_of_this_very_second_hasnt_passed_when_the_request_arrives():
    control = mbm.Control({"requests/r1.json": _request(deadline=mailbox._iso(mbm.NOW))})
    m = mbm.box(control, job=_queued())
    m._claim("dev", "r1")
    assert [s["state"] for s in mbm.statuses(control)] == ["accepted", "accepted"]
    assert m.agent.queue.cancelled == []


def test_a_deadline_of_this_very_second_still_queues_the_request():
    control = mbm.Control()
    m = mbm.box(control)
    enqueued = []
    m.agent.enqueue = lambda env, entities, backfill, cascade, source, dedupe_key: enqueued.append(source) or _queued()
    status = {"state": "accepted", "agent": "etl-01", "deadline": mailbox._iso(mbm.NOW)}
    m._sync("dev", "r1", status, Stored(b"{}", "e1", 0.0))
    assert enqueued == ["pipeline:r1"] and mbm.statuses(control)[-1]["state"] == "accepted"


def test_a_queued_job_whose_deadline_is_this_very_second_is_kept():
    control = mbm.Control()
    m = mbm.box(control, job=_queued())
    status = {"state": "accepted", "agent": "etl-01", "deadline": mailbox._iso(mbm.NOW), "job_id": "j1"}
    m._sync("dev", "r1", status, Stored(b"{}", "e1", 0.0))
    assert m.agent.queue.cancelled == []


def test_a_run_that_vouched_exactly_a_day_ago_still_counts():
    m = mbm.box(mbm.Control(), require_run=True)
    m._runs = {"dev": {mbm.RUN: {"request_id": "r0", "at": mbm.NOW - DAY}}}
    assert m._run_used("dev", mbm.RUN, "r1", mbm.NOW) == "r0"
    m._runs = {"dev": {mbm.RUN: {"request_id": "r0", "at": mbm.NOW - DAY - 1}}}
    assert m._run_used("dev", mbm.RUN, "r1", mbm.NOW) is None


def test_a_run_taken_exactly_a_day_ago_is_still_remembered():
    m = mbm.box(mbm.Control(), require_run=True)
    m._run_taken("dev", "old", "r1", mbm.NOW)
    m._run_taken("dev", "new", "r2", mbm.NOW + DAY)
    assert sorted(m._runs["dev"]) == ["new", "old"]


def test_a_run_started_exactly_a_day_ago_or_five_minutes_ahead_vouches():
    assert mbm.checked(mbm.NOW - DAY)["verified"] is True
    assert mbm.checked(mbm.NOW + mailbox.RUN_CLOCK_SKEW)["verified"] is True


def test_on_onelake_an_owner_at_exactly_its_fresh_time_plus_the_grace_isnt_lost():
    heartbeat = mbm.NOW - 1000
    record = {"kind": "agent_status", "heartbeat_at": mailbox._iso(heartbeat), "stale_after": mailbox._iso(heartbeat + 60)}
    control = mbm.Control({"agents/etl-02.json": record}, store_now=5000.0, modified=5000.0 - 60 - mailbox.TAKEOVER_GRACE)
    assert mbm.box(control)._past_stale("dev", "etl-02") is False


@pytest.mark.parametrize("past, lost", [(mailbox.TAKEOVER_GRACE, False), (mailbox.TAKEOVER_GRACE + 1, True)])
def test_on_this_pc_an_owner_is_lost_only_past_its_stale_time_plus_the_grace(past, lost):
    record = {"kind": "agent_status", "stale_after": mailbox._iso(mbm.NOW - past)}
    assert mbm.box(mbm.Control({"agents/etl-02.json": record}))._past_stale("dev", "etl-02") is lost


def test_a_finished_request_s_files_go_at_exactly_two_weeks():
    control = mbm.Control({"requests/r1.json": {}, "cancel/r1.json": {}})
    status = {"state": "succeeded", "updated_at": mailbox._iso(mbm.NOW - 14 * DAY)}
    mbm.box(control)._expire_files("dev", "r1", status, Stored(b"{}", "e1", 0.0))
    assert control.deleted == ["requests/r1.json", "cancel/r1.json", "status/r1.json"]
