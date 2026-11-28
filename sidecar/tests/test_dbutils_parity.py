"""Databricks dbutils parity on this PC: the documented calls test_dbutils.py doesn't reach. Each one
either works as in Databricks or says plainly that it runs in Databricks only. Nothing leaves this PC."""

from __future__ import annotations

import json
import types

import pytest

from fabric_desktop import dbutils_local, http_session
from fabric_desktop.notebook import Kernel


def _ok(out):
    assert out["status"] == "ok", out
    return out


def _last(out):
    return _ok(out)["outputs"][-1]["text"]


def _error(out):
    assert out["status"] == "error", out
    return out["outputs"][-1]


# ---------------------------------------------------------------- fs


def test_fs_mv_moves_a_file_on_this_pc(tmp_path):
    src, dest = (tmp_path / "a.txt").as_posix(), (tmp_path / "b.txt").as_posix()
    k = Kernel()
    _ok(k.run(f"dbutils.fs.put('file:{src}', 'hi')\ndbutils.fs.mv('file:{src}', 'file:{dest}')"))
    assert not (tmp_path / "a.txt").exists() and (tmp_path / "b.txt").read_text() == "hi"


@pytest.mark.parametrize("src, dest", [("dbfs:/tmp/a", "file:/x"), ("file:/x", "dbfs:/mnt/raw/a"),
                                       ("/mnt/raw/a", "file:/x")])
def test_fs_mv_refuses_databricks_storage_on_either_side(tmp_path, src, dest):
    out = _error(Kernel().run(f"dbutils.fs.mv({src!r}, {dest!r})"))
    assert "Databricks storage" in out["evalue"] or "DBFS path" in out["evalue"]


@pytest.mark.parametrize("call", ["mount('abfss://x', '/mnt/x')", "unmount('/mnt/x')", "updateMount('a', '/mnt/x')"])
def test_mounting_says_it_runs_in_databricks(call):
    out = _error(Kernel().run(f"dbutils.fs.{call}"))
    assert out["ename"] == "NotImplementedError" and "runs in Databricks" in out["evalue"]


def test_listing_mounts_is_empty_and_refreshing_them_succeeds():
    assert _last(Kernel().run("(dbutils.fs.mounts(), dbutils.fs.refreshMounts())")) == "([], True)"


@pytest.mark.parametrize("part, names", [
    ("fs", "ls, head, put, cp, mv, rm, mkdirs"),
    ("widgets", "text, dropdown, combobox, multiselect"),
    ("secrets", "get, getBytes, list, listScopes"),
    ("notebook", "run, exit"),
])
def test_help_says_what_works_on_this_pc(part, names):
    out = _ok(Kernel().run(f"dbutils.{part}.help()"))
    assert names in out["outputs"][0]["text"]


# ---------------------------------------------------------------- widgets


def test_a_combobox_takes_a_value_that_isnt_one_of_its_choices():
    k = Kernel()
    _ok(k.run("dbutils.widgets.combobox('fruit', 'apple', ['apple', 'pear'], 'Fruit')"))
    k.widget_values = {"fruit": "kiwi"}       # typed into the box, as a combobox allows
    assert _last(k.run("dbutils.widgets.get('fruit')")) == "'kiwi'"
    assert k.namespace["dbutils"].widgets.describe()[0]["kind"] == "combobox"


def test_a_dropdown_value_set_in_the_app_that_isnt_a_choice_falls_back_to_the_default():
    k = Kernel()
    _ok(k.run("dbutils.widgets.dropdown('env', 'dev', ['dev', 'prod'])"))
    k.widget_values = {"env": "qa"}
    assert _last(k.run("dbutils.widgets.get('env')")) == "'dev'"


def test_removing_a_widget_that_doesnt_exist_says_so():
    assert _error(Kernel().run("dbutils.widgets.remove('nope')"))["ename"] == "InputWidgetNotDefined"


def test_remove_all_clears_every_widget():
    k = Kernel()
    _ok(k.run("dbutils.widgets.text('a', '1')\ndbutils.widgets.text('b', '2')"))
    k.widget_values = {"a": "x"}
    _ok(k.run("dbutils.widgets.removeAll()"))
    assert _last(k.run("dbutils.widgets.getAll()")) == "{}"
    assert _error(k.run("dbutils.widgets.get('b')"))["ename"] == "InputWidgetNotDefined"


def test_a_multiselect_default_must_be_among_its_choices():
    out = _error(Kernel().run("dbutils.widgets.multiselect('d', 'Mon,Sun', ['Mon', 'Tue'])"))
    assert "isn't one of the choices" in out["evalue"]


# ---------------------------------------------------------------- secrets


