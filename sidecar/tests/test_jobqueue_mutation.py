"""jobqueue.py paths that mutation testing (batch 22, docs/quality/mutation-testing.md) showed no test
checked: the default backoff, longest backoff and attempts, what fail, retry_later, cancel, requeue,
prune and set_payload answer, the backoff doubling, recovering a lease renewed before the clock went
back, each reason requeue refuses, pruning by age and with dry_run, the default list limits, and a
queue file damaged inside. The queue file is under tmp_path; nothing runs."""

from __future__ import annotations

import re
import sqlite3

import pytest

from fabric_desktop import jobqueue
from fabric_desktop.jobqueue import CLOCK_MARGIN, JobQueue


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def queue(tmp_path, **kw):
    clock = Clock()
    return JobQueue(tmp_path / "q.sqlite3", clock=clock, **kw), clock


def _dead(q, key=None):
    job = q.enqueue("meta", {"env": "dev"}, max_attempts=1, dedupe_key=key)
    claimed = q.claim("w", 60)
    assert q.retry_later(job["id"], "w", "boom", claim=claimed["claim_id"])["state"] == "dead"
    return job


# ---- defaults

def test_the_default_backoff_is_a_minute_up_to_half_an_hour(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    assert (q.backoff_seconds, q.max_backoff_seconds) == (60, 1800)


def test_a_job_has_three_attempts_unless_told_otherwise(tmp_path):
    q, _ = queue(tmp_path)
    assert q.enqueue("meta", {})["max_attempts"] == 3


def test_the_default_list_limits_are_fifty_and_two_hundred(tmp_path):
    q, clock = queue(tmp_path)
    for i in range(201):
        clock.t += 1
        job = q.enqueue("meta", {"env": "dev", "i": i})
        q.claim("w", 60)
        q.succeed(job["id"], "w")
    assert len(q.list()) == 50
    assert len(q.for_env("dev")) == 200


def test_the_queue_file_can_be_in_a_folder_not_made_yet_and_opened_again(tmp_path):
    path = tmp_path / "a" / "b" / "q.sqlite3"
    JobQueue(path).enqueue("meta", {})
    assert len(JobQueue(path).list()) == 1


# ---- adding and claiming

def test_a_job_id_is_the_time_and_twelve_hex_digits(tmp_path):
    q, _ = queue(tmp_path)
    assert re.fullmatch(r"job-\d{8}-\d{6}-[0-9a-f]{12}", q.enqueue("meta", {})["id"])


def test_a_delayed_job_is_due_later_and_has_at_least_one_attempt(tmp_path):
    q, clock = queue(tmp_path)
    job = q.enqueue("meta", {}, delay=30, max_attempts=0)
    assert job["next_at"] == clock.t + 30 and job["max_attempts"] == 1


def test_a_job_already_queued_under_the_key_comes_back_marked(tmp_path):
    q, _ = queue(tmp_path)
    first = q.enqueue("meta", {}, dedupe_key="sched:a")
    assert "deduplicated" not in first
    again = q.enqueue("meta", {}, dedupe_key="sched:a")
    assert again["id"] == first["id"] and again["deduplicated"] is True


def test_a_claim_leases_the_job_until_now_plus_the_lease(tmp_path):
    q, clock = queue(tmp_path)
    q.enqueue("meta", {})
    assert q.claim("w", 90)["lease_until"] == clock.t + 90


@pytest.mark.parametrize("back, due", [(1000, False), (2000, False), (3000, True)])
def test_a_retry_further_off_than_the_longest_backoff_and_the_margin_is_due(tmp_path, back, due):
    q, clock = queue(tmp_path)  # longest backoff 1800 s, margin 300 s
    job = q.enqueue("meta", {})
    q.claim("w", 60)
    q.retry_later(job["id"], "w", "busy")  # due in 60 s
    clock.t -= back
    got = q.claim("w", 60)
    assert (got is not None and got["id"] == job["id"]) is due


# ---- answers

def test_fail_answers_whether_it_failed_the_job(tmp_path):
    q, _ = queue(tmp_path)
    job = q.enqueue("meta", {})
    q.claim("w", 60)
    assert q.fail(job["id"], "w", "bad table") is True
    assert q.get(job["id"])["state"] == "failed"
    assert q.fail(job["id"], "w", "again") is False


def test_retry_later_answers_with_the_job_as_it_now_is(tmp_path):
    q, clock = queue(tmp_path)
    job = q.enqueue("meta", {})
    q.claim("w", 60)
    got = q.retry_later(job["id"], "w", "throttled")
    assert got["id"] == job["id"] and got["state"] == "queued" and got["error"] == "throttled"
    assert got["next_at"] == clock.t + 60


def test_cancel_answers_whether_it_cancelled_the_job(tmp_path):
    q, _ = queue(tmp_path)
    job = q.enqueue("meta", {})
    assert q.cancel(job["id"], "not now") is True
    assert q.cancel(job["id"], "not now") is False


def test_set_payload_answers_whether_it_changed_the_job(tmp_path):
    q, _ = queue(tmp_path)
    job = q.enqueue("meta", {"tables": ["a", "b"]})
    assert q.set_payload(job["id"], {"tables": ["b"]}) is True
    assert q.get(job["id"])["payload"] == {"tables": ["b"]}
    assert q.set_payload("job-missing", {}) is False


# ---- backoff and recovery

def test_the_backoff_doubles_with_each_attempt(tmp_path):
    q, clock = queue(tmp_path, backoff_seconds=10, max_backoff_seconds=1000)
    job = q.enqueue("meta", {}, max_attempts=5)
    waits = []
    for _ in range(3):
        clock.t += 5000
        q.claim("w", 60)
        waits.append(q.retry_later(job["id"], "w", "busy")["next_at"] - clock.t)
    assert waits == [10, 20, 40]


def test_recover_takes_back_a_lease_further_off_than_the_longest_lease_and_the_margin(tmp_path):
    q, clock = queue(tmp_path)
    near = q.enqueue("meta", {})
    q.claim("w", 100 + CLOCK_MARGIN - 50)  # within the margin: a small correction, still live
    far = q.enqueue("meta", {})
    q.claim("w", 100 + CLOCK_MARGIN + 50)  # renewed before the clock went back
    assert q.recover(max_lease=100) == [far["id"]]
    assert q.get(near["id"])["state"] == "running"


# ---- requeue

def test_requeue_puts_a_dead_job_back_and_answers_with_it(tmp_path):
    q, _ = queue(tmp_path)
    job = _dead(q, key="sched:a")
    got = q.requeue(job["id"], "dana")
    assert got["id"] == job["id"] and got["state"] == "queued"
    assert got["attempts"] == 0 and got["requeued_by"] == "dana"


def test_requeue_puts_back_a_dead_job_without_a_key(tmp_path):
    q, _ = queue(tmp_path)
    job = _dead(q)
    assert q.requeue(job["id"], "dana")["state"] == "queued"


def test_requeue_refuses_an_unknown_job(tmp_path):
    q, _ = queue(tmp_path)
    with pytest.raises(LookupError, match="There is no job job-x in this agent's queue."):
        q.requeue("job-x", "dana")


def test_requeue_refuses_a_job_that_is_not_dead_or_failed(tmp_path):
    q, _ = queue(tmp_path)
    job = q.enqueue("meta", {})
    with pytest.raises(ValueError, match="is queued; only a dead or failed job can be requeued"):
        q.requeue(job["id"], "dana")


def test_requeue_refuses_a_job_from_a_fabric_pipeline(tmp_path):
    q, _ = queue(tmp_path)
    job = _dead(q, key="pipeline:run-1")
    with pytest.raises(ValueError, match="came from a Fabric pipeline"):
        q.requeue(job["id"], "dana")
    assert q.get(job["id"])["state"] == "dead"


def test_requeue_refuses_a_job_with_a_newer_run_queued(tmp_path):
    q, _ = queue(tmp_path)
    job = _dead(q, key="sched:a")
    q.enqueue("meta", {}, dedupe_key="sched:a")
    with pytest.raises(ValueError, match="has a newer run queued or running"):
        q.requeue(job["id"], "dana")


# ---- prune

def test_prune_removes_finished_jobs_by_age_and_dry_run_only_lists_them(tmp_path):
    q, clock = queue(tmp_path)
    job = q.enqueue("meta", {})
    q.claim("w", 60)
    q.succeed(job["id"], "w")
    clock.t += 2 * 86400
    assert q.prune(3) == []
    assert q.prune(1, dry_run=True) == [job["id"]]
    assert q.get(job["id"]) is not None
    assert q.prune(1) == [job["id"]]
    assert q.get(job["id"]) is None


def test_prune_counts_a_day_as_86400_seconds(tmp_path):
    q, clock = queue(tmp_path)
    job = q.enqueue("meta", {})
    q.claim("w", 60)
    q.succeed(job["id"], "w")
    clock.t += 86400.5
    assert q.prune(1, dry_run=True) == [job["id"]]


# ---- a damaged file

def test_a_file_that_opens_but_fails_its_check_is_set_aside(monkeypatch, tmp_path):
    path = tmp_path / "q.sqlite3"
    JobQueue(path).enqueue("meta", {})
    real = sqlite3.connect
    calls = []

    class Checked:
        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        def __setattr__(self, name, value):
            if name == "db":
                object.__setattr__(self, name, value)
            else:
                setattr(self.db, name, value)

        def execute(self, sql, *args):
            if sql == "pragma quick_check" and not calls:
                calls.append(sql)
                return self.db.execute("select '*** in database main *** Page 3: btreeInitPage() returns error code 11'")
            return self.db.execute(sql, *args)

    monkeypatch.setattr(jobqueue.sqlite3, "connect", lambda *a, **kw: Checked(real(*a, **kw)))
    q = JobQueue(path)
    assert calls and q.set_aside is not None and q.set_aside.name.startswith("q.sqlite3.corrupt-")
    assert q.list() == []
