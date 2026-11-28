"""More notebookutils-in-an-exercise tests, from a mutation run: cells and the parameters cell,
notebook.run's arguments, exit values and depth limit, runMultiple, every fs call under Files/ and what
it refuses, runtime.context, and install. The notebooks here are the test's own few lines (an
assignment and an exit), run as notebook.run runs them; no learner code runs."""

from __future__ import annotations

import json
import os
import sys

import pytest

from fabric_desktop.academy import notebookutils_lab as nl

MARK = "# CELL ********************"
PARAMS = "# PARAMETERS CELL ********************"


@pytest.fixture
def utils(tmp_path):
    (tmp_path / nl.NOTEBOOKS).mkdir()
    (tmp_path / nl.FILES).mkdir()
    box = {}
    box["u"] = u = nl.Utils(tmp_path, lambda: {"nb": box["u"].notebook, "ctx": box["u"].runtime.context, "seen": box})
    return u


def _nb(utils, name, text):
    (utils.work / nl.NOTEBOOKS / f"{name}.py").write_bytes(text.encode("utf-8"))


# ---- cells

def test_cells_split_at_markers_and_flag_the_parameters_cell():
    text = "\n".join(["a = 1", MARK, "b = 2", "", PARAMS, "c = 3", MARK, MARK, "d = 4"])
    assert nl.cells(text) == [(False, 1, "a = 1"), (False, 3, "b = 2\n"), (True, 6, "c = 3"), (False, 9, "d = 4")]
    assert nl.cells("") == []
    assert nl.cells(MARK) == []
    assert nl.cells("# NOT A CELL ****\nx") == [(False, 1, "# NOT A CELL ****\nx")]


# ---- notebook.run

def test_a_notebook_is_named_with_or_without_py_and_must_be_there(utils):
    _nb(utils, "clean", "x = 1\n")
    assert utils._notebook("clean") == utils.work / nl.NOTEBOOKS / "clean.py"
    assert utils._notebook("clean.py") == utils.work / nl.NOTEBOOKS / "clean.py"
    with pytest.raises(ValueError, match="isn't a notebook name like clean_orders"):
        utils._notebook("../clean")
    with pytest.raises(ValueError):
        utils._notebook("a" * 64)
    _nb(utils, "a" * 63, "x = 1\n")
    assert utils._notebook("a" * 63).name == "a" * 63 + ".py"
    with pytest.raises(FileNotFoundError, match=r"There's no notebook other in this exercise \(notebooks/other.py\)\."):
        utils._notebook("other")


def test_run_returns_the_exit_value_as_a_string_or_none(utils):
    _nb(utils, "ends", "nb.exit(12)\n")
    _nb(utils, "none", "nb.exit(None)\n")
    _nb(utils, "quiet", "x = 1\n")
    assert utils.run("ends") == "12"
    assert utils.run("none") is None
    assert utils.run("quiet") is None
    with pytest.raises(NotImplementedError, match="leave out workspace"):
        utils.run("ends", workspace="other")


def test_arguments_are_set_after_the_parameters_cell_or_before_the_first(utils):
    _nb(utils, "params", "\n".join(["x = 'before'", PARAMS, "x = 'default'", MARK, "nb.exit(x)"]))
    _nb(utils, "plain", "\n".join(["nb.exit(x)"]))
    _nb(utils, "late", "\n".join(["first = x", MARK, "x = 'cell'", MARK, "nb.exit(first + '/' + x)"]))
    assert utils.run("params", 90, {"x": "given"}) == "given"
    assert utils.run("params") == "default"
    assert utils.run("plain", 90, {"x": "given"}) == "given"
    assert utils.run("late", 90, {"x": "given"}) == "given/cell"


def test_argument_names_are_python_names(utils):
    _nb(utils, "a", "x = 1\n")
    with pytest.raises(ValueError, match=r"^Notebook arguments are named like Python variables; 'not ok' isn't\.$"):
        utils.run("a", 90, {"ok": 1, "not ok": 2})


