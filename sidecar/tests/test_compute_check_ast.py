"""PB-014: the compute check reads a cell's Python, so notebookutils is found by any name, and code it
can't follow is Unknown, never taken as runs here."""

from __future__ import annotations

import os
import sys

import pytest
from fabric_desktop import advisor, proof, risk, validate
from fabric_desktop.fabric_run import compute_check

LH = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}
MERGE_KEYS = "MERGE INTO LH_Keys.keys.customer t USING s ON t.id = s.id WHEN MATCHED THEN DELETE"


def check(*sources):
    return compute_check([{"type": "code", "source": s, "meta": {}} for s in sources], LH)


@pytest.mark.parametrize("sources", [
    ("import notebookutils as nu\nnu.fs.mount('abfss://c@a.dfs.core.windows.net', '/data')",),
    ("from notebookutils import fs", "fs.mount('abfss://c@a.dfs.core.windows.net', '/data')"),
    ("from notebookutils import fs as f\nf.unmount('/data')",),
    ("from notebookutils.fs import mount",),
    ("from notebookutils import mssparkutils as msu\nmsu.lakehouse.create('Gold')",),
    ("import notebookutils as nu", "files = nu.fs", "files.mount('abfss://c@a', '/m')"),
    ("from notebookutils import *\nwarehouse.getWithProperties('w')",),
    ("import importlib\nnu = importlib.import_module('notebookutils')\nnu.udf.getFunctions('f')",),
    ("import notebookutils as nu\ngetattr(nu.fs, 'mount')('a', 'b')",),
])
def test_aliased_and_from_imports_of_fabric_only_utilities_run_in_fabric_only(sources):
    local = check(*sources)["local"]
    assert local["status"] == "blocked" and not local["ok"]
    assert local["reasons"][0]["text"] == "uses Fabric notebook utilities that need Fabric"
    assert local["reasons"][0]["cell"] == len(sources) or "from notebookutils.fs import" in sources[0]


@pytest.mark.parametrize("sources", [
    ("from notebookutils import fs\nfs.ls('Files/')",),
    ("import notebookutils as nu\nnu.credentials.getSecret('https://kv', 'name')",),
    ("# nu.fs.mount('a', 'b') is how Fabric mounts\nimport notebookutils as nu\nnu.fs.ls('Files/')",),
    ("fs = 'abc'\nfs.mount = 1",),  # not notebookutils
    ("spark.sql(f\"DELETE FROM sales WHERE day < '{cutoff}'\")",),
    ("spark.sql(f'SELECT * FROM {table} LIMIT 10')",),
    ("q = 'MERGE INTO dbo.sales t USING s ON t.id = s.id WHEN MATCHED THEN DELETE'\nspark.sql(q)",),
    ("exec('x = 1')",),
])
def test_code_the_check_can_read_and_that_runs_here_stays_ok(sources):
    out = check(*sources)
    assert out["local"] == {"ok": True, "status": "ok", "reasons": [], "unknown": []}
    assert out["fabric"]["ok"]


@pytest.mark.parametrize("source, words", [
    ("def run(q):\n    return spark.sql(q)", "runs SQL built at run time"),
    ("spark.sql(build_query())", "runs SQL built at run time"),
    ("spark.sql(f'{statement}')", "runs SQL built at run time"),
    ("spark.sql(f'MERGE INTO {target} t USING s ON t.id = s.id WHEN MATCHED THEN DELETE')", "whose table is known only at run time"),
    ("target = pick()\nspark.sql('DELETE FROM ' + target + ' WHERE id = 1')", "whose table is known only at run time"),
    ("spark.sql('UPDATE {} SET a = 1'.format(t))", "whose table is known only at run time"),
    ("import notebookutils as nu\ngetattr(nu.fs, name)('a')", "by a name known only at run time"),
    ("exec(code)", "runs code built at run time (exec)"),
    ("mod = __import__(name)", "imports a module named only at run time"),
    ("df = spark.read.table('x'", "isn't Python the check can read"),
])
def test_code_the_check_cant_follow_is_unknown_not_runs_here(source, words):
    local = check("x = 1", source)["local"]
    assert local["ok"] is False and local["status"] == "unknown" and local["reasons"] == []
    assert [u["cell"] for u in local["unknown"]] == [2]
    assert words in local["unknown"][0]["text"] and local["unknown"][0]["code"]


