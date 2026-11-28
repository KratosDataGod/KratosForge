"""Run requests from Fabric pipelines (mailbox.py): what mutation testing (batch 14,
docs/quality/mutation-testing.md) showed no test checked. A fake agent, queue and control files stand
in; nothing runs and nothing reaches OneLake or Fabric."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest
import requests

from fabric_desktop import mailbox
from fabric_desktop.leases import Stored

NOW = 1_800_000_000.0
ITEM = "11111111-2222-3333-4444-555555555555"
RUN = "66666666-7777-8888-9999-000000000000"


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


def box(control, environments=("dev",), free=True, job=None, **kw):
    agent = SimpleNamespace(config=SimpleNamespace(name="etl-01"), clock=lambda: NOW,
                            has_free_worker=lambda: free, queue=Queue(job))
    return mailbox.Mailbox(agent, lambda env: control, list(environments), **kw)


def statuses(control):
    return [json.loads(data) for key, data in control.written if key.startswith("status/")]


# ---- defaults


def test_the_defaults_take_no_backfills_keep_files_two_weeks_and_allow_three_agents():
    m = box(Control())
    assert m.allow_backfill is False and m.max_attempts == 3 and m.take_over is False
    assert m.retention == 14 * 24 * 3600
    _, reason = m._parse("dev", "r1", json.dumps({"env": "dev", "backfill": "full"}).encode())
    assert "doesn't take backfills from pipelines" in reason


def test_a_status_is_written_as_indented_json():
    control = Control()
    box(control)._write_status("dev", "r1", {"state": "accepted"}, None)
    assert control.written == [("status/r1.json", b'{\n  "state": "accepted"\n}')]


# ---- the pipeline run a request names


def fabric_time(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def checked(started, **job):
    found = {"jobType": "Pipeline", "itemId": ITEM.upper(), "status": "InProgress",
             "startTimeUtc": fabric_time(started)} | job
    m = box(Control(), verify_run=lambda env, item, run: found, require_run=True)
    return m._check_run("dev", {"item_id": ITEM, "run_id": RUN}, NOW)


def test_a_run_up_to_a_day_old_vouches_for_a_request():
    got = checked(NOW - 24 * 3600 + 60)
    assert got["verified"] is True and got["detail"] == f"pipeline run {RUN} of item {ITEM}, InProgress"
    old = checked(NOW - 24 * 3600 - 1800)  # 24.5 hours
    assert old["verified"] is False and old["detail"] == "it started 24 hours ago; the most is 24"


def test_a_run_may_start_up_to_five_minutes_ahead_of_this_clock():
    assert checked(NOW + 299.5)["verified"] is True
    ahead = checked(NOW + 300.5)
    assert ahead["verified"] is False and ahead["detail"] == "Fabric gives it a start time in the future"


@pytest.mark.parametrize("job_type, said", [("Notebook", "it is a Notebook, not a pipeline run"),
                                            (None, "it is a job of unknown type, not a pipeline run")])
def test_a_job_that_isnt_a_pipeline_run_is_named(job_type, said):
    got = checked(NOW - 60, jobType=job_type)
    assert got["verified"] is False and got["detail"] == said


def test_a_lookup_fabric_couldnt_answer_is_marked_to_try_again():
    def busy(env, item, run):
        raise _status_error(429)

    m = box(Control(), verify_run=busy, require_run=True)
    got = m._check_run("dev", {"item_id": ITEM, "run_id": RUN}, NOW)
    assert got["transient"] is True and got["verified"] is False
    assert got["detail"].startswith("couldn't look it up in the dev workspace just now: ")
    def gone(env, item, run):
        raise _status_error(404)

    m.verify_run = gone
    got = m._check_run("dev", {"item_id": ITEM, "run_id": RUN}, NOW)
    assert "transient" not in got and got["detail"].startswith("couldn't find it in the dev workspace: ")


def _status_error(status):
    exc = RuntimeError(f"HTTP {status}")
    exc.status = status  # type: ignore[attr-defined]
    return exc


def test_runs_older_than_a_day_are_forgotten_and_newer_ones_kept(tmp_path):
    m = box(Control(), require_run=True, runs_file=tmp_path / "runs.json")
    m._run_taken("dev", "old", "r1", NOW)
    m._run_taken("dev", "recent", "r2", NOW + 24 * 3600 - 10)
    m._run_taken("dev", "new", "r3", NOW + 24 * 3600 + 10)
    assert sorted(m._runs["dev"]) == ["new", "recent"]
    assert sorted(json.loads((tmp_path / "runs.json").read_text(encoding="utf-8"))["dev"]) == ["new", "recent"]


# ---- the status follows the job


def synced(job, cancel=False, state="accepted"):
    files = {"cancel/r1.json": {}} if cancel else {}
    control = Control(files)
    m = box(control, job=job)
    m._sync("dev", "r1", {"state": state, "agent": "etl-01", "job_id": job["id"]}, Stored(b"{}", "e1", 0.0))
    return statuses(control)[-1]


def test_a_running_job_has_no_finish_time_yet():
    got = synced({"id": "j1", "state": "running", "attempts": 1, "started_at": NOW - 5, "finished_at": NOW - 1})
    assert got["state"] == "running" and "finished_at" not in got and "cancel" not in got
    done = synced({"id": "j1", "state": "succeeded", "attempts": 1, "started_at": NOW - 5, "finished_at": NOW - 1},
                  state="running")
    assert done["state"] == "succeeded" and done["finished_at"] == mailbox._iso(NOW - 1)


def test_a_cancel_while_running_is_noted_and_the_run_goes_on():
    got = synced({"id": "j1", "state": "running", "attempts": 1, "started_at": NOW - 5}, cancel=True)
    assert got["state"] == "running"
    assert got["cancel"] == "requested while running: the run in progress finishes, and it won't be retried"
    done = synced({"id": "j1", "state": "succeeded", "attempts": 1, "finished_at": NOW - 1}, cancel=True,
                  state="running")
    assert "cancel" not in done


def test_a_job_change_updates_only_this_agent_s_open_requests():
    job = {"source": "pipeline:r1", "payload": {"env": "dev"}}
    for status in ({"state": "accepted", "agent": "etl-02"}, {"state": "succeeded", "agent": "etl-01"}):
        control = Control({"status/r1.json": status})
        box(control, job={"id": "j1", "state": "running", "attempts": 1}).job_changed(job)
        assert control.written == []
    control = Control({"status/r1.json": {"state": "accepted", "agent": "etl-01"}})
    box(control, job={"id": "j1", "state": "running", "attempts": 1}).job_changed(job)
    assert statuses(control)[-1]["state"] == "running"


def test_a_cancel_check_is_only_for_this_agent_s_environments():
    control = Control({"cancel/r1.json": {}})
    m = box(control, environments=("dev",))
    assert m.cancel_requested({"source": "pipeline:r1", "payload": {"env": "dev"}}) is True
    assert m.cancel_requested({"source": "pipeline:r1", "payload": {"env": "prod"}}) is False
    assert m.cancel_requested({"source": "schedule:r1", "payload": {"env": "dev"}}) is False


# ---- takeover


def lost_owner_files(agents, stale_after=NOW - 3600):
    request = {"contract": 1, "env": "dev", "entities": ["silver.orders"]}
    record = {"kind": "agent_status", "stale_after": mailbox._iso(stale_after)}
    return {"requests/r1.json": request, "agents/etl-02.json": record,
            "status/r1.json": {"state": "running", "agent": "etl-02", "agents": agents}}


def test_a_request_three_agents_lost_fails_instead_of_going_round_again():
    control = Control(lost_owner_files(["a", "b", "etl-02"]))
    m = box(control, take_over=True, free=False)  # claim = "always": a busy agent still takes it over
    status, stored = m._read("dev", "status/r1.json")
    m._take_over("dev", "r1", status, stored)
    [new] = statuses(control)
    assert new["state"] == "failed"
    assert new["error"] == "3 agents took this request and stopped reporting: a, b, etl-02"


def test_a_busy_agent_leaves_a_lost_request_when_it_claims_only_while_free():
    control = Control(lost_owner_files(["a", "b", "etl-02"]))
    m = box(control, take_over=True, free=False, claim="when_free")
    m._take_over("dev", "r1", *m._read("dev", "status/r1.json"))
    assert control.written == []


@pytest.mark.parametrize("age, lost", [(100, False), (130, True)])
def test_on_onelake_an_owner_is_lost_after_its_fresh_time_plus_the_grace(age, lost):
    heartbeat = NOW - 1000
    record = {"kind": "agent_status", "heartbeat_at": mailbox._iso(heartbeat),
              "stale_after": mailbox._iso(heartbeat + 60)}
    control = Control({"agents/etl-02.json": record}, store_now=5000.0, modified=5000.0 - age)
    assert box(control)._past_stale("dev", "etl-02") is lost


# ---- old files


@pytest.mark.parametrize("ago, removed", [(14 * 86400 - 5, False), (14 * 86400 + 5, True)])
def test_a_finished_request_s_files_go_after_two_weeks(ago, removed):
    control = Control({"requests/r1.json": {}, "cancel/r1.json": {}})
    status = {"state": "succeeded", "updated_at": mailbox._iso(NOW - ago)}
    box(control)._expire_files("dev", "r1", status, Stored(b"{}", "e1", 0.0))
    assert control.deleted == (["requests/r1.json", "cancel/r1.json", "status/r1.json"] if removed else [])


# ---- LH_Meta's control files


def test_listing_a_mailbox_folder_onelake_refuses_is_an_error():
    class Refusing:
        def get(self, url, params=None, headers=None, timeout=None):
            return type("Resp", (), {"status_code": 400, "headers": {},
                                     "json": lambda _self: {"error": {"code": "InvalidQueryParameterValue"}},
                                     "close": lambda _self: None})()

    control = mailbox.OneLakeControl("ws", "meta-lh", lambda: "tok", session=Refusing(), sleep=lambda s: None)
    with pytest.raises(requests.HTTPError, match=r"\(400: InvalidQueryParameterValue\)"):
        control.list("requests")
