"""Teams notice edges an uncapped mutation run found unchecked (batch 251, docs/quality/mutation-testing.md):
what a send counts when outbox steps fail (dropped, sent, waiting, lost, never queued), an outbox that
can't say which routes rest sends nothing it holds, the ids of sent messages whose rows can't be deleted
yet (hidden from the list, kept on disk by id and time, a reused id not hidden, a stale or malformed
kept-ids file), an escalation route without an address, the digest's threshold and title, the audit
line's length, quiet hours at their last minute and over a whole day, and plain False and 0.0 answers."""

import dataclasses
import datetime as dt
import json
import logging
import sqlite3

import pytest
import requests

from fabric_desktop.workloads import notify
from fabric_desktop.workloads.notify import Outbox, Quiet, send, single

from test_agent import Clock

HOOK = "https://contoso.webhook.office.com/hook"


def change():
    return {
        "change": "opened",
        "incident": {
            "kind": "run_failed",
            "name": "a",
            "env": "prod",
            "title": "a failed",
            "text": "boom",
            "owner": "",
        },
    }


def test_a_send_with_no_outbox_drops_nothing():
    out = send([change()], single(HOOK), None, "agent", lambda url, title, lines: None)
    assert out["dropped"] == 0 and out["sent"] == 1


def test_an_outbox_that_cant_say_which_routes_rest_sends_nothing_it_holds(
    tmp_path, monkeypatch
):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    outbox.add("Waiting", ["x"], "Teams was down")

    def busy(addresses):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(outbox, "resting", busy)
    posted = []
    out = send(
        [change()],
        single(HOOK),
        outbox,
        "agent",
        lambda url, title, lines: posted.append(title),
    )
    # The new one only, posted once without being kept.
    assert posted == ["New: a failed (agent)"]
    assert (
        out["sent"] == 1
        and out["waiting"] == 0
        and "database is locked" in out["error"]
    )
    assert [i["title"] for i in outbox.pending()] == ["Waiting"]


def test_a_sent_message_whose_row_cant_be_deleted_yet_isnt_listed_as_waiting(tmp_path):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    item_id = outbox.sending("Sent", ["x"])
    # A row that can't be deleted, while the file still reads.
    with outbox._db() as db:
        db.execute(
            "CREATE TRIGGER keep BEFORE DELETE ON outbox BEGIN SELECT RAISE(ABORT, 'locked'); END"
        )
    outbox.done(item_id)
    assert outbox.pending() == [] and outbox.count() == 0
    with outbox._db() as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone() == (1,)


def test_no_kept_ids_file_is_left_once_a_sent_messages_row_is_deleted(tmp_path):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    outbox.done(outbox.sending("Sent", ["x"]))
    assert not (tmp_path / ("o.sqlite3" + notify.GONE_SUFFIX)).exists()
    assert outbox.pending() == []


def test_quiet_hours_cant_be_changed_once_read():
    quiet = Quiet.parse("22:00-06:00")
    with pytest.raises(dataclasses.FrozenInstanceError):
        quiet.exempt = ()  # type: ignore[misc]


def test_a_post_that_never_connected_is_not_one_that_may_have_arrived():
    assert notify.outcome_unknown(requests.exceptions.ConnectTimeout("no")) is False


def test_a_kept_id_whose_row_now_holds_another_message_doesnt_hide_it(
    tmp_path, monkeypatch
):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    item_id = outbox.sending("Sent", ["x"])
    # Its row can't be deleted yet.
    monkeypatch.setattr(outbox, "_settle_gone", lambda: False)
    outbox.done(item_id)
    # Another process deleted it, and SQLite gave its id to a new message.
    with outbox._db() as db:
        db.execute(
            "UPDATE outbox SET title = 'New', created = created + 1 WHERE id = ?",
            (item_id,),
        )
    assert [i["title"] for i in outbox.pending()] == ["New"]


def test_a_new_outbox_file_removes_the_kept_ids_left_from_the_one_before(tmp_path):
    path = tmp_path / "o.sqlite3"
    Outbox(path, Clock())
    kept = tmp_path / ("o.sqlite3" + notify.GONE_SUFFIX)
    kept.write_text("[[1, 1800000000.0]]", encoding="utf-8")
    path.unlink()
    Outbox(path, Clock())
    assert not kept.exists()


class Broken:
    def __enter__(self):
        raise sqlite3.OperationalError("database is locked")

    def __exit__(self, *a):
        return False


def test_an_outbox_that_cant_be_read_says_plainly_no_resend_and_no_rest(
    tmp_path, monkeypatch
):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    monkeypatch.setattr(outbox, "_db", Broken)
    assert outbox.idempotent(1) is False
    assert outbox.route_failed("default", HOOK, "down") == 0.0


def busy(*args, **kwargs):
    raise sqlite3.OperationalError("database is locked")


def refuse(url, title, lines):
    raise notify.answered("Teams", 503)


def test_steps_of_the_outbox_that_fail_count_nothing_they_didnt_do(
    tmp_path, monkeypatch
):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    for step in ("trim", "pending", "count"):
        monkeypatch.setattr(outbox, step, busy)
    posted = []
    out = send([change()], single(HOOK), outbox, "agent", lambda *a: posted.append(a))
    assert len(posted) == 1
    assert (out["sent"], out["dropped"], out["waiting"]) == (1, 0, 0)
    assert "database is locked" in out["error"]