class _Refused:
    def __init__(self, status):
        self.status_code = status

    def json(self):
        return {"error": {"code": "Forbidden", "message": "Caller is not authorized"}}


@pytest.mark.parametrize("status", [401, 403])
def test_a_key_vault_that_refuses_to_list_names_the_role_needed(monkeypatch, status):
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=lambda *a, **k: _Refused(status)))
    out = _error(Kernel(token=lambda audience: "tok").run("dbutils.secrets.list('kv')"))
    assert out["ename"] == "PermissionError" and "Key Vault Secrets User" in out["evalue"]
    assert "tok" not in out["evalue"]


def test_list_scopes_explains_that_a_scope_is_a_key_vault_name():
    out = _ok(Kernel().run("dbutils.secrets.listScopes()"))
    assert "Azure Key Vault name" in out["outputs"][0]["text"] and out["outputs"][-1]["text"] == "[]"


# ---------------------------------------------------------------- task values, context, the rest


def test_a_task_value_never_set_needs_a_default(tmp_path):
    from fabric_desktop.notebook import NotebookStore
    store = NotebookStore(tmp_path)
    store.save("load", [{"type": "code", "source": "dbutils.jobs.taskValues.set('rows', 1)"}])
    store.save("report", [{"type": "code", "source":
                           "dbutils.notebook.exit(dbutils.jobs.taskValues.get('load', 'missing', default='none'))"}])
    store.save("strict", [{"type": "code", "source": "dbutils.jobs.taskValues.get('load', 'missing')"}])
    k = Kernel(load_notebook=lambda name: store.load(name)["cells"])
    out = k.run("r = notebookutils.notebook.runMultiple({'activities': [{'name': 'load', 'path': 'load'}, "
                "{'name': 'report', 'path': 'report', 'dependencies': ['load']}]})\n"
                "r['report']['exitVal']")
    assert _last(out) == "'none'"
    out = k.run("notebookutils.notebook.runMultiple({'activities': [{'name': 'load', 'path': 'load'}, "
                "{'name': 'strict', 'path': 'strict', 'dependencies': ['load']}]})")
    assert out["status"] == "error"
    assert any("no default was given" in str(o.get("text", "")) + str(o.get("evalue", "")) for o in out["outputs"])


def test_a_task_value_must_be_json():
    out = _error(Kernel().run("dbutils.jobs.taskValues.set('when', object())"))
    assert out["ename"] == "TypeError"


def test_an_empty_option_raises_on_get_and_falls_back_on_get_or_else():
    k = Kernel()
    ctx = "dbutils.notebook.entry_point.getDbutils().notebook().getContext()"
    assert _last(k.run(f"{ctx}.currentRunId().getOrElse('local')")) == "'local'"
    assert _error(k.run(f"{ctx}.currentRunId().get()"))["evalue"] == "None.get"
    assert _last(k.run(f"{ctx}.tags()['isLocal']")) == "'true'"


@pytest.mark.parametrize("call, words", [
    ("library.install('dbfs:/x.whl')", "runs in Databricks"),
    ("credentials.showRoles()", "Databricks on AWS"),
])
def test_databricks_only_calls_say_so(call, words):
    out = _error(Kernel().run(f"dbutils.{call}"))
    assert out["ename"] == "NotImplementedError" and words in out["evalue"]


def test_summarize_shows_the_data_profile():
    out = _ok(Kernel().run("import polars as pl\ndbutils.data.summarize(pl.DataFrame({'a': [1, 2]}))"))
    table = out["outputs"][-1]
    assert table["type"] == "table" and table["summary"] and table["columns"] == ["a"]


# ---------------------------------------------------------------- the Databricks Utilities reference, call by call
# docs.databricks.com/aws/en/dev-tools/databricks-utils: each test names what the reference says.


def test_ls_lists_databricks_file_info_with_a_folder_ending_in_a_slash(tmp_path):
    # "ls(dir: String): Seq": FileInfo(path, name, size, modificationTime); isDir() reads the trailing /.
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("hello")
    k = Kernel()
    _ok(k.run(f"rows = dbutils.fs.ls('file:{tmp_path.as_posix()}')"))
    rows = {r.name: r for r in k.namespace["rows"]}
    assert set(rows) == {"sub/", "a.txt"}
    sub, a = rows["sub/"], rows["a.txt"]
    assert sub.isDir() and not sub.isFile() and sub.size == 0 and sub.path.endswith("/sub/")
    assert a.isFile() and a.size == 5 and a.modificationTime > 0 and tuple(a) == (a.path, "a.txt", 5, a.modificationTime)
    assert repr(a).startswith("FileInfo(path='file:") and "name='a.txt', size=5, modificationTime=" in repr(a)


