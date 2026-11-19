"""WR-311: Databricks' notebook magics on this PC. Each one does what the Databricks docs say it does,
or stops with an error that names the magic (and, before a run, the cell) and says what to use.

What Databricks does comes from these pages, quoted where a test pins it:

- Magic commands: https://docs.databricks.com/aws/en/notebooks/notebooks-code
- %run: https://docs.databricks.com/aws/en/notebooks/notebook-workflows
- %pip: https://docs.databricks.com/aws/en/libraries/notebooks-python-libraries
- %fs and dbutils.fs: https://docs.databricks.com/aws/en/dev-tools/databricks-utils
"""

from __future__ import annotations

import pytest

from fabric_desktop import depgraph, magics, notebook_formats, pip_magic, shell
from fabric_desktop.fabric_run import compute_check
from fabric_desktop.notebook import Kernel, NotebookStore


def _code(source):
    return {"type": "code", "source": source}


def _error(out):
    assert out["status"] == "error", out
    return out["outputs"][-1]["evalue"]


# ---- %run: "Execute another notebook, importing its functions and variables." (notebook-workflows)

@pytest.fixture
def store(tmp_path):
    s = NotebookStore(tmp_path)
    s.save("helpers", [_code("rate = 0.2\ndef with_tax(x):\n    return round(x * (1 + rate), 2)")])
    s.save("broken", [_code("ok = 1"), {"type": "markdown", "source": "# Notes"}, _code("raise ValueError('bad row')")])
    return s


@pytest.mark.parametrize("path", ["helpers", "./helpers", "../shared/helpers"])
def test_run_shares_the_other_notebooks_variables_and_functions(store, path):
    """ "the functions and variables defined in it become available in the calling notebook", and the
    path "should be resolved relative to the currently running notebook" (./ prefix). This PC keeps
    its notebooks in one folder, so a relative path finds the notebook by its name."""
    k = Kernel(load_notebook=lambda name: store.load(name)["cells"])
    assert k.run(f"%run {path}")["status"] == "ok"
    out = k.run("with_tax(10), rate")
    assert out["outputs"] == [{"type": "text", "text": "(12.0, 0.2)"}]


def test_run_that_fails_names_the_notebook_and_its_cell(store):
    k = Kernel(load_notebook=lambda name: store.load(name)["cells"])
    out = k.run("%run ./broken")
    assert out["outputs"][0]["evalue"] == "bad row"  # the other notebook's own error is shown first
    assert _error(out) == "Notebook 'broken' failed at cell 3; its error is shown above."
    assert k.run("ok")["outputs"] == [{"type": "text", "text": "1"}]  # inline: cells before it ran here


# ---- %md: "Switch cell language to Markdown. Renders Markdown content in the cell." (notebooks-code)

def test_md_renders_and_runs_nothing():
    k = Kernel()
    out = k.run("%md x = 1\n**bold** {not code}")
    assert out["status"] == "ok" and out["outputs"] == [{"type": "markdown", "text": "x = 1\n**bold** {not code}"}]
    assert k.run("'x' in dir()")["outputs"] == [{"type": "text", "text": "False"}]


# ---- %sql: "Results are available as _sqldf ... in subsequent Python and SQL cells", and "_sqldf
# is reassigned each time a SQL cell is run" (notebooks-code).

def test_sql_cell_result_is_the_cell_result_and_sqldf():
    k = Kernel()
    out = k.run("%sql\nSELECT id, id * 2 AS twice FROM range(3) ORDER BY id")
    assert out["status"] == "ok" and out["outputs"][0]["rows"] == [[0, 0], [1, 2], [2, 4]]
    assert k.run("_sqldf.count()")["outputs"] == [{"type": "text", "text": "3"}]
    k.run("%sql\nSELECT 'next' AS v")
    assert k.run("_sqldf.columns")["outputs"] == [{"type": "text", "text": "['v']"}]
    k.run("%sql\nSELECT 7 AS n")
    assert k.run("%sql\nSELECT n + 1 AS m FROM _sqldf")["outputs"][0]["rows"] == [[8]]  # in SQL cells too
    assert k.run("%sql\nSELECT m * 10 AS p FROM _sqldf")["outputs"][0]["rows"] == [[80]]  # the newest result
    assert k.run("_sqldf.collect()[0][0]")["outputs"] == [{"type": "text", "text": "80"}]
    out = k.run("%sql\nSELECT '_sqldf' AS s, p FROM `_sqldf`")  # a string that says _sqldf stays as written
    assert out["outputs"][0]["rows"] == [["_sqldf", 80]]
    read = depgraph.analyze("%sql\nSELECT * FROM _sqldf")  # the last result, not a lakehouse table
    assert read["reads"] == [] and read["uses"] == ["_sqldf"]