def test_sql_kept_in_a_variable_is_read_through_every_value_it_is_given():
    assert check(f"q = {MERGE_KEYS!r}", "spark.sql(q)")["local"]["status"] == "blocked"
    assert check(f"spark.sql({MERGE_KEYS[:30]!r} + {MERGE_KEYS[30:]!r})")["local"]["status"] == "blocked"
    assert check("q = 'SELECT 1'\nq = 'SELECT 2'\nspark.sql(q)")["local"]["ok"]
    assert check("q = 'SELECT 1'\nfor q in queries:\n    spark.sql(q)")["local"]["status"] == "unknown"


def test_a_known_blocker_wins_over_unknown_and_unknown_code_reaches_fabric_too():
    local = check("exec(code)\ndf.rdd.count()")["local"]
    assert local["status"] == "blocked" and local["unknown"] == []
    fabric = check("exec(code)")["fabric"]
    assert fabric == {"ok": False, "status": "unknown", "reasons": [],
                      "unknown": [{"cell": 1, "text": "runs code built at run time (exec), which the check can't read",
                                   "code": "exec(code)"}]}
    assert check("exec(code)\nx = 'C:\\\\data'")["fabric"]["status"] == "blocked"


def test_prove_validate_advice_and_the_risk_check_read_the_same_unknown():
    cells = [{"type": "code", "source": "from fabric_desktop import lakehouse\nlakehouse.write_table(df, 'dbo.out')", "meta": {}},
             {"type": "code", "source": "spark.sql(query)", "meta": {}}]
    compat = compute_check(cells, LH)
    unknown = compat["local"]["unknown"][0]
    assert unknown["cell"] == 2

    p = proof.check(cells, LH)
    assert not [b for b in p["blockers"] if b["cell"] == 2]
    assert [w["text"] for w in p["warnings"] if w["cell"] == 2] == [f"may not run on this PC: it {unknown['text']}"]

    sev = {where: [f["severity"] for f in validate.check(cells, LH, where)["findings"] if f["code"] == "runs_on_pc_unknown"]
           for where in ("pc", "fabric", None)}
    assert sev == {"pc": ["warning"], "fabric": ["info"], None: ["warning"]}

    advice = advisor.advise("nb", "a" * 64, compat, [])
    assert advice["place"] == advisor.FABRIC
    texts = [r["text"] for r in advice["reasons"] if r["kind"] == "compatibility"]
    assert texts == [f"Unknown whether it runs on this PC: the check can't read cell 2: {unknown['text']}"]

    facts = {"where": "fabric", "compatibility": compat}
    alt = [f for f in risk.check_compatibility(facts) if f["check"] == "local_alternative"]
    assert [f["outcome"] for f in alt] == [risk.UNKNOWN] and alt[0]["evidence"] == [f"Cell 2: {unknown['text']}"]
    here = risk.check_compatibility({"where": "pc", "compatibility": compat})
    assert [f["outcome"] for f in here] == [risk.UNKNOWN]


# ---- #1226: another notebook's cells, names inside f-strings, other names for the Spark session

