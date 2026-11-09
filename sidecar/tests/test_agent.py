"""The headless agent: config, unattended sign-in, the durable queue, table leases, and real runs."""

import json
import re
import sqlite3
import time
import threading
from pathlib import Path

import pytest
import requests

from fabric_desktop import agent_config, cli
from fabric_desktop.agent import Agent
from fabric_desktop.credentials import ManagedIdentity, ServicePrincipal, default_scope, resource_of
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.leases import LeaseBusy, LeaseLost, Leases, LocalBackend, OneLakeBackend
from fabric_desktop.meta import LocalStore
from fabric_desktop.meta.templates import init_project
from fabric_desktop.retrying import is_retryable
from fabric_desktop.runtime import Runtime


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def config_text(project: Path, home: Path, extra: str = "") -> str:
    return f"""
[agent]
name = "etl-01"
home = {json.dumps(str(home))}
project = {json.dumps(str(project))}
environments = ["dev"]
poll_seconds = 0.1

[auth]
type = "user"

[retry]
max_attempts = 3
backoff_seconds = 10
{extra}
"""


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(config_text(project, tmp_path / "home"), encoding="utf-8")
    return agent_config.load(path)


def finish(agent: Agent) -> None:
    for f in list(agent._running.values()):
        f.result(timeout=120)


# ---- agent.toml

def test_config_reads_schedules_and_defaults(tmp_path, setup):
    cfg = setup
    assert cfg.name == "etl-01" and cfg.concurrency == 1 and cfg.max_attempts == 3
    path = tmp_path / "with-schedules.toml"
    path.write_text(config_text(cfg.project, tmp_path / "h", """
[[schedule]]
id = "dev-hourly"
env = "dev"
every_minutes = 60

[[schedule]]
id = "dev-morning"
env = "dev"
entities = ["gold.fact_sales"]
weekly = { days = ["Monday", "Friday"], times = ["06:00"] }
"""), encoding="utf-8")
    cfg = agent_config.load(path)
    assert [s.id for s in cfg.schedules] == ["dev-hourly", "dev-morning"]
    assert cfg.schedules[0].config["type"] == "Cron" and cfg.schedules[1].entities == ["gold.fact_sales"]


def test_config_refuses_secrets_and_lists_every_problem(tmp_path, setup):
    raw = {"agent": {"project": str(tmp_path / "nowhere"), "environments": ["prod", "qa"]},
           "auth": {"type": "service_principal", "client_secret": "s3cret"},
           "alerts": {"teams_webhook": "https://example.invalid/hook"},
           "schedule": [{"id": "x", "env": "prod", "daily": ["25:00"]}, {"id": "y", "env": "dev", "every_minutes": 5}]}
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.parse(raw)
    text = str(err.value)
    for expected in ("auth.client_secret: secrets don't go", "alerts.teams_webhook: secrets don't go",
                     "isn't a metadata project", "'qa' isn't one of", "tenant_id and client_id",
                     "schedule x: Times are HH:MM", "schedule y: dev isn't in agent.environments"):
        assert expected in text
    assert "s3cret" not in text


def test_agent_name_must_be_one_the_mailbox_can_take_over(tmp_path, setup, monkeypatch):
    raw = {"agent": {"project": str(setup.project), "environments": ["dev"], "name": "etl 01"}, "auth": {"type": "user"}}
    with pytest.raises(agent_config.ConfigError, match="agent.name: 'etl 01' isn't 1 to 64 letters"):
        agent_config.parse(raw)
    del raw["agent"]["name"]
    monkeypatch.setattr(agent_config.socket, "gethostname", lambda: "Büro PC#2")
    assert agent_config.parse(raw).name == "B-ro-PC-2"  # the default always passes
    monkeypatch.setattr(agent_config.socket, "gethostname", lambda: "x" * 70)
    assert agent_config.parse(raw).name == "x" * 64
    monkeypatch.setattr(agent_config.socket, "gethostname", lambda: "")
    assert agent_config.parse(raw).name == "agent"


def test_webhook_comes_from_an_environment_variable(tmp_path, setup, monkeypatch):
    path = tmp_path / "a.toml"
    path.write_text(config_text(setup.project, tmp_path / "h", '[alerts]\nteams_webhook_env = "FD_HOOK"'), encoding="utf-8")
    monkeypatch.setenv("FD_HOOK", "https://example.invalid/hook")
    assert agent_config.load(path).teams_webhook == "https://example.invalid/hook"


# ---- sign-in without a person

def test_scopes_map_to_their_resource():
    assert resource_of("https://api.fabric.microsoft.com/Item.ReadWrite.All") == "https://api.fabric.microsoft.com"
    assert resource_of("https://storage.azure.com/user_impersonation") == "https://storage.azure.com"
    assert resource_of("https://analysis.windows.net/powerbi/api/Tenant.Read.All") == "https://analysis.windows.net/powerbi/api"
    assert default_scope(["https://api.fabric.microsoft.com/Workspace.ReadWrite.All",
                          "https://api.fabric.microsoft.com/Item.Execute.All"]) == "https://api.fabric.microsoft.com/.default"
    with pytest.raises(ValueError):
        default_scope(["https://api.fabric.microsoft.com/a", "https://storage.azure.com/b"])


class FakeApp:
    def __init__(self, result=None):
        self.asked, self.result = [], result

    def acquire_token_for_client(self, scopes=None, resource=None):
        self.asked.append(scopes or resource)
        return self.result or {"access_token": f"token-for-{scopes or resource}"}


def test_service_principal_asks_for_each_resource_default():
    app = FakeApp()
    sp = ServicePrincipal("tenant", "app-id", app=app)
    assert sp.fabric_token() == "token-for-['https://api.fabric.microsoft.com/.default']"
    sp.storage_token()
    assert app.asked[1] == ["https://storage.azure.com/.default"]
    assert sp.signed_in_tenant() == "tenant" and "app-id" in sp.signed_in_user()
    bad = ServicePrincipal("tenant", "app-id", app=FakeApp({"error": "invalid_client", "error_description": "no cert"}))
    with pytest.raises(RuntimeError, match="invalid_client"):
        bad.fabric_token()
    assert bad.silent_token(["https://api.fabric.microsoft.com/x"]) is None
    with pytest.raises(ValueError, match="certificate_path or client_secret_env"):
        ServicePrincipal("tenant", "app-id")


