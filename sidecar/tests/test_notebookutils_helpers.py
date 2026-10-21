"""notebookutils.lakehouse (read-only), runtime.context and credentials.isValidToken on this PC."""

import base64
import json
import time

import pytest

from fabric_desktop import fabric_api, lakehouse
from fabric_desktop.notebook import Kernel


def _jwt(exp):
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


class FakeClient:
    calls = []

    def __init__(self, token_provider, **_kw):
        self.token = token_provider()

    def find_lakehouse(self, ws, name):
        FakeClient.calls.append(("find", ws, name, self.token))
        return {"id": "lh1", "displayName": name, "type": "Lakehouse", "workspaceId": ws} if name == "LH_Bronze" else None

    def get_lakehouse(self, ws, lh):
        return {"id": lh, "displayName": "LH_Bronze", "workspaceId": ws, "properties": {"oneLakeTablesPath": "x"}}

    def list_lakehouses(self, ws):
        return [{"id": "lh1", "displayName": "LH_Bronze", "workspaceId": ws}, {"id": "lh2", "displayName": "LH_Silver"}]

    def list_tables(self, ws, lh):
        return [{"name": "sales", "type": "Managed", "format": "Delta"}]


@pytest.fixture
def k(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr(fabric_api, "FabricClient", FakeClient)
    monkeypatch.setattr(lakehouse, "_attached", None)
    return Kernel(token=lambda audience: f"token-{audience}")


def test_lakehouse_get_list_and_tables_read_as_you(k):
    out = k.run("lh = notebookutils.lakehouse.get('LH_Bronze', 'ws1')\n"
                "props = notebookutils.lakehouse.getWithProperties('LH_Bronze', 'ws1')\n"
                "(lh.id, lh['displayName'], props.properties['oneLakeTablesPath'], "
                "[x.displayName for x in notebookutils.lakehouse.list('ws1', maxResults=1)], "
                "[t.name for t in notebookutils.lakehouse.listTables('LH_Bronze', 'ws1')])")
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["text"] == "('lh1', 'LH_Bronze', 'x', ['LH_Bronze'], ['sales'])"
    assert FakeClient.calls[0] == ("find", "ws1", "LH_Bronze", "token-pbi")


def test_lakehouse_defaults_to_the_attached_one_and_explains_without_it(k, monkeypatch):
    out = k.run("notebookutils.lakehouse.get()")
    assert out["status"] == "error" and "attach one" in out["outputs"][-1]["evalue"]
    attached = type("A", (), {"name": "LH_Bronze", "workspace_id": "ws9"})()
    monkeypatch.setattr(lakehouse, "_attached", attached)
    out = k.run("notebookutils.lakehouse.get().workspaceId")
    assert out["outputs"][-1]["text"] == "'ws9'"
    out = k.run("notebookutils.lakehouse.get('Nope')")
    assert out["outputs"][-1]["ename"] == "LookupError"


@pytest.mark.parametrize("call", ["create('x')", "update('x', 'y')", "delete('x')", "loadTable({}, 't')"])
def test_lakehouse_changes_run_in_fabric(k, call):
    out = k.run(f"notebookutils.lakehouse.{call}")
    assert out["outputs"][-1]["ename"] == "NotImplementedError"
    assert not FakeClient.calls


def test_is_valid_token_reads_only_the_expiry(k):
    k.namespace.update(good=_jwt(time.time() + 600), old=_jwt(time.time() - 5))
    out = k.run("[notebookutils.credentials.isValidToken(t) for t in (good, old, 'junk')]")
    assert out["outputs"][-1]["text"] == "[True, False, False]"


def test_runtime_context_has_fabric_keys(k):
    out = k.run("c = notebookutils.runtime.context\n(c['isForInteractive'], c['currentNotebookId'], c['productType'])")
    assert out["outputs"][-1]["text"] == "(True, None, 'KratosForge')"


def test_where_it_runs_knows_reads_from_changes():
    from fabric_desktop.fabric_run import compute_check
    reads = compute_check([{"type": "code", "source": "notebookutils.lakehouse.list()"}], {"id": "lh"})
    changes = compute_check([{"type": "code", "source": "notebookutils.lakehouse.create('x')"}], {"id": "lh"})
    assert reads["local"]["ok"] and not changes["local"]["ok"]


class FakeNotebooks(FakeClient):
    def list_notebooks(self, ws):
        return [{"id": "nb1", "displayName": "Load sales", "type": "Notebook", "workspaceId": ws},
                {"id": "nb2", "displayName": "Report", "type": "Notebook", "workspaceId": ws}]

    def get_item_definition(self, ws, item, fmt=None):
        FakeClient.calls.append(("definition", ws, item, fmt))
        payload = base64.b64encode(b'{"cells": [], "nbformat": 4}').decode()
        return {"definition": {"parts": [{"path": "notebook-content.ipynb", "payload": payload}, {"path": ".platform"}]}}


def test_notebook_items_read_as_you(k, monkeypatch):
    monkeypatch.setattr(fabric_api, "FabricClient", FakeNotebooks)
    out = k.run("(notebookutils.notebook.get('Report', 'ws1').id, "
                "[n.displayName for n in notebookutils.notebook.list('ws1', maxResults=1)], "
                "notebookutils.notebook.getDefinition('Load sales', 'ws1'))")
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["text"] == """('nb2', ['Load sales'], '{"cells": [], "nbformat": 4}')"""
    assert ("definition", "ws1", "nb1", "ipynb") in FakeClient.calls
    assert k.run("notebookutils.notebook.get('Nope', 'ws1')")["outputs"][-1]["ename"] == "LookupError"


@pytest.mark.parametrize("call", ["create('x')", "update('x', 'y')", "updateDefinition('x', '{}')", "delete('x')"])
def test_notebook_item_changes_run_in_fabric(k, call):
    out = k.run(f"notebookutils.notebook.{call}")
    assert out["outputs"][-1]["ename"] == "NotImplementedError"
    assert "Publish to OneLake" in out["outputs"][-1]["evalue"]


def test_where_it_runs_knows_notebook_item_changes():
    from fabric_desktop.fabric_run import compute_check
    reads = compute_check([{"type": "code", "source": "notebookutils.notebook.getDefinition('x')"}], {"id": "lh"})
    changes = compute_check([{"type": "code", "source": "notebookutils.notebook.updateDefinition('x', d)"}], {"id": "lh"})
    runs = compute_check([{"type": "code", "source": "notebookutils.notebook.run('x')"}], {"id": "lh"})
    assert reads["local"]["ok"] and runs["local"]["ok"] and not changes["local"]["ok"]


def test_negative_max_results_lists_everything():
    from fabric_desktop.notebookutils_local import _limit
    assert _limit([1, 2, 3], -1) == [1, 2, 3]
    assert _limit([1, 2, 3], 2) == [1, 2]
    assert _limit([1, 2, 3], 0) == []
