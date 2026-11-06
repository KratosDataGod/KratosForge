"""notebookutils and Structured Streaming in Academy PySpark exercises (DP-700 E20, E26, E42): the
answer runs other notebooks of the exercise with parameters and exit values, streams files and Delta
tables with a checkpoint (availableNow), and MERGEs each batch with delta.tables.DeltaTable.

Most tests run the answer with the pack runner (run_reference, a ``python -I`` process with kf_learner);
the ones that matter for the sandbox run it in the Academy learner process (run_learner)."""

from __future__ import annotations

import deltalake
import pyarrow as pa
import pytest

from fabric_desktop import notebook_dag
from fabric_desktop.academy import exercises, learner_pkg, notebookutils_lab, pyspark_lab
from fabric_desktop.academy import workspace as ws

SETUP = ("CREATE SCHEMA bronze; CREATE TABLE bronze.orders AS SELECT * FROM (VALUES (1, 'east', 10), (2, 'west', 20), "
         "(3, 'east', 5)) t(id, region, amount)")
CHILD = b'''# Fabric notebook source

# PARAMETERS CELL ********************

region = "all"

# CELL ********************

from pyspark.sql import functions as F
df = spark.read.table("bronze.orders").where(F.col("region") == region)
df.write.mode("overwrite").saveAsTable(f"silver.orders_{region}")
notebookutils.notebook.exit(df.count())
'''
LANDING = {"Files/landing/day1.csv": b"id,amount\n1,10\n2,20\n"}
SCHEMA = ("from pyspark.sql.types import StructType, StructField, IntegerType, DoubleType\n"
          "schema = StructType([StructField('id', IntegerType()), StructField('amount', DoubleType())])\n")
LOAD_FILES = SCHEMA + """
(spark.readStream.schema(schema).option("header", "true").csv("Files/landing")
   .writeStream.option("checkpointLocation", "Files/checkpoints/landing").trigger(availableNow=True)
   .toTable("bronze.landed"))
"""
UPSERT = """
from delta.tables import DeltaTable
def upsert(batch, batch_id):
    if not DeltaTable.isDeltaTable(spark, "silver.orders"):
        batch.write.saveAsTable("silver.orders")
        return
    (DeltaTable.forName(spark, "silver.orders").alias("t").merge(batch.alias("s"), "t.id = s.id")
       .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())
(spark.readStream.table("bronze.orders").writeStream.foreachBatch(upsert)
   .option("checkpointLocation", "Files/checkpoints/orders").trigger(availableNow=True).start())
"""


def _work(tmp_path, assets=None, setup=SETUP):
    work = tmp_path / "data" / "academy" / "w"
    work.mkdir(parents=True)
    pyspark_lab.prepare(work, setup, assets)
    return work


def _ok(run):
    assert run["status"] == "ok", run["stderr"][-2000:]
    return run["stdout"]


def _append(work, name, rows):
    path = str(work / "tables" / name.replace(".", "/"))
    schema = pa.schema(deltalake.DeltaTable(path).schema().to_arrow())
    deltalake.write_deltalake(path, pa.Table.from_pylist(rows).cast(schema), mode="append")


# ---- notebookutils (E20)

def test_cells_split_at_fabric_markers_and_mark_the_parameters_cell():
    found = notebookutils_lab.cells(CHILD.decode())
    assert [p for p, _s, _c in found] == [False, True, False]
    assert 'region = "all"' in found[1][2] and found[2][1] == 8


def test_run_passes_arguments_after_the_parameters_cell_and_returns_the_exit_value(tmp_path):
    work = _work(tmp_path, {"notebooks/by_region.py": CHILD})
    out = _ok(pyspark_lab.run_reference(work, 'print(repr(notebookutils.notebook.run("by_region", 60, {"region": "east"})))'))
    assert out.strip() == "'2'"
    assert pyspark_lab._rows(work, "silver.orders_east")[1] == [(1, "east", 10), (3, "east", 5)]