@pytest.mark.parametrize("sources, how", [
    (("%run Helpers",), "%run"),
    (("x = 1\n%run Helpers\ny = 2",), "%run"),
    (("import notebookutils as nu\nnu.notebook.run('Child', 60)",), "notebook.run"),
    (("from notebookutils import notebook", "notebook.runMultiple(['a', 'b'])"), "notebook.runMultiple"),
    (("from notebookutils import mssparkutils\nmssparkutils.notebook.run('Child')",), "notebook.run"),
])
def test_a_cell_that_runs_another_notebook_is_unknown_on_both_sides(sources, how):
    out = check(*sources)
    for side in ("local", "fabric"):
        assert out[side]["status"] == "unknown" and not out[side]["ok"], side
        assert out[side]["unknown"][0]["text"] == f"runs another notebook ({how}), whose cells this check doesn't read"
        assert out[side]["unknown"][0]["cell"] == len(sources)


def test_a_name_inside_an_f_string_is_read_through_its_string_values():
    merge = 'spark.sql(f"MERGE INTO {t} t USING s ON t.id = s.id WHEN MATCHED THEN DELETE")'
    assert check("t = 'LH_Keys.keys.customer'", merge)["local"]["status"] == "blocked"
    assert check("t = 'dbo.sales'", merge)["local"]["ok"]
    assert check("t = 'dbo.sales'\nt = pick()", merge)["local"]["status"] == "unknown"
    assert check("t = 7", merge)["local"]["status"] == "unknown"
    assert check(merge.replace("{t}", "{t!r}"), "t = 'dbo.sales'")["local"]["status"] == "unknown"  # repr isn't the text


@pytest.mark.parametrize("sources", [
    ("from pyspark.sql import SparkSession\ns = SparkSession.builder.getOrCreate()", f"s.sql({MERGE_KEYS!r})"),
    ("session = spark", f"session.sql({MERGE_KEYS!r})"),
    ("session = spark\nother = session", f"other.sql({MERGE_KEYS!r})"),
    (f"SparkSession.builder.appName('x').getOrCreate().sql({MERGE_KEYS!r})",),
])
def test_sql_on_another_name_for_the_spark_session_is_read_like_spark_sql(sources):
    assert check(*sources)["local"]["status"] == "blocked"


def test_sql_on_something_that_isnt_the_spark_session_is_left_alone():
    assert check(f"db.sql({MERGE_KEYS!r})")["local"]["ok"]
    assert check("s = spark\ns = make_db()", f"s.sql({MERGE_KEYS!r})")["local"]["ok"]


# ---- #1273: a notebook the code runs is checked too, when it's on this PC

MOUNT = "import notebookutils as nu\nnu.fs.mount('abfss://c@a.dfs.core.windows.net', '/data')"


def cells(*sources):
    return [{"type": "code", "source": s, "meta": {}} for s in sources]


def loader(notebooks):
    def load(name):
        if name not in notebooks:
            raise FileNotFoundError(name)
        return cells(*notebooks[name])
    return load


def test_a_helper_notebook_that_runs_everywhere_keeps_the_notebook_ok():
    out = compute_check(cells("x = 1", "%run Helpers"), LH, loader({"Helpers": ["def clean(df):\n    return df"]}))
    for side in ("local", "fabric"):
        assert out[side] == {"ok": True, "status": "ok", "reasons": [], "unknown": []}, side


@pytest.mark.parametrize("source, how", [
    ("%run Helpers", "%run"),
    ("%run ./Helpers {\"day\": \"2026-01-01\"}", "%run"),
    ("import notebookutils as nu\nnu.notebook.run('Helpers', 60)", "notebook.run"),
    ("from notebookutils import mssparkutils\nmssparkutils.notebook.run('Folder/Helpers.ipynb')", "notebook.run"),
])
def test_what_stops_the_helper_on_this_pc_stops_the_cell_that_runs_it(source, how):
    out = compute_check(cells("x = 1", source), LH, loader({"Helpers": ["y = 2", MOUNT]}))
    assert out["local"]["status"] == "blocked"
    assert [(r["cell"], r["text"]) for r in out["local"]["reasons"]] == [
        (2, f"runs Helpers ({how}), whose cell 2 uses Fabric notebook utilities that need Fabric")]
    assert out["fabric"]["status"] == "ok"  # mounting is fine in Fabric