def test_sqldf_is_left_alone_when_qualified_or_in_a_comment_and_a_result_has_one_view():
    from fabric_desktop import notebook

    class Frame:
        def __init__(self):
            self.views = []

        def createOrReplaceTempView(self, name):  # noqa: N802 - the Spark name
            self.views.append(name)
    frame = Frame()
    namespace = {"_sqldf": frame}
    query = notebook._with_sqldf("SELECT * FROM mydb._sqldf -- _sqldf\n/* _sqldf */ JOIN _sqldf", namespace)
    view = frame.views[0]
    assert query == f"SELECT * FROM mydb._sqldf -- _sqldf\n/* _sqldf */ JOIN {view}"
    assert notebook._with_sqldf("SELECT * FROM _sqldf", namespace) == f"SELECT * FROM {view}"
    assert frame.views == [view]  # the same result is read through the view it already has
    assert notebook._with_sqldf("SELECT 1 /* _sqldf never closed", namespace) == "SELECT 1 /* _sqldf never closed"


@pytest.mark.parametrize("quote", ["'", '"'])
def test_sqldf_reads_an_unclosed_quote_in_linear_time(quote):
    import time

    from fabric_desktop import notebook
    text = "SELECT 1 " + (quote + "\\") * 50_000  # an unclosed quote ending in a backslash, 100 KB
    started = time.perf_counter()
    assert notebook._with_sqldf(text + " _sqldf", {"_sqldf": object()}) == text + " _sqldf"
    assert notebook._SQLDF.sub(lambda m: "X" if m["name"] else m.group(0), text + " _sqldf") == text + " _sqldf"
    assert time.perf_counter() - started < 2


# ---- %python in a notebook whose default language isn't Python. "commands of the previous default
# language are automatically prefixed with a language magic command" (notebooks-code).

SQL_NOTEBOOK = "\n".join([
    "-- Databricks notebook source",
    "SELECT 41 AS answer",
    "", "-- COMMAND ----------", "",
    "-- MAGIC %python",
    "-- MAGIC n = _sqldf.collect()[0][0] + 1",
    "-- MAGIC n",
    "", "-- COMMAND ----------", "",
    "-- MAGIC %md The answer above.",
])


def test_python_cells_run_in_a_sql_default_notebook():
    cells = notebook_formats.parse_source(SQL_NOTEBOOK, "nb.sql")["cells"]
    k = Kernel()
    outs = [k.run(c["source"]) if c["type"] == "code" else None for c in cells]
    assert outs[0]["outputs"][0]["rows"] == [[41]]
    assert outs[1]["status"] == "ok" and outs[1]["outputs"] == [{"type": "text", "text": "42"}]
    assert cells[2] == {**cells[2], "type": "markdown"}
    assert k.run("%python\nn")["outputs"] == [{"type": "text", "text": "42"}]


# ---- %scala and %r: "Executes Scala code in the cell" (notebooks-code). This PC has no Scala or R.

@pytest.mark.parametrize("header", ["%scala", "%r", "%%spark", "%%sparkr"])
def test_scala_and_r_cells_are_refused_naming_the_magic(header):
    out = Kernel().run(f"{header}\nval x = 1")
    assert out["outputs"][-1]["ename"] == "RunsInFabric"
    assert _error(out) == (f"This {header} cell needs Fabric's Spark, which this PC doesn't have, so nothing ran. "
                           "Switch Runs on to Fabric to run it, or write the cell in Python or SQL.")


