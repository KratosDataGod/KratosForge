"""Line magics and SQL results in notebook cells (magics.py and the kernel)."""

import json
import re

import pytest

from fabric_desktop import depgraph, envprofile, magics, proof
from fabric_desktop.fabric_run import compute_check, fabric_ipynb, fabric_sql_magics
from fabric_desktop.notebook import Kernel

LH = {"workspace_id": "w", "lakehouse_id": "l", "name": "LH"}


def _view(k):
    k.run("spark.createDataFrame([(1, 'a'), (2, 'b'), (3, 'b')], ['id', 'v']).createOrReplaceTempView('t')")


def test_sql_cell_keeps_its_result_in_sqldf_for_the_next_cell():
    k = Kernel()
    _view(k)
    out = k.run("%%sql\nSELECT v, count(*) AS n FROM t GROUP BY v ORDER BY v")
    assert out["status"] == "ok" and out["outputs"][0]["rows"] == [["a", 1], ["b", 2]]
    out = k.run("_sqldf.filter('n > 1').count()")
    assert out["outputs"] == [{"type": "text", "text": "1"}]
    assert any(v["name"] == "_sqldf" for v in k.variables())


def test_sql_cell_that_returns_nothing_leaves_sqldf_alone():
    k = Kernel()
    _view(k)
    k.run("%%sql\nSELECT 1 AS one")
    k.run("%%sql\nCREATE OR REPLACE TEMP VIEW t2 AS SELECT * FROM t")
    assert k.run("_sqldf.columns")["outputs"] == [{"type": "text", "text": "['one']"}]


def test_sql_line_magic_returns_a_dataframe_inside_python():
    k = Kernel()
    _view(k)
    out = k.run("df = %sql SELECT * FROM t WHERE v = 'b'\nprint(df.count())\n%sql SELECT max(id) AS top FROM t")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].strip() == "2"
    assert out["outputs"][-1]["rows"] == [[3]]


def test_sql_line_magic_refuses_in_place_changes_and_needs_a_query():
    k = Kernel()
    out = k.run("x = %sql MERGE INTO a USING b ON a.id = b.id WHEN MATCHED THEN DELETE")
    assert out["status"] == "error" and "Fabric" in out["outputs"][-1]["evalue"]
    out = k.run("x = %sql")
    assert out["status"] == "error" and "needs a query" in out["outputs"][-1]["evalue"]


def test_unknown_magic_names_the_supported_ones():
    out = Kernel().run("%nope")
    assert "%lsmagic" in out["outputs"][-1]["evalue"]


def test_rewrite_and_blank_keep_assignments_and_line_numbers():
    code = "a = 1\n  df = %sql SELECT 1\n%time a + 1\nb, c = %sql SELECT 1, 2\nx = 5 % 2"
    assert magics.rewrite(code).split("\n")[1] == "  df = __fd_magic__('sql', 'SELECT 1')"
    assert magics.blank(code) == "a = 1\n  df = None\n\nb, c = None\nx = 5 % 2"
    assert [(m.line, m.name, m.target) for m in magics.find(code)] == [(2, "sql", "df"), (3, "time", None),
                                                                       (4, "sql", "b, c")]


def test_returns_rows_reads_the_last_statement():
    assert magics.returns_rows("SELECT 1")
    assert magics.returns_rows("-- top\nWITH x AS (SELECT 1) SELECT * FROM x")
    assert magics.returns_rows("CREATE TEMP VIEW v AS SELECT 1;\nSELECT * FROM v;")
    assert not magics.returns_rows("INSERT INTO t SELECT 1")
    assert not magics.returns_rows("SELECT 1;\nDROP TABLE t")
    assert not magics.returns_rows("")


def test_dependencies_see_sqldf_and_sql_line_reads():
    sql_cell = depgraph.analyze("%%sql\nSELECT * FROM dbo.sales")
    assert "_sqldf" in sql_cell["defines"]
    py = depgraph.analyze("df = %sql SELECT * FROM dbo.orders\nn = df.count()")
    assert py["parsed"] and "df" in py["defines"]
    assert {"kind": "table", "name": "dbo.orders", "line": 1} in py["reads"]
    graph = depgraph.graph([{"type": "code", "source": "%%sql\nSELECT * FROM dbo.sales", "cell_id": "a"},
                            {"type": "code", "source": "_sqldf.count()", "cell_id": "b"}])
    assert json.dumps(graph).count('"_sqldf"') >= 1