def test_what_stops_the_helper_in_fabric_stops_the_cell_that_runs_it_in_fabric():
    out = compute_check(cells("%run Helpers"), LH, loader({"Helpers": ["open(r'C:\\data\\sales.csv').read()"]}))
    assert out["fabric"]["status"] == "blocked" and out["fabric"]["reasons"][0]["cell"] == 1
    assert out["fabric"]["reasons"][0]["text"].startswith("runs Helpers (%run), whose cell 1 reads or writes a path on this PC")


def test_what_the_check_cant_read_in_the_helper_is_unknown_in_the_cell_that_runs_it():
    out = compute_check(cells("%run Helpers"), LH, loader({"Helpers": ["exec(code)"]}))
    for side in ("local", "fabric"):
        assert out[side]["status"] == "unknown" and not out[side]["ok"], side
        assert out[side]["unknown"][0]["cell"] == 1
        assert out[side]["unknown"][0]["text"].startswith("runs Helpers (%run), whose cell 1 ")
    sql = compute_check(cells("%run Helpers"), LH, loader({"Helpers": ["spark.sql(query)"]}))
    assert sql["local"]["status"] == "unknown" and sql["fabric"]["status"] == "ok"  # any SQL runs in Fabric


def test_a_helper_that_isnt_on_this_pc_is_unknown():
    out = compute_check(cells("%run Helpers"), LH, loader({}))
    for side in ("local", "fabric"):
        assert out[side]["unknown"][0]["text"] == \
            "runs Helpers (%run), which isn't a notebook on this PC, so the check can't read it", side


def test_notebooks_that_run_each_other_in_a_loop_are_unknown_and_the_check_stops():
    out = compute_check(cells("%run A"), LH, loader({"A": ["%run B"], "B": ["%run A"]}))
    for side in ("local", "fabric"):
        assert out[side]["status"] == "unknown"
        assert "in a loop or more than 5 deep" in out[side]["unknown"][0]["text"]


def test_a_chain_deeper_than_the_kernel_runs_is_unknown():
    chain = {f"N{i}": [f"%run N{i + 1}"] for i in range(8)} | {"N8": ["x = 1"]}
    out = compute_check(cells("%run N0"), LH, loader(chain))
    assert out["local"]["status"] == "unknown" and "more than 5 deep" in out["local"]["unknown"][0]["text"]


def test_what_the_runner_cant_name_is_still_unknown_with_a_loader():
    load = loader({"Helpers": ["x = 1"]})
    for source, how in (("%run -b setup.py", "%run -b"),  # no resources folder given (#1288 reads it when there is)
                        ("%run -c setup.py", "%run"),
                        ("import notebookutils as nu\nnu.notebook.run(name)", "notebook.run"),
                        ("import notebookutils as nu\nnu.notebook.runMultiple(names)", "notebook.runMultiple")):
        out = compute_check(cells(source), LH, load)
        assert out["local"]["unknown"][0]["text"] == \
            f"runs another notebook ({how}), whose cells this check doesn't read", source


def test_the_helpers_own_missing_lakehouse_isnt_the_notebooks():
    out = compute_check(cells("%run Helpers"), LH, loader({"Helpers": ["x = 1"]}))
    assert out["fabric"]["ok"]  # the helper has no lakehouse of its own; this notebook's is the one that counts
    assert compute_check(cells("%run Helpers"), None, loader({"Helpers": ["x = 1"]}))["fabric"]["reasons"][0]["cell"] is None


def test_prove_and_validate_pass_the_loader_on():
    load = loader({"Helpers": [MOUNT]})
    nb = cells("from fabric_desktop import lakehouse\nlakehouse.write_table(df, 'dbo.out')", "%run Helpers")
    text = "runs Helpers (%run), whose cell 1 uses Fabric notebook utilities that need Fabric"
    assert [b["text"] for b in proof.check(nb, LH, load)["blockers"]] == [f"can't run on this PC: {text}"]
    found = [(f["code"], f["severity"], f["text"]) for f in validate.check(nb, LH, "pc", load=load)["findings"]
             if f["code"] == "runs_on_pc"]
    assert found == [("runs_on_pc", "error", f"It {text}.")]
    assert not [f for f in validate.check(nb, LH, "pc")["findings"] if f["code"] == "runs_on_pc"]  # without it: Unknown