def test_a_csharp_cell_is_refused_without_sending_it_to_fabric():
    out = Kernel().run("%%csharp\nvar x = 1;")
    assert out["outputs"][-1]["ename"] == "RunsInFabric"
    assert _error(out) == ("This %%csharp cell is .NET for Spark, which neither this PC nor Fabric runs, so nothing "
                           "ran. Write the cell in Python, Scala or SQL.")


def test_runs_on_check_names_the_scala_cell():
    check = compute_check([_code("x = 1"), _code("%scala\nval y = 2")], None)
    assert [(r["cell"], r["text"]) for r in check["local"]["reasons"]] == [(2, "uses a Scala, R or .NET cell")]


# ---- %fs: "Execute dbutils filesystem commands. Shorthand for dbutils.fs commands." and
# "%fs ls /Volumes/main/default/my-volume/ is the same as dbutils.fs.ls(...)" (notebooks-code,
# databricks-utils).

def test_fs_subcommands_map_to_dbutils_fs(tmp_path):
    k = Kernel()
    root = f"file:{tmp_path.as_posix()}"
    assert k.run(f"%fs mkdirs {root}/in")["status"] == "ok"
    assert k.run(f'%fs put {root}/in/a.txt "one,two"')["status"] == "ok"
    assert "one,two" in k.run(f"%fs head {root}/in/a.txt")["outputs"][0]["text"]
    assert k.run(f"%fs cp {root}/in/a.txt {root}/in/b.txt")["status"] == "ok"
    assert k.run(f"%fs mv {root}/in/b.txt {root}/c.txt")["status"] == "ok"
    assert k.run(f"%fs rm {root}/in/a.txt")["status"] == "ok"
    listed = k.run(f"%fs ls {root}")["outputs"][0]
    same = k.run(f"sorted(f.name for f in dbutils.fs.ls('{root}'))")["outputs"][-1]["text"]
    assert sorted(r[0] for r in listed["rows"]) == eval(same) == ["c.txt", "in"]  # noqa: S307 - the kernel's repr
    assert k.run("%fs mounts")["outputs"] == k.run("dbutils.fs.mounts()")["outputs"] == [{"type": "text", "text": "[]"}]
    assert k.run("%fs refreshMounts")["outputs"] == [{"type": "text", "text": "True"}]


@pytest.mark.parametrize("line, command", [("ls dbfs:/databricks-datasets", "ls"),
                                           ("ls /Volumes/main/default/my-volume/", "ls"),
                                           ("head /FileStore/tables/a.csv", "head"),
                                           ("cp file:/tmp/a.csv dbfs:/tmp/a.csv", "cp")])
def test_fs_on_databricks_storage_is_refused_naming_the_command(line, command):
    """dbfs:/ and /Volumes/ don't exist on this PC, and a bare absolute path is DBFS in Databricks."""
    text = _error(Kernel().run(f"%fs {line}"))
    assert text.startswith(f"%fs {command}: ") and "file:" in text and "Files/" in text


@pytest.mark.parametrize("command", ["mount", "unmount", "updateMount"])
def test_fs_mounting_is_refused_naming_the_command(command):
    out = Kernel().run(f"%fs {command} wasbs://raw@acct.blob.core.windows.net /mnt/raw")
    assert out["outputs"][-1]["ename"] == "NotImplementedError"
    assert _error(out) == (f"%fs {command} mounts storage, which only Databricks can do, so nothing ran. On this "
                           "PC, read the attached lakehouse (Files/...) or a file: path instead.")


def test_fs_command_this_pc_lacks_is_refused_by_name():
    assert _error(Kernel().run("%fs chmod 777 Files/x")) == \
        "%fs chmod isn't a command here. Use ls, head, cp, mv, rm, mkdirs or put."


def test_runs_on_check_names_the_fs_cell_before_it_runs():
    cells = [_code("x = 1"), _code("%fs ls dbfs:/mnt/raw"), _code("%fs mount wasbs://a /mnt/a"),
             _code("%fs chmod 777 Files/x"), _code("%fs ls Files/raw\n%fs mounts")]
    reasons = compute_check(cells, None)["local"]["reasons"]
    assert [(r["cell"], r["text"]) for r in reasons] == [
        (2, "reads dbfs:/mnt/raw with %fs ls, which is Databricks storage this PC can't reach"),
        (3, "mounts storage with %fs mount, which runs in Databricks only"),
        (4, "runs %fs chmod, which this PC doesn't have (it has ls, head, cp, mv, rm, mkdirs and put)")]