def test_environment_check_parses_cells_with_assigned_magics():
    found = envprofile.imports("import polars as pl\ndf = %sql SELECT 1")
    assert found["parsed"] and [m["module"] for m in found["modules"]] == ["polars"]


def test_compute_check_and_proof_catch_sql_line_writes():
    cells = [{"type": "code", "source": "from fabric_desktop import lakehouse\n"
                                        "x = %sql DELETE FROM dbo.sales WHERE id = 1"}]
    local = compute_check(cells, LH)["local"]
    assert not local["ok"] and "in place" in local["reasons"][0]["text"]
    assert local["reasons"][0]["code"].startswith("x = %sql DELETE")
    checked = proof.check([{"type": "code", "source": "from fabric_desktop import lakehouse\n"
                                                      "%sql INSERT INTO dbo.t SELECT 1"}], LH)
    assert any("creates or changes tables" in b["text"] and b["line"] == 2 for b in checked["blockers"])


def test_publish_turns_sql_magics_into_calls_fabric_has():
    cells = [{"type": "code", "source": "%%sql\nSELECT * FROM sales"},
             {"type": "code", "source": "top = _sqldf.limit(5)\n  df = %sql SELECT 1 AS \"x\"\n%sql SELECT 2"},
             {"type": "code", "source": "%%sql\nSELECT * FROM other"}]
    spark = fabric_sql_magics(cells, spark=True)
    assert spark[0]["source"] == '_sqldf = spark.sql("SELECT * FROM sales")\ndisplay(_sqldf)'
    assert spark[1]["source"].split("\n")[1:] == ['  df = spark.sql("SELECT 1 AS \\"x\\"")', 'display(spark.sql("SELECT 2"))']
    assert spark[2]["source"] == "%%sql\nSELECT * FROM other"  # nothing after it reads _sqldf
    python = fabric_sql_magics(cells, spark=False)
    assert python[0]["source"].startswith("_sqldf = lakehouse.sql(")
    nb = fabric_ipynb(cells, None)
    assert "".join(nb["cells"][0]["source"]).startswith("_sqldf = spark.sql(")


# ---- cell languages: Fabric %%name and Databricks %name headers

def test_cell_reads_fabric_and_databricks_headers():
    assert magics.cell("%sql\nSELECT 1") == magics.Cell("sql", "%sql", "\nSELECT 1")
    assert magics.cell("%sql SELECT 1").language == "python"  # the line magic, not a header
    assert magics.cell("  %%sql\nSELECT 2").language == "sql"
    assert magics.cell("%%pyspark\nx = 1") == magics.Cell("python", "%%pyspark", "\nx = 1")
    assert magics.cell("%python\nx = 1").body == "\nx = 1"
    assert magics.cell("%md # Title\ntext") == magics.Cell("markdown", "%md", " # Title\ntext")
    assert magics.cell("%%markdown\n# T").language == "markdown"
    assert [magics.cell(s).language for s in ("%scala\nval x = 1", "%r\n1", "%%spark\n1", "%%sparkr\n1",
                                              "%%csharp\n1", "%%configure\n{}", "%%html\n<b>")] == \
        ["scala", "r", "scala", "r", "csharp", "configure", "html"]
    assert magics.cell("%sqlx\n1").language == "python"


def test_databricks_cells_run_on_this_pc():
    k = Kernel()
    _view(k)
    out = k.run("%sql\nSELECT count(*) AS n FROM t")
    assert out["status"] == "ok" and out["outputs"][0]["rows"] == [[3]]
    assert k.run("_sqldf.columns")["outputs"] == [{"type": "text", "text": "['n']"}]
    assert k.run("%md ## Notes\nSome *text*")["outputs"] == [{"type": "markdown", "text": "## Notes\nSome *text*"}]
    assert k.run("%%markdown\n# Title")["outputs"] == [{"type": "markdown", "text": "# Title"}]
    out = k.run("%python\nx = 40\nx + 2")
    assert out["outputs"] == [{"type": "text", "text": "42"}]
    out = k.run("%scala\nval x = 1")
    assert out["status"] == "error" and out["outputs"][0]["ename"] == "RunsInFabric"


