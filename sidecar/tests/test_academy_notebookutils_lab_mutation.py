"""academy/notebookutils_lab.py paths that its first mutation run (batch 428, docs/quality/mutation/batch-428.md)
showed no test checked. The notebookutils an exercise sees is built here in this process, on an exercise
folder in tmp_path, so no learner process starts."""

from __future__ import annotations

import json
import sys

import pytest

from fabric_desktop.academy import notebookutils_lab as nu

MARK = "# CELL ********************"
PARAMS = "# PARAMETERS CELL ********************"


@pytest.fixture
def work(tmp_path):
    (tmp_path / "notebooks").mkdir()
    (tmp_path / "Files" / "landing").mkdir(parents=True)
    (tmp_path / "Files" / "landing" / "day1.csv").write_text("id\n1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def utils(work):
    return nu.Utils(work, lambda: {"spark": "the session"})


def notebook(work, name, text):
    (work / "notebooks" / f"{name}.py").write_text(text, encoding="utf-8")


def test_cells_start_after_each_marker_with_their_first_line():
    text = f"x = 1\n{MARK}\ny = 2\n{PARAMS}\nz = 3\nw = 4\n{MARK}\n{MARK}\nlast = 5"
    assert nu.cells(text) == [(False, 1, "x = 1"), (False, 3, "y = 2"), (True, 5, "z = 3\nw = 4"), (False, 9, "last = 5")]
    assert nu.cells(f"{MARK}\n") == [] and nu.cells("") == []
    assert nu.cells("# MAGIC ********************\na") == [(False, 2, "a")]
    assert nu.cells("#  CELL ********************\na") == [(False, 1, "#  CELL ********************\na")]


def test_run_sets_arguments_after_the_parameters_cell_and_returns_the_exit_value(work, utils):
    notebook(work, "child", f"{PARAMS}\nregion = 'all'\n{MARK}\nnotebookutils_exit(region + str(spark))\n")
    seen = {}

    def namespace():
        ns = {"spark": 1, "notebookutils_exit": nu.Utils.exit}
        seen["ns"] = ns
        return ns

    u = nu.Utils(work, namespace)
    assert u.run("child", 60, {"region": "east"}) == "east1"
    assert u.run("child.py") == "all1"
    assert seen["ns"]["region"] == "all"


def test_without_a_parameters_cell_the_arguments_are_set_first(work):
    notebook(work, "child", "got = region\n")
    ns = {}
    u = nu.Utils(work, lambda: ns)
    assert u.run("child", 0, {"region": "west"}) is None and ns["got"] == "west"


def test_exit_values_are_strings_and_none_stays_none(work, utils):
    notebook(work, "seven", "raise NotebookExit(7)\n")
    notebook(work, "nothing", "raise NotebookExit(None)\n")
    u = nu.Utils(work, lambda: {"NotebookExit": nu.NotebookExit})
    assert u.run("seven") == "7" and u.run("nothing") is None
    with pytest.raises(nu.NotebookExit) as done:
        nu.Utils.exit()
    assert done.value.value == "" and str(nu.NotebookExit(12)) == "12"


def test_a_failing_cell_names_the_notebook_and_its_line(work):
    notebook(work, "broken", f"a = 1\n{MARK}\nb = 2\nraise ValueError('bad')\n")
    u = nu.Utils(work, dict)
    with pytest.raises(ValueError) as err:
        u.run("broken")
    frame = err.traceback[-1]
    assert str(frame.path) == "notebooks/broken.py"
    assert frame.lineno == 3  # 0-based: line 4 of the file
    assert u._running == []


@pytest.mark.parametrize("name, error, says", [
    ("x y", ValueError, "'x y' isn't a notebook name like clean_orders."),
    ("a" * 64, ValueError, "isn't a notebook name"),
    ("missing", FileNotFoundError, "There's no notebook missing in this exercise (notebooks/missing.py)."),
])
def test_a_notebook_is_named_like_a_variable_and_must_be_there(utils, name, error, says):
    with pytest.raises(error) as err:
        utils.run(name)
    assert says in str(err.value)


def test_a_notebook_name_of_63_characters_is_fine(work, utils):
    name = "a" * 63
    notebook(work, name, "x = 1\n")
    assert utils.run(name) is None


def test_another_workspace_and_bad_argument_names_are_refused(work, utils):
    notebook(work, "child", "x = 1\n")
    with pytest.raises(NotImplementedError, match="leave out workspace"):
        utils.run("child", workspace="other")
    with pytest.raises(ValueError, match="'not ok' isn't"):
        utils.run("child", 0, {"ok": 1, "not ok": 2})
    with pytest.raises(ValueError, match="1 isn't"):
        utils.run("child", 0, {1: 2})


def test_ten_notebooks_deep_is_the_limit(work):
    notebook(work, "loop", "depth.append(1)\nrun('loop')\n")
    depth = []
    u = nu.Utils(work, lambda: {"depth": depth, "run": u.run})
    with pytest.raises(RecursionError, match="would be the 11th notebook run inside another; the exercise stops at 10"):
        u.run("loop")
    assert len(depth) == 10 and u._running == []


def test_the_context_names_the_running_notebook(work):
    notebook(work, "child", "seen = dict(ctx)\nname = ctx['currentNotebookName']\n")
    ns = {}
    u = nu.Utils(work, lambda: ns)
    ctx = u.runtime.context
    ns["ctx"] = ctx
    assert ctx["currentNotebookName"] == "answer" and ctx["isReferenceRun"] is False
    u.run("child")
    assert ns["name"] == "child" and ns["seen"]["isReferenceRun"] is True
    assert json.loads(json.dumps(ctx)) == {"currentNotebookName": "answer", "isLocal": True, "isReferenceRun": False,
                                          "isForPipeline": False, "currentWorkspaceName": "Academy exercise",
                                          "defaultLakehouseName": "exercise"}
    assert ctx.get("isLocal") is True and ctx.get("nope", 3) == 3 and "isLocal" in ctx
    assert sorted(ctx) == sorted(ctx.keys()) and len(list(ctx.items())) == len(list(ctx.values())) == 6
    assert "answer" in repr(ctx)


def test_run_multiple_runs_each_activity_with_its_arguments(work, utils):
    notebook(work, "child", f"{PARAMS}\nv = 0\n{MARK}\nraise NotebookExit(v)\n")
    u = nu.Utils(work, lambda: {"NotebookExit": nu.NotebookExit})
    dag = {"activities": [{"name": "a", "path": "child", "args": {"v": 1}},
                          {"name": "b", "path": "child", "args": {"v": 2}, "dependencies": ["a"]}]}
    out = u.notebook.runMultiple(dag)
    assert {k: v["exitVal"] for k, v in out.items()} == {"a": "1", "b": "2"}
    assert u.notebook.validateDAG(dag) is True
    with pytest.raises(ValueError, match="needs a path and a unique name"):
        u.notebook.validateDAG({"activities": [{"name": "a"}]})


def test_what_needs_fabric_says_so(utils):
    with pytest.raises(NotImplementedError, match=r"notebookutils.credentials.getToken isn't in an Academy exercise"):
        utils.credentials.getToken("x")
    with pytest.raises(AttributeError):
        utils.lakehouse.__wrapped__  # noqa: B018
    for name in ("credentials", "lakehouse", "variableLibrary", "udf", "warehouse", "connections", "session", "env"):
        with pytest.raises(NotImplementedError, match=f"notebookutils.{name}.x "):
            getattr(utils, name).x  # noqa: B018


def test_fs_works_under_files(work, utils):
    fs = utils.fs
    assert fs.put("Files/out/note.txt", "hi") is True
    with pytest.raises(FileExistsError, match="Files/out/note.txt exists; pass overwrite=True"):
        fs.put("Files/out/note.txt", "again")
    assert fs.put("Files/out/note.txt", "hello", overwrite=True) and fs.head("Files/out/note.txt") == "hello"
    assert fs.append("Files/out/note.txt", " there") and fs.head("Files/out/note.txt", 7) == "hello t"
    with pytest.raises(FileNotFoundError, match="createFileIfNotExists=True"):
        fs.append("Files/out/new.txt", "x")
    assert fs.append("Files/deep/new.txt", "x", createFileIfNotExists=True) and fs.head("Files/deep/new.txt") == "x"
    assert fs.mkdirs("Files/a/b") and (work / "Files" / "a" / "b").is_dir() and fs.mkdirs("Files/a/b")
    assert fs.exists("/lakehouse/default/Files/landing/day1.csv") and fs.exists("file:/lakehouse/default/Files/out")
    assert fs.exists("lakehouse/default/Files/landing") and not fs.exists("Files/none")
    assert fs.exists("Files\\landing\\day1.csv") and fs.exists("Files/./landing//day1.csv")


def test_ls_lists_a_folder_or_one_file_with_fabric_s_fields(work, utils):
    entries = utils.fs.ls("Files/landing/")
    [day] = entries
    assert (day.name, day.path, day.isDir, day.size, day.isFile()) == ("day1.csv", "Files/landing/day1.csv", False, 5, True)
    assert day.modifyTime == int((work / "Files" / "landing" / "day1.csv").stat().st_mtime * 1000)
    assert repr(day) == "FileInfo(path=Files/landing/day1.csv, name=day1.csv, size=5)"
    [one] = utils.fs.ls("Files/landing/day1.csv")
    assert one.path == "Files/landing/day1.csv"
    [folder] = utils.fs.ls("Files")
    assert (folder.name, folder.isDir, folder.size, folder.isFile()) == ("landing", True, 0, False)
    with pytest.raises(FileNotFoundError, match="Files/none doesn't exist."):
        utils.fs.ls("Files/none")


@pytest.mark.parametrize("path", ["Tables/x", "Files/../answer.py", "/etc/passwd", "abfss://x/Files/a", "file:/tmp/x",
                                  "http://x/Files", "https://x/Files", "", "."])
def test_fs_refuses_anything_outside_files(utils, path):
    with pytest.raises(ValueError, match="an exercise's files are under Files/"):
        utils.fs.exists(path)


def test_rm_cp_and_mv(work, utils):
    fs = utils.fs
    with pytest.raises(PermissionError, match="Files/ itself can't be removed."):
        fs.rm("Files", recurse=True)
    with pytest.raises(IsADirectoryError, match="Files/landing is a folder; pass recurse=True to remove it."):
        fs.rm("Files/landing")
    with pytest.raises(FileNotFoundError, match="Files/none doesn't exist."):
        fs.rm("Files/none")
    with pytest.raises(IsADirectoryError, match="pass recurse=True to copy it."):
        fs.cp("Files/landing", "Files/copy")
    assert fs.cp("Files/landing", "Files/copy", recurse=True) and fs.exists("Files/copy/day1.csv")
    assert fs.cp("Files/landing/day1.csv", "Files/x/y/day.csv") and fs.head("Files/x/y/day.csv") == "id\n1\n"
    with pytest.raises(FileExistsError, match="Files/copy/day1.csv exists; pass overwrite=True"):
        fs.mv("Files/x/y/day.csv", "Files/copy/day1.csv")
    assert fs.mv("Files/x/y/day.csv", "Files/copy/day1.csv", overwrite=True) and not fs.exists("Files/x/y/day.csv")
    with pytest.raises(FileNotFoundError):
        fs.mv("Files/copy/day1.csv", "Files/new/day1.csv")
    assert fs.mv("Files/copy/day1.csv", "Files/new/day1.csv", create_path=True) and fs.exists("Files/new/day1.csv")
    assert fs.rm("Files/new/day1.csv") and not fs.exists("Files/new/day1.csv")
    assert fs.rm("Files/copy", recurse=True) and not fs.exists("Files/copy")


@pytest.mark.skipif(sys.platform == "win32", reason="making a symlink needs a privilege on Windows")
def test_a_link_on_the_way_is_refused(work, utils, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (work / "Files" / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError, match="linked is a link"):
        utils.fs.exists("Files/linked/x")
    (work / "notebooks" / "ln.py").symlink_to(work / "Files" / "landing" / "day1.csv")
    with pytest.raises(PermissionError, match="ln.py is a link"):
        utils.run("ln")
    with pytest.raises(PermissionError, match="That path isn't in this exercise."):
        nu._no_link(outside / "x", work)


def test_help_names_what_is_there(utils, capsys):
    utils.notebook.help()
    utils.fs.help()
    out = capsys.readouterr().out
    assert "run, exit, runMultiple, validateDAG" in out and "ls, exists, head, put, append, mkdirs, rm, cp, mv" in out


def test_install_gives_notebookutils_and_mssparkutils(work, monkeypatch):
    for name in ("notebookutils", "mssparkutils"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    module = nu.install(work, dict)
    assert sys.modules["notebookutils"] is module and sys.modules["mssparkutils"] is module
    assert module.mssparkutils is module and module.NotebookExit is nu.NotebookExit
    for name in ("notebook", "fs", "runtime", "credentials", "lakehouse", "variableLibrary", "udf", "warehouse",
                 "connections", "session", "env"):
        assert hasattr(module, name), name
    assert module.runtime.context["currentNotebookName"] == "answer"


def test_head_reads_64_kib_unless_told_otherwise(work, utils):
    (work / "Files" / "big.txt").write_bytes(b"a" * 70000)
    assert len(utils.fs.head("Files/big.txt")) == 65536


def test_put_append_and_copy_make_the_folders_they_need(work, utils):
    fs = utils.fs
    assert fs.put("Files/a/b/c.txt", "x") and fs.head("Files/a/b/c.txt") == "x"
    assert fs.append("Files/d/e/f.txt", "y", createFileIfNotExists=True) and fs.head("Files/d/e/f.txt") == "y"
    fs.mkdirs("Files/into")
    (work / "Files" / "into" / "kept.txt").write_text("k", encoding="utf-8")
    assert fs.cp("Files/landing", "Files/into", recurse=True)
    assert fs.exists("Files/into/day1.csv") and fs.exists("Files/into/kept.txt")


def test_copy_and_move_into_folders_that_are_there_or_not_yet(work, utils):
    fs = utils.fs
    assert fs.cp("Files/landing/day1.csv", "Files/landing/again.csv") and fs.exists("Files/landing/again.csv")
    assert fs.mv("Files/landing/again.csv", "Files/landing/moved.csv", create_path=True)
    assert fs.mv("Files/landing/moved.csv", "Files/p/q/moved.csv", create_path=True) and fs.exists("Files/p/q/moved.csv")


def test_inside_a_notebook_another_runs_the_context_names_the_innermost(work):
    notebook(work, "outer", "run('inner')\n")
    notebook(work, "inner", "names.append(ctx['currentNotebookName'])\n")
    names = []
    u = nu.Utils(work, lambda: {"run": u.run, "ctx": u.runtime.context, "names": names})
    u.run("outer")
    assert names == ["inner"]


@pytest.mark.skipif(sys.platform == "win32", reason="making a symlink needs a privilege on Windows")
def test_copying_a_folder_never_reads_through_a_link_inside_it(work, utils, tmp_path_factory):
    secret = tmp_path_factory.mktemp("outside") / "secret.txt"
    secret.write_text("not the exercise's", encoding="utf-8")
    (work / "Files" / "landing" / "link.txt").symlink_to(secret)
    assert utils.fs.cp("Files/landing", "Files/copy", recurse=True)
    assert (work / "Files" / "copy" / "link.txt").is_symlink()
    with pytest.raises(PermissionError, match="link.txt is a link"):
        utils.fs.head("Files/copy/link.txt")
