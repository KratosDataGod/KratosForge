"""The agent's job queue under contention (EA-011 hardening): several workers, in threads and in other
processes, claiming and finishing jobs on one queue file; leases running out while other workers
recover and claim again; and a queue file that stays locked while a run renews its lease.

What must hold: without lapsed leases each job is claimed once and finished once; with them, a job may
run again but only one claim of it can finish it (the fencing claim_id), and nothing errors with
"database is locked" except where a test makes the lock outlast the busy timeout on purpose.
"""

import dataclasses
import json
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from fabric_desktop import agent_config, jobqueue
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.meta.templates import init_project

from test_agent import config_text

SIDECAR = Path(__file__).resolve().parents[1]

# A worker in another process: claims and finishes until the queue is empty, prints the ids it finished.
_WORKER = """
import json, sys
from fabric_desktop.jobqueue import JobQueue
q = JobQueue(sys.argv[1])
done = []
while True:
    job = q.claim(sys.argv[2], 600)
    if job is None:
        break
    assert q.succeed(job["id"], sys.argv[2], {"by": sys.argv[2]}, claim=job["claim_id"])
    done.append(job["id"])
print(json.dumps(done))
"""


def _drain(q: JobQueue, owner: str, out: list[str], errors: list[BaseException]) -> None:
    try:
        while True:
            job = q.claim(owner, 600)
            if job is None:
                return
            assert q.heartbeat(job["id"], owner, 600, claim=job["claim_id"])
            assert q.succeed(job["id"], owner, {"by": owner}, claim=job["claim_id"])
            out.append(job["id"])
    except BaseException as exc:  # noqa: BLE001 - reported by the test
        errors.append(exc)