def test_run_multiple_runs_the_dag_in_order_with_its_arguments(tmp_path):
    work = _work(tmp_path, {"notebooks/by_region.py": CHILD})
    answer = """
import notebookutils
dag = {"activities": [{"name": "east", "path": "by_region", "args": {"region": "east"}},
                      {"name": "west", "path": "by_region.py", "args": {"region": "west"}, "dependencies": ["east"]}]}
assert mssparkutils.notebook.validateDAG(dag)
print(sorted((k, v["exitVal"]) for k, v in notebookutils.notebook.runMultiple(dag).items()))
"""
    assert _ok(pyspark_lab.run_reference(work, answer)).strip() == "[('east', '2'), ('west', '1')]"


def test_a_failed_activity_skips_its_dependents_and_fails_the_answer(tmp_path):
    work = _work(tmp_path, {"notebooks/by_region.py": CHILD, "notebooks/broken.py": b"raise ValueError('bad input')\n"})
    answer = """
dag = {"activities": [{"name": "b", "path": "broken"}, {"name": "w", "path": "by_region", "dependencies": ["b"]}]}
notebookutils.notebook.runMultiple(dag)
"""
    run = pyspark_lab.run_reference(work, answer)
    assert run["status"] == "error" and "b: ValueError: bad input" in run["stderr"] and "Skipped: b failed" in run["stderr"]


def test_exit_in_the_answer_ends_it_without_an_error(tmp_path):
    work = _work(tmp_path)
    run = pyspark_lab.run_reference(work, "print('before')\nnotebookutils.notebook.exit('done')\nprint('after')")
    assert _ok(run).split() == ["before"]


@pytest.mark.parametrize("name", ["../answer", "notebooks/x", "x y", ""])
def test_a_notebook_name_that_could_leave_the_folder_is_refused(tmp_path, name):
    work = _work(tmp_path)
    run = pyspark_lab.run_reference(work, f"notebookutils.notebook.run({name!r})")
    assert run["status"] == "error" and "isn't a notebook name" in run["stderr"]


def test_a_notebook_that_runs_itself_stops(tmp_path):
    work = _work(tmp_path, {"notebooks/loop.py": b"notebookutils.notebook.run('loop')\n"})
    run = pyspark_lab.run_reference(work, "notebookutils.notebook.run('loop')")
    assert run["status"] == "error" and "RecursionError" in run["stderr"]


def test_fs_works_under_files_and_refuses_anything_else(tmp_path):
    work = _work(tmp_path, LANDING)
    answer = """
fs = notebookutils.fs
fs.put("Files/out/note.txt", "hi")
fs.append("Files/out/note.txt", " there")
fs.cp("Files/landing/day1.csv", "/lakehouse/default/Files/archive/day1.csv")
print(sorted(f.name for f in fs.ls("Files")), fs.head("Files/out/note.txt"), fs.exists("Files/archive/day1.csv"))
for bad in ("Tables/bronze/orders", "Files/../answer.py", "abfss://x@onelake.dfs.fabric.microsoft.com/Files/a", "/etc/passwd"):
    try:
        fs.ls(bad)
        print("allowed", bad)
    except ValueError:
        pass
try:
    notebookutils.credentials.getToken("storage")
except NotImplementedError as e:
    print("absent")
"""
    out = _ok(pyspark_lab.run_reference(work, answer)).split("\n")
    assert out[0] == "['archive', 'landing', 'out'] hi there True" and out[1] == "absent"


def test_runtime_context_prints_as_json_and_names_the_running_notebook(tmp_path):
    work = _work(tmp_path, {"notebooks/who.py": b"notebookutils.notebook.exit(notebookutils.runtime.context['currentNotebookName'])\n"})
    answer = ("import json\nctx = notebookutils.runtime.context\nprint(json.loads(json.dumps(ctx))['currentNotebookName'])\n"
              "print(notebookutils.notebook.run('who'), json.loads(json.dumps(ctx))['currentNotebookName'], ctx['isLocal'])\n")
    assert _ok(pyspark_lab.run_reference(work, answer)).split() == ["answer", "who", "answer", "True"]