def test_a_notice_that_couldnt_be_kept_and_wasnt_taken_is_lost_not_queued(
    tmp_path, monkeypatch
):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    monkeypatch.setattr(outbox, "sending", busy)
    out = send([change()], single(HOOK), outbox, "agent", refuse)
    assert out["lost"] == 1 and out["queued"] == 0 and "503" in out["error"]
    assert outbox.pending() == []


def test_while_routes_cant_be_read_a_refused_notice_isnt_kept_to_send_later(
    tmp_path, monkeypatch
):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    monkeypatch.setattr(outbox, "resting", busy)
    out = send([change()], single(HOOK), outbox, "agent", refuse)
    assert out["lost"] == 1 and out["queued"] == 0
    assert outbox.pending() == []


def test_an_escalation_to_a_route_without_an_address_or_none_at_all_goes_nowhere():
    routing = notify.Routing(
        [notify.Route("default", HOOK), notify.Route("on-call", "")]
    )
    escalated = dict(change(), change="escalated")
    escalated["incident"] = dict(escalated["incident"], escalated_to="on-call")
    assert routing.for_change(escalated) == []
    escalated["incident"]["escalated_to"] = "managers"
    assert routing.for_change(escalated) == []


def test_kept_ids_that_arent_id_and_time_pairs_are_ignored(tmp_path):
    path = tmp_path / "o.sqlite3"
    Outbox(path, Clock()).add("Waiting", ["x"], "")
    kept = tmp_path / ("o.sqlite3" + notify.GONE_SUFFIX)
    kept.write_text(
        '[[1], ["1", 2.0], [1, "x"], [1, null], {"1": 2}, [1, 2, 3]]', encoding="utf-8"
    )
    assert [i["title"] for i in Outbox(path, Clock()).pending()] == ["Waiting"]


def test_a_kept_id_another_outbox_settled_meanwhile_is_left_alone(tmp_path):
    path = tmp_path / "o.sqlite3"
    outbox = Outbox(path, Clock())
    item_id = outbox.sending("Sent", ["x"])
    shared = notify._GONE[str(path.resolve())]
    real = outbox._db

    class Racing:
        """The delete runs, and meanwhile another Outbox on the file settles the same id."""

        def __enter__(self):
            self.cm = real()
            return self.cm.__enter__()

        def __exit__(self, *exc):
            out = self.cm.__exit__(*exc)
            shared.pop(item_id, None)
            return out

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(outbox, "_db", Racing)
        outbox.done(item_id)
    assert outbox.pending() == [] and item_id not in shared


def test_each_notice_for_a_route_that_failed_and_cant_be_kept_is_counted_lost(
    tmp_path, monkeypatch
):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    monkeypatch.setattr(outbox, "resting", busy)
    second = dict(
        change(), incident=dict(change()["incident"], name="b", title="b failed")
    )
    out = send([change(), second], single(HOOK), outbox, "agent", refuse)
    assert out["lost"] == 2 and out["sent"] == 0


def test_a_sent_message_read_back_from_the_outbox_is_kept_on_disk_by_id_and_time(
    tmp_path,
):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    outbox.add("Waiting", ["x"], "")
    [item] = outbox.pending()
    with outbox._db() as db:
        db.execute(
            "CREATE TRIGGER keep BEFORE DELETE ON outbox BEGIN SELECT RAISE(ABORT, 'locked'); END"
        )
    outbox.done(item["id"])
    kept = tmp_path / ("o.sqlite3" + notify.GONE_SUFFIX)
    assert json.loads(kept.read_text(encoding="utf-8")) == [
        [item["id"], item["created"]]
    ]


def test_a_message_that_goes_logs_no_warning(tmp_path, caplog):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    with caplog.at_level(logging.WARNING, logger=notify.__name__):
        outbox.done(outbox.sending("Sent", ["x"]))
    assert caplog.records == []


def test_the_audit_line_keeps_300_characters_of_the_error():
    lines = []

    class Audit:
        def record(self, action, outcome, seconds, params, **kw):
            lines.append(kw["error"])

    notify.audit_trouble(Audit(), {"error": "e" * 400}, "alerts.sqlite3")
    assert lines == ["e" * 300]


def test_five_changes_are_five_messages_and_a_digest_names_one_new_problem_alone():
    def one(kind, n):
        inc = dict(change()["incident"], name=f"t{n}", title=f"t{n} failed")
        return {"change": kind, "incident": inc}

    assert len(notify.messages([one("opened", n) for n in range(5)], "agent")) == 5
    changes = [one("opened", 0)] + [one("recovered", n) for n in range(1, 6)]
    [(title, lines)] = notify.messages(changes, "agent")
    assert title == "1 new problem, 5 recovered (agent)" and len(lines) == 6


def at(hour, minute=0):
    return dt.datetime(2026, 11, 20, hour, minute)


def test_quiet_hours_end_at_their_last_minute():
    assert Quiet.parse("01:00-06:00").until(at(6), "high") is None
    assert Quiet.parse("22:00-06:00").until(at(6), "high") is None
    assert Quiet.parse("22:00-06:00").until(at(5, 59), "high") == at(6)


def test_quiet_hours_that_start_and_end_at_the_same_minute_hold_all_day():
    quiet = Quiet.parse("22:00-22:00")
    assert quiet.until(at(10), "high") == at(22)
    assert quiet.until(at(22), "high") == at(22) + dt.timedelta(days=1)