def test_service_principal_loads_a_pem_certificate(tmp_path):
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from fabric_desktop.credentials import _certificate
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    pem = tmp_path / "agent.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()) + cert.public_bytes(serialization.Encoding.PEM))
    cred = _certificate(pem, None)
    assert cred["thumbprint"] == cert.fingerprint(hashes.SHA1()).hex() and "PRIVATE KEY" in cred["private_key"]


def test_managed_identity_asks_for_the_resource():
    client = FakeApp()
    mi = ManagedIdentity(client=client)
    mi.storage_token()
    assert client.asked == ["https://storage.azure.com"]


# ---- the queue

def test_queue_retries_with_backoff_then_gives_up(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.sqlite3", clock=clock, backoff_seconds=10, max_backoff_seconds=15)
    job = q.enqueue("meta", {"env": "dev"}, max_attempts=3)
    assert q.claim("w1", 60)["attempts"] == 1
    assert q.retry_later(job["id"], "w1", "timeout")["next_at"] == clock.t + 10
    assert q.claim("w1", 60) is None  # not due yet
    clock.t += 10
    assert q.claim("w1", 60)["attempts"] == 2
    assert q.retry_later(job["id"], "w1", "timeout")["next_at"] == clock.t + 15  # 20, capped at 15
    clock.t += 15
    q.claim("w1", 60)
    dead = q.retry_later(job["id"], "w1", "timeout")
    assert dead["state"] == "dead" and "Gave up after 3 attempts" in dead["error"]
    assert q.counts()["dead"] == 1


def test_a_claim_waiting_for_the_queue_sees_a_job_added_while_it_waited(tmp_path):
    # Another thread or process adds a job while this claim waits for the write lock. The claim
    # reads the time once it has the lock, so the job is due to it (a time read before waiting
    # made the job look not yet due: the claim came back empty).
    clock = Clock()
    q = JobQueue(tmp_path / "q.sqlite3", clock=clock, backoff_seconds=0)
    other = sqlite3.connect(tmp_path / "q.sqlite3", isolation_level=None)
    try:
        other.execute("begin immediate")  # holds the write lock, as a writer elsewhere does
        got = []
        waiter = threading.Thread(target=lambda: got.append(q.claim("w1", 60)))
        waiter.start()
        waiter.join(0.5)
        assert waiter.is_alive()  # waiting for the lock
        clock.t += 5
        other.execute("insert into jobs (id, kind, payload, state, source, max_attempts, next_at, created_at)"
                      " values ('job-late', 'meta', '{}', 'queued', '', 3, ?, ?)", (clock.t, clock.t))
        other.execute("commit")
        waiter.join(10)
    finally:
        other.close()
    assert got and got[0] is not None and got[0]["id"] == "job-late"


def test_queue_puts_back_a_job_whose_worker_died(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.sqlite3", clock=clock, backoff_seconds=0)
    job = q.enqueue("meta", {"env": "dev"})
    q.claim("crashed-worker", 60)
    clock.t += 30
    assert q.heartbeat(job["id"], "crashed-worker", 60)  # still alive: renewed to t+90
    clock.t += 59  # t+89: the renewed lease still holds
    assert q.recover() == []
    clock.t += 2  # t+91: it ran out
    assert q.recover() == [job["id"]]
    again = q.claim("w2", 60)
    assert again["id"] == job["id"] and again["attempts"] == 2 and again["owner"] == "w2"
    # The old worker comes back: it can't finish or renew a job it no longer holds.
    assert not q.heartbeat(job["id"], "crashed-worker", 60)
    assert not q.succeed(job["id"], "crashed-worker")
    assert q.succeed(job["id"], "w2", {"ok": 1}) and q.get(job["id"])["result"] == {"ok": 1}


def test_an_unreadable_queue_file_is_set_aside_and_the_agent_starts_empty(setup, caplog):
    path = setup.home / "queue.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"garbage" * 1000)
    (setup.home / "queue.sqlite3-wal").write_bytes(b"old wal")
    agent = Agent(setup)
    assert agent.queue.list() == [] and agent.queue.set_aside.name.startswith("queue.sqlite3.corrupt-")
    assert agent.queue.set_aside.read_bytes() == b"garbage" * 1000  # kept for a person to look at
    assert not (setup.home / "queue.sqlite3-wal").exists() or (setup.home / "queue.sqlite3-wal").read_bytes() != b"old wal"
    assert "started with an empty queue" in caplog.text
    job = agent.enqueue("dev")
    assert agent.queue.get(job["id"])["state"] == "queued"
    assert Agent(setup).queue.set_aside is None  # the new file is fine


def test_a_queue_file_damaged_inside_is_set_aside_too(tmp_path):
    path = tmp_path / "q.sqlite3"
    q = JobQueue(path)
    for i in range(200):
        q.enqueue("meta", {"env": "dev", "pad": "x" * 500, "i": i})
    data = bytearray(path.read_bytes())
    data[8192:12288] = b"\xff" * 4096  # a page in the middle, past the header
    path.write_bytes(bytes(data))
    assert JobQueue(path).set_aside is not None


def test_a_busy_queue_file_is_not_set_aside(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "q.sqlite3"
    JobQueue(path)
    monkeypatch.setattr(JobQueue, "_open", lambda self: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    with pytest.raises(sqlite3.OperationalError):
        JobQueue(path)
    assert path.exists() and not list(tmp_path.glob("*.corrupt-*"))


def test_a_run_whose_job_was_claimed_again_cant_renew_or_finish_the_new_attempt(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.sqlite3", clock=clock, backoff_seconds=0)
    job = q.enqueue("meta", {"env": "dev"})
    first = q.claim("etl-01", 60)
    clock.t += 61
    assert q.recover() == [job["id"]]
    second = q.claim("etl-01", 60)  # the same agent claims it again while its first run still works
    assert second["claim_id"] and second["claim_id"] != first["claim_id"]
    old = first["claim_id"]
    assert not q.heartbeat(job["id"], "etl-01", 60, claim=old)
    assert not q.succeed(job["id"], "etl-01", {"ok": 1}, claim=old)
    assert not q.fail(job["id"], "etl-01", "boom", claim=old)
    assert q.retry_later(job["id"], "etl-01", "timeout", claim=old) is None
    assert not q.cancel(job["id"], "stop", owner="etl-01", claim=old)
    clock.t += 61
    assert q.recover() == [job["id"]] and q.get(job["id"])["state"] == "queued"
    assert not q.cancel(job["id"], "stop", owner="etl-01", claim=old)  # nor the queued retry
    second = q.claim("etl-01", 60)
    assert q.get(job["id"])["state"] == "running"
    assert q.heartbeat(job["id"], "etl-01", 60, claim=second["claim_id"])
    assert q.succeed(job["id"], "etl-01", {"ok": 2}, claim=second["claim_id"])
    assert q.get(job["id"])["result"] == {"ok": 2}


def test_a_queue_file_from_before_claim_ids_gets_the_column(tmp_path):
    import sqlite3
    path = tmp_path / "q.sqlite3"
    JobQueue(path).enqueue("meta", {"env": "dev"})
    with sqlite3.connect(path) as db:
        db.execute("alter table jobs drop column claim_id")
    q = JobQueue(path)
    assert q.claim("w1", 60)["claim_id"]


def test_one_failed_heartbeat_doesnt_let_the_lease_lapse(setup):
    import dataclasses
    import sqlite3
    import threading
    agent = Agent(dataclasses.replace(setup, job_lease_seconds=0.3))
    agent.queue.enqueue("meta", {"env": "dev"})
    job = agent.queue.claim(setup.name, 0.3)
    real, calls, renewed = agent.queue.heartbeat, [], []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        ok = real(*a, **k)
        renewed.append(ok)
        return ok
    agent.queue.heartbeat = flaky
    done, lost = threading.Event(), threading.Event()
    beat = threading.Thread(target=agent._heartbeat, args=(job, done, lost))
    beat.start()
    try:
        deadline = time.monotonic() + 10
        while len(renewed) < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
        # The beats after the failed one renewed the lease. (Asserting recover() == [] against the
        # wall clock failed on a slow runner whenever one beat came late: a 0.3 s lease has no slack.)
        assert renewed[:3] == [True, True, True] and beat.is_alive() and not lost.is_set()
        assert agent.queue.get(job["id"])["lease_until"] > job["lease_until"]
    finally:
        done.set()
        beat.join(5)


def test_a_run_that_lost_its_lease_stops_and_leaves_the_job_to_the_next_attempt(setup, monkeypatch):
    import dataclasses
    agent = Agent(dataclasses.replace(setup, job_lease_seconds=0.15))
    job = agent.enqueue("dev")
    claimed = agent.queue.claim(setup.name, 0.15)
    tables = []

    def run_environment(env, entities, backfill, cascade, job=None, guard=None):
        # meanwhile its lease ran out and the job was claimed again: the next renewal finds that
        with agent.queue._tx() as db:
            db.execute("update jobs set claim_id = 'a-newer-claim' where id = ?", (claimed["id"],))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            guard()  # before each table
            tables.append(1)
            time.sleep(0.02)
        raise AssertionError("the run never stopped")
    monkeypatch.setattr(agent.runtime, "run_environment", run_environment)
    agent.run_job(claimed)
    after = agent.queue.get(job["id"])
    assert after["state"] == "running" and after["claim_id"] == "a-newer-claim"  # the newer attempt's, untouched
    [line] = [e for e in agent.audit.recent(10) if e["action"] == "agent_job"]
    assert line["outcome"] == "lease_lost", line


def test_a_run_whose_lease_ran_out_at_the_end_is_not_recorded_as_ok(setup, monkeypatch):
    agent = Agent(setup)
    job = agent.enqueue("dev")
    claimed = agent.queue.claim(setup.name, setup.job_lease_seconds)

    def run_environment(env, entities, backfill, cascade, job=None, guard=None):
        with agent.queue._tx() as db:  # claimed again just as it finished
            db.execute("update jobs set claim_id = 'a-newer-claim' where id = ?", (claimed["id"],))
        return [{"entity": "silver.orders", "status": "ok", "rows_written": 1}]
    monkeypatch.setattr(agent.runtime, "run_environment", run_environment)
    agent.run_job(claimed)
    assert agent.queue.get(job["id"])["state"] == "running"
    [line] = [e for e in agent.audit.recent(10) if e["action"] == "agent_job"]
    assert line["outcome"] == "lease_lost", line


def test_queue_survives_a_new_process_and_dedupes(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    first = q.enqueue("meta", {"env": "dev"}, dedupe_key="schedule:a")
    q.close()
    q2 = JobQueue(tmp_path / "q.sqlite3")  # as after a restart
    again = q2.enqueue("meta", {"env": "dev"}, dedupe_key="schedule:a")
    assert again["id"] == first["id"] and again["deduplicated"]
    assert q2.counts()["queued"] == 1



def _finished(q, clock, state, days_ago, **kw):
    """A job that reached ``state`` ``days_ago`` days before the clock's time now."""
    now = clock.t
    clock.t = now - days_ago * 86400
    job = q.enqueue("meta", {"env": "dev"}, **kw)
    if state != "queued":
        q.claim("w1", 60)
        if state == "succeeded":
            q.succeed(job["id"], "w1")
        elif state == "failed":
            q.fail(job["id"], "w1", "a check failed")
        elif state == "dead":
            q.retry_later(job["id"], "w1", "timeout")
    clock.t = now
    return q.get(job["id"])


def test_prune_removes_old_finished_jobs_and_keeps_the_rest(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.sqlite3", clock=clock, backoff_seconds=0)
    old_ok, old_failed = _finished(q, clock, "succeeded", 40), _finished(q, clock, "failed", 40)
    old_cancelled = q.enqueue("meta", {"env": "dev"})
    clock.t -= 40 * 86400
    q.cancel(old_cancelled["id"], "not wanted")
    clock.t += 40 * 86400
    recent = _finished(q, clock, "succeeded", 5)
    dead = _finished(q, clock, "dead", 40, max_attempts=1)  # needs a person: kept
    running = q.enqueue("meta", {"env": "dev"})
    q.claim("w2", 60)
    queued = _finished(q, clock, "queued", 40)  # after the claim, which takes the oldest due job
    assert q.get(running["id"])["state"] == "running" and dead["state"] == "dead"
    old = {old_ok["id"], old_failed["id"], old_cancelled["id"]}
    assert set(q.prune(30, dry_run=True)) == old
    assert q.get(old_ok["id"]) is not None  # a dry run deletes nothing
    assert set(q.prune(30)) == old
    assert all(q.get(i) is None for i in old)
    assert {j["id"] for j in q.list()} == {recent["id"], dead["id"], queued["id"], running["id"]}
    assert q.prune(30) == []


def test_requeue_puts_a_dead_job_back_with_its_attempts_reset(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.sqlite3", clock=clock, backoff_seconds=0)
    dead = _finished(q, clock, "dead", 0, max_attempts=1)
    again = q.requeue(dead["id"], "chris (cli)")
    assert again["state"] == "queued" and again["attempts"] == 0 and again["requeued_by"] == "chris (cli)"
    assert again["error"] is None and again["finished_at"] is None
    assert q.claim("w1", 60)["id"] == dead["id"]
    failed = _finished(q, clock, "failed", 0)
    assert q.requeue(failed["id"], "chris")["state"] == "queued"


def test_requeue_refuses_jobs_that_are_not_dead_or_failed(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    queued = q.enqueue("meta", {"env": "dev"})
    with pytest.raises(ValueError, match="queued"):
        q.requeue(queued["id"], "chris")
    q.claim("w1", 60)
    with pytest.raises(ValueError, match="running"):
        q.requeue(queued["id"], "chris")
    q.succeed(queued["id"], "w1")
    with pytest.raises(ValueError, match="succeeded"):
        q.requeue(queued["id"], "chris")
    with pytest.raises(LookupError):
        q.requeue("job-missing", "chris")


def test_requeue_refuses_a_pipeline_job_and_one_with_a_newer_run(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.sqlite3", clock=clock, backoff_seconds=0)
    piped = _finished(q, clock, "failed", 0, dedupe_key="pipeline:r1")
    with pytest.raises(ValueError, match="pipeline"):
        q.requeue(piped["id"], "chris")
    nightly = _finished(q, clock, "failed", 0, dedupe_key="schedule:nightly")
    q.enqueue("meta", {"env": "dev"}, dedupe_key="schedule:nightly")  # the schedule already queued the next run
    with pytest.raises(ValueError, match="newer run"):
        q.requeue(nightly["id"], "chris")


def test_an_older_queue_file_gets_the_requeue_column(tmp_path):
    import sqlite3
    path = tmp_path / "q.sqlite3"
    JobQueue(path).close()
    db = sqlite3.connect(path)
    db.execute("alter table jobs drop column requeued_by")  # as written before requeue
    db.close()
    q = JobQueue(path)
    job = _finished(q, Clock(), "failed", 0)
    assert q.requeue(job["id"], "chris")["requeued_by"] == "chris"

# ---- table leases

def test_leases_block_a_second_writer_until_released_or_expired(tmp_path):
    clock = Clock()
    backend = LocalBackend(tmp_path / "leases")
    a = Leases(backend, "agent-a", ttl=60, wait=0, clock=clock)
    b = Leases(backend, "agent-b", ttl=60, wait=0, clock=clock)
    assert a.try_acquire("LH_Silver/dbo.orders")
    assert b.try_acquire("LH_Silver/dbo.orders") is None and b.holder("LH_Silver/dbo.orders") == "agent-a"
    assert b.try_acquire("LH_Silver/dbo.customers")  # other tables are free
    clock.t += 61  # agent-a died without releasing
    assert b.try_acquire("LH_Silver/dbo.orders")
    with pytest.raises(LeaseBusy, match="agent-b"):
        with a.hold("LH_Silver/dbo.orders"):
            pass


def test_a_lease_change_windows_keeps_refusing_leaves_the_lease_and_no_temporary_file(tmp_path, monkeypatch):
    from fabric_desktop import leases
    backend = LocalBackend(tmp_path / "leases")
    etag = backend.create("LH_Silver/dbo.orders", b"agent-a")

    def refused(src, dst):
        raise PermissionError("[WinError 32] The process cannot access the file")
    monkeypatch.setattr(leases.os, "replace", refused)
    monkeypatch.setattr(leases.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        backend.replace("LH_Silver/dbo.orders", b"agent-a renewed", etag)
    monkeypatch.undo()
    assert not [p.name for p in (tmp_path / "leases").rglob("*.tmp")]
    assert backend.read("LH_Silver/dbo.orders").data == b"agent-a"


def test_hold_waits_for_the_lease_and_releases_it(tmp_path):
    backend = LocalBackend(tmp_path / "leases")
    order, waiting = [], threading.Event()

    def sleep(_):
        order.append("waited")
        waiting.set()
        threading.Event().wait(0.05)

    first = Leases(backend, "a", ttl=60, wait=30)
    second = Leases(backend, "b", ttl=60, wait=30, sleep=sleep)
    with first.hold("t"):
        done = []

        def run():
            with second.hold("t"):
                done.append("b wrote")
        t = threading.Thread(target=run)
        t.start()
        # Release only once b has found the lease taken: a fixed pause let a slow Windows runner
        # start b after the release, so b never had to wait.
        assert waiting.wait(10), "b never waited for the lease"
        assert done == []
        order.append("a wrote")
    t.join(5)
    assert done == ["b wrote"] and order[0] == "waited" and "a wrote" in order
    assert backend.read("t") is None  # released


def test_a_lease_taken_over_mid_write_is_reported(tmp_path):
    backend = LocalBackend(tmp_path / "leases")
    leases = Leases(backend, "a", ttl=0.3, wait=0)
    with pytest.raises(LeaseLost):
        with leases.hold("t"):
            stored = backend.read("t")
            backend.replace("t", b'{"owner": "b", "expires": 9e12}', stored.etag)
            threading.Event().wait(0.3)


class FakeDfs:
    """Just enough of the OneLake DFS API for conditional lease files."""

    def __init__(self):
        self.files, self.calls, self.n = {}, [], 0

    def _resp(self, status, etag="", content=b""):
        r = requests.Response()
        r.status_code, r._content = status, content
        r.headers.update({"ETag": etag, "Last-Modified": "Thu, 15 Oct 2026 20:00:00 GMT"} if etag else {})
        return r

    def put(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("PUT", headers.get("If-None-Match") or headers.get("If-Match")))
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
        if headers.get("If-Match") and headers["If-Match"] != etag:
            return self._resp(412)
        self.n += 1
        self.files[url] = (body, f'"{self.n}"')
        return self._resp(200, f'"{self.n}"')

    def get(self, url, headers=None, timeout=None):
        cur = self.files.get(url)
        return self._resp(200, cur[1], cur[0]) if cur else self._resp(404)

    def delete(self, url, headers=None, timeout=None):
        cur = self.files.get(url)
        if not cur or cur[1] != headers.get("If-Match"):
            return self._resp(412)
        del self.files[url]
        return self._resp(200)


def test_onelake_leases_use_conditional_requests():
    dfs = FakeDfs()
    backend = OneLakeBackend("ws", "meta-lh", lambda: "tok", session=dfs)
    a, b = Leases(backend, "a", wait=0), Leases(backend, "b", wait=0)
    etag = a.try_acquire("LH_Gold/dbo.fact_sales")
    url = "https://onelake.dfs.fabric.microsoft.com/ws/meta-lh/Files/_fabric_desktop/leases/LH_Gold/dbo.fact_sales.json"
    assert etag and json.loads(dfs.files[url][0])["owner"] == "a"
    assert ("PUT", "*") in dfs.calls
    assert b.try_acquire("LH_Gold/dbo.fact_sales") is None
    assert backend.replace("LH_Gold/dbo.fact_sales", b"{}", '"stale"') is None
    assert backend.delete("LH_Gold/dbo.fact_sales", etag) and url not in dfs.files


def test_retryable_errors():
    busy = requests.Response()
    busy.status_code = 429
    assert is_retryable(LeaseBusy("x")) and is_retryable(requests.ConnectionError())
    assert is_retryable(requests.HTTPError(response=busy))
    assert not is_retryable(ValueError("bad config")) and not is_retryable(KeyError("no entity"))


def test_a_fabric_outage_is_retried_not_failed(setup, monkeypatch):
    from fabric_desktop.fabric_api import FabricError
    agent = Agent(setup)

    def outage(*args, **kwargs):
        raise FabricError("GET /workspaces failed with 503: Service unavailable", "ServiceUnavailable", 503)

    monkeypatch.setattr(agent.runtime, "run_environment", outage)
    job = agent.enqueue("dev")
    agent.step()
    finish(agent)
    again = agent.queue.get(job["id"])
    assert again["state"] == "queued" and "503" in again["error"]


# ---- the agent, running the sample project for real

def test_agent_runs_a_queued_environment_with_run_details(setup):
    agent = Agent(setup)
    job = agent.enqueue("dev")
    agent.step()
    finish(agent)
    done = agent.queue.get(job["id"])
    assert done["state"] == "succeeded", done["error"]
    # It once failed here in a full local run and didn't again in 2 more full runs and 6 alone: say
    # what the job recorded, so the next failure shows which count was off and why.
    result = done["result"] or {}
    assert result.get("tables") == 6 and result.get("failed") == 0, f"job result {result!r}, attempts {done['attempts']}"
    log = LocalStore(setup.project / ".local" / "dev", "dev").meta("run_log")
    assert set(log["worker"]) == {"etl-01"} and set(log["job_id"]) == {job["id"]}
    assert set(log["attempt"]) == {1} and all(b.startswith("agent-") for b in log["batch_id"])
    assert not list((setup.project / ".local" / "dev" / "_leases").glob("*.json"))  # every lease released
    [line] = [e for e in agent.audit.recent(10) if e["action"] == "agent_job"]
    assert line["outcome"] == "ok" and line["app"] == "agent" and line["target"]["job_id"] == job["id"]
    assert line["target"]["env"] == "dev" and line["target"]["tables"] == 6 and agent.audit.verify()["ok"]


def test_agent_refuses_environments_it_was_not_given(setup):
    with pytest.raises(PermissionError, match="may run dev, not prod"):
        Agent(setup).enqueue("prod")
    with pytest.raises(PermissionError):
        Runtime(setup).run_environment("prod")


def test_a_busy_table_is_retried_for_just_the_tables_that_did_not_run(setup, monkeypatch):
    import dataclasses
    from fabric_desktop import meta_service
    Leases(LocalBackend(setup.project / ".local" / "dev" / "_leases"), "someone-else", ttl=3600) \
        .try_acquire("LH_Gold/dbo.fact_sales")
    # Wait 0.2 s for a busy table, but keep each lease a minute long: a 0.2 s lease must be renewed
    # every 0.07 s, which a busy runner misses, and the tables that aren't busy then fail with LeaseLost.
    # (The agent's table_lease_seconds is 15 s or more, so only a test makes a lease this short.)
    real = meta_service.Leases
    monkeypatch.setattr(meta_service, "Leases", lambda backend, owner, ttl, wait: real(backend, owner, ttl=60, wait=wait))
    agent = Agent(dataclasses.replace(setup, table_lease_seconds=0.2))
    job = agent.enqueue("dev")
    agent.step()
    finish(agent)
    again = agent.queue.get(job["id"])
    assert again["state"] == "queued" and "is being written by someone-else" in again["error"]
    assert again["payload"]["entities"] == ["gold.fact_sales"]


def test_a_failed_check_fails_the_job_without_retrying(setup, tmp_path):
    for f in (setup.project / ".local" / "dev" / "LH_Bronze" / "Files").rglob("*.csv"):
        f.unlink()  # no landing files: bronze fails, and so does everything after it
    agent = Agent(setup)
    job = agent.enqueue("dev", ["bronze.orders"])
    agent.step()
    finish(agent)
    # This failed once in a full local run with only its summary line kept (flaky ledger), so each
    # check says what it saw.
    done = agent.queue.get(job["id"])
    recent = agent.audit.recent(10)
    assert done["state"] == "failed", (done, recent)
    lines = [e for e in recent if e["action"] == "agent_job"]
    assert len(lines) == 1, recent
    assert lines[0]["outcome"] == "error" and "bronze.orders" in lines[0]["error"], lines[0]


def test_asking_for_the_project_again_leaves_the_one_a_job_holds_whole(setup):
    """A job validates and runs the Project it was given while the agent's loop asks for the project
    again (status, incidents, the mailbox). Asking again must not reload that Project in place: a
    reload empties its entities before refilling them, and a job that validated then saw gold.dim_customer
    reading a silver.customers that wasn't there (the flaky ledger's test_a_failed_check_fails_the_job)."""
    runtime = Runtime(setup)
    held = runtime._project()
    names = sorted(held.entities)
    (setup.project / "metadata" / "entities" / "silver.customers.toml").unlink()
    again = runtime._project()
    assert "silver.customers" not in again.entities  # the next caller sees the folder as it is now
    assert sorted(held.entities) == names  # the job's Project is untouched


def test_a_job_from_a_crashed_agent_runs_again_after_restart(setup):
    clock = Clock()
    q = JobQueue(setup.home / "queue.sqlite3", clock=clock, backoff_seconds=0)
    job = q.enqueue("meta", {"env": "dev", "entities": ["bronze.orders"]})
    q.claim("etl-01-before-crash", 300)
    clock.t += 301
    agent = Agent(setup, queue=q, clock=clock)
    agent.step()  # recover, then claim it again
    finish(agent)
    done = q.get(job["id"])
    assert done["state"] == "succeeded" and done["attempts"] == 2 and done["owner"] == "etl-01"


def test_schedules_fire_once_when_due_and_do_not_pile_up(tmp_path, setup):
    path = tmp_path / "s.toml"
    path.write_text(config_text(setup.project, tmp_path / "h2", '[[schedule]]\nid = "every-15"\nenv = "dev"\nevery_minutes = 15'),
                    encoding="utf-8")
    cfg = agent_config.load(path)
    clock = Clock()
    agent = Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)
    assert agent.tick_schedules() == []  # the first tick only works out the next time
    nxt = agent.queue.schedule_next("every-15")
    assert nxt > clock.t
    clock.t = nxt + 1
    assert agent.tick_schedules() == ["every-15"]
    clock.t = agent.queue.schedule_next("every-15") + 1
    agent.tick_schedules()  # due again while the first is still queued: no second job
    assert agent.queue.counts()["queued"] == 1


def test_every_minutes_keeps_its_times_across_a_restart(tmp_path, setup, monkeypatch):
    import datetime as dt
    import types
    from fabric_desktop import local_schedules
    path = tmp_path / "s.toml"
    path.write_text(config_text(setup.project, tmp_path / "h3", '[[schedule]]\nid = "hourly"\nenv = "dev"\nevery_minutes = 60'),
                    encoding="utf-8")

    def load_at(when):  # agent.toml parsed at ``when``
        fake = type("Now", (dt.datetime,), {"now": classmethod(lambda cls, tz=None: when)})
        monkeypatch.setattr(local_schedules, "dt", types.SimpleNamespace(**{**vars(dt), "datetime": fake}))
        cfg = agent_config.load(path)
        monkeypatch.setattr(local_schedules, "dt", dt)
        return cfg
    clock = Clock(dt.datetime(2026, 10, 23, 10, 37).timestamp())
    queue = JobQueue(setup.home / "grid.sqlite3", clock=clock)
    Agent(load_at(dt.datetime(2026, 10, 23, 10, 37)), queue=queue, clock=clock).tick_schedules()
    first = queue.schedule_next("hourly")
    assert first == dt.datetime(2026, 10, 23, 11, 0).timestamp()
    clock.t = dt.datetime(2026, 10, 23, 10, 50).timestamp()
    agent = Agent(load_at(dt.datetime(2026, 10, 23, 10, 50)), queue=queue, clock=clock)  # restarted
    clock.t = first
    assert agent.tick_schedules() == ["hourly"]
    assert queue.schedule_next("hourly") == first + 3600  # not 10:50 + 60 minutes


def test_cli_check_enqueue_and_status(setup, capsys):
    assert cli._agent(cli_args(setup, "check")) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["entities"] == 6 and checked["environments"]["dev"]["target"] == "local"
    assert cli._agent(cli_args(setup, "enqueue", env="dev", entities=["silver.orders"])) == 0
    queued = json.loads(capsys.readouterr().out)
    assert queued["state"] == "queued" and queued["payload"]["entities"] == ["silver.orders"]
    assert cli._agent(cli_args(setup, "status")) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["queued"] == 1



def test_agent_prunes_at_start_and_then_daily(setup):
    clock = Clock()
    q = JobQueue(setup.home / "queue.sqlite3", clock=clock, backoff_seconds=0)
    old = _finished(q, clock, "succeeded", 31)
    agent = Agent(setup, queue=q, clock=clock)
    agent.step()
    assert q.get(old["id"]) is None
    later = _finished(q, clock, "succeeded", 31)
    clock.t += 3600
    agent.step()  # pruned an hour ago: not yet
    assert q.get(later["id"]) is not None
    clock.t += 86400
    agent.step()
    assert q.get(later["id"]) is None


def test_agent_sweeps_old_local_copies_in_its_home_at_start_and_then_daily(setup):
    import os
    clock = Clock()
    agent = Agent(setup, queue=JobQueue(setup.home / "queue.sqlite3", clock=clock, backoff_seconds=0), clock=clock)
    old = time.time() - 40 * 86400
    copy = setup.home / "onelake-cache" / "test" / "LH_Landing" / "Files" / "x.json"
    spill = setup.home / "staging" / "_writes" / "left-over.parquet"
    kept = [setup.home / "audit" / "audit-2020-01.jsonl", setup.home / "logs" / "agent.log.3"]
    for path in (copy, spill, *kept):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        os.utime(path, (old, old))
    agent.step()
    assert not copy.exists() and not spill.exists()  # local_retention_days (30) and the day for spills
    assert all(p.exists() for p in kept)  # the audit trail, logs and queue are never the sweep's
    again = setup.home / "files" / "lh" / "a.csv"
    again.parent.mkdir(parents=True, exist_ok=True)
    again.write_text("x")
    os.utime(again, (old, old))
    clock.t += 3600
    agent.step()  # swept an hour ago: not yet
    assert again.exists()
    clock.t += 86400
    agent.step()
    assert not again.exists()


def test_a_sweep_that_fails_is_logged_and_the_pass_goes_on(setup, monkeypatch, caplog):
    from fabric_desktop import retention
    clock = Clock()
    agent = Agent(setup, queue=JobQueue(setup.home / "queue.sqlite3", clock=clock, backoff_seconds=0), clock=clock)

    def broken(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(retention, "sweep", broken)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent.step()
    assert any("couldn't clean up local copies" in r.getMessage() for r in caplog.records)
    assert agent._policy_at is not None  # the rest of the pass ran


def test_cli_requeue_and_prune(setup, capsys):
    clock = Clock()
    q = JobQueue(setup.home / "queue.sqlite3", clock=clock, backoff_seconds=0)
    dead = _finished(q, clock, "dead", 0, max_attempts=1)
    old = _finished(q, clock, "succeeded", 400)
    assert cli._agent(cli_args(setup, "requeue", job_id=dead["id"])) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "queued" and out["attempts"] == 0 and out["requeued_by"].endswith("(cli)")
    assert cli._agent(cli_args(setup, "requeue", job_id=dead["id"])) == 1  # queued now: refused
    assert "only a dead or failed job" in capsys.readouterr().out
    assert cli._agent(cli_args(setup, "prune", days=None, dry_run=True)) == 0
    assert json.loads(capsys.readouterr().out)["jobs"] == [old["id"]]
    assert q.get(old["id"]) is not None
    assert cli._agent(cli_args(setup, "prune", days=None, dry_run=False)) == 0
    assert json.loads(capsys.readouterr().out) == {"dry_run": False, "older_than_days": 30.0, "jobs": [old["id"]]}
    assert q.get(old["id"]) is None
    actions = [json.loads(line)["action"] for f in (setup.home / "audit").glob("*.jsonl")
               for line in f.read_text(encoding="utf-8").splitlines()]
    assert "agent_requeue" in actions and "agent_prune" in actions


def test_config_checks_queue_retention_days(tmp_path, setup):
    path = tmp_path / "r.toml"
    path.write_text(config_text(setup.project, tmp_path / "h3").replace("poll_seconds = 0.1",
                                                                        "poll_seconds = 0.1\nqueue_retention_days = 0"),
                    encoding="utf-8")
    with pytest.raises(agent_config.ConfigError, match="queue_retention_days"):
        agent_config.load(path)
    assert setup.queue_retention_days == 30

def test_cli_reports_a_bad_config(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[agent]\n", encoding="utf-8")
    assert cli._agent(cli_args(None, "status", config=str(bad))) == 2
    assert "agent.project" in capsys.readouterr().out


def cli_args(cfg, command, config=None, env="", entities=None, **extra):
    import argparse
    return argparse.Namespace(config=config or str(cfg.path), agent_command=command, env=env,
                              entities=entities or [], backfill="", cascade=False, **extra)


def test_a_lost_commit_race_merges_again_and_succeeds(setup, monkeypatch):
    from deltalake.exceptions import CommitFailedError

    from fabric_desktop.meta import Engine, Project
    project = Project(setup.project)
    store = LocalStore(setup.project / ".local" / "dev", "dev")
    real, calls = store.write, []

    def flaky(lakehouse, schema, table, df, mode="overwrite", metadata=None, **kw):
        calls.append(table)
        if table == "orders" and lakehouse == "LH_Bronze" and calls.count("orders") == 1:
            raise CommitFailedError("another writer committed version 1 first")
        return real(lakehouse, schema, table, df, mode, metadata, **kw)
    monkeypatch.setattr(store, "write", flaky)
    monkeypatch.setattr("fabric_desktop.meta.engine.time.sleep", lambda s: None)
    res = Engine(project, store).run("bronze.orders")
    assert res.status == "ok" and calls.count("orders") == 2


def test_a_writer_that_commits_in_between_is_never_overwritten(setup, monkeypatch):
    """The app and an agent (or a run in Fabric) run the same table: the second commit happens
    after the first one read the table. Its rows must survive, and the first run merges again."""
    import polars as pl

    from fabric_desktop.meta import Engine, Project
    project = Project(setup.project)
    store = LocalStore(setup.project / ".local" / "dev", "dev")
    assert Engine(project, store).run("bronze.orders").status == "ok"
    e = project.entities["bronze.orders"]
    lh = project.layer(e.layer).lakehouse
    # A full load replaces every row, so it reads only the table's schema and version (schema_at).
    real_read, raced = store.schema_at, []
    first = real_read(lh, e.schema, e.table)[1]

    def read_then_other_writer_commits(lakehouse, schema, table):
        out = real_read(lakehouse, schema, table)
        if table == e.table and not raced:
            raced.append(True)
            rows = store.read(lakehouse, schema, table)
            other = rows.head(1).with_columns(pl.lit("other-writer").alias("_run_id"))
            store.write(lakehouse, schema, table, pl.concat([rows, other], how="diagonal_relaxed"))
        return out
    real_write, outcomes = store.write, []

    def write(lakehouse, schema, table, df, mode="overwrite", metadata=None, **kw):
        try:
            v = real_write(lakehouse, schema, table, df, mode, metadata, **kw)
        except Exception as exc:
            outcomes.append(type(exc).__name__)
            raise
        if table == e.table and "read_version" in kw:
            outcomes.append(("ok", kw["read_version"], v))
        return v
    monkeypatch.setattr(store, "schema_at", read_then_other_writer_commits)
    monkeypatch.setattr(store, "write", write)
    monkeypatch.setattr("fabric_desktop.meta.engine.time.sleep", lambda s: None)
    res = Engine(project, store).run("bronze.orders")
    assert res.status == "ok", res.message
    # The stale commit was refused; the merge made again read the other writer's version.
    assert outcomes == ["CommitFailedError", ("ok", first + 1, first + 2)]


def test_an_overwrite_from_a_stale_read_is_refused(tmp_path):
    import polars as pl
    from deltalake.exceptions import CommitFailedError
    store = LocalStore(tmp_path, "dev")
    store.write("LH", "dbo", "t", pl.DataFrame({"a": [1]}))
    _, version = store.read_at("LH", "dbo", "t")
    store.write("LH", "dbo", "t", pl.DataFrame({"a": [2]}), mode="append")
    with pytest.raises(CommitFailedError):
        store.write("LH", "dbo", "t", pl.DataFrame({"a": [1, 9]}), read_version=version)
    assert sorted(store.read("LH", "dbo", "t")["a"].to_list()) == [1, 2]
    with pytest.raises(CommitFailedError):  # there was no table when read, but there is now
        store.write("LH", "dbo", "t", pl.DataFrame({"a": [5]}), read_version=None)
    store.write("LH", "dbo", "new", pl.DataFrame({"a": [5]}), read_version=None)
    _, version = store.read_at("LH", "dbo", "t")
    store.write("LH", "dbo", "t", pl.DataFrame({"a": [7], "b": ["x"]}), read_version=version)
    assert store.read("LH", "dbo", "t").to_dicts() == [{"a": 7, "b": "x"}]


def test_a_lease_file_being_deleted_reads_as_busy_not_an_error(tmp_path, monkeypatch):
    """Windows briefly refuses to open a file another process is deleting."""
    from pathlib import Path

    from fabric_desktop.leases import LocalBackend
    backend = LocalBackend(tmp_path)
    backend.create("LH/dbo.t", b"{}")
    real, failures = Path.read_bytes, [PermissionError("being deleted"), PermissionError("being deleted")]

    def read_bytes(self):
        if failures:
            raise failures.pop()
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert backend.read("LH/dbo.t").data == b"{}"


def test_job_ids_made_in_the_same_second_carry_48_random_bits(tmp_path):
    """200 jobs in one second with 24 random bits collided about once in 800 runs (UNIQUE constraint failed)."""
    q = JobQueue(tmp_path / "q.sqlite3", clock=lambda: 1_700_000_000.0)
    ids = [q.enqueue("meta", {"i": i})["id"] for i in range(200)]
    assert len(set(ids)) == 200
    assert all(re.fullmatch(r"job-\d{8}-\d{6}-[0-9a-f]{12}", i) for i in ids), ids[:3]


# ---- the policy file (#1670's follow-up): the agent records a change in its own audit log

def test_agent_records_the_policy_file_at_start_and_a_change_at_its_next_check(setup, monkeypatch, tmp_path):
    from fabric_desktop import agent as agent_mod
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"show_preview": False}), encoding="utf-8")
    clock = Clock()
    agent = Agent(setup, queue=JobQueue(setup.home / "queue.sqlite3", clock=clock), clock=clock)
    seen = lambda: [(e["action"], e["target"]["state"]) for e in agent.audit.entries()  # noqa: E731
                    if e["action"].startswith("policy_file_")]
    agent.step()
    assert seen() == [("policy_file_seen", "applied")]
    assert (setup.home / "policy-seen.json").exists()  # in the agent's home, apart from the desktop app's
    policy.write_text(json.dumps({"show_preview": True}), encoding="utf-8")
    clock.t += agent_mod.POLICY_CHECK_SECONDS - 1
    agent.step()
    assert len(seen()) == 1  # not time to look again yet
    clock.t += 1
    agent.step()
    assert seen() == [("policy_file_seen", "applied"), ("policy_file_changed", "applied")]
    clock.t += agent_mod.POLICY_CHECK_SECONDS
    agent.step()
    assert len(seen()) == 2  # unchanged: nothing new
    policy.unlink()
    clock.t += agent_mod.POLICY_CHECK_SECONDS
    agent.step()
    assert seen()[-1] == ("policy_file_changed", "none")


def test_a_policy_check_that_fails_is_logged_and_the_loop_carries_on(setup, monkeypatch, caplog):
    from fabric_desktop import policy_watch
    def broken(*_):
        raise OSError("disk gone")
    monkeypatch.setattr(policy_watch, "check", broken)
    clock = Clock()
    q = JobQueue(setup.home / "queue.sqlite3", clock=clock, backoff_seconds=0)
    agent = Agent(setup, queue=q, clock=clock)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent.step()
    assert "couldn't check the policy file" in caplog.text
    assert agent._pruned_at is not None  # the rest of the pass ran
