"""Mutation-testing gaps in databricks_notebook_helpers.py: the stand-in notebookutils, the walk over
dbutils, the probe of each call, the statuses and the generated page. Nothing here reads docs/."""

from __future__ import annotations

import types

import pytest

from fabric_desktop import databricks_notebook_helpers as helpers
from fabric_desktop import dbutils_local, magics

NS = types.SimpleNamespace


def test_the_stub_raises_not_called_whatever_it_is_given():
    with pytest.raises(helpers._NotCalled):
        helpers._stub()
    with pytest.raises(helpers._NotCalled):
        helpers._stub(1, 2, key="v")


def test_the_built_dbutils_sits_on_a_notebookutils_that_does_nothing(monkeypatch):
    seen = {}

    def build(host, nbu):
        seen["nbu"] = nbu
        return NS(marker=True)

    monkeypatch.setattr(helpers.dbutils_local, "build", build)
    assert helpers.built().marker is True
    nbu = seen["nbu"]
    for name in ("ls", "head", "put", "cp", "mv", "rm", "mkdirs"):
        with pytest.raises(helpers._NotCalled):
            getattr(nbu.fs, name)("x")
    for call in (nbu.notebook.run, nbu.notebook.exit, nbu.credentials.getSecret, nbu.credentials.getToken):
        with pytest.raises(helpers._NotCalled):
            call("x")


def test_the_real_built_dbutils_lists_fs_and_widget_calls():
    calls = helpers.public_calls()
    assert {"fs.ls", "fs.cp", "widgets.text", "widgets.get"} <= calls
    assert not any(c.endswith(".help") or c == "help" for c in calls)
    assert "widgets.bind" not in calls and "widgets.describe" not in calls


def _widgets():
    w = dbutils_local.Widgets()
    return w


def test_public_calls_walks_namespaces_and_skips_private_and_help():
    db = NS(help=lambda: None, _hidden=lambda: None, top=lambda: None, value=3,
            fs=NS(ls=lambda: None, help=lambda: None, _x=lambda: None, inner=NS(deep=lambda: None)))
    assert helpers.public_calls(db) == {"top", "fs.ls", "fs.inner.deep"}


def test_bind_and_describe_are_left_out_only_on_widgets():
    w = _widgets()
    db = NS(widgets=w, other=NS(bind=lambda: None, describe=lambda: None))
    calls = helpers.public_calls(db)
    assert "widgets.text" in calls
    assert "widgets.bind" not in calls and "widgets.describe" not in calls
    assert {"other.bind", "other.describe"} <= calls


def test_a_widgets_object_is_walked_with_its_prefix():
    calls = helpers.public_calls(NS(w=_widgets()))
    assert "w.text" in calls and "w.dropdown" in calls


def test_probe_finds_a_missing_part_at_any_depth():
    db = NS(fs=NS(ls=lambda: None))
    assert helpers._probe(db, "nope") == ("missing", "")
    assert helpers._probe(db, "fs.nope") == ("missing", "")
    assert helpers._probe(db, "fs.ls.deeper") == ("missing", "")


def test_probe_reports_a_refusal_with_its_message():
    def refuse():
        raise NotImplementedError("not on this PC")
    assert helpers._probe(NS(fs=NS(mount=refuse)), "fs.mount") == ("refused", "not on this PC")


def test_probe_counts_any_other_error_as_running(capsys):
    def wrong_args(a):
        return a

    def stand_in():
        raise helpers._NotCalled

    def prints():
        print("noise")

    assert helpers._probe(NS(a=wrong_args), "a") == ("runs", "")
    assert helpers._probe(NS(a=stand_in), "a") == ("runs", "")
    assert helpers._probe(NS(a=prints), "a") == ("runs", "")
    assert capsys.readouterr().out == ""


DATA = {
    "source": "The source line.",
    "dbutils": {"fs.ls": "", "fs.cp": "Paths differ.", "fs.mount": "ignored note", "jobs.gone": "",
                "fs.bad": "keeps its note"},
    "cells": {"%python": "", "%sql": "Runs on DuckDB.", "%scala": "", "%r": "an R note"},
    "line_magics": {"%run": "", "%pip": "Own environment."},
}


def _refuse():
    raise NotImplementedError("Mounting runs in Databricks.")


def _bad(x):
    raise ValueError(x)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(helpers, "load", lambda: DATA)
    monkeypatch.setattr(helpers, "built", lambda: NS(fs=NS(ls=lambda: None, cp=lambda: None, mount=_refuse,
                                                               bad=_bad)))
    return helpers.rows()


