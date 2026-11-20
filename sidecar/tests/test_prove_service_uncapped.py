"""Mutation testing batch 177 (docs/quality/mutation-testing.md): proving a notebook and replaying a
failure (prove_service.py), every mutant. Each test pins a behavior a surviving mutant changed without
any other test noticing. Most use a bare ProveMixin with only the parts a call needs. Some open and
check bundles with test_replay's service, and one replays in the notebook's own process. The rest run
a proof against the fake Fabric of test_prove, which runs the published notebook in another process.
Nothing reaches a tenant."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from fabric_desktop import depgraph, envprofile, known_folders, prove_service, receipts, replay, verify
from fabric_desktop.prove_service import ProveMixin

from test_prove import CELLS, FakeFabric, call, payload, svc, wait  # noqa: F401 - the fixture is used by name


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """App data and Downloads go to this test's folder, never the real ones."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(known_folders, "downloads", lambda: tmp_path / "Downloads")


class Bare(ProveMixin):
    def __init__(self, root: Path):
        self.root = root


@pytest.fixture
def bare(tmp_path):
    return Bare(tmp_path / "app")


def test_a_proof_asks_fabric_every_5_seconds():
    assert prove_service.POLL_SECONDS == 5


def test_a_replay_bundle_is_at_most_100_mib():
    assert ProveMixin.MAX_BUNDLE_BYTES == 100 * 1024 * 1024


def test_the_dependencies_redactions_and_receipts_come_from_their_modules(bare, monkeypatch):
    cells = [{"type": "code", "source": "a = 1"}, {"type": "code", "source": "print(a)"}]
    assert bare.notebook_dependencies(cells) == depgraph.graph(cells)
    assert bare.receipt_redactions() == dict(receipts.REDACTIONS)
    asked = []
    monkeypatch.setattr(receipts.ReceiptStore, "list", lambda self, name, limit: asked.append((name, limit)) or ["r"])
    assert bare.receipts_list() == ["r"] and bare.receipts_list("nb", 3) == ["r"]
    assert asked == [(None, 50), ("nb", 3)]


def test_fabrics_packages_are_remembered_by_module_or_distribution(tmp_path, monkeypatch):
    bare = Bare(tmp_path / "not" / "made" / "yet")  # its folder and the folders above it are made
    monkeypatch.setattr(envprofile, "_distribution", lambda m: ("PyYAML", "6.0") if m == "yaml" else (None, None))
    monkeypatch.setattr(prove_service, "_now", lambda: "T")
    # Fabric reported the module's own name, not the distribution's.
    bare._remember_fabric_environment({"packages": {"yaml": "6.0.1", "pandas": "2.2"}, "python": "3.11"}, ["yaml", "pandas"])
    bare._remember_fabric_environment({"packages": {"pyyaml": "6.0.2"}, "python": "3.11"}, ["yaml"])  # and again
    seen = {"pandas": {"python": "3.11", "seen_at": "T", "version": "2.2"},
            "yaml": {"python": "3.11", "seen_at": "T", "version": "6.0.2"}}
    assert bare._observed_file.read_text(encoding="utf-8") == json.dumps(seen, indent=1, sort_keys=True)


def _export(monkeypatch, bare):
    monkeypatch.setattr(Bare, "receipt_export", lambda self, id, format="html", hide=None: {"format": "html", "text": "x"})


def test_a_saved_receipt_says_what_it_left_out(bare, monkeypatch):
    _export(monkeypatch, bare)
    assert bare.receipt_save("abc")["hidden"] == []
    assert bare.receipt_save("abc", hide=["code"])["hidden"] == ["code"]


def test_a_receipt_gives_up_after_999_taken_names(bare, monkeypatch, tmp_path):
    _export(monkeypatch, bare)
    tried = []

    def taken(path, mode, encoding=None):
        tried.append(Path(path).name)
        raise FileExistsError(path)

    monkeypatch.setattr(prove_service, "open", taken, raising=False)
    with pytest.raises(ValueError, match="free file name"):
        bare.receipt_save("abcdef123456789")
    assert len(tried) == 999 and tried[-1] == "receipt-abcdef123456 (999).html"


