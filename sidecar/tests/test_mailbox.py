"""Run requests from Fabric pipelines: the LH_Meta mailbox, its contract, and the sample notebook."""

import dataclasses
import json
import os
import time
from pathlib import Path

import pytest
import requests

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.mailbox import FINAL, LocalControl, OneLakeControl
from fabric_desktop.meta import LocalStore
from fabric_desktop.meta.templates import init_project

NOTEBOOK = Path(__file__).resolve().parents[2] / "examples" / "pipeline" / "run_on_agent.ipynb"


def config_text(project: Path, home: Path, pipeline: str = "enabled = true\npoll_seconds = 1") -> str:
    return f"""
[agent]
name = "etl-01"
home = {json.dumps(str(home))}
project = {json.dumps(str(project))}
environments = ["dev"]

[auth]
type = "user"

[retry]
max_attempts = 3
backoff_seconds = 0

[pipeline]
{pipeline}
"""


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(config_text(project, tmp_path / "home"), encoding="utf-8")
    return agent_config.load(path)


@pytest.fixture
def nb():
    """The sample notebook's parameter and function cells, as a pipeline would run them."""
    cells = [c for c in json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"] if c["cell_type"] == "code"]
    scope: dict = {}
    exec("".join(cells[0]["source"]) + "\n" + "".join(cells[1]["source"]), scope)
    return scope


def root(cfg) -> Path:
    return cfg.project / ".local" / "dev" / "_control"


def write_request(cfg, rid: str, body, age: float = 0) -> Path:
    path = root(cfg) / "requests" / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return path


def status(cfg, rid: str) -> dict | None:
    path = root(cfg) / "status" / f"{rid}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run(agent: Agent) -> None:
    """One loop pass, then wait for the jobs it started."""
    agent._mailbox_at = 0
    agent.step()
    for f in list(agent._running.values()):
        f.result(timeout=120)


# ---- the whole round trip, with the sample notebook

def test_a_pipeline_request_runs_and_reports_back(setup, nb):
    agent = Agent(setup)
    request = nb["make_request"]("dev", "bronze.orders, silver.orders", 60, "pl_nightly 8c1f")
    assert nb["submit"](str(root(setup)), "run-8c1f", request)
    assert not list((root(setup) / "requests").glob("*.tmp"))
    run(agent)
    done = nb["wait"](str(root(setup)), "run-8c1f", 1, 0, sleep=lambda s: None)
    assert done["state"] == "succeeded", done
    assert done["contract"] == 1 and done["agent"] == "etl-01" and done["submitted_by"] == "pl_nightly 8c1f"
    assert done["entities"] == ["bronze.orders", "silver.orders"] and done["attempts"] == 1
    assert done["result"]["tables"] == 2 and done["result"]["failed"] == 0
    assert done["started_at"] <= done["finished_at"]
    job = agent.queue.get(done["job_id"])
    assert job["source"] == "pipeline:run-8c1f" and job["state"] == "succeeded"
    log = LocalStore(setup.project / ".local" / "dev", "dev").meta("run_log")
    assert done["job_id"] in set(log["job_id"])


def test_the_same_request_runs_once_however_often_it_is_sent_or_read(setup, nb, tmp_path):
    r = str(root(setup))
    request = nb["make_request"]("dev", "bronze.orders", 60, "")
    assert nb["submit"](r, "rerun", request)
    assert not nb["submit"](r, "rerun", request)  # the activity retried: no second request
    a = Agent(setup)
    b = Agent(dataclasses.replace(setup, name="etl-02", home=tmp_path / "home-b"))
    b.mailbox.poll()  # etl-02 takes it first
    a.mailbox.poll()
    run(a)
    run(b)
    run(b)
    assert status(setup, "rerun")["agent"] == "etl-02" and status(setup, "rerun")["state"] == "succeeded"
    assert a.queue.counts()["queued"] == 0 and sum(a.queue.counts().values()) == 0
    assert sum(b.queue.counts().values()) == 1


# ---- refused requests

@pytest.mark.parametrize("body, reason", [
    ("{not json", "isn't JSON"),
    ("", "empty"),
    ("[1, 2]", "a JSON object"),
    ({"env": "prod"}, "this is the dev mailbox"),
    ({"env": "dev", "contract": 2}, "speaks contract 1"),
    ({"env": "dev", "entities": ["silver.nope"]}, "no entity called silver.nope"),
    ({"env": "dev", "entities": "bronze.orders"}, "a list of entity names"),
    ({"env": "dev", "backfill": "full"}, "doesn't take backfills"),
    ({"env": "dev", "entities": ["bronze.orders"], "cascade": True}, "doesn't take backfills"),
    ({"env": "dev", "deadline": "tomorrow"}, "deadline"),
    ({"env": "dev", "deadline": "2026-10-17T03:00:00"}, "time zone"),
    ({"env": "dev", "request_id": "other"}, "doesn't match the file name"),
    ({"env": "dev", "submitted_by": "x" * 201}, "at most 200"),
])
def test_malformed_or_disallowed_requests_are_rejected_with_a_reason(setup, body, reason):
    agent = Agent(setup)
    write_request(setup, "bad", body, age=120)
    run(agent)
    s = status(setup, "bad")
    assert s["state"] == "rejected" and reason in s["reason"], s
    assert sum(agent.queue.counts().values()) == 0


def test_a_half_written_request_gets_time_to_finish(setup):
    agent = Agent(setup)
    path = write_request(setup, "slow", '{"env": "de')
    run(agent)
    assert status(setup, "slow") is None  # still being written, perhaps
    path.write_text(json.dumps({"env": "dev", "entities": ["bronze.orders"]}), encoding="utf-8")
    run(agent)
    assert status(setup, "slow")["state"] == "succeeded"


def test_an_entity_allowlist_limits_what_a_pipeline_may_run(setup, tmp_path):
    path = tmp_path / "allow.toml"
    path.write_text(config_text(setup.project, tmp_path / "h2", 'enabled = true\nentities = ["silver.*"]'), encoding="utf-8")
    agent = Agent(agent_config.load(path))
    write_request(setup, "all", {"env": "dev"})
    write_request(setup, "gold", {"env": "dev", "entities": ["silver.orders", "gold.fact_sales"]})
    write_request(setup, "silver", {"env": "dev", "entities": ["silver.orders"]})
    agent.mailbox.poll()
    assert "only takes silver.*" in status(setup, "all")["reason"]
    assert status(setup, "gold")["reason"] == "gold.fact_sales isn't in pipeline.entities in agent.toml"
    assert status(setup, "silver")["state"] == "accepted"


def test_ids_that_cannot_name_a_file_are_left_alone(setup):
    agent = Agent(setup)
    write_request(setup, "has space", {"env": "dev"})
    run(agent)
    assert not (root(setup) / "status").exists() or not list((root(setup) / "status").iterdir())


# ---- deadlines and cancelling

def test_a_request_past_its_deadline_expires(setup):
    agent = Agent(setup)
    write_request(setup, "late", {"env": "dev", "deadline": "2020-01-01T00:00:00Z"})
    run(agent)
    s = status(setup, "late")
    assert s["state"] == "expired" and "2020-01-01" in s["reason"] and "job_id" not in s
    assert sum(agent.queue.counts().values()) == 0


def test_a_queued_request_expires_when_its_deadline_passes_before_it_starts(setup):
    agent = Agent(setup)
    soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    write_request(setup, "waits", {"env": "dev", "deadline": soon})
    agent.mailbox.poll()  # taken and queued, but no worker free yet
    assert status(setup, "waits")["state"] == "accepted"
    agent.clock = lambda: time.time() + 7200
    agent.mailbox.poll()
    s = status(setup, "waits")
    assert s["state"] == "expired" and agent.queue.get(s["job_id"])["state"] == "cancelled"
    run(agent)  # nothing left to run
    assert agent.queue.counts()["succeeded"] == 0


def test_a_cancel_file_stops_a_request_before_it_runs(setup, nb):
    agent = Agent(setup)
    r = str(root(setup))
    write_request(setup, "early", {"env": "dev"})
    nb["cancel"](r, "early")
    write_request(setup, "queued", {"env": "dev", "entities": ["bronze.orders"]})
    agent.mailbox.poll()
    nb["cancel"](r, "queued")
    run(agent)
    assert status(setup, "early")["state"] == "cancelled" and "job_id" not in status(setup, "early")
    s = status(setup, "queued")
    assert s["state"] == "cancelled" and agent.queue.get(s["job_id"])["state"] == "cancelled"


def test_a_cancel_that_arrives_between_polls_stops_the_worker_before_it_starts(setup, nb):
    agent = Agent(setup)
    write_request(setup, "between", {"env": "dev", "entities": ["bronze.orders"]})
    agent.mailbox.poll()
    nb["cancel"](str(root(setup)), "between")
    agent._mailbox_at = agent.clock()  # this pass doesn't read the mailbox; the worker checks anyway
    agent.step()
    for f in list(agent._running.values()):
        f.result(timeout=120)
    s = status(setup, "between")
    assert s["state"] == "cancelled" and agent.queue.get(s["job_id"])["state"] == "cancelled"
    assert agent.queue.counts()["succeeded"] == 0


def test_cancelling_a_running_request_lets_the_run_finish(setup, nb, monkeypatch):
    agent = Agent(setup)
    write_request(setup, "busy", {"env": "dev", "entities": ["bronze.orders"]})
    agent.mailbox.poll()
    real = agent.runtime.run_environment

    def slow(*args, **kwargs):
        nb["cancel"](str(root(setup)), "busy")
        agent.mailbox.poll()  # the loop reads the mailbox while the run goes on
        return real(*args, **kwargs)
    monkeypatch.setattr(agent.runtime, "run_environment", slow)
    run(agent)
    s = status(setup, "busy")
    assert s["state"] == "succeeded" and "finishes" in s["cancel"]


def test_the_notebook_gives_up_cancels_and_fails_after_its_timeout(setup, nb):
    r, t = str(root(setup)), [0.0]
    nb["submit"](r, "nobody", nb["make_request"]("dev", "", 60, ""))
    with pytest.raises(TimeoutError, match="not taken by any agent"):
        nb["wait"](r, "nobody", 1, 30, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert (root(setup) / "cancel" / "nobody.json").exists()
    agent = Agent(setup)
    run(agent)  # an agent that starts later sees the cancel and doesn't run it
    assert status(setup, "nobody")["state"] == "cancelled"


# ---- failures and crashes

def test_a_failed_run_reports_failed_with_the_error(setup):
    for f in (setup.project / ".local" / "dev" / "LH_Bronze" / "Files").rglob("*.csv"):
        f.unlink()
    agent = Agent(setup)
    write_request(setup, "nofiles", {"env": "dev", "entities": ["bronze.orders"]})
    run(agent)
    s = status(setup, "nofiles")
    assert s["state"] == "failed" and "bronze.orders" in s["error"] and s["state"] in FINAL


def test_a_request_claimed_by_an_agent_that_crashed_before_queueing_still_runs(setup):
    agent = Agent(setup)
    write_request(setup, "orphan", {"env": "dev", "entities": ["bronze.orders"]})
    (root(setup) / "status").mkdir(parents=True)
    (root(setup) / "status" / "orphan.json").write_text(json.dumps(
        {"contract": 1, "request_id": "orphan", "agent": "etl-01", "env": "dev", "state": "accepted",
         "entities": ["bronze.orders"], "updated_at": "2026-10-16T21:00:00Z"}), encoding="utf-8")
    run(agent)  # queued on this poll
    run(agent)
    assert status(setup, "orphan")["state"] == "succeeded"
    assert agent.queue.counts()["succeeded"] == 1


def test_a_request_whose_worker_died_mid_run_runs_again(setup):
    t = [time.time()]
    queue = JobQueue(setup.home / "queue.sqlite3", clock=lambda: t[0], backoff_seconds=0)
    agent = Agent(setup, queue=queue, clock=lambda: t[0])
    write_request(setup, "crash", {"env": "dev", "entities": ["bronze.orders"]})
    agent.mailbox.poll()
    queue.claim("etl-01", 300)  # a worker took it and then the machine went down
    agent.mailbox.poll()
    assert status(setup, "crash")["state"] == "running"
    t[0] += 301
    run(agent)
    s = status(setup, "crash")
    assert s["state"] == "succeeded" and s["attempts"] == 2


def test_status_written_by_another_agent_is_not_touched(setup):
    agent = Agent(setup)
    write_request(setup, "theirs", {"env": "dev"})
    (root(setup) / "status").mkdir(parents=True)
    theirs = {"contract": 1, "request_id": "theirs", "agent": "etl-02", "env": "dev", "state": "running"}
    (root(setup) / "status" / "theirs.json").write_text(json.dumps(theirs), encoding="utf-8")
    run(agent)
    assert status(setup, "theirs") == theirs and sum(agent.queue.counts().values()) == 0


def test_finished_requests_are_removed_after_the_retention_period(setup):
    agent = Agent(setup)
    write_request(setup, "old", {"env": "dev", "entities": ["bronze.orders"]})
    run(agent)
    assert status(setup, "old")["state"] == "succeeded"
    run(agent)
    assert status(setup, "old") is not None
    agent.clock = lambda: time.time() + 15 * 86400
    agent.mailbox.poll()
    assert status(setup, "old") is None and not (root(setup) / "requests" / "old.json").exists()


def test_one_unreachable_mailbox_does_not_stop_the_loop(setup, caplog):
    agent = Agent(setup)

    def broken(env):
        raise requests.ConnectionError("OneLake is unreachable")
    agent.mailbox._control_for = broken
    agent.mailbox._controls.clear()
    run(agent)
    assert "couldn't read the pipeline mailbox" in caplog.text


def test_the_mailbox_is_off_unless_enabled(setup, tmp_path):
    path = tmp_path / "off.toml"
    path.write_text(config_text(setup.project, tmp_path / "h3", ""), encoding="utf-8")
    agent = Agent(agent_config.load(path))
    write_request(setup, "ignored", {"env": "dev"})
    run(agent)
    assert agent.mailbox is None and status(setup, "ignored") is None


def test_config_checks_the_pipeline_section(setup):
    raw = {"agent": {"project": str(setup.project), "environments": ["dev"]}, "auth": {"type": "user"},
           "pipeline": {"enabled": "yes", "environments": ["prod"], "poll_seconds": 0}}
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.parse(raw)
    text = str(err.value)
    for expected in ("pipeline.environments: prod isn't in agent.environments", "pipeline.enabled: true or false",
                     "pipeline.poll_seconds"):
        assert expected in text
    cfg = agent_config.parse({"agent": raw["agent"], "auth": raw["auth"]})
    assert not cfg.pipeline.enabled and cfg.pipeline.environments == ["dev"] and not cfg.pipeline.allow_backfill


# ---- the OneLake side

class FakeDfs:
    """The ADLS Gen2 calls the control files make: conditional create and replace, read, list."""

    def __init__(self):
        self.files, self.n = {}, 0

    @staticmethod
    def _resp(status, etag="", content=b"", headers=None):
        r = requests.Response()
        r.status_code, r._content = status, content
        r.headers.update(({"ETag": etag, "Last-Modified": "Thu, 15 Oct 2026 20:00:00 GMT"} if etag else {}) | (headers or {}))
        return r

    def put(self, url, params=None, headers=None, timeout=None):
        cur = self.files.get(url)
        if headers.get("If-None-Match") == "*" and cur:
            return self._resp(409)
        if headers.get("If-Match") and (not cur or cur[1] != headers["If-Match"]):
            return self._resp(412)
        self.n += 1
        self.files[url] = (b"", f'"{self.n}"')
        return self._resp(201, f'"{self.n}"')

    def patch(self, url, params=None, data=None, headers=None, timeout=None):
        body, etag = self.files[url]
        if params["action"] == "append":
            self.files[url] = (data, etag)
            return self._resp(202)
        self.n += 1
        self.files[url] = (body, f'"{self.n}"')
        return self._resp(200, f'"{self.n}"')

    def get(self, url, params=None, headers=None, timeout=None):
        if params and params.get("resource") == "filesystem":
            prefix = f"{url}/{params['directory']}/"
            names = [u[len(url) + 1:] for u in self.files if u.startswith(prefix)]
            return self._resp(200, content=json.dumps({"paths": [{"name": n} for n in names]}).encode()) \
                if names else self._resp(404)
        cur = self.files.get(url)
        return self._resp(200, cur[1], cur[0]) if cur else self._resp(404)


def test_onelake_control_files_claim_once_and_list_requests():
    dfs = FakeDfs()
    a = OneLakeControl("ws", "meta-lh", lambda: "tok", session=dfs)
    assert a.list("requests") == []  # no folder yet
    base = "https://onelake.dfs.fabric.microsoft.com/ws/meta-lh/Files/_fabric_desktop"
    dfs.files[f"{base}/requests/r1.json"] = (b'{"env": "prod"}', '"0"')
    dfs.files[f"{base}/requests/.r2.tmp"] = (b"", '"0"')
    assert a.list("requests") == ["r1.json"]
    assert a.create("status/r1.json", b'{"state": "accepted"}')
    assert a.create("status/r1.json", b'{"state": "accepted"}') is None  # a second agent loses the claim
    assert json.loads(a.read("status/r1.json").data)["state"] == "accepted"


def test_local_control_files_keep_their_folders(tmp_path):
    c = LocalControl(tmp_path)
    assert c.create("status/a.json", b"{}") and (tmp_path / "status" / "a.json").exists()
    assert c.list("status") == ["a.json"] and c.list("nothing") == []


# ---- which pipeline run asked (PB-049): OneLake can't say who wrote a file, so the run is checked

ITEM, RUN = "0a1b2c3d-0000-4000-8000-000000000001", "0a1b2c3d-0000-4000-8000-0000000000aa"


def fabric_time(seconds_ago: float) -> str:
    """How Fabric writes a job's start: UTC, seven decimals, no zone."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - seconds_ago)) + ".7812154"


one_pass = run  # the helper above; the tests below have a parameter called run


def checked(setup, job=None, error=None, require=False, run=True):
    agent = Agent(setup)
    asked = []

    def verify(env, item_id, run_id):
        asked.append((env, item_id, run_id))
        if error:
            raise error
        return job
    agent.mailbox.verify_run, agent.mailbox.require_run = verify, require
    body = {"env": "dev", "entities": ["bronze.orders"], "submitted_by": "pl_nightly"}
    if run:
        body["pipeline_run"] = {"item_id": ITEM.upper(), "run_id": RUN}
    write_request(setup, "r1", body)
    one_pass(agent)
    return status(setup, "r1"), asked


def test_a_pipeline_run_that_checks_out_is_recorded_as_verified(setup):
    done, asked = checked(setup, {"jobType": "Pipeline", "itemId": ITEM, "status": "InProgress",
                                  "startTimeUtc": fabric_time(120)})
    assert asked == [("dev", ITEM, RUN)], "ids are compared and sent in lower case"
    assert done["state"] == "succeeded" and done["submitted_by"] == "pl_nightly"
    assert done["pipeline_run"]["verified"] is True and done["pipeline_run"]["status"] == "InProgress"


@pytest.mark.parametrize("job, says", [
    ({"jobType": "RunNotebook", "itemId": ITEM, "startTimeUtc": fabric_time(60)}, "not a pipeline run"),
    ({"jobType": "Pipeline", "itemId": "0a1b2c3d-0000-4000-8000-0000000000ff", "startTimeUtc": fabric_time(60)},
     "different item"),
    ({"jobType": "Pipeline", "itemId": ITEM, "startTimeUtc": fabric_time(3 * 86400)}, "started 72 hours ago"),
    ({"jobType": "Pipeline", "itemId": ITEM}, "no start time"),
])
def test_a_run_that_doesnt_check_out_is_recorded_and_still_runs_unless_required(setup, job, says):
    done, _ = checked(setup, job)
    assert done["state"] == "succeeded" and done["pipeline_run"]["verified"] is False
    assert says in done["pipeline_run"]["detail"]


def test_with_require_run_an_unverified_run_is_rejected_and_nothing_is_queued(setup):
    from fabric_desktop.fabric_api import FabricError
    done, _ = checked(setup, error=FabricError("GET failed with 404: ItemNotFound", "ItemNotFound", 404), require=True)
    assert done["state"] == "rejected" and "didn't check out" in done["reason"] and "require_run" in done["reason"]
    assert done["pipeline_run"]["verified"] is False and "couldn't find it in the dev workspace" in done["pipeline_run"]["detail"]
    assert "job_id" not in done


def test_with_require_run_a_request_naming_no_run_is_rejected_without_asking_fabric(setup):
    done, asked = checked(setup, run=False, require=True)
    assert done["state"] == "rejected" and "only takes requests that name the pipeline run" in done["reason"]
    assert asked == []


@pytest.mark.parametrize("run", [{"item_id": ITEM}, {"item_id": "pl_nightly", "run_id": RUN}, "8c1f"])
def test_a_malformed_pipeline_run_is_rejected(setup, run):
    write_request(setup, "r1", {"env": "dev", "pipeline_run": run})
    one_pass(Agent(setup))
    assert status(setup, "r1")["state"] == "rejected" and "both GUIDs" in status(setup, "r1")["reason"]


def test_a_local_environment_records_that_the_run_couldnt_be_looked_up(setup):
    write_request(setup, "r1", {"env": "dev", "entities": ["bronze.orders"], "pipeline_run": {"item_id": ITEM, "run_id": RUN}})
    run(Agent(setup))
    done = status(setup, "r1")
    assert done["state"] == "succeeded" and done["pipeline_run"]["verified"] is False
    assert "runs on this machine" in done["pipeline_run"]["detail"]


def test_the_notebook_names_its_pipeline_run_when_given_one(nb):
    assert "pipeline_run" not in nb["make_request"]("dev", "", 60, "x")
    assert nb["make_request"]("dev", "", 60, "x", ITEM, RUN)["pipeline_run"] == {"item_id": ITEM, "run_id": RUN}


def test_fabric_times_with_seven_decimals_and_no_zone_are_utc():
    from fabric_desktop.mailbox import _fabric_utc
    assert _fabric_utc("2026-10-28T06:35:00.7812154") == pytest.approx(_fabric_utc("2026-10-28T06:35:00.781215Z"))
    assert _fabric_utc("2026-10-28T08:35:00+02:00") == _fabric_utc("2026-10-28T06:35:00Z")
    with pytest.raises(ValueError):
        _fabric_utc("yesterday")


def test_with_require_run_a_lookup_fabric_couldnt_answer_waits_for_the_next_poll(setup):
    from fabric_desktop.fabric_api import FabricError
    done, asked = checked(setup, error=FabricError("GET failed with 503", None, 503), require=True)
    assert done is None and len(asked) == 1, "no status yet: the request is looked at again next poll"


def test_with_require_run_a_finished_run_is_rejected(setup):
    done, _ = checked(setup, {"jobType": "Pipeline", "itemId": ITEM, "status": "Completed",
                              "startTimeUtc": fabric_time(120)}, require=True)
    assert done["state"] == "rejected" and "already finished (Completed)" in done["reason"]


def test_a_run_that_starts_in_the_future_doesnt_check_out(setup):
    done, _ = checked(setup, {"jobType": "Pipeline", "itemId": ITEM, "status": "InProgress",
                              "startTimeUtc": fabric_time(-3600)})
    assert done["pipeline_run"]["verified"] is False and "in the future" in done["pipeline_run"]["detail"]


# ---- #969: under require_run, one pipeline run vouches for one request

GOING = {"jobType": "Pipeline", "itemId": ITEM, "status": "InProgress", "startTimeUtc": fabric_time(120)}


def required(setup, agent=None):
    agent = agent or Agent(setup)
    agent.mailbox.verify_run, agent.mailbox.require_run = (lambda env, item_id, run_id: GOING), True
    return agent


def ask(setup, agent, rid):
    write_request(setup, rid, {"env": "dev", "entities": ["bronze.orders"], "pipeline_run": {"item_id": ITEM, "run_id": RUN}})
    one_pass(agent)
    return status(setup, rid)


def test_with_require_run_a_second_request_naming_the_same_run_is_rejected(setup):
    agent = required(setup)
    assert ask(setup, agent, "r1")["state"] == "succeeded"
    second = ask(setup, agent, "r2")
    assert second["state"] == "rejected" and second["reason"] == f"pipeline run {RUN} already asked for request r1"
    assert "job_id" not in second


def test_the_same_request_seen_twice_stays_one_job(setup):
    agent = required(setup)
    assert ask(setup, agent, "r1")["state"] == "succeeded"
    one_pass(agent)
    assert status(setup, "r1")["attempts"] == 1
    assert agent.mailbox._run_used("dev", RUN, "r1", time.time()) is None, "a retry of r1 is still r1"


def test_a_restarted_agent_keeps_the_runs_it_accepted(setup):
    assert ask(setup, required(setup), "r1")["state"] == "succeeded"
    again = required(setup)  # a new Agent reads home/pipeline-runs.json
    assert ask(setup, again, "r2")["state"] == "rejected"


def test_an_accepted_run_is_forgotten_after_run_max_age(setup):
    from fabric_desktop import jsonfile, mailbox
    old = time.time() - mailbox.RUN_MAX_AGE - 60
    jsonfile.write_json(setup.home / "pipeline-runs.json", {"dev": {RUN: {"request_id": "r0", "at": old}}})
    agent = required(setup)
    assert ask(setup, agent, "r1")["state"] == "succeeded"
    runs = jsonfile.read_json(setup.home / "pipeline-runs.json", {})
    assert runs["dev"][RUN]["request_id"] == "r1" and len(runs["dev"]) == 1


def test_without_require_run_one_run_can_still_name_many_requests(setup):
    agent = Agent(setup)
    agent.mailbox.verify_run = lambda env, item_id, run_id: GOING
    assert ask(setup, agent, "r1")["state"] == "succeeded" and ask(setup, agent, "r2")["state"] == "succeeded"
    assert not (setup.home / "pipeline-runs.json").exists()


def test_a_damaged_runs_file_is_read_as_empty(setup):
    (setup.home).mkdir(parents=True, exist_ok=True)
    (setup.home / "pipeline-runs.json").write_text('{"dev": ["not", "a", "map"]}', encoding="utf-8")
    assert ask(setup, required(setup), "r1")["state"] == "succeeded"


def test_an_entry_with_no_usable_time_is_dropped(setup):
    from fabric_desktop import jsonfile
    jsonfile.write_json(setup.home / "pipeline-runs.json", {"dev": {RUN: {"request_id": "r0", "at": "yesterday"}}})
    assert ask(setup, required(setup), "r1")["state"] == "succeeded"


def test_a_refused_mailbox_listing_is_one_line_and_still_retryable():
    from fabric_desktop.retrying import is_retryable

    class Refusing:
        def __init__(self, status, body, headers=None):
            self.status, self.body, self.headers, self.sent = status, body, headers or {}, 0

        def get(self, url, params=None, headers=None, timeout=None):
            self.sent += 1
            body = self.body
            return type("Resp", (), {"status_code": self.status, "headers": {"Retry-After": "0", **self.headers},
                                     "json": lambda _self: body, "close": lambda _self: None})()

    dfs = Refusing(403, {"error": {"code": "AuthorizationPermissionMismatch", "message": "x"}})
    control = OneLakeControl("ws", "meta-lh", lambda: "tok", session=dfs, sleep=lambda s: None)
    with pytest.raises(requests.HTTPError) as err:
        control.list("requests")
    assert str(err.value) == "OneLake didn't list the requests mailbox folder (403: AuthorizationPermissionMismatch)."
    assert "https://" not in str(err.value) and not is_retryable(err.value)

    dfs = Refusing(429, {})
    control = OneLakeControl("ws", "meta-lh", lambda: "tok", session=dfs, sleep=lambda s: None)
    with pytest.raises(requests.HTTPError) as err:
        control.list("requests")
    assert dfs.sent > 1 and is_retryable(err.value)  # waited out first, then the agent tries next poll