def test_fs_refuses_a_link_under_files(tmp_path):
    work = _work(tmp_path, LANDING)
    try:
        (work / "Files" / "outside").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    run = pyspark_lab.run_reference(work, "notebookutils.fs.ls('Files/outside')")
    assert run["status"] == "error" and "is a link" in run["stderr"]


def test_the_dag_rules_are_the_notebooks_own():
    from fabric_desktop import notebookutils_local
    assert notebookutils_local._activities is notebook_dag.activities
    with pytest.raises(ValueError, match="cycle"):
        notebook_dag.activities({"activities": [{"name": "a", "path": "a", "dependencies": ["b"]},
                                                {"name": "b", "path": "b", "dependencies": ["a"]}]})


# ---- streaming (E26, E42)

def test_a_file_stream_takes_each_file_once(tmp_path):
    work = _work(tmp_path, LANDING)
    assert "2 rows from 1 new file" in _ok(pyspark_lab.run_reference(work, LOAD_FILES))
    assert "nothing new" in _ok(pyspark_lab.run_reference(work, LOAD_FILES))
    (work / "Files" / "landing" / "day2.csv").write_text("id,amount\n3,30\n", encoding="utf-8")
    _ok(pyspark_lab.run_reference(work, LOAD_FILES))
    assert pyspark_lab._rows(work, "bronze.landed") == (["id", "amount"], [(1, 10.0), (2, 20.0), (3, 30.0)])


def test_a_delta_stream_merges_each_batch_and_reads_only_new_commits(tmp_path):
    work = _work(tmp_path)
    _ok(pyspark_lab.run_reference(work, UPSERT))
    _ok(pyspark_lab.run_reference(work, UPSERT))
    assert len(pyspark_lab._rows(work, "silver.orders")[1]) == 3
    _append(work, "bronze.orders", [{"id": 4, "region": "west", "amount": 1}, {"id": 1, "region": "east", "amount": 99}])
    assert "2 rows from versions 1..1" in _ok(pyspark_lab.run_reference(work, UPSERT))
    assert pyspark_lab._rows(work, "silver.orders")[1] == [(1, "east", 99), (2, "west", 20), (3, "east", 5), (4, "west", 1)]


def test_a_stream_to_a_table_needs_a_checkpoint(tmp_path):
    work = _work(tmp_path, LANDING)
    no_checkpoint = LOAD_FILES.replace('.option("checkpointLocation", "Files/checkpoints/landing")', "")
    run = pyspark_lab.run_reference(work, no_checkpoint)
    assert run["status"] == "error" and "checkpointLocation" in run["stderr"]


def test_an_update_commit_stops_the_stream_unless_skip_change_commits(tmp_path):
    work = _work(tmp_path)
    copy = ('(spark.readStream{opt}.table("bronze.orders").writeStream'
            '.option("checkpointLocation", "Files/checkpoints/copy").toTable("silver.copy"))')
    _ok(pyspark_lab.run_reference(work, copy.format(opt="")))
    deltalake.DeltaTable(str(work / "tables" / "bronze" / "orders")).update(updates={"amount": "amount + 1"}, predicate="id = 1")
    run = pyspark_lab.run_reference(work, copy.format(opt=""))
    assert run["status"] == "error" and "skipChangeCommits" in run["stderr"]
    _ok(pyspark_lab.run_reference(work, copy.format(opt='.option("skipChangeCommits", "true")')))
    assert len(pyspark_lab._rows(work, "silver.copy")[1]) == 3