def test_a_bundle_is_named_from_60_characters_and_gives_up_after_99_taken_names(bare, monkeypatch):
    monkeypatch.setattr(Bare, "_replay_bundle", lambda self, *a: {"cells": []})
    tried = []

    def taken(path, bundle, samples, mode):
        tried.append(Path(path).name)
        raise FileExistsError(path)

    monkeypatch.setattr(replay, "write", taken)
    with pytest.raises(ValueError, match="free file name"):
        bare.replay_bundle_save("n" * 80, [], 0)
    assert len(tried) == 99
    assert tried[0].startswith("replay-" + "n" * 60 + "-") and not tried[0].startswith("replay-" + "n" * 61)
    assert tried[-1].endswith("-99.zip")


def test_bundles_in_downloads_are_listed_newest_first_50_at_most(bare, monkeypatch, tmp_path):
    import os

    folder = tmp_path / "Downloads"
    folder.mkdir()
    for i in range(52):
        p = folder / f"replay-{i:02d}.zip"
        p.write_bytes(b"z")
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    monkeypatch.setattr(replay, "read", lambda p: {"notebook": "nb", "samples": [1, 2]} if p.name.endswith("51.zip")
                        else {"notebook": "nb"})
    rows = bare.replay_bundles_list()
    assert len(rows) == 50
    assert [r["file"] for r in rows[:2]] == ["replay-51.zip", "replay-50.zip"] and rows[-1]["file"] == "replay-02.zip"
    assert rows[0]["samples"] == 2 and rows[1]["samples"] == 0


def test_a_bundle_of_exactly_the_limit_isnt_too_large(bare, monkeypatch):
    import base64

    monkeypatch.setattr(Bare, "MAX_BUNDLE_BYTES", 10)
    with pytest.raises(ValueError) as err:
        bare.replay_bundle_open(content=base64.b64encode(b"x" * 10).decode())
    assert "too large" not in str(err.value)
    with pytest.raises(ValueError, match="too large"):
        bare.replay_bundle_open(content=base64.b64encode(b"x" * 11).decode())


def test_a_replay_copy_thats_gone_names_the_bundles_notebook(bare):
    with pytest.raises(LookupError, match=r"Open the bundle again \(events\)"):
        bare._replay_copy({"bundle": "gone", "notebook": "events"}, "events replay")
    with pytest.raises(LookupError, match=r"Open the bundle again \(events replay\)"):
        bare._replay_copy({"bundle": "gone"}, "events replay")


def test_the_live_replay_is_looked_up_without_making_a_session(bare):
    asked = []
    bare.sessions = types.SimpleNamespace(session=lambda name, create=True: asked.append((name, create)))
    bare._replay_session = {"name": "nb", "pid": 1}
    assert bare._replay_live() is None
    assert asked == [("nb", False)]


def test_a_notebook_whose_extras_cant_be_read_isnt_a_replay(bare):
    def broken(name, key):
        raise OSError("unreadable")

    bare.notebooks = types.SimpleNamespace(extra=broken)
    assert bare._is_replay("nb") is False


def _saved(bare, rows):
    bare._proofs_file.parent.mkdir(parents=True, exist_ok=True)
    bare._proofs_file.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_a_saved_proof_keeps_its_pc_side_without_its_cells_and_the_last_200(bare, monkeypatch):
    monkeypatch.setattr(Bare, "_proof_state", lambda self: {})
    _saved(bare, [{"id": f"p{i}", "pc": {}} for i in range(200)])
    bare._proof_save({"id": "new", "_cancel": False, "pc": {"status": "ok", "cells": {"0": {}}}})
    rows = bare._proofs_saved()
    assert len(rows) == 200 and rows[0]["id"] == "p1"
    assert rows[-1] == {"id": "new", "pc": {"status": "ok"}}


