"""Fuzz the app's own state files on disk as they are read back: the pipeline mailbox's requests,
statuses and accepted-runs file (mailbox.py), table lease files (leases.py), notebook versions and
their index (notebook_versions.py), and the Optimize outbox's waiting items (optimize/outbox.py).
The access cache's view (access_cache.py) is pinned over random rows of the shape it writes.

Each case is a seeded mutation of a valid file (tests/settings_fuzz.py): torn, empty, not UTF-8,
the wrong type, a field of the wrong type, NaN, huge numbers, deep nesting, or a folder where the
file goes. A damaged file is refused by name or ignored as the module's design says, without a
crash, and without holding up or losing the good entries beside it."""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import access_cache, mailbox, notebook_versions
from fabric_desktop.leases import Leases, LocalBackend, Stored
from fabric_desktop.optimize.outbox import Outbox

from tests.settings_fuzz import mutations

NOW = 1_800_000_000.0
CASES = 120
DEEP = b"[" * 30_000 + b"]" * 30_000  # parses only past Python's recursion limit
BIG = b"1" + b"0" * 400  # an int too big for a float


# ---- the pipeline mailbox

class Control:
    """Control files in memory; ``modified`` 0 makes every file old enough to judge."""

    def __init__(self, files):
        self.files = dict(files)

    def read(self, key):
        data = self.files.get(key)
        if data is None:
            return None
        return mailbox.Stored(data if isinstance(data, bytes) else json.dumps(data).encode(), "e1", 0.0)

    def create(self, key, data):
        if key in self.files:
            return None
        self.files[key] = data
        return "e2"

    def replace(self, key, data, etag):
        self.files[key] = data
        return "e3"

    def delete(self, key, etag):
        self.files.pop(key, None)
        return True

    def list(self, folder):
        return sorted(k.split("/", 1)[1] for k in self.files if k.startswith(folder + "/"))


class Log(logging.Logger):
    def __init__(self):
        super().__init__("fuzz")
        self.warned: list[str] = []

    def warning(self, msg, *args, **kw):  # type: ignore[override]
        self.warned.append(msg)


def _mailbox(control, **kw):
    queue = SimpleNamespace(find=lambda key: None, get=lambda job_id: None, cancel=lambda job_id, why: True)
    agent = SimpleNamespace(config=SimpleNamespace(name="etl-01"), clock=lambda: NOW, has_free_worker=lambda: True,
                            queue=queue, enqueue=lambda *a, **k: {"id": "j1", "state": "queued", "attempts": 0})
    return mailbox.Mailbox(agent, lambda env: control, ["dev"], log=Log(), **kw)


GOOD_REQUEST = {"contract": 1, "env": "dev", "entities": [], "deadline": "2030-01-01T00:00:00Z", "submitted_by": "nightly"}
OWN_STATUS = {"contract": 1, "request_id": "a", "agent": "etl-01", "host": "pc", "env": "dev", "state": "accepted",
              "entities": [], "deadline": "2030-01-01T00:00:00Z", "agents": ["etl-01"],
              "received_at": "2027-01-15T08:00:00Z", "updated_at": "2027-01-15T08:00:00Z"}


def _check_poll(files):
    """A damaged request ``a`` never stops request ``b`` after it from being claimed."""
    control = Control(files | {"requests/b.json": GOOD_REQUEST})
    box = _mailbox(control)
    box.poll()
    assert "couldn't read the pipeline mailbox" not in box.log.warned
    assert json.loads(control.files["status/b.json"])["state"] == "accepted"
    return control


@pytest.mark.parametrize("seed", [1, 2])
def test_a_damaged_request_is_rejected_by_name_and_the_next_one_is_still_claimed(seed):
    cases = [*mutations(GOOD_REQUEST, seed, CASES), ("deep", DEEP)]
    for label, raw in cases:
        control = _check_poll({"requests/a.json": raw})
        status = json.loads(control.files["status/a.json"])
        assert status["state"] in ("accepted", "rejected", "expired"), label
        if status["state"] == "rejected":
            assert isinstance(status["reason"], str) and status["reason"], label


def test_a_request_nested_too_deeply_is_rejected_with_a_plain_reason():
    control = _check_poll({"requests/a.json": DEEP})
    status = json.loads(control.files["status/a.json"])
    assert status["state"] == "rejected"
    assert status["reason"] == ("the request can't be read because it's nested too deeply, "
                                "so write it as one flat JSON object, as the contract shows")