# ---- #1288: notebook.run's own lakehouse, runMultiple's named notebooks, %run -b files

def test_run_multiple_follows_every_named_notebook():
    load = loader({"A": ["x = 1"], "B": ["y = 2", MOUNT]})
    for source in ("import notebookutils as nu\nnu.notebook.runMultiple(['A', 'B'])",
                   "import notebookutils as nu\nnu.notebook.runMultiple({'activities': [{'name': 'a', 'path': 'A'}, "
                   "{'name': 'b', 'path': 'Folder/B.ipynb', 'dependencies': ['a']}]})"):
        out = compute_check(cells(source), LH, load)
        assert [r["text"] for r in out["local"]["reasons"]] == [
            "runs B (notebook.runMultiple), whose cell 2 uses Fabric notebook utilities that need Fabric"], source
    ok = compute_check(cells("import notebookutils as nu\nnu.notebook.runMultiple(['A'])"), LH, load)
    assert ok["local"]["status"] == ok["fabric"]["status"] == "ok"


@pytest.mark.parametrize("arg", ["names", "['A', name]", "{'activities': [{'name': 'a'}]}", "[]"])
def test_run_multiple_with_a_name_known_only_at_run_time_is_unknown(arg):
    out = compute_check(cells(f"import notebookutils as nu\nnu.notebook.runMultiple({arg})"), LH, loader({"A": ["x = 1"]}))
    assert out["local"]["unknown"][0]["text"] == \
        "runs another notebook (notebook.runMultiple), whose cells this check doesn't read"


def test_a_notebook_run_child_with_no_lakehouse_of_its_own_is_blocked_in_fabric():
    """notebook.run runs the child in its own session, which in Fabric uses its own lakehouse."""
    child = {"cells": cells("df = spark.read.table('dbo.t')"), "lakehouse": None}
    load = lambda name: child  # noqa: E731
    run = compute_check(cells("import notebookutils as nu\nnu.notebook.run('Child')"), LH, load)
    assert run["local"]["status"] == "ok"  # on this PC it uses this notebook's lakehouse
    assert run["fabric"]["reasons"][0]["text"].startswith("runs Child (notebook.run) in a session of its own, "
                                                          "and Child has no lakehouse attached")
    shared = compute_check(cells("%run Child"), LH, load)  # %run shares this session and its lakehouse
    assert shared["fabric"]["status"] == "ok"
    attached = compute_check(cells("import notebookutils as nu\nnu.notebook.run('Child')"), LH,
                             lambda name: child | {"lakehouse": LH})
    assert attached["fabric"]["status"] == "ok"
    no_tables = compute_check(cells("import notebookutils as nu\nnu.notebook.run('Child')"), LH,
                              lambda name: {"cells": cells("x = 1"), "lakehouse": None})
    assert no_tables["fabric"]["status"] == "ok"  # it reads no tables, so it needs no lakehouse


@pytest.fixture
def resources(tmp_path):
    builtin = tmp_path / "Main.resources" / "builtin"
    (builtin / "lib").mkdir(parents=True)
    (builtin / "setup.py").write_text(MOUNT, encoding="utf-8")
    (builtin / "lib" / "ok.py").write_text("x = 1", encoding="utf-8")
    (builtin / "fix.sql").write_text("RESTORE TABLE dbo.t TO VERSION AS OF 2", encoding="utf-8")
    (builtin / "data.csv").write_text("a\n1", encoding="utf-8")
    return str(tmp_path / "Main.resources")


