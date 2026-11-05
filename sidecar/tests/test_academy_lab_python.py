"""Python exercises through the Academy workspace (lab.py and the academy_* methods): Start, Save, Run,
Check, Reset. The script and state stay out of the learner process's reach, Check runs in a fresh copy
every time, and a pass is recorded with the help it had."""

from __future__ import annotations

import functools
import json
import shutil

import pytest

from fabric_desktop.academy import appcontainer, exercises, lab, registry, sandbox
from fabric_desktop.academy_service import AcademyMixin

pytest.importorskip("polars")

PACK, EID = "dp-700", "E36"
ORDERS = "order_id,amount\n1,10\n2,20\n2,20\n3,\n"
SOLUTION = """import os
import polars as pl
df = pl.read_csv("orders.csv").filter(pl.col("amount").is_not_null()).unique("order_id", keep="first").sort("order_id")
os.makedirs("out", exist_ok=True)
df.write_parquet("out/orders.parquet")
print("wrote", df.height)
"""
STARTER = "# load orders.csv into out/orders.parquet, one row per order\n"
VALIDATION = [
    {"kind": "notebook_ran", "prints": "wrote"},
    {"kind": "data_quality", "path": "out/orders.parquet", "fail": "Each order should appear once, with an amount.",
     "rules": [{"rule": "unique", "columns": ["order_id"]}, {"rule": "not_null", "column": "amount"}]},
]


class _Svc(AcademyMixin):
    def __init__(self, root):
        self.root = root
        self.rows = []

    def _progress_record(self, row):
        self.rows.append(row)
        return []

    def _profile_path(self):
        return self.root / "profile.json"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    packs = tmp_path / "packs"
    shutil.copytree(registry.ROOT, packs)
    folder = packs / PACK / "exercises" / EID
    for p in folder.iterdir():
        if p.suffix == ".sql":
            p.unlink()
    ex = json.loads((folder / "exercise.json").read_text(encoding="utf-8"))
    ex.update({"adapter": "python_lab", "engine_label": "Python with Polars on this PC", "starter": "answer.py",
               "files": ["orders.csv"], "validation": VALIDATION})
    ex.pop("setup", None)
    (folder / "exercise.json").write_text(json.dumps(ex), encoding="utf-8")
    (folder / "answer.py").write_text(STARTER, encoding="utf-8")
    (folder / "orders.csv").write_text(ORDERS, encoding="utf-8")
    monkeypatch.setattr(exercises, "get", functools.partial(exercises.get, root=packs))
    monkeypatch.setattr(exercises, "file_text", functools.partial(exercises.file_text, root=packs))
    monkeypatch.setattr(lab, "_exercise_folder", lambda pid, eid: packs / pid / "exercises" / eid)
    data = tmp_path / "data"
    data.mkdir()
    return _Svc(data)


def test_start_keeps_the_script_and_state_outside_the_learner_workspace(svc):
    st = svc.academy_start(PACK, EID)
    assert st["started"] and st["answer"] == STARTER
    folder = lab.workspace(svc.root, PACK, EID)
    assert sorted(p.name for p in folder.iterdir()) == ["answer.py", "run", "state.json"]
    assert sorted(p.name for p in (folder / "run").iterdir()) == ["answer.py", "orders.csv"]
    assert svc.academy_exercise(PACK, EID)["workspace"]["started"]


def test_run_shows_output_and_keeps_what_it_wrote_until_reset(svc):
    svc.academy_start(PACK, EID)
    got = svc.academy_run(PACK, EID, SOLUTION)
    assert got["status"] == "ok" and "wrote 2" in got["stdout"], got
    assert got["runs_as"] in (sandbox.RUNS_AS, appcontainer.RUNS_AS)   # the AppContainer where it can be used
    folder = lab.workspace(svc.root, PACK, EID)
    assert (folder / "run" / "out" / "orders.parquet").is_file()
    svc.academy_reset(PACK, EID)
    assert not (folder / "run" / "out").exists() and (folder / "run" / "orders.csv").is_file()


def test_the_learner_process_cant_change_its_own_state_or_saved_script(svc):
    svc.academy_start(PACK, EID)
    got = svc.academy_run(PACK, EID, "open('../state.json', 'w').write('{}')\n")
    assert got["status"] == "error" and "PermissionError" in got["stderr"]
    got = svc.academy_run(PACK, EID, "print(open('../answer.py').read())\n")
    assert got["status"] == "error"
    assert lab.state(svc.root, PACK, EID)["started"]


def test_check_runs_fresh_so_a_leftover_output_cant_pass(svc):
    svc.academy_start(PACK, EID)
    assert svc.academy_run(PACK, EID, SOLUTION)["status"] == "ok"   # leaves a correct out/orders.parquet in run/
    svc.academy_save(PACK, EID, "print('wrote nothing')\n")
    res = svc.academy_validate(PACK, EID)
    assert not res["passed"] and not svc.rows
    assert "isn't" in res["results"][1]["detail"] or "no" in res["results"][1]["detail"].lower()
    assert res["results"][1]["message"] == "Each order should appear once, with an amount."
    assert not (lab.workspace(svc.root, PACK, EID) / "check").exists()


def test_a_saved_solution_passes_and_is_recorded(svc):
    svc.academy_start(PACK, EID)
    svc.academy_hint(PACK, EID, 1)
    svc.academy_save(PACK, EID, SOLUTION)
    res = svc.academy_validate(PACK, EID)
    assert res["passed"], res
    assert res["verification"]["engine"] == "Python with Polars on this PC"
    assert res["hints_used"] == 1 and res["recorded"] and len(svc.rows) == 1
    assert lab.state(svc.root, PACK, EID)["last"]["passed"]


def test_a_linked_run_folder_is_refused(svc, tmp_path):
    svc.academy_start(PACK, EID)
    folder = lab.workspace(svc.root, PACK, EID)
    shutil.rmtree(folder / "run")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (folder / "run").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(Exception):
        svc.academy_run(PACK, EID, "print(1)\n")
    assert list(elsewhere.iterdir()) == []


def test_an_oversized_script_is_refused(svc):
    svc.academy_start(PACK, EID)
    with pytest.raises(ValueError, match="over 1 MB"):
        svc.academy_run(PACK, EID, "x = 1\n" * 300_000)
