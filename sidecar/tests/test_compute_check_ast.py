"""PB-014: the compute check reads a cell's Python, so notebookutils is found by any name, and code it
can't follow is Unknown, never taken as runs here."""

from __future__ import annotations

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