def test_run_b_reads_the_file_from_built_in_resources(resources):
    blocked = compute_check(cells("%run -b setup.py"), LH, resources=resources)
    assert blocked["local"]["reasons"][0]["text"] == \
        "runs setup.py (%run -b), which uses Fabric notebook utilities that need Fabric"
    assert compute_check(cells("%run -b lib/ok.py"), LH, resources=resources)["local"]["status"] == "ok"
    sql = compute_check(cells("%run -b fix.sql"), LH, resources=resources)["local"]
    assert sql["status"] == "blocked" and "RESTORE" in sql["reasons"][0]["text"]


@pytest.mark.parametrize("source", ["%run -b missing.py", "%run -b data.csv", "%run -b ../../outside.py",
                                    "%run -c setup.py"])
def test_run_b_of_a_file_it_cant_read_is_unknown(resources, source):
    out = compute_check(cells(source), LH, resources=resources)["local"]
    assert out["status"] == "unknown", source


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need Developer Mode on Windows")
def test_run_b_of_a_file_that_links_outside_the_resources_is_unknown(resources, tmp_path):
    """The kernel refuses such a file (notebook_resources.real_path), so the check doesn't read it."""
    (tmp_path / "outside.py").write_text("x = 1", encoding="utf-8")
    os.symlink(tmp_path / "outside.py", os.path.join(resources, "builtin", "link.py"))
    out = compute_check(cells("%run -b link.py"), LH, resources=resources)
    for side in ("local", "fabric"):
        assert out[side]["status"] == "unknown"
        assert out[side]["unknown"][0]["text"] == ("runs link.py (%run -b), which links outside this notebook's "
                                                  "resources, so the check can't read it")


def test_run_b_in_a_notebook_it_runs_reads_this_notebooks_resources(resources):
    """The kernel runs a %run notebook in this session, with this notebook's resources folder."""
    out = compute_check(cells("%run Helpers"), LH, loader({"Helpers": ["%run -b setup.py"]}), resources=resources)
    assert out["local"]["reasons"][0]["text"] == ("runs Helpers (%run), whose cell 1 runs setup.py (%run -b), which uses "
                                                  "Fabric notebook utilities that need Fabric")


@pytest.mark.parametrize("call, status", [
    ("nu.notebook.run('Child', 90, useRootDefaultLakehouse=True)", "ok"),
    ("nu.notebook.run('Child', 90, {'useRootDefaultLakehouse': True})", "ok"),
    ("nu.notebook.run('Child', arguments={'day': '1', 'useRootDefaultLakehouse': True})", "ok"),
    ("nu.notebook.runMultiple({'activities': [{'name': 'c', 'path': 'Child', 'args': {'useRootDefaultLakehouse': True}}]})",
     "ok"),
    ("nu.notebook.run('Child', 90, useRootDefaultLakehouse=False)", "blocked"),
    ("nu.notebook.run('Child', 90, {'day': '1'})", "blocked"),
    ("nu.notebook.run('Child', 90, useRootDefaultLakehouse=flag)", "unknown"),
    ("nu.notebook.run('Child', 90, args)", "unknown"),
    ("nu.notebook.runMultiple({'activities': [{'name': 'c', 'path': 'Child', 'args': {'useRootDefaultLakehouse': True}},"
     " {'name': 'd', 'path': 'Child'}]})", "blocked"),
])
def test_use_root_default_lakehouse_gives_the_child_this_notebooks_lakehouse(call, status):
    """useRootDefaultLakehouse=True runs the child with the caller's lakehouse, so its own missing one doesn't matter."""
    child = {"cells": cells("df = spark.read.table('dbo.t')"), "lakehouse": None}
    out = compute_check(cells(f"import notebookutils as nu\n{call}"), LH, lambda name: child)
    assert out["fabric"]["status"] == status, out["fabric"]
    if status == "unknown":
        assert "useRootDefaultLakehouse" in out["fabric"]["unknown"][0]["text"]
    assert out["local"]["status"] == "ok"
