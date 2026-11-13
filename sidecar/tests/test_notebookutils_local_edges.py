"""notebookutils on this PC: listing and removing when OneLake refuses, the runtime context read
every way a dict is read, item lookups without a sign-in, and a notebook definition with no
content. OneLake and Fabric are stand-ins; nothing reaches a tenant."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from fabric_desktop import lakehouse
from fabric_desktop import notebookutils_local as nu
from fabric_desktop.onelake import FileStore
from tests.test_notebookutils_fs import fs, store  # noqa: F401 - fixtures


def _http(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(str(status), response=resp)


def test_the_store_reads_with_the_attached_lakehouses_storage_sign_in(monkeypatch):
    monkeypatch.setattr(lakehouse, "_current", lambda: SimpleNamespace(storage_token=lambda: "st"))
    assert isinstance(nu._store(), FileStore)


def test_a_listing_onelake_refuses_for_another_reason_is_raised(store, fs, monkeypatch):  # noqa: F811
    def busy(*a, **k):
        raise _http(503)

    monkeypatch.setattr(store, "list", busy)
    with pytest.raises(requests.HTTPError, match="503"):
        fs.ls("Files/raw")


def test_removing_a_folder_onelake_says_isnt_empty_asks_for_recurse(store, fs, monkeypatch):  # noqa: F811
    def conflict(*a, **k):
        raise _http(409)

    monkeypatch.setattr(store, "delete", conflict)
    with pytest.raises(IsADirectoryError, match="Files/one.txt is a folder that isn't empty. Pass recurse=True"):
        fs.rm("Files/one.txt")


def test_any_other_refusal_to_remove_is_raised(store, fs, monkeypatch):  # noqa: F811
    def refuse(*a, **k):
        raise _http(403)

    monkeypatch.setattr(store, "delete", refuse)
    with pytest.raises(requests.HTTPError, match="403"):
        fs.rm("Files/one.txt", recurse=True)


def test_the_runtime_context_refreshes_on_every_kind_of_read():
    names = iter(["A", "B", "C", "D", "E", "F", "G"])
    ctx = nu._make_runtime(nu.Host(notebook_name=lambda: next(names))).context
    assert "'currentNotebookName': 'B'" in repr(ctx)
    assert ctx.get("currentNotebookName") == "C"
    assert "D" in list(ctx.values())
    assert ctx.copy()["currentNotebookName"] == "E"
    assert ctx != {}
    assert ctx.get("missing", "x") == "x"


def test_an_item_reads_its_keys_as_attributes():
    item = nu.Item({"displayName": "Sales"})
    assert item.displayName == "Sales"
    with pytest.raises(AttributeError, match="id"):
        item.id  # noqa: B018


def test_fabric_items_need_a_sign_in():
    with pytest.raises(RuntimeError, match="Sign in first"):
        nu._client(nu.Host())


class _Fabric:
    def __init__(self, parts):
        self.parts = parts

    def list_notebooks(self, ws):
        return [{"displayName": "Load sales", "id": "nb1", "workspaceId": ws}]

    def get_item_definition(self, ws, item, fmt=None):
        return {"definition": {"parts": self.parts}}


def test_a_notebook_definition_with_no_content_says_so(monkeypatch):
    monkeypatch.setattr(nu, "_client", lambda host: _Fabric([{"path": ".platform", "payload": ""}]))
    notebook = nu._make_notebook(nu.Host(token=lambda scope: "tok"))
    with pytest.raises(LookupError, match="Fabric returned no content for the notebook Load sales"):
        notebook.getDefinition("Load sales", "ws1")


def test_user_data_functions_list_their_names():
    udf = nu.UdfFunctions({"displayName": "Pricing"}, [{"name": "quote"}])
    assert repr(udf) == "UdfFunctions(Pricing: quote)"
    assert dir(udf) == ["functionDetails", "itemDetails", "quote"]
    assert repr(nu.UdfFunctions({"displayName": "Empty"}, [])) == "UdfFunctions(Empty: none)"