def test_workers_in_threads_and_processes_claim_each_job_once(tmp_path):
    path = tmp_path / "q.sqlite3"
    q = JobQueue(path)
    ids = {q.enqueue("meta", {"env": "dev", "n": n})["id"] for n in range(150)}
    env = os.environ | {"PYTHONPATH": str(SIDECAR) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    procs = [subprocess.Popen([sys.executable, "-c", _WORKER, str(path), f"proc-{n}"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env) for n in range(2)]
    threads, done, errors = [], [[] for _ in range(6)], []
    for n in range(6):
        t = threading.Thread(target=_drain, args=(JobQueue(path), f"thread-{n}", done[n], errors))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(120)
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        done.append(json.loads(out))
    assert not errors, errors
    finished = [i for d in done for i in d]
    assert Counter(finished).most_common(1)[0][1] == 1, "a job was claimed twice"
    assert set(finished) == ids
    assert q.counts() == {s: 0 for s in jobqueue.STATES} | {"succeeded": 150}
    for job in q.list(200):  # the result is the one its only claim wrote
        assert job["result"]["by"] in {f"thread-{n}" for n in range(6)} | {"proc-0", "proc-1"}


def test_with_leases_running_out_under_contention_only_one_claim_finishes_each_job(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3", backoff_seconds=0)
    ids = [q.enqueue("meta", {"env": "dev"}, max_attempts=1000)["id"] for _ in range(40)]
    lease = 0.15
    wins: dict[str, list[str]] = {i: [] for i in ids}
    lost = Counter()
    stop, errors, lock = threading.Event(), [], threading.Lock()

    def worker(n: int) -> None:
        rnd, owner = random.Random(n), f"w{n % 2}"  # two owner names: a lapsed claim can't win on its name
        try:
            while not stop.is_set():
                job = q.claim(owner, lease)
                if job is None:
                    time.sleep(0.01)
                    continue
                time.sleep(rnd.uniform(0, 2.5 * lease))  # some runs outlast their lease
                if q.succeed(job["id"], owner, {"claim": job["claim_id"]}, claim=job["claim_id"]):
                    with lock:
                        wins[job["id"]].append(job["claim_id"])
                else:
                    with lock:
                        lost[job["id"]] += 1
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            errors.append(exc)

    def recoverer() -> None:
        try:
            while not stop.is_set():
                q.recover()
                time.sleep(0.02)
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)] + [threading.Thread(target=recoverer)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and q.counts()["succeeded"] < len(ids):
        time.sleep(0.05)
    stop.set()
    for t in threads:
        t.join(30)
    assert not errors, errors
    assert q.counts()["succeeded"] == len(ids)
    for i in ids:
        assert len(wins[i]) == 1, (i, wins[i])  # one claim finished it, however many ran
        assert q.get(i)["result"] == {"claim": wins[i][0]} and q.get(i)["claim_id"] == wins[i][0]
    assert sum(lost.values()) > 0, "no lease ran out: the test didn't exercise fencing"


def _agent(tmp_path: Path, lease: float) -> Agent:
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(config_text(project, tmp_path / "home"), encoding="utf-8")
    return Agent(dataclasses.replace(agent_config.load(path), job_lease_seconds=lease))


def _short_busy_timeout(monkeypatch) -> None:
    """The queue's connections wait 0.05 s for a lock instead of 30, so a held lock is a real
    "database is locked" error at the heartbeat, as one held longer than 30 s would be."""
    real = sqlite3.connect
    monkeypatch.setattr(jobqueue.sqlite3, "connect", lambda path, timeout=5.0, **k: real(path, timeout=0.05, **k))


def test_a_lock_held_across_several_beats_doesnt_end_the_heartbeat_or_lose_the_lease(tmp_path, monkeypatch):
    agent = _agent(tmp_path, lease=0.6)
    agent.queue.enqueue("meta", {"env": "dev"})
    job = agent.queue.claim(agent.config.name, 0.6)
    holder = sqlite3.connect(agent.queue.path, timeout=5, isolation_level=None)
    _short_busy_timeout(monkeypatch)
    calls, real = [], agent.queue.heartbeat

    def counted(*a, **k):
        calls.append(time.monotonic())
        return real(*a, **k)
    agent.queue.heartbeat = counted
    done, lost = threading.Event(), threading.Event()
    beat = threading.Thread(target=agent._heartbeat, args=(job, done, lost))
    try:
        holder.execute("begin exclusive")  # held for two beats (each one fails with "database is locked")
        beat.start()
        deadline = time.monotonic() + 10
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(calls) >= 2
        time.sleep(0.05)
        holder.execute("commit")
        before = len(calls)
        deadline = time.monotonic() + 10
        while len(calls) < before + 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert beat.is_alive() and not lost.is_set()
        row = agent.queue.get(job["id"])
        assert row["state"] == "running" and row["lease_until"] > time.time()  # renewed once the lock went
        assert agent.queue.recover() == []
    finally:
        done.set()
        beat.join(5)
        holder.close()


def test_a_lock_that_outlasts_the_lease_loses_the_job_to_the_next_claim_and_the_old_run_cant_finish(tmp_path, monkeypatch):
    agent = _agent(tmp_path, lease=0.3)
    agent.queue.enqueue("meta", {"env": "dev"})
    owner = agent.config.name
    job = agent.queue.claim(owner, 0.3)
    holder = sqlite3.connect(agent.queue.path, timeout=5, isolation_level=None)
    _short_busy_timeout(monkeypatch)
    done, lost = threading.Event(), threading.Event()
    beat = threading.Thread(target=agent._heartbeat, args=(job, done, lost))
    try:
        holder.execute("begin exclusive")
        beat.start()
        time.sleep(0.5)  # past the lease, every renewal locked out
        # Another agent's recover, inside the lock: the lapsed job goes back to the queue.
        holder.execute("update jobs set state = 'queued', owner = null, lease_until = null where id = ?"
                       " and lease_until < ?", (job["id"], time.time()))
        holder.execute("commit")
        assert lost.wait(5), "the run never learned its lease was gone"
        beat.join(5)
        assert not beat.is_alive()
        again = agent.queue.claim("etl-02", 60)
        assert again["id"] == job["id"] and again["claim_id"] != job["claim_id"]
        assert not agent.queue.succeed(job["id"], owner, {"old": 1}, claim=job["claim_id"])
        assert agent.queue.succeed(job["id"], "etl-02", {"new": 1}, claim=again["claim_id"])
        assert agent.queue.get(job["id"])["result"] == {"new": 1}
    finally:
        done.set()
        holder.close()


def test_a_lock_when_the_run_records_its_outcome_is_tried_again_not_lost(tmp_path, monkeypatch):
    # Found by these tests: a finished run whose succeed() met a lock past the busy wait was logged
    # as "job crashed" and the job ran again once its lease lapsed. Now the outcome is tried again.
    agent = _agent(tmp_path, lease=60)
    agent.SETTLE_WAITS = (0.01, 0.01)
    job = agent.enqueue("dev")
    claimed = agent.queue.claim(agent.config.name, 60)
    monkeypatch.setattr(agent.runtime, "run_environment", lambda env, entities, backfill, cascade, job=None, guard=None:
                        [{"entity": "silver.orders", "status": "ok", "rows_written": 1}])
    real, tries = agent.queue.succeed, []

    def locked_twice(*a, **k):
        tries.append(1)
        if len(tries) <= 2:
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)
    agent.queue.succeed = locked_twice
    agent.run_job(claimed)
    assert len(tries) == 3 and agent.queue.get(job["id"])["state"] == "succeeded"
    [line] = [e for e in agent.audit.recent(10) if e["action"] == "agent_job"]
    assert line["outcome"] == "ok", line
    # a lock that never clears is still an error, after the last try
    agent.queue.succeed = lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked"))
    again = agent.queue.enqueue("meta", {"env": "dev"})
    second = agent.queue.claim(agent.config.name, 60)
    assert second["id"] == again["id"]
    with pytest.raises(sqlite3.OperationalError):
        agent.run_job(second)
    assert agent.queue.get(again["id"])["state"] == "running"  # left for the lease to lapse, as before
