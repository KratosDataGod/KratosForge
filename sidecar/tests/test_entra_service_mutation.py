"""Entra ID groups service (central/entra_service.py): what the mutation run (batch 126,
docs/quality/mutation-testing.md) showed no test pinned. Subdomains left out by default, a script shown is
never saved, how each saved script is encoded, the protected-cells note, an object id that isn't one, the
order of workspaces, the shortest search, fifteen names per filter, Graph's timeout and its error's length.

No network: the Graph session and the platform catalog are stand-ins (test_entra_graph)."""

from __future__ import annotations

import os

import pytest
from test_entra_graph import Reply, Svc, _catalog

from fabric_desktop import csv_cells, http_session, known_folders
from fabric_desktop.central import entra
from fabric_desktop.redact import one_line


class Graph:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.replies(params or {})


@pytest.fixture
def graph(monkeypatch):
    def install(replies):
        g = Graph(replies)
        monkeypatch.setattr(http_session, "session", lambda: g)
        return g
    return install


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    folder = tmp_path / "Downloads"
    monkeypatch.setattr(known_folders, "downloads", lambda: folder)
    return folder


def test_an_unknown_domain_is_refused_before_the_catalog_is_asked():
    svc = Svc(_catalog())
    with pytest.raises(ValueError, match="nope isn't a domain"):
        svc._entra_plan("nope", None, False)
    assert svc.catalog.calls == []


def test_subdomains_are_left_out_unless_asked_for(downloads):
    svc = Svc(_catalog())
    assert {r["domain_id"] for r in svc.platform_entra_groups("sales")["rows"]} == {"sales"}
    assert {r["domain_id"] for r in svc.platform_entra_groups("sales", subdomains=True)["rows"]} == {"sales", "sales-emea"}
    saved = svc.platform_entra_save("sales", "csv")
    assert "Sales EMEA" not in (downloads / os.path.basename(saved["path"])).read_text(encoding="utf-8-sig")


def test_a_script_shown_is_never_saved(downloads):
    out = Svc(_catalog()).platform_entra_script("sales", "powershell")
    assert "path" not in out and not downloads.exists()


@pytest.mark.parametrize("kind, bom", [("csv", True), ("powershell", True), ("roles", True), ("az", False)])
def test_csv_and_powershell_files_carry_a_byte_order_mark_and_shell_scripts_unix_line_ends(downloads, kind, bom):
    out = Svc(_catalog()).platform_entra_save("sales", kind)
    data = open(out["path"], "rb").read()
    assert data.startswith(b"\xef\xbb\xbf") is bom
    if kind == "az":
        assert data == out["text"].encode("utf-8")  # "\n" on every system, Windows too
    else:
        body = data[3:].decode("utf-8")
        assert body == out["text"].replace("\n", os.linesep)


def test_protected_cells_come_with_a_note_and_none_without(monkeypatch):
    svc = Svc(_catalog())
    out = svc.platform_entra_script("sales", "csv")
    assert out["protected"] == 0 and out["notes"] == []
    monkeypatch.setitem(entra.KINDS, "csv", ("csv", lambda rows, owners, cells: cells("=1+1")))
    out = svc.platform_entra_script("sales", "csv")
    assert out["protected"] == 1 and out["notes"] == [csv_cells.note(1, can_keep_raw=False)]


def test_linking_refuses_a_group_id_that_isnt_an_object_id():
    svc = Svc(_catalog())
    with pytest.raises(ValueError, match="Paste the group's object ID"):
        svc.platform_entra_link("sales", "readers", "prod", "Sales Readers")
    assert not svc.catalog.calls


def test_workspaces_are_sorted_by_name():
    products = [{"domain_id": "sales", "ports": [{"workspace_id": "a", "workspace_name": "Zed", "env": "prod"},
                                                 {"workspace_id": "b", "workspace_name": "Alpha", "env": "prod"}]}]
    out = Svc(_catalog(products=products))._entra_workspaces({"sales"})
    assert out == {"prod": [{"id": "b", "name": "Alpha"}, {"id": "a", "name": "Zed"}]}


def test_two_characters_are_enough_to_search_and_graph_gets_30_seconds(graph):
    g = graph(lambda params: Reply(200, {"value": []}))
    assert Svc(_catalog()).platform_entra_find("ab") == []
    assert len(g.calls) == 1 and g.calls[0]["timeout"] == 30
    assert Svc(_catalog()).platform_entra_find("a") == [] and len(g.calls) == 1


def test_graphs_error_keeps_200_characters_of_its_text(graph):
    text = "x" * 500
    graph(lambda params: Reply(400, text=text))
    with pytest.raises(RuntimeError) as err:
        Svc(_catalog())._graph("/groups")
    assert str(err.value) == f"Microsoft Graph answered 400: {one_line(text, 200)}"


def test_every_name_is_checked_fifteen_at_a_time(graph, monkeypatch):
    rows = [{"suggested": f"G{i:02}", "linked": None, "domain_id": "d", "role": f"r{i}", "env": ""} for i in range(31)]
    svc = Svc(_catalog())
    monkeypatch.setattr(svc, "_entra_plan", lambda domain_id, envs, subdomains: ({}, rows))

    def replies(params):
        names = [n.strip(" '") for n in params["$filter"].split("(", 1)[1].rstrip(")").split(",")]
        return Reply(200, {"value": [{"id": f"id-{n}", "displayName": n} for n in names]})
    g = graph(replies)
    status = svc.platform_entra_check("d")["status"]
    assert len(g.calls) == 3
    assert all(s["exists"] for s in status.values()) and len(status) == 31
