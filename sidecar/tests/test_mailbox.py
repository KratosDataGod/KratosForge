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