def test_a_notebook_that_runs_itself_stops_at_ten(utils):
    _nb(utils, "loop", "seen['depth'] = seen.get('depth', 0) + 1\nnb.run('loop')\n")
    with pytest.raises(RecursionError) as err:
        utils.run("loop")
    assert str(err.value) == ("Notebook 'loop' would be the 11th notebook run inside another; the exercise stops at 10, "
                              "so a notebook can't run itself forever.")
    assert utils._running == [] and utils.runtime.context["currentNotebookName"] == "answer"


def test_the_context_names_the_running_notebook(utils):
    _nb(utils, "who", "nb.exit(ctx['currentNotebookName'] + ' ' + str(ctx['isReferenceRun']))\n")
    assert utils.run("who") == "who True"
    ctx = utils.runtime.context
    assert ctx["currentNotebookName"] == "answer" and ctx["isReferenceRun"] is False
    assert json.loads(json.dumps(ctx)) == {"currentNotebookName": "answer", "isLocal": True, "isReferenceRun": False,
                                           "isForPipeline": False, "currentWorkspaceName": "Academy exercise",
                                           "defaultLakehouseName": "exercise"}


def test_the_context_reads_fresh_through_every_dict_method(utils):
    ctx = utils.runtime.context
    utils._running.append("nbx")
    assert ctx.get("currentNotebookName") == "nbx"
    utils._running[-1] = "nby"
    assert dict(ctx.items())["currentNotebookName"] == "nby"
    utils._running[-1] = "nbz"
    assert "nbz" in list(ctx.values())
    utils._running[-1] = "nbk"
    assert "currentNotebookName" in list(ctx.keys())
    assert "nbk" in repr(ctx)
    dict.clear(ctx)
    assert "isLocal" in ctx
    dict.clear(ctx)
    assert "isLocal" in list(iter(ctx))
    dict.clear(ctx)
    assert len(list(ctx.keys())) == 6
    dict.clear(ctx)
    assert len(list(ctx.values())) == 6 and ctx.get("nope", 5) == 5
    utils._running.clear()


def test_run_multiple_runs_each_notebook_and_validate_dag_checks_it(utils, capsys):
    _nb(utils, "a", "nb.exit('A')\n")
    _nb(utils, "b", "nb.exit('B')\n")
    got = utils.notebook.runMultiple({"activities": [{"name": "a", "path": "a"},
                                                     {"name": "b", "path": "b", "dependencies": ["a"]}]})
    assert {k: v["exitVal"] for k, v in got.items()} == {"a": "A", "b": "B"}
    assert utils.notebook.validateDAG(["a", "b"]) is True
    with pytest.raises(ValueError):
        utils.notebook.validateDAG(["a", "a"])
    utils.notebook.help()
    utils.fs.help()
    out = capsys.readouterr().out
    assert "run, exit, runMultiple, validateDAG" in out and "ls, exists, head, put, append, mkdirs, rm, cp, mv" in out


# ---- what an exercise doesn't have

def test_the_fabric_only_parts_say_so(utils):
    with pytest.raises(NotImplementedError, match=r"^notebookutils\.credentials\.getToken isn't in an Academy exercise"):
        utils.credentials.getToken("x")
    with pytest.raises(AttributeError):
        utils.lakehouse.__wrapped__   # noqa: B018 - a dunder lookup is a plain AttributeError
    for name in ("credentials", "lakehouse", "variableLibrary", "udf", "warehouse", "connections", "session", "env"):
        assert isinstance(getattr(utils, name), nl._Absent)


# ---- files

@pytest.mark.parametrize("path", ["/Files/x", "abfss://ws@onelake/x", "file:Files/x", "http://x/Files", "https://x",
                                  "Tables/x", "", "Files/../x", "."])
def test_paths_outside_files_are_refused(utils, path):
    with pytest.raises(ValueError, match="an exercise's files are under Files/"):
        utils._local(path)


@pytest.mark.parametrize("path", ["Files/a.txt", "file:/lakehouse/default/Files/a.txt", "/lakehouse/default/Files/a.txt",
                                  "lakehouse/default/Files/a.txt", "Files\\a.txt", "./Files//a.txt"])
def test_lakehouse_prefixes_and_backslashes_name_the_same_file(utils, path):
    assert utils._local(path) == utils.work / nl.FILES / "a.txt"


