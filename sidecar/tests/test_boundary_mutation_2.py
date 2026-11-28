"""Boundary mutants (`<` and `<=`, `>` and `>=` swapped) in modules earlier mutation batches covered
before the tool made them (docs/quality/mutation-testing.md, batch 37). Each test pins what happens
exactly at a limit, the one value where the two operators disagree."""

from __future__ import annotations

import datetime as dt
import sys
from types import SimpleNamespace

import pytest
import requests

from fabric_desktop import retrying as R
from fabric_desktop import leases as L
from fabric_desktop import maintenance, notebook, tmdl
from fabric_desktop import widgets as W
from fabric_desktop.localdata import discovery as D
from fabric_desktop.meta import mesh
from fabric_desktop.meta.templates import init_project


class _Resp:
    def __init__(self, status: int, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}


# --------------------------------------------------------------------------- retrying.py

def test_a_poll_at_exactly_its_deadline_looks_once_more():
    # timeout 10: the first look ends at t=10, exactly the deadline, which isn't past it yet
    times = iter([0.0, 10.0, 11.0])
    looks = []

    def fetch():
        looks.append(1)
        return "running", None

    with pytest.raises(R.PollTimedOut) as e:
        R.poll_until(fetch, lambda s: s == "done", timeout=10, headers=None, sleep=lambda s: None,
                     clock=lambda: next(times))
    assert e.value.last == "running" and len(looks) == 2


def test_a_wait_ending_exactly_at_the_deadline_is_not_started():
    sends, slept = [], []

    def send():
        sends.append(1)
        return _Resp(503, {"Retry-After": "2"})

    resp = R.send_with_retry(send, "GET", sleep=slept.append, deadline=2.0, clock=lambda: 0.0)
    assert resp.status_code == 503 and len(sends) == 1 and slept == []


def test_a_wait_ending_just_before_the_deadline_is_started():
    sends = []

    def send():
        sends.append(1)
        return _Resp(503, {"Retry-After": "2"}) if len(sends) == 1 else _Resp(200)

    assert R.send_with_retry(send, "GET", sleep=lambda s: None, deadline=2.001, clock=lambda: 0.0).status_code == 200


def test_a_remote_wait_is_slept_in_naps_with_no_empty_nap_at_the_end():
    slept = []
    answers = iter([_Resp(429, {"Retry-After": "0.5"}), _Resp(200)])
    resp, sent = R.send_remote(lambda: next(answers), repeatable=True, sleep=slept.append)
    assert (resp.status_code, sent) == (200, 2)
    assert slept == [0.25, 0.25]


def test_a_dropped_connection_is_tried_again_when_the_cap_left_exactly_fits_the_wait():
    calls = []

    def send():
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("dropped")
        return _Resp(200)

    # jitter 1.0 makes the first backoff exactly base_wait; the whole cap is that one wait
    resp, sent = R.send_remote(send, repeatable=True, sleep=lambda s: None, base_wait=1.0, total_cap=1.0,
                               jitter=lambda: 1.0)
    assert (resp.status_code, sent) == (200, 2)


# --------------------------------------------------------------------------- tmdl.py

def _bim():
    return {"name": "M", "model": {"tables": [{"name": "Sales", "columns": [{"name": "Key", "dataType": "int64"}],
                                                "measures": [{"name": "Total", "expression": "SUM(Sales[Key])"}]}]}}


def test_tmdl_of_exactly_the_size_limit_is_read(monkeypatch):
    files, _ = tmdl.from_bim(_bim())
    monkeypatch.setattr(tmdl, "MAX_CHARS", sum(len(t) for t in files.values()))
    assert tmdl.to_bim(files, "M")[0]["model"]["tables"][0]["name"] == "Sales"
    monkeypatch.setattr(tmdl, "MAX_CHARS", tmdl.MAX_CHARS - 1)
    with pytest.raises(ValueError, match="larger than 20 MB"):
        tmdl.to_bim(files, "M")


def test_a_model_writing_exactly_the_size_limit_is_written(monkeypatch):
    files, _ = tmdl.from_bim(_bim())
    size = sum(len(t) for t in files.values())
    monkeypatch.setattr(tmdl, "MAX_CHARS", size)
    assert tmdl.from_bim(_bim())[0] == files
    monkeypatch.setattr(tmdl, "MAX_CHARS", size - 1)
    with pytest.raises(ValueError, match="would be larger than 20 MB"):
        tmdl.from_bim(_bim())