@pytest.mark.parametrize("seed", [1, 2])
def test_a_damaged_status_never_holds_up_the_requests_after_it(seed):
    cases = [*mutations(OWN_STATUS, seed, CASES), ("deep", DEEP),
             ("deadline number", json.dumps(OWN_STATUS | {"deadline": 5}).encode()),
             ("deadline text", json.dumps(OWN_STATUS | {"deadline": "soon"}).encode())]
    for label, raw in cases:
        control = _check_poll({"requests/a.json": GOOD_REQUEST, "status/a.json": raw})
        assert "status/a.json" in control.files, label  # never deleted for being damaged


@pytest.mark.parametrize("seed", [1, 2])
def test_a_damaged_accepted_runs_file_keeps_its_good_entries(tmp_path, seed):
    run, other = "66666666-7777-8888-9999-000000000000", "11111111-2222-3333-4444-555555555555"
    valid = {"dev": {run: {"request_id": "x", "at": NOW}, other: {"request_id": "y", "at": NOW}}}
    path = tmp_path / "runs.json"
    huge = json.dumps(valid).replace(f'"at": {NOW}', '"at": ' + BIG.decode(), 1).encode()
    for label, raw in [*mutations(valid, seed, CASES), ("huge at", huge),
                       ("infinite at", json.dumps(valid).replace(f'"at": {NOW}', '"at": Infinity', 1).encode())]:
        path.write_bytes(raw)
        box = _mailbox(Control({}), runs_file=path, require_run=True)
        for env in box._runs.values():
            for entry in env.values():
                assert -1e12 < entry["at"] < 1e12, label
        box._run_used("dev", run, "z", NOW)
        box._run_taken("dev", "22222222-2222-2222-2222-222222222222", "z", NOW)
        path.unlink(missing_ok=True)
        for p in tmp_path.glob("runs.json.corrupt-*"):
            p.unlink()
    path.write_bytes(huge)
    box = _mailbox(Control({}), runs_file=path, require_run=True)
    assert box._run_used("dev", other, "z", NOW) == "y"  # the good entry beside it is kept


# ---- table lease files

GOOD_LEASE = {"owner": "someone", "host": "pc", "pid": 1, "acquired_at": "2026-01-01T00:00:00+00:00",
              "expires": 0.0, "ttl": 10}


@pytest.mark.parametrize("seed", [1, 2])
def test_a_damaged_lease_file_that_has_gone_unwritten_past_its_length_is_taken_over(tmp_path, seed):
    now = time.time()
    lease = GOOD_LEASE | {"expires": now - 990, "acquired_at": "2026-01-01T00:00:00+00:00"}
    cases = [*mutations(lease, seed, CASES), ("deep", DEEP), ("huge expires", b'{"expires": ' + BIG + b"}"),
             ("nan expires", b'{"expires": NaN, "ttl": 10}'), ("infinite expires", b'{"expires": Infinity}'),
             ("far expires", json.dumps(lease | {"expires": 1e300}).encode())]
    for i, (label, raw) in enumerate(cases):
        backend = LocalBackend(tmp_path / str(i))
        path = backend._path("k")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        os.utime(path, (now - 1000, now - 1000))
        leases = Leases(backend, "me", ttl=10, wait=0)
        assert isinstance(leases._holder_and_host("k"), str), label
        assert leases.try_acquire("k", now) is not None, label
        assert leases.holder("k") == "me", label


def test_a_lease_whose_body_gives_itself_a_very_long_length_runs_out_after_a_day(tmp_path):
    now = time.time()
    raw = json.dumps(GOOD_LEASE | {"ttl": 1e15, "expires": now + 1e15}).encode()
    backend = LocalBackend(tmp_path / "local")
    path = backend._path("k")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    os.utime(path, (now - 1000, now - 1000))
    assert Leases(backend, "me", ttl=10, wait=0).try_acquire("k", now) is not None  # locally
    leases = Leases(backend, "me", ttl=10, wait=0)
    day = 24 * 3600
    assert leases._expired(Stored(raw, "e", modified=now - day - 5, store_now=now))  # on OneLake's clock
    assert not leases._expired(Stored(raw, "e", modified=now - day + 60, store_now=now))
    normal = json.dumps(GOOD_LEASE | {"ttl": 300, "expires": now + 300}).encode()
    assert not leases._expired(Stored(normal, "e", modified=now - 200, store_now=now))  # a live lease stands