def test_a_saved_proof_is_found_by_its_id(bare, monkeypatch):
    monkeypatch.setattr(Bare, "_proof_state", lambda self: {})
    _saved(bare, [{"id": "a", "name": "x", "pc": {}}, {"id": "b", "name": "y", "pc": {}}])
    assert bare.prove_status("b")["name"] == "y"


def test_proofs_are_listed_newest_first_20_at_most(bare, monkeypatch):
    monkeypatch.setattr(Bare, "_proof_state", lambda self: {})
    rows = [{"id": f"p{i:02d}", "name": "nb", "started_at": f"2026-01-{i + 1:02d}", "pc": {}} for i in range(22)]
    rows.append({"id": "undated", "name": "nb", "started_at": None, "pc": {}})
    _saved(bare, rows)
    listed = bare.prove_list()
    assert len(listed) == 20 and [r["id"] for r in listed[:2]] == ["p21", "p20"]
    assert [r["id"] for r in bare.prove_list(limit=30)][-1] == "undated"


def test_cancelling_a_proof_cancels_the_fabric_job_only_while_fabric_runs(bare, monkeypatch):
    cancelled = []
    bare.fabric_jobs = types.SimpleNamespace(cancel_job=lambda *a: cancelled.append(a))
    record = {"id": "p", "state": "verifying", "_cancel": False,
              "fabric": {"job_id": "j", "workspace_id": "w", "item_id": "i"}}
    monkeypatch.setattr(Bare, "_proof_state", lambda self: {"p": record})
    assert bare.prove_cancel("p") == {"cancelled": True}
    assert cancelled == []


def _finished(bare, monkeypatch, tables):
    record = {"id": "p", "name": "nb", "state": "done", "lakehouse": {}, "receipt_id": "r0", "code": "c",
              "tables": tables, "pc": {}}
    monkeypatch.setattr(Bare, "_proof_state", lambda self: {"p": record})
    monkeypatch.setattr(Bare, "_proof_location", lambda self, ref: ("root", None), raising=False)
    monkeypatch.setattr(Bare, "_proof_output", lambda self, root, options, id, side, table: f"{side}:{table}", raising=False)
    asked = []

    def compare(pc, fabric, rules, offset):
        asked.append((pc, fabric, offset))
        return {"result": "match", "level": "complete"}

    monkeypatch.setattr(verify, "compare", compare)
    return asked


def test_verifying_a_table_again_reads_that_table_from_the_first_differing_row(bare, monkeypatch):
    asked = _finished(bare, monkeypatch, [{"table": "dbo.a", "pc_written": True, "fabric_written": True},
                                          {"table": "dbo.b", "pc_written": True, "fabric_written": False}])
    assert bare.prove_verify("p", "dbo.a")["table"] == "dbo.a"
    assert asked == [("pc:dbo.a", "fabric:dbo.a", 0)]
    with pytest.raises(LookupError, match="dbo.b wasn't written by both sides"):
        bare.prove_verify("p", "dbo.b")


def test_a_saved_verification_has_no_limitations_when_the_compare_gave_none(bare, monkeypatch):
    _finished(bare, monkeypatch, [{"table": "dbo.a", "pc_written": True, "fabric_written": True}])
    kept = []
    monkeypatch.setattr(receipts, "new_receipt", lambda kind, **kw: kept.append(kw) or {})
    monkeypatch.setattr(receipts.ReceiptStore, "put", lambda self, body: "r1")
    assert bare.prove_verify("p", "dbo.a", save=True)["receipt_id"] == "r1"
    assert kept[0]["limitations"] == []


def test_a_proof_where_neither_side_wrote_a_table_compares_only_the_time():
    record = {"pc": {"status": "ok"}, "fabric": {"status": "ok"}}
    assert prove_service._verdict(record, [], []) == {
        "verdict": "nothing", "text": "Neither run wrote a table, so only the time is compared.", "problems": []}


