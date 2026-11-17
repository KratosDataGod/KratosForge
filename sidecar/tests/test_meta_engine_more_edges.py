"""A metadata project's run engine (meta/engine.py): more of the paths the main tests don't reach. Text cast to
a time, a landing zone without a lakehouse, reading an adopted table, and the note a run leaves while it's
under way: a note that can't be written or removed, and notes of earlier runs that are malformed or can't be
checked. The engine is built bare with stand-ins for its store, so nothing is run; all on local disk."""

from __future__ import annotations

import datetime as dt
import json
import socket
from types import SimpleNamespace

import polars as pl
import pytest
from deltalake import write_deltalake

from fabric_desktop import jsonfile
from fabric_desktop.meta import engine as eng
from fabric_desktop.meta.engine import CheckStop


def bare(**attrs):
    """An Engine with only the attributes a test's method reads."""
    e = object.__new__(eng.Engine)
    for k, v in attrs.items():
        setattr(e, k, v)
    return e


def test_text_becomes_a_utc_time_and_text_that_isnt_one_becomes_empty():
    df = pl.DataFrame({"at": [" 2026-11-11T08:30:00Z ", "not a time", None]})
    out = df.select(eng._cast(df, "at", pl.Datetime("us", "UTC")).alias("at"))["at"].to_list()
    assert out == [dt.datetime(2026, 11, 11, 8, 30, tzinfo=dt.timezone.utc), None, None]


def test_a_landing_zone_with_no_lakehouse_stops_the_entity_with_why():
    def landing_lakehouse(zone):
        raise LookupError("The zone CRM has no landing lakehouse in Prod yet")

    store = SimpleNamespace(env="prod", landing_zone=lambda source: {"id": "z1", "name": "CRM"},
                            landing_lakehouse=landing_lakehouse)
    with pytest.raises(CheckStop, match="^The zone CRM has no landing lakehouse in Prod yet$"):
        bare(store=store).landing_lakehouse("crm/accounts/*.csv")


def test_an_adopted_local_table_is_read_as_it_is_without_storage_options(tmp_path):
    uri = str(tmp_path / "adopted")
    write_deltalake(uri, pl.DataFrame({"id": [1, 2]}).to_arrow())
    asked = []
    e = bare(source_options=lambda: asked.append("options") or {})
    assert e.read_source_table(uri)["id"].to_list() == [1, 2]
    assert asked == []  # a local folder needs no sign-in
    assert e._uri_options("abfss://ws@onelake/lh/Tables/t") == {} and asked == ["options"]


def entity(name="silver.orders"):
    return SimpleNamespace(name=name, schema="silver", table="orders", layer="silver")


def marking(tmp_path, running_dir):
    return bare(running_dir=running_dir, run_id="r-20261111-120000-0123456789ab", run_context={"host": "app"},
                load_date=dt.date(2026, 11, 11), _running=None)


def test_with_nowhere_to_keep_notes_a_run_leaves_none(tmp_path):
    e = marking(tmp_path, None)
    e._mark_running(entity(), "2026-11-11T12:00:00+00:00", None, "b1")
    assert e._running is None
    e._unmark_running()  # nothing to remove


def test_a_note_that_cant_be_written_is_logged_and_the_run_goes_on(tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(eng._log, "warning", lambda *a: warned.append(a))

    def full(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(jsonfile, "write_text", full)
    e = marking(tmp_path, tmp_path / "running")
    e._mark_running(entity(), "2026-11-11T12:00:00+00:00", "2026-11-01", "b1")
    assert e._running is None
    assert warned == [("Couldn't note that run %s is under way: %s", e.run_id, warned[0][2])]
    assert str(warned[0][2]) == "disk full"


def test_a_note_is_written_with_the_run_s_context_and_one_that_cant_be_removed_is_logged(tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(eng._log, "warning", lambda *a: warned.append(a))
    (tmp_path / "running").mkdir()
    e = marking(tmp_path, tmp_path / "running")
    e._mark_running(entity(), "2026-11-11T12:00:00+00:00", None, "b1")
    note = json.loads(e._running.read_text(encoding="utf-8"))
    assert note["entity"] == "silver.orders" and note["batch_id"] == "b1" and note["backfill"] == ""
    assert note["host"] == "app" and note["pc"] == socket.gethostname() and note["load_date"] == "2026-11-11"

    class Stuck:
        stem = e.run_id

        def unlink(self, missing_ok):
            raise PermissionError("in use")

    e._running = Stuck()
    e._unmark_running()
    assert e._running is None
    assert warned[0][0] == "Couldn't remove the note for run %s: %s" and warned[0][1] == e.run_id


def stopped_engine(tmp_path, store):
    running = tmp_path / "running"
    running.mkdir(exist_ok=True)
    project = SimpleNamespace(layer=lambda layer: SimpleNamespace(lakehouse="LH_Silver"))
    return bare(running_dir=running, run_id="r-20261111-130000-aaaaaaaaaaaa", store=store, project=project)


def note(tmp_path, file, **fields):
    path = tmp_path / "running" / file
    path.write_text(json.dumps({"entity": "silver.orders", "pc": socket.gethostname(), "pid": -1} | fields),
                    encoding="utf-8")
    return path


def test_a_note_of_an_ended_run_with_a_malformed_id_is_removed_without_a_record(tmp_path, monkeypatch):
    from fabric_desktop import schedule_service
    monkeypatch.setattr(schedule_service, "_pid_alive", lambda pid, identity: False)
    e = stopped_engine(tmp_path, store=None)  # the store is never read for it
    path = note(tmp_path, "r-bad.json", run_id="r-not-a-run-id")
    res = SimpleNamespace(steps=[])
    e._record_stopped(entity(), res)
    assert not path.exists() and res.steps == []


def test_a_note_that_cant_be_checked_is_left_for_the_next_run_and_logged(tmp_path, monkeypatch):
    from fabric_desktop import schedule_service
    monkeypatch.setattr(schedule_service, "_pid_alive", lambda pid, identity: False)
    warned = []
    monkeypatch.setattr(eng._log, "warning", lambda *a: warned.append(a))

    def exists(*args):
        raise OSError("OneLake didn't answer")

    e = stopped_engine(tmp_path, store=SimpleNamespace(exists=exists))
    path = note(tmp_path, "r-20261111-110000-bbbbbbbbbbbb.json", run_id="r-20261111-110000-bbbbbbbbbbbb")
    res = SimpleNamespace(steps=[])
    e._record_stopped(entity(), res)
    assert path.exists() and res.steps == []
    assert warned[0][0] == "Couldn't check %s for a run that stopped: %s" and warned[0][1] == path.name
    assert "OneLake didn't answer" in str(warned[0][2])