def test_a_model_with_exactly_the_table_limit_is_written(monkeypatch):
    monkeypatch.setattr(tmdl, "MAX_TABLES", 1)
    assert "definition/tables/Sales.tmdl" in tmdl.from_bim(_bim())[0]
    monkeypatch.setattr(tmdl, "MAX_TABLES", 0)
    with pytest.raises(ValueError, match="1 tables"):
        tmdl.from_bim(_bim())


def test_a_fenced_expression_never_closed_runs_to_the_end_of_the_file():
    files = {"definition/model.tmdl": "model Model\n",
             "definition/tables/T.tmdl": "table T\n\tmeasure M = ```\n\t\t1 +\n\t\t2"}
    model, _ = tmdl.to_bim(files, "M")
    assert model["model"]["tables"][0]["measures"][0]["expression"] == "1 +\n2"


# --------------------------------------------------------------------------- maintenance.py

class _Entry:
    def __init__(self, mtime: float):
        self._mtime = mtime

    def stat(self, follow_symlinks: bool = True):
        return SimpleNamespace(st_mtime=self._mtime)


def test_a_file_exactly_at_the_age_limit_is_not_yet_older(monkeypatch):
    monkeypatch.setattr(maintenance.time, "time", lambda: 100_000.0)
    assert maintenance._older_than(_Entry(100_000.0 - 2 * 3600), 2) is False
    assert maintenance._older_than(_Entry(100_000.0 - 2 * 3600 - 0.5), 2) is True


def test_a_commit_made_exactly_when_the_stopped_run_started_counts_as_after(monkeypatch, tmp_path):
    class _Table:
        def __init__(self, uri):
            pass

        def history(self, n):
            return [{"timestamp": 5000}]

    monkeypatch.setattr(maintenance, "DeltaTable", _Table)
    out = maintenance.stopped_run_leftovers(str(tmp_path), since=5.0)
    assert out["files"] == [] and "after the stopped run started" in out["refused"]


# --------------------------------------------------------------------------- meta/mesh.py

TODAY = dt.date(2026, 11, 9)


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "project", "3", "Chris", True)


def _put(p, rel, text):
    path = p.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_two_days_in_a_row_are_listed_not_written_as_a_range():
    assert mesh._days_text(["mon", "tue"]) == "Mon, Tue"
    assert mesh._days_text(["mon", "tue", "wed"]) == "Mon to Wed"


def test_a_link_to_a_product_that_stops_today_blocks_the_promote(proj):
    _put(proj, "metadata/links/crm.toml", 'alias = "crm"\nproduct = "crm/customers"\nport = "main"\n')
    proj.reload()
    errors, notes = mesh.deprecated_links(
        proj, [{"id": "crm/customers", "status": "deprecated", "deprecated_on": TODAY.isoformat()}], TODAY)
    assert notes == [] and len(errors) == 1 and f"was deprecated on {TODAY.isoformat()}" in errors[0]


def test_a_product_deprecated_today_has_given_its_readers_notice(proj):
    _put(proj, "metadata/products/orders.toml",
         'name = "orders"\nstatus = "published"\nversion = 4\n[[port]]\nname = "main"\nentity = "silver.orders"\n')
    proj.reload()
    me = mesh.project_name(proj)
    published = [{"id": f"{me}/orders", "version": "3", "status": "deprecated", "deprecated_on": TODAY.isoformat(),
                  "ports": [{"port": "main", "env": "prod", "columns": {"ghost": "string"}}],
                  "consumers": [{"consumer_project": "finance", "alias": "orders", "port": "main",
                                 "reads": {"prod": "prod"}, "accepts": "2-3"}]}]
    errors, notes = mesh.breaking_changes(proj, published, "prod", TODAY)
    assert errors == [] and len(notes) == 1 and "had notice" in notes[0]


# --------------------------------------------------------------------------- widgets.py

@pytest.fixture
def no_kernel(monkeypatch):
    monkeypatch.setattr(W.registry, "show", None)
    monkeypatch.setattr(W.registry, "capture", None)


def test_a_slider_whose_min_equals_its_max_is_allowed(no_kernel):
    s = W.IntSlider(value=5, min=5, max=5)
    s.max = 5
    s.min = 5
    assert (s.min, s.max, s.value) == (5, 5, 5)
    with pytest.raises(W.TraitError):
        s.min = 6


def test_the_registry_keeps_exactly_max_widgets(no_kernel, monkeypatch):
    monkeypatch.setattr(W, "MAX_WIDGETS", 3)
    reg = W._Registry()
    ids = [reg.add(W.Label(value=str(i))) for i in range(3)]
    assert list(reg.widgets) == ids
    reg.add(W.Label(value="4"))
    assert ids[0] not in reg.widgets and len(reg.widgets) == 3