def test_a_live_lease_is_still_not_taken_over(tmp_path):
    backend = LocalBackend(tmp_path)
    first, second = Leases(backend, "first", ttl=30, wait=0), Leases(backend, "second", ttl=30, wait=0)
    assert first.try_acquire("k") is not None
    assert second.try_acquire("k") is None


# ---- notebook versions

NB = {"cells": [{"cell_type": "code", "source": "x = 1"}], "metadata": {}}
BAD_ID = "20270115080000-deadbeef"


def _two_versions(root):
    notebook_versions.record(root, "N", NB, now=NOW)
    notebook_versions.record(root, "N", NB, "checkpoint", "keep", now=NOW + 100)
    return root / "N"


@pytest.mark.parametrize("seed", [1, 2])
def test_a_damaged_version_file_is_skipped_and_the_good_ones_still_list_and_save(tmp_path, seed):
    valid = {"id": BAD_ID, "t": NOW + 50, "at": "2027-01-15T08:00:50Z", "kind": "auto", "label": "",
             "hash": "h", "nb": {"cells": [{"cell_type": "code", "source": "y"}]}}
    cases = [*mutations(valid, seed, CASES), ("deep", DEEP),
             ("cells number", json.dumps(valid | {"nb": {"cells": 5}}).encode()),
             ("cells text", json.dumps(valid | {"nb": {"cells": "ab"}}).encode()),
             ("cells of numbers", json.dumps(valid | {"nb": {"cells": [1]}}).encode()),
             ("t infinite", json.dumps(valid | {"t": float("inf")}).encode()),
             ("t past year 3000", json.dumps(valid | {"t": 1e20}).encode())]
    for i, (label, raw) in enumerate(cases):
        root = tmp_path / str(i)
        folder = _two_versions(root)
        (folder / f"{BAD_ID}.json").write_bytes(raw)
        (folder / "index.json").unlink()
        ids = {v["id"] for v in notebook_versions.listing(root, "N", NB)["versions"]}
        assert len(ids) in (2, 3), label
        later = {"cells": [{"cell_type": "code", "source": "z = 3"}]}
        assert notebook_versions.record(root, "N", later, now=NOW + 900) is not None, label
        # The good versions stay; the mutated one, when it still reads as a version, may be pruned by age.
        assert ids - {BAD_ID} <= {v["id"] for v in notebook_versions.listing(root, "N", NB)["versions"]}, label
        if BAD_ID not in ids:
            assert (folder / f"{BAD_ID}.json").read_bytes() == raw, label  # an unreadable one is left as it was


@pytest.mark.parametrize("seed", [1, 2])
def test_a_damaged_index_is_rebuilt_from_the_version_files(tmp_path, seed):
    root = tmp_path / "base"
    folder = _two_versions(root)
    valid = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    bad_t = json.loads(json.dumps(valid))
    bad_t["versions"][0]["t"] = float("inf")
    cases = [*mutations(valid, seed, CASES), ("deep", b'{"versions": ' + DEEP + b"}"),
             ("t infinite", json.dumps(bad_t).encode())]
    for label, raw in cases:
        (folder / "index.json").write_bytes(raw)
        assert len(notebook_versions.listing(root, "N", NB)["versions"]) == 2, label
    (folder / "index.json").write_bytes(json.dumps(bad_t).encode())
    assert notebook_versions.record(root, "N", {"cells": []}, now=NOW + 900) is not None


def test_a_folder_where_a_version_file_goes_is_skipped(tmp_path):
    folder = _two_versions(tmp_path)
    (folder / f"{BAD_ID}.json").mkdir()
    assert len(notebook_versions.listing(tmp_path, "N", NB)["versions"]) == 2
    with pytest.raises(ValueError, match="That version isn't kept any more."):
        notebook_versions.get(tmp_path, "N", BAD_ID)


# ---- the Optimize outbox

def _outbox(tmp_path):
    return Outbox(tmp_path / "outbox.db", clock=lambda: NOW)


def _damage(path, payload):
    with sqlite3.connect(path) as db:
        db.execute("update outbox set payload = ? where rowid = (select min(rowid) from outbox)", (payload,))
    db.close()


@pytest.mark.parametrize("kind,first,later", [
    ("analysis", {"run": {"run_id": "r1"}}, {"run": {"run_id": "r2"}}),
    ("local_executions", {"rows": [{"execution_id": "e1"}]}, {"rows": [{"execution_id": "e2"}]})])
