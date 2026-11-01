"""Databricks dbutils parity on this PC: the documented calls test_dbutils.py doesn't reach. Each one
either works as in Databricks or says plainly that it runs in Databricks only. Nothing leaves this PC."""

from __future__ import annotations

import types

import pytest

from fabric_desktop import http_session
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


@pytest.mark.parametrize("src, dest", [("dbfs:/tmp/a", "file:/x"), ("file:/x", "/Volumes/main/raw/a"),
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
