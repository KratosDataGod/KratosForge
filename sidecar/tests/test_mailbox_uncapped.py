"""Run requests from Fabric pipelines (mailbox.py): what the uncapped mutation run (batch 189,
docs/quality/mutation-testing.md) showed no test checked. A fake agent, queue and control files stand
in; nothing runs and nothing reaches OneLake or Fabric."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import mailbox
from fabric_desktop.errors import NotSetUp
from fabric_desktop.meta import OneLakeStore
from fabric_desktop.leases import Stored

NOW = 1_800_000_000.0


class Control:
    """Control files in memory, with every write kept."""

    def __init__(self, files=None, store_now=None, modified=0.0):
        self.files = dict(files or {})
        self.store_now, self.modified = store_now, modified
        self.written: list[tuple[str, bytes]] = []
        self.deleted: list[str] = []

    def read(self, key):
        data = self.files.get(key)
        if data is None:
            return None
        return Stored(data if isinstance(data, bytes) else json.dumps(data).encode(), "e1", self.modified,
                      store_now=self.store_now)

    def create(self, key, data):
        self.written.append((key, data))
        self.files[key] = data
        return True

    def replace(self, key, data, etag):
        return self.create(key, data)

    def delete(self, key, etag):
        self.deleted.append(key)
        self.files.pop(key, None)

    def list(self, folder):
        return sorted(k.split("/", 1)[1] for k in self.files if k.startswith(folder + "/"))


class Queue:
    def __init__(self, job=None):
        self.job, self.cancelled = job, []

    def find(self, key):
        return self.job

    def get(self, job_id):
        return self.job

    def cancel(self, job_id, why):
        self.cancelled.append(why)
        return True


def box(control, environments=("dev",), free=True, job=None, clock=NOW, **kw):
    agent = SimpleNamespace(config=SimpleNamespace(name="etl-01"), clock=lambda: clock,
                            has_free_worker=lambda: free, queue=Queue(job))
    return mailbox.Mailbox(agent, lambda env: control, list(environments), **kw)


def statuses(control):
    return [json.loads(data) for key, data in control.written if key.startswith("status/")]


# ---- a request that can't be read yet


def test_an_unreadable_request_is_rejected_once_it_is_a_minute_old():
    young = Control({"requests/r1.json": b"{"}, store_now=NOW, modified=NOW - 59.5)
    box(young)._claim("dev", "r1")
    assert young.written == []  # its writer may still be at it
    old = Control({"requests/r1.json": b"{"}, store_now=NOW, modified=NOW - 60)
    box(old)._claim("dev", "r1")
    assert statuses(old)[-1]["state"] == "rejected"


# ---- the holder of a local control folder


def test_the_refusal_names_the_holder_s_host_only_when_it_has_one():
    with_host = mailbox._in_use(Path("ctl"), {"agent": "etl-02", "pid": 42, "host": "pc-7"})
    assert "by agent etl-02 (process 42 on pc-7)." in with_host
    without = mailbox._in_use(Path("ctl"), {"agent": "etl-02", "pid": 42})
    assert "by agent etl-02 (process 42)." in without
    nobody = mailbox._in_use(Path("ctl"), {})
    assert "by another agent. Only one agent" in nobody


# ---- takeover


def agent_record(stale_after, heartbeat_at=None):
    record = {"kind": "agent_status", "stale_after": mailbox._iso(stale_after)}
    if heartbeat_at is not None:
        record["heartbeat_at"] = mailbox._iso(heartbeat_at)
    return record


def test_an_agent_is_lost_only_a_minute_past_its_stale_after():
    stale = NOW - 1000
    control = Control({"agents/etl-02.json": agent_record(stale)})
    assert box(control, clock=stale + 60)._past_stale("dev", "etl-02") is False
    assert box(control, clock=stale + 60.5)._past_stale("dev", "etl-02") is True


# ---- the request's size


def test_a_request_may_be_64_kib_and_no_more():
    m = box(Control())
    _, reason = m._parse("dev", "r1", b" " * (64 * 1024 + 1))
    assert reason == "the request is 65537 bytes; the most is 65536"
    _, reason = m._parse("dev", "r1", b" " * (64 * 1024))
    assert reason == "the request file is empty"


# ---- a pipeline run Fabric couldn't look up just then

ITEM = "11111111-2222-3333-4444-555555555555"
RUN = "66666666-7777-8888-9999-000000000000"


def _status_error(status):
    exc = RuntimeError(f"HTTP {status}")
    exc.status = status  # type: ignore[attr-defined]
    return exc


def _check(status):
    def lookup(env, item, run):
        raise _status_error(status)
    return box(Control(), verify_run=lookup)._check_run("dev", {"item_id": ITEM, "run_id": RUN}, NOW)


def test_timeouts_throttling_and_server_errors_are_tried_again_and_others_are_not():
    for status in (408, 429, 500, 502, 503, 504):
        assert _check(status).get("transient") is True, status
    for status in (400, 404, 409, 501, 505):
        assert "transient" not in _check(status), status


def test_a_run_lookup_is_tried_again_for_fifteen_minutes_then_the_request_is_rejected():
    request = json.dumps({"env": "dev", "pipeline_run": {"item_id": ITEM, "run_id": RUN}}).encode()

    def busy(env, item, run):
        raise _status_error(503)

    young = Control({"requests/r1.json": request}, store_now=NOW, modified=NOW - 899.5)
    box(young, verify_run=busy, require_run=True)._claim("dev", "r1")
    assert young.written == []
    old = Control({"requests/r1.json": request}, store_now=NOW, modified=NOW - 900)
    box(old, verify_run=busy, require_run=True)._claim("dev", "r1")
    got = statuses(old)[-1]
    assert got["state"] == "rejected" and got["reason"].startswith("its pipeline run didn't check out")


# ---- a job whose request another agent took over


def test_taken_over_names_the_agent_that_has_the_request_now():
    job = {"source": "pipeline:r1", "payload": {"env": "dev"}}
    control = Control({"status/r1.json": {"state": "running", "agent": "etl-02"}})
    assert box(control).taken_over(job) == "etl-02"
    mine = Control({"status/r1.json": {"state": "running", "agent": "etl-01"}})
    assert box(mine).taken_over(job) == ""


# ---- a status write another agent got to first


class Refusing(Control):
    """Control files whose replace is always refused: the status changed since it was read."""

    def replace(self, key, data, etag):
        self.written.append(("refused/" + key, data))
        return False


def test_a_refused_status_write_checks_whether_another_agent_took_the_request_over():
    queued = {"id": "j1", "state": "queued", "attempts": 0}
    taken = Refusing({"status/r1.json": {"state": "accepted", "agent": "etl-02"}})
    m = box(taken, job=dict(queued))
    m._sync("dev", "r1", {"state": "accepted", "agent": "etl-01"}, Stored(b"{}", "e1", 0.0))
    assert m.agent.queue.cancelled == ["agent etl-02 took this request over"]
    still_mine = Refusing({"status/r1.json": {"state": "accepted", "agent": "etl-01"}})
    m = box(still_mine, job=dict(queued))
    m._sync("dev", "r1", {"state": "accepted", "agent": "etl-01"}, Stored(b"{}", "e1", 0.0))
    assert m.agent.queue.cancelled == []


def test_a_written_status_change_is_logged_and_nothing_more_is_read(caplog):
    control = Control()
    m = box(control, job={"id": "j1", "state": "running", "attempts": 1})
    with caplog.at_level("INFO", logger="fabric_desktop.mailbox"):
        m._sync("dev", "r1", {"state": "accepted", "agent": "etl-01"}, Stored(b"{}", "e1", 0.0))
    assert [r.getMessage() for r in caplog.records] == ["pipeline request running"]
    assert statuses(control)[-1]["state"] == "running"


# ---- a request accepted but never queued


def test_a_request_never_queued_ends_cancelled_or_expired_and_says_so(caplog):
    stored = Stored(b"{}", "e1", 0.0)
    status = {"state": "accepted", "agent": "etl-01", "deadline": "2020-01-01T00:00:00Z", "waiting": "room"}
    control = Control()
    with caplog.at_level("INFO", logger="fabric_desktop.mailbox"):
        box(control)._end_unqueued("dev", "r1", dict(status), stored, True)
        box(control)._end_unqueued("dev", "r1", dict(status), stored, False)
    cancelled, expired = statuses(control)
    assert cancelled["state"] == "cancelled" and cancelled["reason"] == "a cancel file arrived before it was queued"
    assert expired["state"] == "expired"
    assert expired["reason"] == "its deadline 2020-01-01T00:00:00Z passed before it started"
    assert "waiting" not in cancelled and "waiting" not in expired
    assert [r.getMessage() for r in caplog.records] == ["pipeline request cancelled", "pipeline request expired"]


def test_a_request_waiting_for_room_says_why_once(caplog):
    full = mailbox.QueueFull.__new__(mailbox.QueueFull)
    full.limit, full.waiting = 5, 5
    control = Control()
    m = box(control)
    stored = Stored(b"{}", "e1", 0.0)
    with caplog.at_level("INFO", logger="fabric_desktop.mailbox"):
        m._wait_for_room("dev", "r1", {"state": "accepted"}, stored, full)
    first = statuses(control)[-1]
    assert first["waiting"] == "the agent's queue is full (agent.max_queued = 5); it is queued when there is room"
    assert [r.getMessage() for r in caplog.records] == ["pipeline request waiting: the agent's queue is full"]
    m._wait_for_room("dev", "r1", first, stored, full)
    assert len(statuses(control)) == 1


# ---- this agent's own copy of a request another agent has now


def test_a_queued_copy_is_cancelled_and_logged_and_a_running_one_left(caplog):
    with caplog.at_level("INFO", logger="fabric_desktop.mailbox"):
        queued = box(Control(), job={"id": "j1", "state": "queued"})
        queued._handed_over("r1", {"agent": "etl-02"})
    assert queued.agent.queue.cancelled == ["agent etl-02 took this request over"]
    assert [r.getMessage() for r in caplog.records] == ["pipeline request taken over by another agent"]
    caplog.clear()
    with caplog.at_level("INFO", logger="fabric_desktop.mailbox"):
        running = box(Control(), job={"id": "j1", "state": "running"})
        running._handed_over("r1", {"agent": "etl-02"})
    assert running.agent.queue.cancelled == [] and caplog.records == []


# ---- taking a request over


def test_a_request_taken_over_is_logged_and_then_follows_this_agent_s_job(caplog):
    control = Control({"agents/etl-02.json": agent_record(NOW - 1000),
                       "requests/r1.json": {"env": "dev"},
                       "status/r1.json": {"state": "running", "agent": "etl-02", "agents": ["etl-02"]}})
    m = box(control, take_over=True, job={"id": "j1", "state": "queued", "attempts": 0})
    with caplog.at_level("INFO", logger="fabric_desktop.mailbox"):
        m._take_over("dev", "r1", {"state": "running", "agent": "etl-02", "agents": ["etl-02"]},
                     Stored(b"{}", "e1", 0.0))
    first, synced = statuses(control)
    assert first["agent"] == "etl-01" and first["taken_over_from"] == "etl-02" and "job_id" not in first
    assert synced["job_id"] == "j1" and synced["agents"] == ["etl-02", "etl-01"]
    assert "pipeline request taken over" in [r.getMessage() for r in caplog.records]


def test_taken_over_is_empty_for_a_job_that_isnt_this_agent_s_pipeline_job():
    control = Control({"status/x.json": {"state": "running", "agent": "etl-02"}})
    m = box(control)
    assert m.taken_over({"source": "schedule:x", "payload": {"env": "dev"}}) == ""
    assert m.taken_over({"source": "pipeline:x", "payload": {"env": "prod"}}) == ""
    assert m.taken_over({"source": "pipeline:x", "payload": {"env": "dev"}}) == "etl-02"


# ---- where the control files are


def test_a_onelake_environment_without_lh_meta_is_not_set_up_yet():
    store = OneLakeStore.__new__(OneLakeStore)
    with pytest.raises(NotSetUp, match="LH_Meta doesn't exist in this environment's workspace yet"):
        mailbox.control_backend_for(store, None, lambda: "token")


def test_a_control_folder_that_already_exists_can_be_held(tmp_path):
    folder = tmp_path / "control"
    folder.mkdir()
    held = mailbox.hold_control_folder(folder, {"agent": "etl-01"})
    held.close()
    assert (folder / mailbox.CONTROL_LOCK).exists()


def test_a_control_folder_is_made_with_its_parents(tmp_path):
    folder = tmp_path / "env" / "control"
    mailbox.hold_control_folder(folder, {"agent": "etl-01"}).close()
    assert folder.is_dir()


def test_a_shorter_holder_record_replaces_a_longer_one_whole(tmp_path):
    folder = tmp_path / "control"
    mailbox.hold_control_folder(folder, {"agent": "etl-01", "pid": 123456789, "host": "a-long-host-name"}).close()
    held = mailbox.hold_control_folder(folder, {"agent": "etl-2"})
    try:
        assert mailbox._holder(folder / mailbox.CONTROL_LOCK) == {"agent": "etl-2"}
    finally:
        held.close()


# ---- what a request asks for


def test_cascade_is_kept_only_when_it_is_true_or_false():
    m = box(Control(), allow_backfill=True)
    request, reason = m._parse("dev", "r1", json.dumps({"env": "dev", "entities": ["silver.a"], "cascade": True}).encode())
    assert reason == "" and request["cascade"] is True
    request, reason = m._parse("dev", "r1", json.dumps({"env": "dev", "cascade": "yes"}).encode())
    assert reason == "cascade is true or false" and request["cascade"] is False


# ---- a request accepted but not queued yet


def test_a_cancel_file_ends_a_request_not_queued_yet_without_a_deadline():
    control = Control({"cancel/r1.json": {}})
    box(control)._sync("dev", "r1", {"state": "accepted", "agent": "etl-01"}, Stored(b"{}", "e1", 0.0))
    got = statuses(control)[-1]
    assert got["state"] == "cancelled" and got["reason"] == "a cancel file arrived before it was queued"


def test_a_job_the_pipeline_cancelled_is_cancelled_not_expired():
    control = Control()
    m = box(control, job={"id": "j1", "state": "cancelled", "attempts": 0, "error": "the pipeline cancelled it"})
    m._sync("dev", "r1", {"state": "accepted", "agent": "etl-01", "job_id": "j1"}, Stored(b"{}", "e1", 0.0))
    assert statuses(control)[-1]["state"] == "cancelled"


def test_an_agent_without_a_status_record_is_not_lost():
    assert box(Control(), clock=NOW + 10**9)._past_stale("dev", "etl-02") is False


def test_onelake_s_clock_is_used_only_with_both_a_store_time_and_a_heartbeat():
    stale = NOW - 1000
    # No store time (a folder on this PC): this PC's clock, against stale_after
    local = Control({"agents/etl-02.json": agent_record(stale, heartbeat_at=stale - 100)}, modified=0.0)
    assert box(local, clock=stale + 30)._past_stale("dev", "etl-02") is False
    # A store time but no heartbeat: this PC's clock too
    no_beat = Control({"agents/etl-02.json": agent_record(stale)}, store_now=NOW, modified=NOW)
    assert box(no_beat, clock=stale + 61)._past_stale("dev", "etl-02") is True


def test_at_least_one_agent_may_hold_a_request():
    assert box(Control(), max_attempts=0).max_attempts == 1
    assert box(Control(), max_attempts=-2).max_attempts == 1


def test_a_checked_run_records_fabric_s_status_and_start_time():
    started = "2027-01-15T08:00:00.1234567"
    job = {"jobType": "Pipeline", "itemId": ITEM, "status": "InProgress", "startTimeUtc": started}
    m = box(Control(), verify_run=lambda env, item, run: job)
    got = m._check_run("dev", {"item_id": ITEM, "run_id": RUN}, mailbox._fabric_utc(started) + 60)
    assert got["status"] == "InProgress" and got["started_at"] == started and got["verified"] is True


def test_a_record_whose_stale_after_isnt_a_time_is_not_lost():
    control = Control({"agents/etl-02.json": {"kind": "agent_status", "stale_after": "soon"}})
    assert box(control, clock=NOW + 10**9)._past_stale("dev", "etl-02") is False


def test_a_request_taken_over_keeps_what_it_asked_and_drops_the_last_agent_s_run():
    control = Control({"agents/etl-02.json": agent_record(NOW - 1000), "requests/r1.json": {"env": "dev"}})
    m = box(control, take_over=True, job={"id": "j9", "state": "queued", "attempts": 0})
    status = {"state": "running", "agent": "etl-02", "env": "dev", "entities": ["silver.a"], "job_id": "j1",
              "attempts": 2, "started_at": "x", "error": "boom"}
    m._take_over("dev", "r1", status, Stored(b"{}", "e1", 0.0))
    first = statuses(control)[0]
    assert first["env"] == "dev" and first["entities"] == ["silver.a"]
    assert not {"job_id", "attempts", "started_at", "error"} & set(first)


def test_an_agent_that_always_claims_takes_a_request_with_no_worker_free():
    control = Control({"requests/r1.json": {"env": "dev"}})
    box(control, free=False, job={"id": "j1", "state": "queued", "attempts": 0})._poll_env("dev")
    assert statuses(control)[0]["state"] == "accepted"


def test_a_request_not_queued_yet_is_queued_with_its_entities():
    control = Control()
    m = box(control)
    asked = []

    def enqueue(env, entities, backfill, cascade, source, dedupe_key):
        asked.append((env, entities, backfill, cascade, source, dedupe_key))
        return {"id": "j1", "state": "queued", "attempts": 0}

    m.agent.enqueue = enqueue
    m._sync("dev", "r1", {"state": "accepted", "agent": "etl-01", "entities": ["silver.a"], "backfill": "full",
                          "cascade": True}, Stored(b"{}", "e1", 0.0))
    assert asked == [("dev", ["silver.a"], "full", True, "pipeline:r1", "pipeline:r1")]
    assert statuses(control)[-1]["job_id"] == "j1"


def test_a_poll_hands_over_a_request_another_agent_holds():
    control = Control({"requests/r1.json": {"env": "dev"}, "status/r1.json": {"state": "accepted", "agent": "etl-02"}})
    m = box(control, job={"id": "j1", "state": "queued", "attempts": 0})
    m._poll_env("dev")
    assert m.agent.queue.cancelled == ["agent etl-02 took this request over"] and control.written == []


def test_a_long_host_name_is_cut_to_64_characters():
    said = mailbox._in_use(Path("ctl"), {"agent": "etl-02", "host": "h" * 70})
    assert f"(on {'h' * 64})." in said


def test_a_request_held_by_a_lost_agent_isnt_taken_over_unless_the_agent_takes_over():
    control = Control({"requests/r1.json": {"env": "dev"}, "agents/etl-02.json": agent_record(NOW - 1000),
                       "status/r1.json": {"state": "accepted", "agent": "etl-02"}})
    box(control)._poll_env("dev")
    assert control.written == []
    box(control, take_over=True, job={"id": "j1", "state": "queued", "attempts": 0})._poll_env("dev")
    assert statuses(control)[0]["taken_over_from"] == "etl-02"


def test_an_old_run_says_how_many_hours_ago_it_started():
    job = {"jobType": "Pipeline", "itemId": ITEM, "status": "InProgress",
           "startTimeUtc": mailbox._iso(NOW - 25.5 * 3600).replace("Z", "")}
    got = box(Control(), verify_run=lambda env, item, run: job)._check_run("dev", {"item_id": ITEM, "run_id": RUN}, NOW)
    assert got["detail"] == "it started 26 hours ago; the most is 24"


class _Listing:
    status_code = 200
    headers: dict = {}

    def json(self):
        return {"paths": [{"name": "x/requests/r1.json"}, {"name": "x/requests/sub", "isDirectory": "true"}]}


def test_a_onelake_listing_waits_a_minute_for_an_answer():
    seen = []

    class Http:
        def get(self, url, params, headers, timeout):
            seen.append(timeout)
            return _Listing()

    control = mailbox.OneLakeControl("ws", "lh", lambda: "token", session=Http())
    assert control.list("requests") == ["r1.json"]
    assert seen == [60]


# ---- each limit, exactly at it


def test_a_finished_request_s_files_go_once_they_are_exactly_retention_days_old():
    updated = NOW - 14 * 86400
    files = {"requests/r1.json": {}, "status/r1.json": {}}
    control = Control(files)
    box(control)._expire_files("dev", "r1", {"state": "succeeded", "updated_at": mailbox._iso(updated)},
                               Stored(b"{}", "e1", 0.0))
    assert control.deleted == ["requests/r1.json", "status/r1.json"]
    kept = Control(files)
    box(kept)._expire_files("dev", "r1", {"state": "succeeded", "updated_at": mailbox._iso(updated + 1)},
                            Stored(b"{}", "e1", 0.0))
    assert kept.deleted == []


def test_a_run_vouches_for_one_request_for_exactly_a_day():
    m = box(Control(), require_run=True)
    m._runs = {"dev": {"run": {"request_id": "r1", "at": NOW - 86400}}}
    assert m._run_used("dev", "run", "r2", NOW) == "r1"
    assert m._run_used("dev", "run", "r2", NOW + 0.5) is None


def test_submitted_by_may_be_200_characters():
    m = box(Control())
    _, reason = m._parse("dev", "r1", json.dumps({"env": "dev", "submitted_by": "x" * 200}).encode())
    assert reason == ""
    _, reason = m._parse("dev", "r1", json.dumps({"env": "dev", "submitted_by": "x" * 201}).encode())
    assert reason == "submitted_by is text of at most 200 characters"


def _run_started(age):
    job = {"jobType": "Pipeline", "itemId": ITEM, "status": "InProgress",
           "startTimeUtc": mailbox._iso(NOW - age).replace("Z", "")}
    return box(Control(), verify_run=lambda env, item, run: job)._check_run("dev", {"item_id": ITEM, "run_id": RUN}, NOW)


def test_a_run_exactly_a_day_old_or_exactly_five_minutes_ahead_still_vouches():
    assert _run_started(86400)["verified"] is True
    assert _run_started(-300)["verified"] is True


def test_on_onelake_s_clock_an_agent_is_lost_only_past_its_freshness_and_the_grace():
    record = agent_record(NOW - 1000, heartbeat_at=NOW - 1100)  # fresh for 100 s
    exactly = Control({"agents/etl-02.json": record}, store_now=NOW, modified=NOW - 160)
    assert box(exactly)._past_stale("dev", "etl-02") is False
    past = Control({"agents/etl-02.json": record}, store_now=NOW, modified=NOW - 160.5)
    assert box(past)._past_stale("dev", "etl-02") is True


def test_a_deadline_of_exactly_now_hasn_t_passed():
    now = mailbox._iso(NOW)
    control = Control({"requests/r1.json": {"env": "dev", "deadline": now}})
    box(control, job={"id": "j1", "state": "queued", "attempts": 0})._claim("dev", "r1")
    assert statuses(control)[0]["state"] == "accepted"
    queued = box(Control(), job={"id": "j1", "state": "queued", "attempts": 0})
    queued._sync("dev", "r1", {"state": "accepted", "agent": "etl-01", "job_id": "j1", "deadline": now},
                 Stored(b"{}", "e1", 0.0))
    assert queued.agent.queue.cancelled == []
    waiting = box(Control())
    got = []
    waiting.agent.enqueue = lambda *a, **k: got.append(a) or {"id": "j2", "state": "queued", "attempts": 0}
    waiting._sync("dev", "r1", {"state": "accepted", "agent": "etl-01", "deadline": now}, Stored(b"{}", "e1", 0.0))
    assert len(got) == 1


def test_a_run_taken_exactly_a_day_ago_is_still_remembered():
    m = box(Control(), require_run=True)
    m._run_taken("dev", "old", "r1", NOW)
    m._run_taken("dev", "new", "r2", NOW + 86400)
    assert sorted(m._runs["dev"]) == ["new", "old"]


def test_a_second_hold_on_a_held_control_folder_is_refused_and_names_the_holder(tmp_path):
    folder = tmp_path / "control"
    held = mailbox.hold_control_folder(folder, {"agent": "etl-01", "pid": 7})
    try:
        with pytest.raises(mailbox.ControlFolderInUse, match=r"in use by agent etl-01 \(process 7\)"):
            mailbox.hold_control_folder(folder, {"agent": "etl-02"})
    finally:
        held.close()


def test_a_status_that_already_says_what_the_job_says_isnt_written_again():
    control = Control()
    m = box(control, job={"id": "j1", "state": "running", "attempts": 1})
    status = {"state": "running", "agent": "etl-01", "job_id": "j1", "attempts": 1, "updated_at": "2027-01-01T00:00:00Z"}
    m._sync("dev", "r1", status, Stored(b"{}", "e1", 0.0))
    assert control.written == []
