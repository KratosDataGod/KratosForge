"""Run requests from Fabric pipelines (mailbox.py), more of the paths the main tests don't reach:
Windows' lock on a local control folder, a holder that can't be written, status reads that fail
while a job changes, a request gone since the listing, a claim another agent won, requests this
agent won't take, records that can't be judged, and LH_Meta's control files. A fake agent and fake
control files stand in; nothing runs and nothing reaches OneLake."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace


from fabric_desktop import mailbox
from fabric_desktop.leases import Stored
from fabric_desktop.meta import OneLakeStore


class Control:
    """Control files in memory; ``broken`` makes every read fail."""

    def __init__(self, files=None, broken=False, create_ok=True):
        self.files, self.broken, self.create_ok = dict(files or {}), broken, create_ok

    def read(self, key):
        if self.broken:
            raise ConnectionError("OneLake unreachable")
        data = self.files.get(key)
        return None if data is None else Stored(data, "e1", 0.0)

    def create(self, key, data):
        return self.create_ok

    def list(self, folder):
        return [k.split("/", 1)[1] for k in self.files if k.startswith(folder + "/")]


class Log:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args, **kw):
        self.warnings.append(msg % args if args else msg)

    def info(self, *a, **k):
        pass


def box(control, environments=("dev",), free=True, **kw):
    agent = SimpleNamespace(config=SimpleNamespace(name="etl-01"), clock=lambda: 1_800_000_000.0,
                            has_free_worker=lambda: free)
    return mailbox.Mailbox(agent, lambda env: control, list(environments), log=Log(), **kw)


def job(env="dev", rid="r1"):
    return {"source": f"pipeline:{rid}", "payload": {"env": env}}


# ---- the local control folder's lock


def test_windows_locks_the_first_byte_of_the_control_folder_s_lock(tmp_path, monkeypatch):
    locked = []
    monkeypatch.setattr(mailbox, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, locking=lambda fd, mode, n: locked.append((mode, n))))
    f = mailbox.hold_control_folder(tmp_path / "control", {"agent": "etl-01"})
    try:
        assert locked == [(2, 1)]
    finally:
        f.close()
    assert json.loads((tmp_path / "control" / mailbox.CONTROL_LOCK).read_bytes()[1:]) == {"agent": "etl-01"}


def test_a_holder_that_cant_be_written_still_holds_the_folder(tmp_path, monkeypatch):
    real_open = open

    class NoWrite:
        def __init__(self, f):
            self.f = f

        def __getattr__(self, name):
            return getattr(self.f, name)

        def truncate(self, n):
            raise OSError("disk full")

    monkeypatch.setattr(mailbox, "open", lambda path, mode: NoWrite(real_open(path, mode)), raising=False)
    f = mailbox.hold_control_folder(tmp_path / "control", {"agent": "etl-01"})
    f.close()  # read after: Windows locks byte 0 against reading while it's held
    assert (tmp_path / "control" / mailbox.CONTROL_LOCK).read_bytes() == b""


# ---- a job changing, and what the worker asks before it runs


def test_a_job_for_an_environment_this_mailbox_doesnt_serve_is_left_alone():
    control = Control(broken=True)
    b = box(control)
    b.job_changed(job(env="prod"))  # never read: prod isn't this mailbox's
    assert b.log.warnings == []


def test_a_status_that_cant_be_read_when_a_job_changes_is_tried_next_poll():
    b = box(Control(broken=True))
    b.job_changed(job())
    assert b.log.warnings == ["couldn't update the pipeline status"]


def test_an_owner_that_cant_be_read_lets_the_job_carry_on():
    b = box(Control(broken=True))
    assert b.taken_over(job()) == ""
    assert b.log.warnings[0].startswith("Couldn't read the pipeline job's status; carrying on")


def test_a_cancel_that_cant_be_checked_runs_the_job():
    b = box(Control(broken=True))
    assert b.cancel_requested(job()) is False
    assert b.log.warnings[0].startswith("Couldn't check for a cancel request; running the job")


# ---- claiming a request


def test_a_request_deleted_since_the_listing_is_not_claimed():
    control = Control()
    b = box(control)
    b._claim("dev", "gone")
    assert control.files == {}


def test_a_claim_another_agent_won_first_is_left_to_it(monkeypatch):
    control = Control({"requests/r1.json": json.dumps({"env": "dev"}).encode()}, create_ok=False)
    b = box(control)
    synced = []
    monkeypatch.setattr(b, "_sync", lambda *a: synced.append(a))
    b._claim("dev", "r1")
    assert synced == []


def test_a_cascade_naming_two_entities_is_refused():
    b = box(Control(), allow_backfill=True)
    _, reason = b._parse("dev", "r1", json.dumps({"env": "dev", "entities": ["a", "b"], "cascade": True}).encode())
    assert "name exactly one" in reason


def test_a_request_for_an_environment_this_agent_doesnt_serve_is_refused():
    b = box(Control(), environments=("prod",))
    _, reason = b._parse("dev", "r1", json.dumps({"env": "dev"}).encode())
    assert "this agent doesn't take pipeline requests for dev" in reason


def test_an_agent_that_doesnt_check_runs_says_so():
    b = box(Control())
    out = b._check_run("dev", {"item_id": "i", "run_id": "r"}, 0)
    assert out == {"item_id": "i", "run_id": "r", "verified": False, "detail": "this agent doesn't check pipeline runs"}


# ---- taking over from a lost agent


def test_an_owner_whose_name_cant_be_a_file_is_never_lost():
    b = box(Control(broken=True))
    assert b._lost("dev", "") is False and b._lost("dev", "../x") is False


def test_a_record_that_isnt_an_agent_status_cant_say_its_agent_is_lost():
    b = box(Control({"agents/etl-02.json": json.dumps({"kind": "something_else"}).encode()}))
    assert b._past_stale("dev", "etl-02") is False


def test_take_over_waits_for_a_free_worker_and_for_the_request_itself(monkeypatch):
    b = box(Control(), claim="when_free", free=False)
    monkeypatch.setattr(b, "_lost", lambda env, owner: True)
    wrote = []
    monkeypatch.setattr(b, "_write_status", lambda *a: wrote.append(a) or True)
    b._take_over("dev", "r1", {"agent": "etl-02"}, Stored(b"{}", "e", 0.0))
    b.agent.has_free_worker = lambda: True
    b._take_over("dev", "r1", {"agent": "etl-02"}, Stored(b"{}", "e", 0.0))  # its request file is gone
    assert wrote == []


def test_a_finished_status_without_a_readable_time_is_kept():
    control = Control()
    deleted = []
    control.delete = lambda key, etag: deleted.append(key)
    box(control)._expire_files("dev", "r1", {"updated_at": "yesterday"}, Stored(b"{}", "e", 0.0))
    assert deleted == []


def test_an_onelake_environment_s_control_files_are_in_its_lh_meta():
    store = OneLakeStore.__new__(OneLakeStore)
    store.workspace_id = "ws-1"
    control = mailbox.control_backend_for(store, "lh-meta", lambda: "tok")
    assert isinstance(control, mailbox.OneLakeControl)
    assert control.workspace_id == "ws-1" and control.lakehouse_id == "lh-meta"
    assert control._url("requests/r1.json").endswith(f"{mailbox.CONTROL_DIR}/requests/r1.json")