def test_a_snapshot_of_a_busy_record_is_tried_20_times(monkeypatch):
    tries = []

    def busy(*a, **kw):
        tries.append(1)
        raise RuntimeError("dictionary changed size during iteration")

    monkeypatch.setattr(prove_service, "json", types.SimpleNamespace(dumps=busy, loads=json.loads))
    monkeypatch.setattr(prove_service.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="The proof is busy"):
        prove_service._copy({"id": "p"})
    assert len(tries) == 20


def test_a_sandbox_that_cant_be_deleted_names_its_lakehouse(bare):
    box = {"id": "replay-20261120-030000-abcdef", "workspace_id": "11111111-1111-1111-1111-111111111111",
           "lakehouse_id": "22222222-2222-2222-2222-222222222222", "lakehouse": "LH_Bronze"}
    kept = {}
    bare.notebooks = types.SimpleNamespace(extra=lambda name, key: [box],
                                           set_extra=lambda name, key, value: kept.update({key: value}))
    bare._replay_session = None

    def refuse(ref, folder):
        raise OSError("locked")

    bare._proof_delete_folder = refuse
    out = bare.replay_delete_outputs("nb")
    assert out["deleted"] == [] and out["problems"][0].startswith(
        "Files/_fabric_desktop/proofs/replay-20261120-030000-abcdef in LH_Bronze: ")
    assert kept == {"replay_sandboxes": [box]}


# ---- opening and checking a bundle, with the service of test_replay (no notebook process needed)

def _bundle_at(tmp_path, change=None, notebook="events"):
    from test_replay import LH as REPLAY_LH, REPLAY_CELLS

    b = replay.build(notebook, REPLAY_CELLS, 2, {"tables": {}, "files": {}}, REPLAY_LH)
    if change:
        change(b)
    path = tmp_path / f"replay-{len(list(tmp_path.glob('replay-*.zip')))}.zip"
    replay.write(path, b, None)
    return path


def test_an_opened_bundles_notebook_is_named_from_60_characters(tmp_path, monkeypatch):
    from test_replay import service

    s = service(tmp_path, monkeypatch)
    nb = call(s, "replay_bundle_open", path=str(_bundle_at(tmp_path, notebook="n" * 80)))
    assert nb["name"] == "n" * 60 + " replay"


def test_a_bundle_without_ids_never_attaches_a_lakehouse_even_if_one_is_named(tmp_path, monkeypatch):
    from test_replay import service

    s = service(tmp_path, monkeypatch)

    def no_ids(b):  # a bundle that says it has no ids, though its lakehouse still carries one
        b["ids_included"] = False

    nb = call(s, "replay_bundle_open", path=str(_bundle_at(tmp_path, no_ids)))
    assert nb["lakehouse"] is None


def test_a_replay_check_names_the_bundles_lakehouse_and_compares_its_code_cells_environment(tmp_path, monkeypatch):
    from test_replay import service

    s = service(tmp_path, monkeypatch)
    installed = envprofile.runtime(["polars"])["packages"]["polars"]

    def env(b):
        b["cells"] = [{"type": "markdown", "source": "# Events"},
                      {"type": "code", "source": "import polars as pl\nraise ValueError('x')"}]
        b["failed_cell"] = 2
        b["environment"] = {"python": "0.0", "packages": {"polars": "0.0.1"}}

    name = call(s, "replay_bundle_open", path=str(_bundle_at(tmp_path, env)))["name"]
    check = call(s, "replay_check", name=name, lakehouse_ref=None)
    assert "Attach a lakehouse with Add data items. The bundle's tables are in LH_Bronze." in check["blockers"]
    rows = {r["what"]: r for r in check["environment"]}
    assert rows["Python"]["Bundle"] == "0.0"
    assert rows["polars"] == {"what": "polars", "Bundle": "0.0.1", "This PC": installed}


# ---- a whole proof against the fake Fabric of test_prove (the published notebook runs in another process)