def test_ls_writes_a_windows_path_with_forward_slashes():
    # Databricks paths use /; a Windows path listed here is written the same way, and still reads back.
    from types import SimpleNamespace
    folder = SimpleNamespace(path="file:C:\\data\\sub", name="sub", size=0, isDir=True, modificationTime=1)
    file = SimpleNamespace(path="file:C:\\data\\a.txt", name="a.txt", size=5, isDir=False, modificationTime=1)
    assert dbutils_local._file_info(folder).path == "file:C:/data/sub/"
    assert dbutils_local._file_info(file).path == "file:C:/data/a.txt"
    lake = SimpleNamespace(path="abfss://ws@onelake/lh/Files/x", name="x", size=1, isDir=False, modificationTime=1)
    assert dbutils_local._file_info(lake).path == lake.path


def test_ls_paths_read_back(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    k = Kernel()
    _ok(k.run(f"p = dbutils.fs.ls('file:{tmp_path.as_posix()}')[0].path\nprint(dbutils.fs.head(p))"))


def test_head_says_when_it_truncated_and_returns_only_the_text(tmp_path):
    # "head(file, max_bytes=65536)": the example prints "[Truncated to first 25 bytes]".
    f = tmp_path / "big.txt"
    f.write_text("x" * 70000)
    out = _ok(Kernel().run(f"len(dbutils.fs.head('file:{f.as_posix()}'))"))
    assert "[Truncated to first 65536 bytes]" in out["outputs"][0]["text"] and _last(out) == "65536"
    out = _ok(Kernel().run(f"dbutils.fs.head('file:{f.as_posix()}', 70000)[-1]"))
    assert "Truncated" not in str(out["outputs"]) and _last(out) == "'x'"


def test_put_says_how_many_bytes_it_wrote_and_overwrites_only_when_told(tmp_path):
    # "put(file, contents, overwrite=false): boolean", printing "Wrote N bytes."
    p = (tmp_path / "h.txt").as_posix()
    k = Kernel()
    out = _ok(k.run(f"dbutils.fs.put('file:{p}', 'héllo')"))
    assert "Wrote 6 bytes." in out["outputs"][0]["text"] and _last(out) == "True"
    assert _error(k.run(f"dbutils.fs.put('file:{p}', 'again')"))["ename"] == "FileExistsError"
    _ok(k.run(f"dbutils.fs.put('file:{p}', 'again', True)"))
    assert (tmp_path / "h.txt").read_text() == "again"


def test_rm_answers_false_for_nothing_there_and_takes_an_empty_folder_without_recurse(tmp_path):
    # "rm(dir, recurse=false)": "an error occurs when recurse is disabled and the directory is not empty".
    k = Kernel()
    assert _last(k.run(f"dbutils.fs.rm('file:{(tmp_path / 'nope').as_posix()}')")) == "False"
    (tmp_path / "empty").mkdir()
    assert _last(k.run(f"dbutils.fs.rm('file:{(tmp_path / 'empty').as_posix()}')")) == "True"
    assert not (tmp_path / "empty").exists()
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "f").write_text("1")
    assert _error(k.run(f"dbutils.fs.rm('file:{(tmp_path / 'full').as_posix()}')"))["ename"] == "IsADirectoryError"
    assert (tmp_path / "full" / "f").exists()
    assert _last(k.run(f"dbutils.fs.rm('file:{(tmp_path / 'full').as_posix()}', recurse=True)")) == "True"
    assert not (tmp_path / "full").exists()


@pytest.mark.parametrize("call", ["mv", "cp"])
def test_moving_or_copying_onto_a_folder_puts_it_inside(tmp_path, call):
    # The reference's mv example: mv(".../rows.csv", ".../my-data/") moves the file into my-data.
    (tmp_path / "my-data").mkdir()
    (tmp_path / "rows.csv").write_text("a,b")
    out = Kernel().run(f"dbutils.fs.{call}('file:{(tmp_path / 'rows.csv').as_posix()}', "
                       f"'file:{(tmp_path / 'my-data').as_posix()}/')")
    assert _last(out) == "True"
    assert (tmp_path / "my-data" / "rows.csv").read_text() == "a,b"
    assert (tmp_path / "rows.csv").exists() == (call == "cp")


def test_mkdirs_makes_the_parents_too(tmp_path):
    # "mkdirs(dir): boolean": "also creates any necessary parent directories".
    deep = tmp_path / "a" / "b" / "c"
    assert _last(Kernel().run(f"dbutils.fs.mkdirs('file:{deep.as_posix()}')")) == "True" and deep.is_dir()