def test_python_header_keeps_line_numbers_in_tracebacks():
    out = Kernel().run("%python\nx = 1\nraise ValueError('boom')")
    assert 'line 3' in out["outputs"][-1]["traceback"]


def test_reading_tools_understand_databricks_headers():
    sql = depgraph.analyze("%sql\nSELECT * FROM dbo.sales")
    assert sql["parsed"] and {"kind": "table", "name": "dbo.sales", "line": 1} in sql["reads"]
    assert "_sqldf" in sql["defines"]
    md = depgraph.analyze("%md # Not Python at all (really)")
    assert md["parsed"] and not md["uses"]
    py = depgraph.analyze("%python\nimport polars as pl\ny = pl.DataFrame()")
    assert py["parsed"] and "y" in py["defines"]
    assert envprofile.imports("%sql\nSELECT 1")["modules"] == []
    assert [m["module"] for m in envprofile.imports("%python\nimport polars")["modules"]] == ["polars"]


def test_compute_check_sees_databricks_scala_and_sql_writes():
    cells = [{"type": "code", "source": "%scala\nval x = 1"},
             {"type": "code", "source": "%sql\nDELETE FROM dbo.sales WHERE id = 1"},
             {"type": "code", "source": "%md # fine"}]
    reasons = compute_check(cells, LH)["local"]["reasons"]
    assert [(r["cell"], r["text"][:12]) for r in reasons] == [(1, "uses a Scala"), (2, "changes tabl")]


def test_publish_writes_databricks_headers_as_fabric_ones():
    cells = [{"type": "code", "source": "%sql\nSELECT * FROM sales"},
             {"type": "code", "source": "%md ## Notes\nAbout *sales*"},
             {"type": "code", "source": "%python\nx = 1"},
             {"type": "code", "source": "%scala\nval y = 2"},
             {"type": "code", "source": "%r\n1 + 1"},
             {"type": "code", "source": "%%sql\nSELECT 1"},
             {"type": "markdown", "source": "%md stays"}]
    nb = fabric_ipynb(cells, None)
    got = [(c["cell_type"], "".join(c["source"])) for c in nb["cells"]]
    assert got == [("code", "%%sql\nSELECT * FROM sales"), ("markdown", "## Notes\nAbout *sales*"),
                   ("code", "x = 1"), ("code", "%%spark\nval y = 2"), ("code", "%%sparkr\n1 + 1"),
                   ("code", "%%sql\nSELECT 1"), ("markdown", "%md stays")]


def test_python_notebook_publish_turns_databricks_sql_into_lakehouse_sql():
    cells = [{"type": "code", "source": "from fabric_desktop import lakehouse"},
             {"type": "code", "source": "%sql\nSELECT * FROM dbo.sales"}]
    nb = fabric_ipynb(cells, LH)
    assert "".join(nb["cells"][-1]["source"]) == 'lakehouse.sql("SELECT * FROM dbo.sales")'


# ---- shell: !command, %sh / %%sh / %%bash cells, and %fs

import os
import sys
import time

import pytest

from fabric_desktop import shell

PY = f'"{sys.executable}"'


def test_bang_line_streams_output_and_assigned_keeps_lines():
    k = Kernel()
    out = k.run(f"!{PY} -c \"print('hello')\"")
    assert out["status"] == "ok" and "hello" in out["outputs"][0]["text"]
    out = k.run(f"lines = !{PY} -c \"print('a'); print('b')\"\nlines")
    assert out["outputs"] == [{"type": "text", "text": "['a', 'b']"}]
    assert k.run("_exit_code")["outputs"] == [{"type": "text", "text": "0"}]


def test_bang_line_expands_notebook_values_like_ipython():
    assert shell.expand("echo {n + 1} $name $$HOME $missing {}", {"n": 1, "name": "x"}) == "echo 2 x $HOME $missing {}"
    k = Kernel()
    k.run("word = 'forge'")
    out = k.run(f"!{PY} -c \"print('$word')\"")
    assert "forge" in out["outputs"][0]["text"]