class _Clock:
    """prove_service's time with a monotonic clock that reads 1000 once, then 1003.14159 for good."""

    def __init__(self):
        self.calls = 0

    def monotonic(self):
        self.calls += 1
        return 1000.0 if self.calls == 1 else 1003.14159

    strftime = staticmethod(__import__("time").strftime)
    sleep = staticmethod(__import__("time").sleep)


def test_a_proof_seeds_random_with_0_counts_its_cells_keeps_no_outputs_as_none_and_times_fabric(svc, monkeypatch):  # noqa: F811
    import random
    import re

    class Quick(FakeFabric):
        """A job that took an eighth of a second, less than its notebook's own run."""

        def job_instance(self, ws, item, job):
            return super().job_instance(ws, item, job) | {"startTimeUtc": "2026-10-16T10:00:00Z",
                                                          "endTimeUtc": "2026-10-16T10:00:00.125Z"}

    svc.fake = Quick(svc)
    monkeypatch.setattr(prove_service, "time", _Clock())
    cells = CELLS + [{"type": "code", "source": "import random\nimport time\nprint(random.random())\ntime.sleep(0.3)"},
                     # Silent for over 2 s: its polls while it runs bring no outputs (None), yet it ends with [].
                     {"type": "code", "source": "time.sleep(2.5)"}]
    out = wait(svc, call(svc, "prove_start", **payload(cells))["id"])
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", out["id"])
    assert out["pc"]["cell"] == 6 and out["pc"]["status"] == "ok"
    assert out["pc"]["cells"]["6"]["outputs"] == []
    assert str(random.Random(0).random()) in json.dumps(out["pc"]["cells"]["5"]["outputs"])
    fab = out["fabric"]
    assert fab["engine"] == "python"
    assert fab["elapsed"] == 3 and fab["total_seconds"] == 3.14
    assert fab["job_seconds"] == 0.12 and fab["run_seconds"] > 0.3 and fab["start_seconds"] == 0


def test_a_published_notebook_without_an_id_is_found_by_its_name(svc):  # noqa: F811
    class Nameless(FakeFabric):
        def save_notebook(self, ws, name, ipynb):
            super().save_notebook(ws, name, ipynb)
            return {"displayName": name}

        def list_notebooks(self, ws):
            return [{"id": "other", "displayName": "Another notebook"},
                    {"id": "nb-proof", "displayName": self.saved[-1][0]}]

    svc.fake = Nameless(svc)
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    assert out["fabric"]["item_id"] == "nb-proof" and "/synapsenotebooks/nb-proof?" in out["fabric"]["url"]


def test_cancel_during_the_fabric_run_ends_the_proof_cancelled(svc):  # noqa: F811
    class CancelWhileRunning(FakeFabric):
        def run_item_job(self, ws, item, job, execution_data=None):
            job_id = super().run_item_job(ws, item, job, execution_data)
            for id in list(svc._proof_state()):  # noqa: A001
                assert svc.prove_cancel(id) == {"cancelled": True}
            return job_id

    svc.fake = CancelWhileRunning(svc)
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    assert out["state"] == "cancelled" and out["verdict"] is None


def test_a_replay_seeds_random_with_0_and_remembers_its_lakehouses_name(tmp_path, monkeypatch):
    import random

    from test_replay import LH as REPLAY_LH
    from test_replay import bundle_file, finished, replay_service

    s, _ = replay_service(tmp_path, monkeypatch)
    try:
        name = call(s, "replay_bundle_open", path=str(bundle_file(tmp_path)))["name"]
        call(s, "replay_start", name=name, lakehouse_ref=REPLAY_LH)
        call(s, "start_cell", code="import random\nprint(random.random())", lakehouse_ref=REPLAY_LH, notebook=name)
        snap = finished(s.kernel)
        assert snap["status"] == "ok" and str(random.Random(0).random()) in json.dumps(snap["outputs"])
        call(s, "replay_end", name=name)
        assert [b["lakehouse"] for b in s.replay_sandboxes(name)] == ["LH_Bronze"]
    finally:
        s.kernel.close()