def test_a_multiselect_answers_its_values_joined_with_commas():
    k = Kernel()
    _ok(k.run("dbutils.widgets.multiselect('days', 'Tuesday', ['Monday', 'Tuesday', 'Wednesday'], 'Days')"))
    assert _last(k.run("dbutils.widgets.get('days')")) == "'Tuesday'"
    k.widget_values = {"days": "Monday,Wednesday"}
    assert _last(k.run("dbutils.widgets.get('days')")) == "'Monday,Wednesday'"


def test_a_missing_widget_raises_input_widget_not_defined_by_name():
    out = _error(Kernel().run("dbutils.widgets.get('age')"))
    assert out["ename"] == "InputWidgetNotDefined" and "No widget named age" in out["evalue"]


def test_get_argument_answers_the_fallback_or_raises_without_one():
    # "getArgument(name, optional)": "If the widget does not exist, an optional message can be returned."
    k = Kernel()
    assert _last(k.run("dbutils.widgets.getArgument('fruits', 'Error: Cannot find fruits combobox')")) == \
        "'Error: Cannot find fruits combobox'"
    assert _error(k.run("dbutils.widgets.getArgument('fruits')"))["ename"] == "InputWidgetNotDefined"


def test_get_all_maps_every_widget_to_its_value():
    k = Kernel()
    _ok(k.run("dbutils.widgets.text('a', '1')\ndbutils.widgets.dropdown('b', 'x', ['x', 'y'])"))
    assert _last(k.run("dbutils.widgets.getAll()")) in ("{'a': '1', 'b': 'x'}", "{'b': 'x', 'a': '1'}")


def test_notebook_run_returns_the_exit_value_as_a_string(tmp_path):
    # "run(path, timeoutSeconds, arguments): String"; "exit(value: String)".
    from fabric_desktop.notebook import NotebookStore
    store = NotebookStore(tmp_path)
    store.save("child", [{"type": "code", "source": "dbutils.notebook.exit(42)"}])
    k = Kernel(load_notebook=lambda name: store.load(name)["cells"])
    assert _last(k.run("dbutils.notebook.run('child', 60)")) == "'42'"


def test_secrets_list_shapes_match_databricks(monkeypatch):
    # "Out[10]: [SecretMetadata(key='my-key')]"
    class Resp:
        status_code = 200

        def json(self):
            return {"value": [{"id": "https://kv.vault.azure.net/secrets/my-key"}]}

    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=lambda *a, **k: Resp()))
    assert _last(Kernel(token=lambda audience: "tok").run("dbutils.secrets.list('kv')")) == "[SecretMetadata(key='my-key')]"


def _job(tmp_path, sources):
    from fabric_desktop.notebook import NotebookStore
    store = NotebookStore(tmp_path)
    for name, source in sources.items():
        store.save(name, [{"type": "code", "source": source}])
    return Kernel(load_notebook=lambda name: store.load(name)["cells"])


def test_a_task_value_from_a_task_that_never_ran_raises_even_with_a_default(tmp_path):
    # taskKey: "If the command cannot find this task, a ValueError is raised."
    k = _job(tmp_path, {"load": "x = 1",
                        "report": "dbutils.notebook.exit(dbutils.jobs.taskValues.get('lod', 'rows', default=0))",
                        "after": "dbutils.notebook.exit(dbutils.jobs.taskValues.get('load', 'rows', default=0))"})
    out = k.run("notebookutils.notebook.runMultiple({'activities': [{'name': 'load', 'path': 'load'}, "
                "{'name': 'report', 'path': 'report', 'dependencies': ['load']}]})")
    assert out["status"] == "error" and "No task named 'lod'" in json.dumps(out["outputs"])
    # A task that ran but set nothing is found, so the default answers.
    out = k.run("r = notebookutils.notebook.runMultiple({'activities': [{'name': 'load', 'path': 'load'}, "
                "{'name': 'after', 'path': 'after', 'dependencies': ['load']}]})\nr['after']['exitVal']")
    assert _last(out) == "'0'"


def test_a_task_value_over_48_kib_is_refused():
    # set: "The JSON representation of the value can't exceed 48 KiB."
    out = _error(Kernel().run("dbutils.jobs.taskValues.set('big', 'x' * 50000)"))
    assert out["ename"] == "ValueError" and "48 KiB" in out["evalue"]


def test_a_job_run_sets_at_most_250_task_values():
    out = _error(Kernel().run("for i in range(251):\n    dbutils.jobs.taskValues.set(f'k{i}', i)"))
    assert out["ename"] == "ValueError" and "250" in out["evalue"]
