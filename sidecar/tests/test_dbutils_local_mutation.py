"""dbutils on this PC: what mutation testing (batch 83, docs/quality/mutation-testing.md) showed no test
checked. The %fs path check, widget labels and values set in the app, the user in the notebook
context, the task a value is kept under, the default notebook timeout, and how the Key Vault list
asks for each page and stops on a repeated one. Fakes stand in for the kernel and Key Vault; nothing
leaves this PC."""

from __future__ import annotations

import types

import pytest

from fabric_desktop import dbutils_local, http_session
from fabric_desktop.paging import PagingError


def test_a_drive_path_in_fs_magic_is_this_pc_s_disk_and_other_paths_are_checked():
    assert dbutils_local.fs_magic_path("C:\\data") == "C:\\data"
    assert dbutils_local.fs_magic_path("Files/raw") == "Files/raw"
    assert dbutils_local.fs_magic_path("file:/tmp/x") == "file:/tmp/x"
    with pytest.raises(ValueError, match="DBFS path"):
        dbutils_local.fs_magic_path("/tmp/x")
    with pytest.raises(ValueError, match="Databricks storage"):
        dbutils_local.fs_magic_path("dbfs:/x")


def test_a_widget_s_label_is_its_name_unless_one_is_given():
    w = dbutils_local.Widgets()
    w.text("region", "West")
    w.text("env", "dev", "Environment")
    w.combobox("pick", "a", ["a", "b"])
    w.combobox("other", "a", ["a", "b"], "Other one")
    labels = {d["name"]: d["label"] for d in w.describe()}
    assert labels == {"region": "region", "env": "Environment", "pick": "pick", "other": "Other one"}


def test_a_multiselect_value_set_in_the_app_is_kept_only_when_every_item_is_a_choice():
    w = dbutils_local.Widgets()
    w.multiselect("days", "Mon", ["Mon", "Tue", "Wed"])
    w.set_in_app["days"] = "Mon,Tue"
    assert w.get("days") == "Mon,Tue"
    w.set_in_app["days"] = "Mon,Sun"
    assert w.get("days") == "Mon"
    w.set_in_app["days"] = "Sun"
    assert w.get("days") == "Mon"


def test_a_dropdown_value_set_in_the_app_is_one_choice():
    w = dbutils_local.Widgets()
    w.dropdown("env", "dev", ["dev", "prod", "dev,prod"])
    w.set_in_app["env"] = "prod"
    assert w.get("env") == "prod"
    w.set_in_app["env"] = "dev,prod"  # one choice with a comma in it, not two
    assert w.get("env") == "dev,prod"
    w.set_in_app["env"] = "qa"
    assert w.get("env") == "dev"


def _host(tasks=None):
    return types.SimpleNamespace(tasks=list(tasks or []), task_values={}, notebook_name=lambda: "nb",
                                 display=lambda *a, **kw: None)


def test_a_task_value_is_kept_under_the_task_running_now():
    host = _host(["first", "second"])
    tv = dbutils_local._make_task_values(host)
    tv.set("n", 3)
    assert host.task_values == {"second": {"n": 3}}
    assert tv.get("second", "n") == 3


def test_the_context_user_is_the_windows_user_first(monkeypatch):
    ctx = dbutils_local._make_entry_point(_host()).getDbutils().notebook().getContext
    monkeypatch.setenv("USERNAME", "win-user")
    monkeypatch.setenv("USER", "other-user")
    assert ctx().userName().get() == "win-user"
    monkeypatch.delenv("USERNAME")
    assert ctx().userName().get() == "other-user"
    monkeypatch.delenv("USER")
    assert ctx().userName().get() == ""


def _nbu(calls):
    def run(path, timeout, args):
        calls.append((path, timeout, args))
        return 7
    return types.SimpleNamespace(
        notebook=types.SimpleNamespace(run=run, exit=lambda v: None),
        fs=types.SimpleNamespace(**{n: (lambda *a, **kw: a) for n in ("ls", "head", "put", "cp", "mv", "rm", "mkdirs")}),
        credentials=types.SimpleNamespace(getSecret=lambda s, k: "", getToken=lambda a: ""))


def test_a_notebook_run_without_a_timeout_passes_zero():
    calls: list = []
    du = dbutils_local.build(_host(), _nbu(calls))
    assert du.notebook.run("child") == "7"
    assert calls == [("child", 0, {})]


class _Resp:
    def __init__(self, body):
        self.status_code, self._body = 200, body

    def json(self):
        return self._body


def test_listing_secrets_asks_each_page_with_a_30_second_timeout(monkeypatch):
    sent = []
    pages = {"https://kv.vault.azure.net/secrets": {"value": [{"id": "https://kv.vault.azure.net/secrets/a"}],
                                                    "nextLink": "https://kv.vault.azure.net/secrets?p=2"},
             "https://kv.vault.azure.net/secrets?p=2": {"value": [{"id": "https://kv.vault.azure.net/secrets/b/"}]}}

    def get(url, **kw):
        sent.append((url, kw["timeout"], kw["params"]))
        return _Resp(pages[url])
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=get))
    secrets = dbutils_local._make_secrets(lambda s, k: "", lambda audience: "tok")
    assert [m.key for m in secrets.list("kv")] == ["a", "b"]
    assert sent == [("https://kv.vault.azure.net/secrets", 30, {"api-version": "7.4"}),
                    ("https://kv.vault.azure.net/secrets?p=2", 30, None)]


def test_listing_secrets_stops_when_key_vault_repeats_a_page(monkeypatch):
    sent = []

    def get(url, **kw):
        sent.append(url)
        if len(sent) > 5:
            raise AssertionError("the list kept asking for the same page")
        return _Resp({"value": [], "nextLink": "https://kv.vault.azure.net/secrets?p=2"})
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=get))
    secrets = dbutils_local._make_secrets(lambda s, k: "", lambda audience: "tok")
    with pytest.raises(PagingError, match="same next page twice"):
        secrets.list("kv")
    assert len(sent) == 2