def test_a_link_on_the_way_is_refused(utils, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    try:
        os.symlink(outside, utils.work / nl.FILES / "out", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(PermissionError, match="^out is a link"):
        utils._local("Files/out/x.txt")
    with pytest.raises(PermissionError, match="^That path isn't in this exercise"):
        nl._no_link(outside / "x", utils.work)


def test_ls_lists_a_folder_or_one_file_with_fabrics_fields(utils):
    files = utils.work / nl.FILES
    (files / "land").mkdir()
    (files / "land" / "b.csv").write_bytes(b"12345")
    (files / "land" / "a").mkdir()
    got = utils.fs.ls("Files/land/")
    assert [(f.name, f.path, f.isDir, f.size, f.isFile()) for f in got] == [
        ("a", "Files/land/a", True, 0, False), ("b.csv", "Files/land/b.csv", False, 5, True)]
    assert isinstance(got[1].modifyTime, int) and got[1].modifyTime > 10 ** 12
    assert repr(got[1]) == "FileInfo(path=Files/land/b.csv, name=b.csv, size=5)"
    one = utils.fs.ls("Files/land/b.csv")
    assert [(f.name, f.path) for f in one] == [("b.csv", "Files/land/b.csv")]
    with pytest.raises(FileNotFoundError, match="^Files/nope doesn't exist.$"):
        utils.fs.ls("Files/nope")


def test_put_head_append_and_exists(utils):
    assert utils.fs.exists("Files/new/a.txt") is False
    assert utils.fs.put("Files/new/a.txt", "héllo") is True
    assert utils.fs.exists("Files/new/a.txt") is True
    with pytest.raises(FileExistsError, match="pass overwrite=True"):
        utils.fs.put("Files/new/a.txt", "x")
    assert utils.fs.put("Files/new/a.txt", "abcdef", overwrite=True) is True
    assert utils.fs.head("Files/new/a.txt") == "abcdef" and utils.fs.head("Files/new/a.txt", 3) == "abc"
    assert utils.fs.append("Files/new/a.txt", "gh") is True
    assert utils.fs.head("Files/new/a.txt") == "abcdefgh"
    with pytest.raises(FileNotFoundError, match="pass createFileIfNotExists=True"):
        utils.fs.append("Files/other/b.txt", "x")
    assert utils.fs.append("Files/other/b.txt", "x", createFileIfNotExists=True) is True
    assert utils.fs.head("Files/other/b.txt") == "x"


def test_mkdirs_rm_cp_and_mv(utils):
    files = utils.work / nl.FILES
    assert utils.fs.mkdirs("Files/d/e") is True and (files / "d" / "e").is_dir()
    utils.fs.put("Files/d/e/x.txt", "x")
    with pytest.raises(PermissionError, match="Files/ itself can't be removed"):
        utils.fs.rm("Files/", recurse=True)
    with pytest.raises(IsADirectoryError, match="pass recurse=True to copy"):
        utils.fs.cp("Files/d", "Files/d2")
    assert utils.fs.cp("Files/d", "Files/d2", recurse=True) is True and (files / "d2" / "e" / "x.txt").is_file()
    assert utils.fs.cp("Files/d/e/x.txt", "Files/deep/er/y.txt") is True
    assert (files / "deep" / "er" / "y.txt").read_bytes() == b"x"
    with pytest.raises(IsADirectoryError, match="pass recurse=True to remove"):
        utils.fs.rm("Files/d2")
    assert utils.fs.rm("Files/d2", recurse=True) is True and not (files / "d2").exists()
    assert utils.fs.rm("Files/deep/er/y.txt") is True and not (files / "deep" / "er" / "y.txt").exists()
    with pytest.raises(FileNotFoundError, match="^Files/gone doesn't exist.$"):
        utils.fs.rm("Files/gone")
    utils.fs.put("Files/m.txt", "m")
    utils.fs.put("Files/n.txt", "n")
    with pytest.raises(FileExistsError, match="pass overwrite=True"):
        utils.fs.mv("Files/m.txt", "Files/n.txt")
    assert utils.fs.mv("Files/m.txt", "Files/n.txt", overwrite=True) is True
    assert (files / "n.txt").read_bytes() == b"m" and not (files / "m.txt").exists()
    with pytest.raises(OSError):
        utils.fs.mv("Files/n.txt", "Files/new/dir/n.txt")
    assert utils.fs.mv("Files/n.txt", "Files/new/dir/n.txt", create_path=True) is True
    assert (files / "new" / "dir" / "n.txt").is_file()


# ---- install

def test_install_gives_notebookutils_and_mssparkutils(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "notebookutils", raising=False)
    monkeypatch.delitem(sys.modules, "mssparkutils", raising=False)
    module = nl.install(tmp_path, dict)
    assert sys.modules["notebookutils"] is module and sys.modules["mssparkutils"] is module
    assert module.mssparkutils is module and module.NotebookExit is nl.NotebookExit
    for name in ("notebook", "fs", "runtime", "credentials", "lakehouse", "variableLibrary", "udf", "warehouse",
                 "connections", "session", "env"):
        assert hasattr(module, name)
    assert module.runtime.context["currentNotebookName"] == "answer"
    with pytest.raises(nl.NotebookExit) as done:
        module.notebook.exit("v")
    assert done.value.value == "v" and str(done.value) == "v"
    with pytest.raises(nl.NotebookExit) as done:
        module.notebook.exit()
    assert done.value.value == ""


def test_writes_make_missing_folders_and_reuse_existing_ones(utils):
    files = utils.work / nl.FILES
    assert utils.fs.put("Files/p/q/a.txt", "a") and utils.fs.put("Files/p/q/b.txt", "b")
    assert utils.fs.append("Files/r/s/a.txt", "a", True) and utils.fs.append("Files/r/s/b.txt", "b", True)
    assert utils.fs.mkdirs("Files/m/n") and utils.fs.mkdirs("Files/m/n")
    assert utils.fs.cp("Files/p/q/a.txt", "Files/c/d/a.txt") and utils.fs.cp("Files/p/q/b.txt", "Files/c/d/b.txt")
    assert utils.fs.cp("Files/p", "Files/c", recurse=True)        # into a folder that is there
    assert (files / "c" / "q" / "a.txt").is_file() and (files / "c" / "d" / "b.txt").is_file()
    assert utils.fs.mv("Files/p/q/a.txt", "Files/v/w/a.txt", create_path=True)
    assert utils.fs.mv("Files/p/q/b.txt", "Files/v/w/b.txt", create_path=True)
    assert sorted(p.name for p in (files / "v" / "w").iterdir()) == ["a.txt", "b.txt"]


def test_a_link_inside_a_copied_folder_is_copied_as_a_link(utils):
    files = utils.work / nl.FILES
    (files / "src").mkdir()
    (files / "src" / "real.txt").write_bytes(b"r")
    try:
        os.symlink("real.txt", files / "src" / "alias.txt")
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    utils.fs.cp("Files/src", "Files/dst", recurse=True)
    assert (files / "dst" / "alias.txt").is_symlink()


def test_the_context_names_the_innermost_running_notebook(utils):
    _nb(utils, "outer", "nb.exit(nb.run('inner') + '|' + ctx['currentNotebookName'])\n")
    _nb(utils, "inner", "nb.exit(ctx['currentNotebookName'])\n")
    assert utils.run("outer") == "inner|outer"


def test_head_reads_64_kib_by_default(utils):
    (utils.work / nl.FILES / "big.txt").write_bytes(b"z" * 70_000)
    assert len(utils.fs.head("Files/big.txt")) == 65536


def test_ls_gives_the_modified_time_in_milliseconds(utils):
    (utils.work / nl.FILES / "a.txt").write_bytes(b"a")
    os.utime(utils.work / nl.FILES / "a.txt", (1_700_000_000.25, 1_700_000_000.25))
    assert utils.fs.ls("Files/a.txt")[0].modifyTime == 1_700_000_000_250


def test_an_error_in_a_notebook_names_its_own_line(utils):
    import traceback
    _nb(utils, "fails", "\n".join(["x = 1", MARK, "y = 2", "", "raise ValueError('line 5')"]))
    with pytest.raises(ValueError) as err:
        utils.run("fails")
    last = traceback.extract_tb(err.value.__traceback__)[-1]
    assert (last.filename, last.lineno) == (f"{nl.NOTEBOOKS}/fails.py", 5)