@pytest.mark.parametrize("seed", [1])
def test_a_damaged_outbox_item_never_stops_new_results_being_kept_or_sent(tmp_path, kind, first, later, seed):
    cases = [*mutations(first, seed, 60), ("deep", DEEP), ("not text", b"\xff\xfe")]
    for i, (label, raw) in enumerate(cases):
        folder = tmp_path / str(i)
        folder.mkdir()
        box = _outbox(folder)
        box.add("L", kind, first)
        try:
            text: object = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw
        _damage(folder / "outbox.db", text)
        assert box.add("L", kind, later), label  # the new result waits, never lost
        box.waiting_execution_ids("L")
        due = box.due("L", include_waiting=True)
        assert later in [d["payload"] for d in due], label
        assert all(isinstance(d["payload"], dict) for d in due), label
        status = box.status("L")
        assert status["pending"] + status["dropped"] == 2, label  # an unreadable one is counted, never silent


# ---- the access cache's view (rows from its own tables)

def test_the_access_view_reads_back_any_rows_a_refresh_wrote():
    import polars as pl
    rnd = random.Random(7)
    names = ["", "é", chr(0x202E), "x" * 300, "Ws 1", "#EXT#", None]
    for _ in range(40):
        run = access_cache.Refresh({}, f"2027-01-{rnd.randint(10, 28)}T00:00:00Z", "pc")
        for w in range(rnd.randint(1, 3)):
            ws = {"id": f"w{w}", "name": rnd.choice(names)}
            rows = access_cache.workspace_rows(ws["id"], [
                {"principal": {"id": f"p{j}", "type": rnd.choice(["User", "Group", "App", None]),
                               "displayName": rnd.choice(names)}, "role": rnd.choice(["Admin", None])}
                for j in range(rnd.randint(0, 3))])
            run.add(access_cache.WORKSPACE, ws, None, rnd.choice([rows, None]), problem=rnd.choice([None, "denied"]))
            lake = access_cache.lakehouse_rows(ws["id"], "i1", [{"name": rnd.choice(["R", "", "é"])}])
            run.add(access_cache.LAKEHOUSE, ws, {"id": "i1", "name": rnd.choice(names)},
                    rnd.choice([lake, None]), gone=rnd.random() < 0.2)
        frames = [pl.DataFrame(run.scans) if run.scans else None]
        frames += [pl.DataFrame(run.rows[k]) if run.rows[k] else None for k in (access_cache.WORKSPACE, access_cache.LAKEHOUSE)]
        out = access_cache.view(*frames, {"w0", "w1"})
        assert {w["workspace_id"] for w in out} <= {"w0", "w1"}


def test_an_access_entry_that_cant_be_read_is_shown_as_not_known_and_the_others_still_show(caplog):
    import polars as pl
    run = access_cache.Refresh({}, "2027-01-20T00:00:00Z", "pc")
    for w in ("w0", "w1"):
        ws = {"id": w, "name": w.upper()}
        people = access_cache.workspace_rows(w, [{"principal": {"id": "p", "type": "User", "displayName": "P"},
                                                  "role": "Admin"}])
        run.add(access_cache.WORKSPACE, ws, None, people)
        run.add(access_cache.LAKEHOUSE, ws, {"id": "i1", "name": "L"},
                access_cache.lakehouse_rows(w, "i1", [{"name": "R"}]))
    lakes = [dict(r) for r in run.rows[access_cache.LAKEHOUSE]]
    for r in lakes:
        if r["workspace_id"] == "w0":
            r["paths"] = "[not json"  # written by something else into the central lakehouse
    scans = [dict(s) for s in run.scans]
    for s in scans:
        if s["workspace_id"] == "w1" and s["kind"] == access_cache.WORKSPACE:
            s["observed_at"] = None
    frames = [pl.DataFrame(scans), pl.DataFrame(run.rows[access_cache.WORKSPACE]), pl.DataFrame(lakes)]
    with caplog.at_level("WARNING"):
        out = {w["workspace_id"]: w for w in access_cache.view(*frames, {"w0", "w1"})}
    bad_lake = out["w0"]["lakehouses"][0]
    assert (bad_lake["problem"], bad_lake["known"], bad_lake["roles"]) == (access_cache.UNREADABLE, False, [])
    assert [p["id"] for p in out["w0"]["people"]] == ["p"] and out["w0"]["known"]  # its workspace still shows
    assert (out["w1"]["problem"], out["w1"]["known"], out["w1"]["people"]) == (access_cache.UNREADABLE, False, [])
    assert out["w1"]["lakehouses"][0]["known"] and out["w1"]["lakehouses"][0]["roles"]
    assert sum("can't be read" in r.message for r in caplog.records) == 2
