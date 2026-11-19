"""Teams notice edges the main tests don't pin down (mutation batch 142, docs/quality/mutation-testing.md):
the outbox's limits as numbers, an error cut to its length, the address fingerprint, an escalation
to the route the incident names, quiet hours by the outbox's clock, and a folder that doesn't exist yet."""

import dataclasses
import datetime as dt

import pytest

from fabric_desktop.workloads import notify
from fabric_desktop.workloads.notify import Outbox, Quiet, Route, Routing, send, single

from test_agent import Clock

HOOK = "https://contoso.webhook.office.com/hook"


def change(kind="opened", **inc):
    return {"change": kind, "incident": {"kind": "run_failed", "name": "a", "env": "prod", "title": "a failed",
                                         "text": "boom", "owner": "", **inc}}


def test_the_outbox_limits_are_these_numbers():
    assert notify.MAX_WAITING == 500 and notify.MAX_PER_TRY == 25


def test_an_error_is_kept_to_500_characters(tmp_path):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    outbox.add("t", ["x"], "e" * 600)
    [item] = outbox.pending()
    assert len(item["error"]) == 500 and item["attempts"] == 1


def test_a_failing_routes_address_is_kept_as_a_16_digit_fingerprint(tmp_path):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    outbox.route_failed("default", HOOK, "down")
    with outbox._db() as db:
        [(address,)] = db.execute("SELECT address FROM route_state").fetchall()
    assert len(address) == 16 and address == notify._fingerprint(HOOK) and HOOK not in address


def test_no_routes_send_and_queue_nothing(tmp_path):
    assert send([change()], Routing(), Outbox(tmp_path / "o.sqlite3", Clock()), "agent", lambda *a: None) == {
        "sent": 0, "queued": 0, "held": 0, "dropped": 0, "skipped": "no Teams webhook set"}


def test_an_escalation_goes_to_the_route_the_incident_names():
    routing = Routing([Route("default", "https://d"), Route("on-call", "https://o"), Route("managers", "https://m")],
                      escalate_to="on-call")
    [r] = routing.for_change(change("escalated", escalated_to="managers"))
    assert r.name == "managers"
    [r] = routing.for_change(change("escalated"))
    assert r.name == "on-call"


def test_quiet_hours_go_by_the_outboxs_clock_when_no_time_is_given(tmp_path):
    outbox = Outbox(tmp_path / "o.sqlite3", Clock())
    routing = single(HOOK)
    routing.quiet = Quiet.parse("00:00-23:59")
    posted = []
    res = send([change()], routing, outbox, "agent", lambda url, title, lines: posted.append(title))
    assert res["held"] == 1 and res["sent"] == 0 and posted == []


def test_the_outboxs_folder_is_made_with_its_parents(tmp_path):
    outbox = Outbox(tmp_path / "a" / "b" / "o.sqlite3", Clock())
    assert outbox.count() == 0 and (tmp_path / "a" / "b" / "o.sqlite3").exists()


def test_a_route_cant_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Route("default", HOOK).url = "https://elsewhere"  # type: ignore[misc]


def test_a_waiting_message_keeps_when_it_was_made_and_each_error_is_cut_to_500(tmp_path):
    clock = Clock(1_800_000_123.0)
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    item_id = outbox.sending("t", ["x"])
    clock.t += 5
    assert outbox.pending()[0]["created"] == 1_800_000_123.0
    outbox.not_sent(item_id, "n" * 600)
    assert len(outbox.pending()[0]["error"]) == 500
    outbox.failed(item_id, "f" * 600)
    assert len(outbox.pending()[0]["error"]) == 500
    outbox.route_failed("default", HOOK, "r" * 600)
    with outbox._db() as db:
        [(error,)] = db.execute("SELECT error FROM route_state").fetchall()
    assert len(error) == 500


def test_a_message_due_at_exactly_now_goes_now(tmp_path):
    clock = Clock()
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    outbox.add("t", ["x"], "", not_before=clock())
    posted = []
    assert send([], single(HOOK), outbox, "agent", lambda url, title, lines: posted.append(title))["sent"] == 1
    assert posted == ["t"]


def test_a_route_at_its_longest_rest_is_still_resting_up_to_the_clock_margin(tmp_path):
    clock = Clock()
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    for _ in range(8):
        wait = outbox.route_failed("default", HOOK, "down")
    assert wait == notify.BACKOFF_MAX and outbox.resting({"default": HOOK}) == {"default": "down"}
    edge = clock() + notify.BACKOFF_MAX + notify.CLOCK_MARGIN
    for next_try, resting in ((edge, True), (edge + 1, False)):
        with outbox._db() as db:
            db.execute("UPDATE route_state SET next_try = ?", (next_try,))
        assert bool(outbox.resting({"default": HOOK})) is resting


def test_quiet_hours_start_at_exactly_their_start_by_day_and_overnight():
    day = dt.datetime(2027, 1, 15, 9, 0, tzinfo=dt.timezone.utc)
    assert Quiet.parse("09:00-17:00").until(day, "") == day.replace(hour=17)
    night = day.replace(hour=22)
    assert Quiet.parse("22:00-06:00").until(night, "") == dt.datetime(2027, 1, 16, 6, 0, tzinfo=dt.timezone.utc)


def test_an_audit_line_without_a_queued_count_says_zero():
    lines = []

    class Audit:
        def record(self, *args, **kw):
            lines.append((args, kw))

    notify.audit_trouble(Audit(), {"error": "ConnectionError: down"}, "agent")
    [(args, _kw)] = lines
    assert args[3] == {"outbox": "agent", "queued": 0, "dropped": 0, "unconfirmed": 0, "waiting": 0}
