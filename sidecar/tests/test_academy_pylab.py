"""ACD-011: python_lab and delta_lab exercises run the learner's script in the learner process, then
check what it left in its workspace; the reference passes, planted wrong answers fail in words, and the
exercise checker catches a Python exercise that can't be graded."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fabric_desktop.academy import appcontainer, exercises, pylab, registry, sandbox
from fabric_desktop.academy import workspace as ws

pytest.importorskip("polars")

ORDERS = "order_id,amount\n1,10\n2,20\n2,20\n3,\n"
SOLUTION = """import os
import polars as pl
df = pl.read_csv("orders.csv").filter(pl.col("amount").is_not_null()).unique("order_id", keep="first").sort("order_id")
os.makedirs("out", exist_ok=True)
df.write_parquet("out/orders.parquet")
print("wrote", df.height)
"""
WRONG = {
    "wrong-dupes.py": SOLUTION.replace('.unique("order_id", keep="first")', ""),
    "wrong-crash.py": SOLUTION.replace("print(", "raise ValueError('no amount column')\nprint("),
}
VALIDATION = [
    {"kind": "notebook_ran", "prints": "wrote"},
    {"kind": "artifact_present", "items": [{"path": "out/orders.parquet", "kind": "file", "min_bytes": 1}]},
    {"kind": "schema_matches", "path": "out/orders.parquet", "columns": [{"name": "order_id"}, {"name": "amount"}]},
    {"kind": "data_quality", "path": "out/orders.parquet",
     "rules": [{"rule": "unique", "columns": ["order_id"]}, {"rule": "not_null", "column": "amount"}]},
]


def _exercise(tmp_path: Path, validation=VALIDATION, solution=SOLUTION, wrong=WRONG):
    folder, grading = tmp_path / "ex", tmp_path / "grading"
    folder.mkdir()
    grading.mkdir()
    (folder / "orders.csv").write_text(ORDERS, encoding="utf-8")
    (folder / "answer.py").write_text("# load orders.csv, drop rows without an amount, one row per order\n")
    (grading / "solution.py").write_text(solution, encoding="utf-8")
    for name, text in wrong.items():
        (grading / name).write_text(text, encoding="utf-8")
    return folder, grading, {"id": "E90", "starter": "answer.py", "files": ["orders.csv"], "validation": validation}


def test_reference_passes_and_wrong_answers_fail_in_words(tmp_path):
    folder, grading, ex = _exercise(tmp_path)
    result = pylab.self_test(folder, grading, ex)
    assert result["solution"]["passed"], result["solution"]
    assert result["solution"]["run"]["runs_as"] in (sandbox.RUNS_AS, appcontainer.RUNS_AS)
    dupes, crash = result["wrong"]["wrong-dupes.py"], result["wrong"]["wrong-crash.py"]
    assert not dupes["passed"] and "order_id" in next(r for r in dupes["results"] if not r["passed"])["detail"]
    assert not crash["passed"] and [r["kind"] for r in crash["results"]] == ["notebook_ran"]
    assert "no amount column" in crash["results"][0]["detail"]


def _workspace(tmp_path: Path, answer: str) -> tuple[Path, Path]:
    folder, _, ex = _exercise(tmp_path)
    data_root = tmp_path / "data"
    work = ws.workspace_dir(data_root, "dp-700", "E90")
    work.mkdir(parents=True)
    pylab.prepare(work, folder, ex, answer)
    return data_root, work


def test_grading_material_never_reaches_the_workspace(tmp_path):
    data_root, work = _workspace(tmp_path, "print('hi')\n")
    assert sorted(p.name for p in work.iterdir()) == ["answer.py", "orders.csv"]
    res = pylab.grade(work, "answer.py", [{"kind": "notebook_ran", "prints": "hi"}], data_root)
    assert res["passed"], res


def test_notebook_ran_checks_what_was_printed(tmp_path):
    data_root, work = _workspace(tmp_path, "print('loaded 3 orders')\n")
    assert pylab.grade(work, "answer.py", [{"kind": "notebook_ran", "prints": "loaded 3"}], data_root)["passed"]
    res = pylab.grade(work, "answer.py", [{"kind": "notebook_ran", "prints": "loaded 4"}], data_root)
    assert res["results"] == [{"kind": "notebook_ran", "passed": False,
                               "detail": "Your script ran, but it didn't print 'loaded 4'."}]


def test_a_script_that_runs_too_long_is_stopped_and_said_so(tmp_path):
    data_root, work = _workspace(tmp_path, "while True:\n    pass\n")
    res = pylab.grade(work, "answer.py", VALIDATION, data_root, time_limit=3)
    assert not res["passed"] and res["run"]["status"] == "time_limit"
    assert res["results"] == [{"kind": "notebook_ran", "passed": False,
                               "detail": "Your script ran past its time limit and was stopped."}]


def test_a_table_file_that_isnt_parquet_fails_in_words(tmp_path):
    data_root, work = _workspace(tmp_path, "import os\nos.makedirs('out')\nopen('out/orders.parquet', 'w').write('nope')\n")
    res = pylab.grade(work, "answer.py", VALIDATION[2:3], data_root)
    assert not res["passed"] and "couldn't be read as parquet" in res["results"][0]["detail"]


def test_delta_lab_checks_the_table_log(tmp_path):
    pytest.importorskip("deltalake")
    answer = ("import deltalake, polars as pl\n"
              "df = pl.read_csv('orders.csv')\n"
              "deltalake.write_deltalake('tables/orders', df.to_arrow())\n"
              "deltalake.write_deltalake('tables/orders', df.to_arrow(), mode='overwrite')\n")
    data_root, work = _workspace(tmp_path, answer)
    check = {"kind": "delta_table", "path": "tables/orders", "versions_min": 2, "columns": ["order_id", "amount"]}
    res = pylab.grade(work, "answer.py", [{"kind": "notebook_ran"}, check], data_root)
    assert res["passed"], res
    res = pylab.grade(work, "answer.py", [{**check, "versions_min": 5}], data_root)
    assert not res["passed"]


@pytest.mark.parametrize(("check", "message"), [
    ({"kind": "data_quality", "path": "out/x.parquet"}, "needs rules"),
    ({"kind": "schema_matches", "path": "x", "columns": [{"name": "a"}], "format": "xlsx"}, "format is one of"),
    ({"kind": "delta_table"}, "needs path"),
])
def test_check_fields_are_checked(check, message):
    assert any(message in p for p in pylab.check_problems(check))


# --- wired into the exercise format (exercises.py) -------------------------------------------------

PACK = "dp-700"


@pytest.fixture
def python_pack(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    folder, grading = root / PACK / "exercises" / "E36", root / PACK / "grading" / "E36"
    shutil.rmtree(grading)
    grading.mkdir()
    for p in folder.iterdir():
        if p.suffix == ".sql":
            p.unlink()
    path = folder / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update({"adapter": "python_lab", "engine_label": "Python with Polars on this PC", "starter": "answer.py",
               "files": ["orders.csv"], "validation": VALIDATION})
    ex.pop("setup", None)
    path.write_text(json.dumps(ex), encoding="utf-8")
    (folder / "answer.py").write_text("# your load\n", encoding="utf-8")
    (folder / "orders.csv").write_text(ORDERS, encoding="utf-8")
    (grading / "solution.py").write_text(SOLUTION, encoding="utf-8")
    for name, text in WRONG.items():
        (grading / name).write_text(text, encoding="utf-8")
    return root


def test_a_python_exercise_is_well_formed_and_self_tests(python_pack):
    assert exercises.problems(python_pack) == []
    result = exercises.self_test(PACK, "E36", python_pack)
    assert result["solution"]["passed"], result["solution"]
    assert set(result["wrong"]) == set(WRONG) and not any(r["passed"] for r in result["wrong"].values())


@pytest.mark.parametrize(("change", "remove", "message"), [
    ({}, "grading/E36/solution.py", "reference solution at grading/E36/solution.py"),
    ({}, "grading/E36/wrong-dupes.py", "at least two planted wrong answers (grading/E36/wrong-*.py)"),
    ({"starter": "answer.sql"}, None, "starts from a Python script"),
    ({"validation": [{"kind": "result_matches"}]}, None, "python_lab can't check result_matches"),
    ({"validation": [{"kind": "delta_table"}]}, None, "check delta_table needs path"),
    ({"files": ["missing.csv"]}, None, "missing.csv is missing"),
    ({"files": ["../grading/E36/solution.py"]}, None, "isn't a plain file name"),
    ({"uses": "LAB-04/rebuild"}, None, "only for duckdb_lab"),
])
def test_a_python_exercise_that_cant_be_graded_is_caught(python_pack, change, remove, message):
    path = python_pack / PACK / "exercises" / "E36" / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update(change)
    path.write_text(json.dumps(ex), encoding="utf-8")
    if remove:
        (python_pack / PACK / remove).unlink()
    if change.get("starter") == "answer.sql":
        (python_pack / PACK / "exercises" / "E36" / "answer.sql").write_text("SELECT 1")
    assert any(message in p for p in exercises.problems(python_pack)), exercises.problems(python_pack)