# ---- %sh: "Execute shell commands. Runs only on driver node. Use -e to fail on error." and "To fail
# the cell if the shell command has a non-zero exit status, add the -e option." (notebooks-code).
# The script itself is stood in for: only how the cell treats its exit code is pinned here.

@pytest.fixture
def exits(monkeypatch):
    ran = []

    def script(kind, body, echo=None):
        ran.append((kind, body))
        return 3
    monkeypatch.setattr(shell, "script", script)
    return ran


def test_sh_e_is_an_option_not_a_command():
    assert magics.cell("%sh -e\nls") == magics.Cell("shell", "%sh", "\nls", "-e")
    assert magics.cell("%sh ls -e").body == " ls -e"  # a command that happens to end in -e is kept


def test_plain_sh_cell_goes_on_after_a_non_zero_exit_and_says_so(exits):
    k = Kernel()
    out = k.run("%sh\nfalse")
    assert out["status"] == "ok" and exits == [("sh", "\nfalse")]
    assert "exit code 3" in out["outputs"][0]["text"] and "%sh -e" in out["outputs"][0]["text"]
    assert k.run("_exit_code")["outputs"] == [{"type": "text", "text": "3"}]


def test_sh_e_cell_fails_on_a_non_zero_exit(exits):
    out = Kernel().run("%sh -e\nfalse")
    assert _error(out) == "The script ended with exit code 3." and exits == [("sh", "\nfalse")]


def test_ipython_sh_cells_still_fail_on_a_non_zero_exit(exits):
    assert Kernel().run("%%sh\nfalse")["status"] == "error"


def test_imported_sh_e_cell_keeps_its_option():
    parsed = notebook_formats.parse_source("# Databricks notebook source\n# MAGIC %sh -e\n# MAGIC ls\n", "nb.py")
    assert parsed["cells"][0]["source"] == "%sh -e\nls"


# ---- %pip: "Install Python packages (notebook-scoped)." and "%pip commands do not automatically
# restart the Python process." (notebooks-python-libraries). Installs go through the engine, which
# is stood in for, so nothing is installed; the organization's policy is the engine's to apply.

class Engine:
    def __init__(self, refuse=None):
        self.refuse, self.calls = refuse, []

    def __call__(self, method, *args):
        self.calls.append(method)
        if self.refuse:
            raise self.refuse
        return {"id": "j", "state": "done", "environment": "nb-env"}


def test_pip_install_keeps_the_session_python_running():
    k = Kernel()
    k.engine_call = Engine()
    k.run("kept = 'still here'")
    out = k.run("%pip install pytest")
    assert out["status"] == "ok" and k.engine_call.calls == ["install_packages"]
    assert k.run("kept")["outputs"] == [{"type": "text", "text": "'still here'"}]


def test_pip_install_turned_off_by_policy_is_refused():
    k = Kernel()
    k.engine_call = Engine(PermissionError("Your organization doesn't allow installing packages for notebooks on this PC."))
    out = k.run("%pip install requests")
    assert out["outputs"][-1]["ename"] == "PermissionError" and "Your organization" in _error(out)


def test_pip_without_its_own_process_installs_nothing(monkeypatch):
    monkeypatch.setattr(pip_magic, "time", None)  # an install that went on would poll; this one mustn't
    out = Kernel().run("%pip install requests")  # engine_call is None: the kernel runs inside the engine
    assert out["status"] == "ok" and "This line was skipped" in out["outputs"][0]["text"]


@pytest.mark.parametrize("line, words", [("uninstall -y requests", "%pip uninstall isn't run from a cell"),
                                         ("install --index-url https://x requests", "--index-url isn't supported")])
def test_pip_commands_this_pc_doesnt_run_are_refused_by_name(line, words):
    k = Kernel()
    k.engine_call = Engine()
    assert words in _error(k.run(f"%pip {line}")) and k.engine_call.calls == []