def test_printing_exactly_the_stream_cap_is_not_truncated(no_kernel, monkeypatch):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 5)
    out = W.Output()
    out._write("abcde")
    assert out.outputs == [{"type": "stream", "text": "abcde"}]
    out._write("f")
    assert out.outputs[-1]["text"].startswith("Output truncated")


def test_a_title_one_past_the_end_is_empty(no_kernel):
    tab = W.Tab([W.Label(value="a")], titles=["first"])
    assert tab.get_title(0) == "first" and tab.get_title(1) == ""


def test_an_index_one_past_the_options_is_refused_by_name(no_kernel):
    d = W.Dropdown(options=["a", "b"])
    with pytest.raises(ValueError, match="index 2 is out of range"):
        d.index = 2
    m = W.SelectMultiple(options=["a", "b"])
    with pytest.raises(ValueError, match="index 2 is out of range"):
        m.index = (0, 2)


def test_the_app_selecting_one_past_the_options_is_ignored(no_kernel):
    m = W.SelectMultiple(options=["a", "b"])
    m._event({"index": [0, 2]})
    assert m.value == ("a",)


def test_interact_on_zero_gives_a_slider_from_0_to_1(no_kernel):
    s = W._control("n", 0)
    assert (s.min, s.max, s.value) == (0, 1, 0)


# --------------------------------------------------------------------------- localdata/discovery.py

def _touch(path, text="a\n1\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_a_file_named_only_by_its_suffix_isnt_a_data_file():
    assert D.file_format(".csv") is None
    assert D.file_format("a.csv") == ("csv", "a")


def test_exactly_as_many_files_as_the_limit_is_not_reported(tmp_path):
    for n in range(2):
        _touch(tmp_path / f"t{n}.csv")
    out = D.discover(D.Source(str(tmp_path)), max_files=2)
    assert [t.table for t in out.tables] == ["t0", "t1"] and out.issues == []


# --------------------------------------------------------------------------- leases.py

def test_a_lease_at_exactly_its_expiry_is_not_yet_taken_over(tmp_path):
    backend = L.LocalBackend(tmp_path)
    assert L.Leases(backend, "first", ttl=120, clock=lambda: 1000.0).try_acquire("k")
    assert L.Leases(backend, "second", ttl=120, clock=lambda: 1120.0).try_acquire("k") is None
    assert L.Leases(backend, "second", ttl=120, clock=lambda: 1120.5).try_acquire("k")


class _Raising:
    def replace(self, key, data, etag):
        raise requests.ConnectionError("blip")


class _OneBeat:
    """A stop event whose first wait times out (one renewal beat) and whose second says stop."""
    def __init__(self):
        self.waits = 0

    def wait(self, timeout):
        self.waits += 1
        return self.waits > 1


def test_a_lease_unrenewed_for_exactly_its_length_counts_as_lost():
    leases = L.Leases(_Raising(), "me", ttl=120, clock=lambda: 120.0)
    held = L.Held(leases, "k", "etag", renewed=0.0)
    held._stop = _OneBeat()
    held._renew()
    assert held.lost is True


class _Clock:
    def __init__(self, *times):
        self.times = iter(times)
        self.slept = []

    def monotonic(self):
        return next(self.times)

    def sleep(self, s):
        self.slept.append(s)


def test_a_file_lock_gives_up_at_exactly_its_deadline_posix(monkeypatch, tmp_path):
    tries = []

    def flock(fd, op):
        tries.append(op)
        raise BlockingIOError

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=flock))
    monkeypatch.setattr(L, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(L, "time", _Clock(0.0, 1.0, 2.0))   # deadline 1.0; the first retry is at it exactly
    with pytest.raises(TimeoutError):
        with L._file_lock(tmp_path / "x.lock", timeout=1.0):
            pass
    assert len(tries) == 1


def test_a_file_lock_gives_up_at_exactly_its_deadline_windows(monkeypatch, tmp_path):
    tries = []

    def locking(fd, mode, n):
        tries.append(mode)
        raise OSError("held")

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0, locking=locking))
    monkeypatch.setattr(L, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(L, "time", _Clock(0.0, 1.0, 2.0))
    with pytest.raises(TimeoutError):
        with L._file_lock(tmp_path / "x.lock", timeout=1.0):
            pass
    assert len(tries) == 1