@pytest.mark.parametrize(("code", "said"), [
    ('q = spark.readStream.table("bronze.orders").writeStream.option("checkpointLocation", "../cp").toTable("s.t")',
     "under Files/"),
    ('spark.readStream.table("bronze.orders").writeStream.outputMode("complete")', "isn't in an exercise"),
    ('spark.readStream.table("bronze.orders").writeStream.trigger(continuous="1 second")', "Continuous"),
    ('spark.readStream.format("kafka").load("x")', "not 'kafka'"),
    ('spark.read.table("bronze.orders").writeStream.option("checkpointLocation", "Files/c").toTable("s.t")',
     "spark.readStream"),
])
def test_what_a_stream_cant_do_here_says_so(tmp_path, code, said):
    work = _work(tmp_path)
    run = pyspark_lab.run_reference(work, code)
    assert run["status"] == "error" and said in run["stderr"]


def test_a_checkpoint_kept_for_another_source_is_refused(tmp_path):
    work = _work(tmp_path, LANDING)
    _ok(pyspark_lab.run_reference(work, LOAD_FILES))
    other = ('(spark.readStream.table("bronze.orders").writeStream'
             '.option("checkpointLocation", "Files/checkpoints/landing").toTable("bronze.landed2"))')
    run = pyspark_lab.run_reference(work, other)
    assert run["status"] == "error" and "belongs to another source" in run["stderr"]


# ---- in the Academy learner process

def test_notebooks_streams_and_merge_run_in_the_learner_process(tmp_path):
    work = _work(tmp_path, {"notebooks/by_region.py": CHILD, **LANDING})
    answer = ('print(notebookutils.notebook.run("by_region", 60, {"region": "west"}))\n' + LOAD_FILES + UPSERT
              + 'notebookutils.notebook.exit("ok")\n')
    for _ in range(2):   # the second run takes nothing new
        _ok(pyspark_lab.run_learner(work, answer, data_root=tmp_path / "data"))
    assert len(pyspark_lab._rows(work, "bronze.landed")[1]) == 2
    assert len(pyspark_lab._rows(work, "silver.orders")[1]) == 3
    assert pyspark_lab._rows(work, "silver.orders_west")[1] == [(2, "west", 20)]


def test_a_checkpoint_outside_the_exercise_is_refused_in_the_learner_process(tmp_path):
    work = _work(tmp_path)
    code = ('(spark.readStream.table("bronze.orders").writeStream'
            '.option("checkpointLocation", "/lakehouse/default/Files/../../../../cp").toTable("silver.x"))')
    run = pyspark_lab.run_learner(work, code, data_root=tmp_path / "data")
    assert run["status"] != "ok" and not (tmp_path / "cp").exists()


# ---- exercise content: notebooks and files

def test_assets_go_to_notebooks_and_files(tmp_path):
    folder = tmp_path / "ex"
    folder.mkdir()
    (folder / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "day1.json").write_text("{}\n", encoding="utf-8")
    ex = {"notebooks": ["clean.py"], "files": ["day1.json"], "files_folder": "landing/orders"}
    assert pyspark_lab.asset_problems(ex) == []
    assert sorted(pyspark_lab.assets(ex, folder)) == ["Files/landing/orders/day1.json", "notebooks/clean.py"]


@pytest.mark.parametrize("ex", [{"notebooks": ["clean.sql"]}, {"notebooks": ["../x.py"]},
                                {"files_folder": "../up"}, {"files_folder": "a//b"}, {"notebooks": "clean.py"}])
def test_asset_fields_that_could_misplace_a_file_are_problems(ex):
    assert pyspark_lab.asset_problems(ex)


def test_an_asset_whose_folder_is_a_link_is_refused(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    try:
        (work / "notebooks").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ws.Refused):
        pyspark_lab.prepare(work, "SELECT 1", {"notebooks/x.py": b""})
    assert not (tmp_path / "x.py").exists()


def test_the_bundled_exercises_still_have_no_problems():
    assert exercises.problems() == []


def test_the_new_modules_import_nothing_a_learner_shouldnt_have():
    assert {"notebook_dag.py", "notebookutils_lab.py", "pyspark_stream.py", "pyspark_delta.py"} <= set(learner_pkg.SOURCES)
    assert learner_pkg.import_problems() == []