def test_rows_give_each_dbutils_call_its_status_and_note(fake):
    db = {r["name"]: r for r in fake if r["group"] == "dbutils"}
    assert db["fs.ls"] == {"group": "dbutils", "name": "fs.ls", "status": "runs", "note": ""}
    assert db["fs.cp"] == {"group": "dbutils", "name": "fs.cp", "status": "differs", "note": "Paths differ."}
    assert db["fs.mount"] == {"group": "dbutils", "name": "fs.mount", "status": "refused",
                              "note": "Mounting runs in Databricks."}
    assert db["jobs.gone"] == {"group": "dbutils", "name": "jobs.gone", "status": "missing", "note": ""}
    assert db["fs.bad"]["status"] == "differs" and db["fs.bad"]["note"] == "keeps its note"


def test_rows_refuse_scala_and_r_cells_and_keep_the_rest(fake):
    cells = {r["name"]: r for r in fake if r["group"] == "cells"}
    assert cells["%python"] == {"group": "cells", "name": "%python", "status": "runs", "note": ""}
    assert cells["%sql"] == {"group": "cells", "name": "%sql", "status": "differs", "note": "Runs on DuckDB."}
    for header in ("%scala", "%r"):
        assert cells[header] == {"group": "cells", "name": header, "status": "refused", "note": magics.runs_in_fabric(header)}


def test_rows_list_line_magics_in_order(fake):
    magics_rows = [r for r in fake if r["group"] == "line_magics"]
    assert magics_rows == [{"group": "line_magics", "name": "%run", "status": "runs", "note": ""},
                           {"group": "line_magics", "name": "%pip", "status": "differs", "note": "Own environment."}]
    assert [r["group"] for r in fake] == ["dbutils"] * 5 + ["cells"] * 4 + ["line_magics"] * 2


def test_the_cells_that_run_here_are_exactly_these():
    assert helpers.RUNS_HERE == {"python", "sql", "markdown", "html", "shell"}
    assert helpers.GROUPS == ("dbutils", "cells", "line_magics")


def test_the_page_counts_each_status_per_group_and_lists_every_helper(fake, monkeypatch):
    found = fake + [{"group": "line_magics", "name": "%x", "status": "refused", "note": "a | b"}]
    page = helpers.markdown(found)
    lines = page.split("\n")
    assert lines[0] == "# Databricks notebook helpers on this PC"
    assert "| Helpers | Runs | Differs | Refused | Missing |" in lines
    assert "| --- | ---: | ---: | ---: | ---: |" in lines
    assert "| dbutils | 1 | 2 | 1 | 1 |" in lines
    assert "| Cell magics | 1 | 1 | 2 | 0 |" in lines
    assert "| Line magics | 1 | 1 | 1 | 0 |" in lines
    assert "## dbutils" in lines and "## Cell magics" in lines and "## Line magics" in lines
    assert "| `dbutils.fs.cp` | Differs | Paths differ. |" in lines
    assert "| `dbutils.jobs.gone` | Missing |  |" in lines
    assert "| `%scala` | Refused | " + magics.runs_in_fabric("%scala") + " |" in lines
    assert "| `%x` | Refused | a / b |" in lines
    assert page.endswith("\n\nSource: The source line.\n")


def test_the_page_puts_each_group_in_order_with_its_header_row(fake):
    lines = helpers.markdown(fake).split("\n")
    at = lines.index("## dbutils")
    assert lines[at:at + 5] == ["## dbutils", "", "| Helper | Status | Note |", "| --- | --- | --- |",
                                "| `dbutils.fs.ls` | Runs |  |"]
    assert lines.index("## dbutils") < lines.index("## Cell magics") < lines.index("## Line magics")
    assert lines[lines.index("## Cell magics") - 1] == ""
    summary = lines.index("| Helpers | Runs | Differs | Refused | Missing |")
    assert lines[summary + 5] == "" and lines[summary + 6] == "## dbutils"
    assert lines[summary - 1] == ""


def test_the_page_paths_are_beside_the_module():
    assert helpers.DATA.name == "databricks_notebook_helpers.json" and helpers.DATA.parent.name == "fabric_desktop"
    assert helpers.DOC.parts[-2:] == ("docs", "databricks-notebook-helpers.md")
    assert helpers.DOC.parent.parent == helpers.DATA.parent.parent.parent


def test_load_reads_the_list_beside_the_module():
    data = helpers.load()
    assert set(data) == {"source", "dbutils", "cells", "line_magics"}
    assert "fs.ls" in data["dbutils"] and "%scala" in data["cells"] and "%run" in data["line_magics"]


def test_importing_the_module_writes_nothing(monkeypatch, capsys):
    """Only ``python -m`` writes the docs page; a plain import of the module leaves it alone."""
    import importlib.util

    from fabric_desktop import jsonfile

    writes = []
    monkeypatch.setattr(jsonfile, "write_text", lambda *a, **kw: writes.append(a))
    spec = importlib.util.spec_from_file_location("fabric_desktop._helpers_import_check", helpers.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RUNS_HERE == helpers.RUNS_HERE
    assert writes == [] and capsys.readouterr().out == ""