@pytest.mark.skipif(os.name == "nt", reason="sh scripts; Windows CI may not have bash")
def test_sh_and_bash_cells_run_the_cell_as_a_script_and_fail_on_exit_code():
    k = Kernel()
    out = k.run("%sh echo one\necho two")
    assert out["status"] == "ok" and out["outputs"][0]["text"].split() == ["one", "two"]
    out = k.run("%%bash\nX=3\necho $((X + 1))")
    assert "4" in out["outputs"][0]["text"]
    out = k.run("%%sh\necho before\nexit 3")
    assert out["status"] == "error" and "exit code 3" in out["outputs"][-1]["evalue"]


def test_cmd_cell_explains_off_windows(monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows has cmd")
    out = Kernel().run("%%cmd\ndir")
    assert out["status"] == "error" and "command prompt" in out["outputs"][-1]["evalue"]


def test_missing_shell_says_what_to_install(monkeypatch):
    monkeypatch.setattr(shell.shutil, "which", lambda _name: None)
    with pytest.raises(FileNotFoundError, match="Git for Windows"):
        shell.script("bash", "echo hi")


def test_cancel_ends_a_running_command():
    k = Kernel()
    k.start(f"!{PY} -c \"import time; print('started', flush=True); time.sleep(30)\"")
    deadline = time.monotonic() + 10
    while "started" not in "".join(o.get("text", "") for o in k.poll(-1, wait=0.2)["outputs"]):
        assert time.monotonic() < deadline
    started = time.monotonic()
    k.interrupt()
    snap = k.poll(-1, wait=5)
    assert snap["status"] == "cancelled" and time.monotonic() - started < 6


@pytest.mark.skipif(os.name == "nt", reason="checks the child's pid with os.kill(pid, 0)")
def test_cancel_ends_the_commands_child_processes(tmp_path):
    pidfile = tmp_path / "child.pid"
    child = tmp_path / "child.py"
    child.write_text(f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    parent = tmp_path / "parent.py"
    parent.write_text(f"import subprocess, sys\nsubprocess.Popen([sys.executable, {str(child)!r}]).wait()\n")
    k = Kernel()
    k.start(f"!{PY} \"{parent}\"")
    deadline = time.monotonic() + 10
    while not (pidfile.exists() and pidfile.read_text()):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    pid = int(pidfile.read_text())
    k.interrupt()
    assert k.poll(-1, wait=5)["status"] == "cancelled"
    deadline = time.monotonic() + 5
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "the command's child process is still running"
        time.sleep(0.1)


def test_magic_lines_inside_strings_and_brackets_are_left_alone():
    code = 's = """\n!ls\n%time x\n"""\nt = (1,\n%who\n)\n!echo hi\nx = %sql SELECT 1'
    out = magics.rewrite(code)
    assert out.split("\n")[:7] == code.split("\n")[:7]
    assert out.split("\n")[7:] == ["__fd_magic__('system', 'echo hi')", "x = __fd_magic__('sql', 'SELECT 1')"]
    assert [m.name for m in magics.find(code)] == ["system", "sql"]
    out = Kernel().run('s = """\n!ls\n%time 1\n"""\ns')
    assert out["outputs"] == [{"type": "text", "text": repr("\n!ls\n%time 1\n")}]


def test_fs_magic_maps_onto_notebookutils_fs(tmp_path):
    k = Kernel()
    root = tmp_path.as_posix()
    assert k.run(f"%fs put file:{root}/a.txt \"hi there\"")["status"] == "ok"
    out = k.run(f"%fs ls file:{root}")
    assert out["outputs"][0]["columns"] == ["name", "path", "size", "isDir"] and out["outputs"][0]["rows"][0][0] == "a.txt"
    assert "hi there" in k.run(f"%fs head file:{root}/a.txt")["outputs"][0]["text"]
    out = k.run(f"%fs put file:{root}/a.txt \"again\"")
    assert out["status"] == "error" and "overwrite" in out["outputs"][-1]["evalue"]
    assert k.run(f"%fs put -f file:{root}/a.txt \"again\"")["status"] == "ok"
    assert k.run(f"%fs cp file:{root}/a.txt file:{root}/b.txt")["status"] == "ok"
    assert k.run(f"%fs rm file:{root}/a.txt")["status"] == "ok"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.txt"]
    assert "isn't a command" in k.run("%fs chmod x")["outputs"][-1]["evalue"]
    assert "needs 2 paths" in k.run("%fs cp only-one")["outputs"][-1]["evalue"]


def test_reading_tools_see_shell_commands_as_side_effects():
    assert depgraph.analyze("x = !dir")["defines"] == ["x"]
    assert depgraph.analyze("x = !dir")["effects"] == [{"line": 1, "text": "runs a shell command"}]
    assert depgraph.analyze("!pip install rich")["effects"] == [{"line": 1, "text": "installs packages"}]
    assert depgraph.analyze("%%bash\nrm -rf /tmp/x")["effects"] == [{"line": 1, "text": "runs shell commands"}]
    assert depgraph.analyze("%fs rm Files/old.csv")["effects"] == [{"line": 1, "text": "changes files with %fs"}]
    assert envprofile.imports("import polars\nfiles = !ls")["parsed"]
    checked = proof.check([{"type": "code", "source": "from fabric_desktop import lakehouse\n!echo hi"}], LH)
    assert any("shell commands" in w["text"] for w in checked["warnings"])


def test_windows_shell_cells_are_listed_for_fabric_and_sh_publishes_as_sh():
    cells = [{"type": "code", "source": "%%cmd\ndir"}, {"type": "code", "source": "%sh ls -la\npwd"}]
    assert [r["cell"] for r in compute_check(cells, LH)["fabric"]["reasons"]] == [1]
    assert "".join(fabric_ipynb(cells, None)["cells"][1]["source"]) == "%%sh\nls -la\npwd"


# ---- IPython magics: %%time, %timeit, %%capture, %%writefile, %lsmagic, %env, %who, %reset

def test_time_and_timeit():
    k = Kernel()
    out = k.run("%%time\nx = sum(range(1000))\nx")
    texts = [o.get("text", "") for o in out["outputs"]]
    assert out["status"] == "ok" and "499500" in texts and any(t.startswith("Wall time: ") for t in texts)
    assert "Wall time" in k.run("%time y = 2")["outputs"][0]["text"] and k.run("y")["outputs"][0]["text"] == "2"
    out = k.run("%timeit -n 5 -r 3 sum(range(100))")
    assert out["status"] == "ok", out
    assert "per loop (mean ± std. dev. of 3 runs, 5 loops each)" in out["outputs"][0]["text"]
    out = k.run("%%timeit -n 2 -r 2\nz = [i for i in range(10)]")
    assert "2 runs, 2 loops each" in out["outputs"][0]["text"]
    out = k.run("%%timeit -n 1 -r 1\n1 / 0")
    assert out["status"] == "error" and out["outputs"][-1]["ename"] == "ZeroDivisionError"


def test_capture_keeps_output_and_shows_errors():
    k = Kernel()
    out = k.run("%%capture cap\nprint('quiet')\nspark.createDataFrame([(1,)], ['a'])")
    assert out["status"] == "ok" and out["outputs"] == []
    assert k.run("cap.stdout")["outputs"] == [{"type": "text", "text": "'quiet\\n'"}]
    out = k.run("cap.show()")
    assert out["outputs"][0]["text"] == "quiet\n" and out["outputs"][1]["type"] == "table"
    out = k.run("%%capture\nprint('x')\nraise ValueError('loud')")
    assert out["status"] == "error" and out["outputs"][0]["evalue"] == "loud"
    out = k.run("print('after')")
    assert out["outputs"] == [{"type": "stream", "text": "after\n"}]


def test_writefile_writes_appends_and_can_be_imported(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    k = Kernel()
    target = tmp_path / "helper_mod.py"
    out = k.run(f"%%writefile {target}\ndef double(x):\n    return 2 * x")
    assert out["status"] == "ok" and out["outputs"][0]["text"].startswith("Writing ")
    out = k.run(f"%%writefile -a {target}\nTEN = 10")
    assert out["outputs"][0]["text"].startswith("Appending to ")
    assert k.run("import helper_mod\nhelper_mod.double(helper_mod.TEN)")["outputs"] == [{"type": "text", "text": "20"}]
    assert "needs a file name" in k.run("%%writefile\nx")["outputs"][0]["evalue"]


def test_small_ipython_magics():
    k = Kernel()
    assert "%%capture" in k.run("%lsmagic")["outputs"][0]["text"]
    assert k.run("%matplotlib inline")["status"] == "ok"
    assert "skipped" in k.run("%load_ext sql")["outputs"][0]["text"]
    assert k.run("%load_ext autoreload")["outputs"] == []  # autoreload works (test_notebook_autoreload)
    k.run("%env KF_TEST_VALUE=abc")
    assert k.run("%env KF_TEST_VALUE")["outputs"] == [{"type": "text", "text": "'abc'"}]
    k.run("%env KF_TEST_TOKEN=s3cret")
    assert "s3cret" not in json.dumps(k.run("%env")["outputs"])
    assert "can't be changed" in k.run("%env FABRIC_DESKTOP_X=1")["outputs"][-1]["evalue"]
    for name in ("HTTPS_PROXY", "http_proxy", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        before = os.environ.get(name)
        assert "can't set it" in k.run(f"%env {name}=http://evil:8080")["outputs"][-1]["evalue"]
        assert os.environ.get(name) == before
    k.run("alpha = 1\nbeta = [1, 2]")
    assert k.run("%who")["outputs"][0]["text"].split() == ["alpha", "beta"]
    assert "list" in k.run("%whos")["outputs"][0]["text"]
    assert k.run("%pwd")["outputs"][0]["text"] == repr(os.getcwd())
    assert "working folder" in k.run("%cd /tmp")["outputs"][-1]["evalue"]
    assert "%reset -f" in k.run("%reset")["outputs"][-1]["evalue"]
    k.run("%reset -f")
    assert k.run("alpha")["status"] == "error" and k.run("spark is not None")["outputs"][0]["text"] == "True"
    assert k.run("%%javascript\nalert(1)")["status"] == "error"
    assert "%lsmagic" in k.run("%nope")["outputs"][-1]["evalue"]


def test_reading_tools_and_publish_with_ipython_cell_magics():
    assert magics.cell("%%capture out --no-stderr\nx = 1") == magics.Cell("python", "%%capture", "\nx = 1", "out --no-stderr")
    assert set(depgraph.analyze("%%capture out\nx = 1")["defines"]) == {"out", "x"}
    w = depgraph.analyze("%%writefile helpers.py\nimport os")
    assert w["writes"] == [{"kind": "file", "name": "helpers.py", "line": 1}] and w["parsed"]
    assert depgraph.analyze("%%time\nimport polars as pl")["defines"] == ["pl"]
    assert depgraph.analyze("%reset -f")["dynamic"][0]["text"] == "%reset clears every variable"
    cells = [{"type": "code", "source": "%%time\nx = 1"}, {"type": "code", "source": "%%writefile a.py\nx"}]
    assert ["".join(c["source"]) for c in fabric_ipynb(cells, None)["cells"]] == ["%%time\nx = 1", "%%writefile a.py\nx"]


# ---- %run in Fabric's and Databricks' forms

from fabric_desktop.notebook import NotebookStore, _run_target


def test_run_target_reads_both_forms():
    assert _run_target("Load sales") == ("Load sales", {})
    assert _run_target('Load sales {"day": "2026-01-01", "full": true, "n": null}') == \
        ("Load sales", {"day": "2026-01-01", "full": True, "n": None})
    assert _run_target("helpers {'x': 1}") == ("helpers", {"x": 1})
    assert _run_target('./etl/Load sales $day="2026-01-01" $full=true $who=\'me\'') == \
        ("Load sales", {"day": "2026-01-01", "full": "true", "who": "me"})
    assert _run_target('"My nb.ipynb"') == ("My nb", {})
    assert _run_target("..\\\\shared\\\\helpers") == ("helpers", {})
    with pytest.raises(ValueError, match="needs a notebook name"):
        _run_target("")
    with pytest.raises(ValueError, match="couldn't read"):
        _run_target("nb {oops}")


def test_run_passes_json_parameters_into_the_parameter_cell(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("Load sales", [
        {"type": "code", "source": "day = 'x'\nfull = False", "meta": {"tags": ["parameters"]}},
        {"type": "code", "source": "summary = f'{day} {full}'"},
    ])
    k = Kernel(load_notebook=lambda name: store.load(name)["cells"])
    out = k.run('%run ./Load sales {"day": "2026-01-01", "full": true}\nsummary')
    assert out["status"] == "ok", out
    assert out["outputs"][-1] == {"type": "text", "text": "'2026-01-01 True'"}
    out = k.run('%run Load sales $day="2026-02-02"\nsummary')
    assert out["outputs"][-1] == {"type": "text", "text": "'2026-02-02 False'"}


def test_run_skips_the_fabric_step_which_runs_only_on_fabric_spark(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("Gold", [{"type": "code", "source": "done = 'here'"},
                        {"type": "code", "source": 'spark.sql("OPTIMIZE t VORDER")\ndone = "fabric"',
                         "meta": {"fabric_step": True}}])
    k = Kernel(load_notebook=lambda name: store.load(name)["cells"])
    out = k.run("%run Gold\ndone")
    assert out["status"] == "ok", out
    assert out["outputs"][-1] == {"type": "text", "text": "'here'"}


# ---- %%configure: checked as Fabric would, explained, nothing changed here

from fabric_desktop.notebook import configure_report


def test_configure_checks_the_json_and_says_what_applies_where():
    report = configure_report('-f\n{"driverMemory": "28g", "conf": {"spark.sql.shuffle.partitions": "8"},'
                              ' "defaultLakehouse": {"name": "LH_Silver"}, "driverMemroy": "1g"}')
    assert "JSON is valid" in report and "driverMemory: 28g" in report
    assert "spark.sql.shuffle.partitions" in report and "attach LH_Silver" in report
    assert "Fabric doesn't know driverMemroy" in report
    with pytest.raises(ValueError, match="isn't valid JSON"):
        configure_report('{"driverMemory": 28g}')
    with pytest.raises(ValueError, match="needs a JSON object"):
        configure_report("  ")


def test_configure_cell_in_the_kernel_and_the_runs_on_check():
    k = Kernel()
    out = k.run('%%configure\n{"executorCores": 4}')
    assert out["status"] == "ok" and "executorCores: 4" in out["outputs"][0]["text"]
    assert k.run('%%configure\n{oops}')["status"] == "error"
    reasons = compute_check([{"type": "code", "source": '%%configure\n{oops}'}], LH)["fabric"]["reasons"]
    assert reasons and "can't read" in reasons[0]["text"]


# ---- %%tsql: checked against the target's rules here, runs in Fabric

def test_tsql_cell_is_checked_and_listed_as_fabric_only():
    k = Kernel()
    out = k.run("%%tsql -artifact WH_Sales -type Warehouse -bind df\nCREATE TABLE dbo.t (a NVARCHAR(10))")
    assert out["status"] == "error" and out["outputs"][-1]["ename"] == "RunsInFabric"
    assert "WH_Sales" in out["outputs"][-1]["evalue"]
    assert "Fabric Warehouse" in out["outputs"][0]["text"] and "nvarchar" in out["outputs"][0]["text"]
    out = k.run("%%tsql -artifact LH -type Lakehouse\nSELECT TOP 5 * FROM dbo.sales")
    assert "SQL analytics endpoint" in out["outputs"][0]["text"]
    reasons = compute_check([{"type": "code", "source": "%%tsql -artifact WH\nSELECT 1"}], LH)["local"]["reasons"]
    assert reasons[0]["text"].startswith("runs T-SQL")
    assert depgraph.analyze("%%tsql -artifact WH\nSELECT 1")["parsed"]
    assert envprofile.imports("%%tsql\nSELECT 1")["modules"] == []


def test_a_backslash_ending_a_comment_doesnt_swallow_the_next_magic():
    code = "# the files are in C:\\temp\\\n%who\nx = 1 + \\\n%who"
    assert [(m.line, m.name) for m in magics.find(code)] == [(2, "who")]  # line 4 continues x = 1 + ...
    out = Kernel().run("# the files are in C:\\temp\\\n!echo hi")
    assert out["status"] == "ok" and "hi" in "".join(o.get("text", "") for o in out["outputs"])


def test_returns_rows_skips_block_comments_and_quoted_semicolons():
    assert magics.returns_rows("/* daily load */ SELECT 1")
    assert magics.returns_rows("-- a\n/* b;\nc */\nSELECT 1;\r\n")
    assert magics.returns_rows("SELECT ';\n-- not a comment' AS x")
    assert magics.returns_rows("SELECT 1 AS `a;b`")
    assert not magics.returns_rows("SELECT 1; -- then\nDROP TABLE t")
    assert not magics.returns_rows("/* only a comment */")
    k = Kernel()
    _view(k)
    k.run("%%sql\n/* header */\nSELECT id FROM t")
    assert k.run("_sqldf.count()")["outputs"] == [{"type": "text", "text": "3"}]
    cells = [{"type": "code", "source": "%%sql\n/* header */ SELECT * FROM sales"},
             {"type": "code", "source": "_sqldf.show()"}]
    assert fabric_sql_magics(cells, spark=True)[0]["source"].startswith("_sqldf = spark.sql(")


def test_timeit_refuses_options_it_cant_honour_and_who_filters_by_type():
    k = Kernel()
    k.run("a = 1\nb = 'x'\nc = [1]")
    for code, message in (("%timeit -n0 1", "1 or more"), ("%timeit -r 0 1", "1 or more"),
                          ("%timeit -o 1 + 1", "-o isn't supported"), ("%%timeit -n1 -q\n1", "-q isn't supported")):
        out = k.run(code)
        assert out["status"] == "error" and message in out["outputs"][-1]["evalue"], code
    assert "per loop" in k.run("%timeit -n 2 -r 2 -1")["outputs"][0]["text"]  # -1 is code, not an option
    assert k.run("%who str")["outputs"][0]["text"].split() == ["b"]
    assert k.run("%who int list")["outputs"][0]["text"].split() == ["a", "c"]
    assert k.run("%who")["outputs"][0]["text"].split() == ["a", "b", "c"]


def test_writefile_and_run_refuse_what_they_would_drop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    k = Kernel()
    out = k.run("%%writefile my helpers.py\nx = 1")
    assert out["status"] == "error" and 'quote a name with spaces, like: %%writefile "my helpers.py"' in out["outputs"][0]["evalue"]
    assert not (tmp_path / "my").exists()
    assert "-x isn't supported" in k.run("%%writefile -x a.py\nx = 1")["outputs"][0]["evalue"]
    assert k.run('%%writefile "my helpers.py"\nx = 1')["status"] == "ok" and (tmp_path / "my helpers.py").exists()
    from fabric_desktop.notebook import _run_target
    assert _run_target('./Load $a=1 $b="x y"') == ("Load", {"a": "1", "b": "x y"})
    for line, left in (("Load $a=1 $full", "$full"), ("Load $a=1 junk", "junk")):
        with pytest.raises(ValueError, match=re.escape(repr(left))):
            _run_target(line)


def test_env_says_when_it_cant_set_a_name_and_reads_windows_style_names(monkeypatch):
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    k = Kernel()
    for line, name in (("%env 1BAD=x", "1BAD"), ("%env =x", "a variable with no name"), ("%env 1BAD x", "1BAD")):
        out = k.run(line)
        assert out["status"] == "error" and f"%env can't set {name}" in out["outputs"][0]["evalue"]
    assert k.run("%env ProgramFiles(x86)")["outputs"] == [{"type": "text", "text": repr(r"C:\Program Files (x86)")}]


def test_a_cte_that_writes_isnt_a_query():
    assert not magics.returns_rows("WITH x AS (SELECT 1 AS id) INSERT INTO t SELECT * FROM x")
    assert not magics.returns_rows("with x as (select 1) merge into t using x on true when matched then delete")
    assert magics.returns_rows("WITH a(c) AS (SELECT 'insert'), b AS (SELECT 2) SELECT * FROM a")
    assert magics.returns_rows("EXPLAIN INSERT INTO t SELECT 1")  # the plan is rows
    cells = [{"type": "code", "source": "%%sql\nWITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x"},
             {"type": "code", "source": "_sqldf.show()"}]
    assert fabric_sql_magics(cells, spark=True)[0]["source"].startswith("%%sql")
