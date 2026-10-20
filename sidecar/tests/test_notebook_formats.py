"""Notebook export and import in Fabric, Databricks and Jupyter formats (NB parity L5)."""

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from fabric_desktop import notebook_formats as nbf
from fabric_desktop.service import Service

CELLS = [
    {"type": "markdown", "source": "# Sales\nDaily load.\n\nSecond paragraph."},
    {"type": "code", "source": "run_date = '2026-10-20'", "meta": {"tags": ["parameters"]}},
    {"type": "code", "source": "df = spark.read.table('sales')\nprint(df.count())"},
    {"type": "code", "source": "%%sql\nSELECT *\nFROM sales"},
]


def _roundtrip(text):
    return [{k: c[k] for k in ("type", "source")} | ({"meta": c["meta"]} if c.get("meta") else {})
            for c in nbf.parse_source(text)["cells"]]


def test_fabric_py_matches_fabric_git_format_and_reads_back():
    text = nbf.to_fabric_py(CELLS, {"lakehouse_id": "lh-1", "name": "Sales", "workspace_id": "ws-1"})
    lines = text.split("\n")
    assert lines[0] == "# Fabric notebook source"
    assert lines[2] == "# METADATA ********************"
    assert '# META       "default_lakehouse": "lh-1",' in lines
    assert "# MARKDOWN ********************" in lines and "# # Sales" in lines and "# Daily load." in lines
    assert "# PARAMETERS CELL ********************" in lines
    assert lines.count("# CELL ********************") == 2
    assert "# MAGIC %%sql" in lines and "# MAGIC FROM sales" in lines
    assert '# META   "language": "sparksql",' in lines
    assert _roundtrip(text) == CELLS


def test_fabric_sql_cell_without_magic_gets_one_from_its_language():
    text = "\n".join(["# Fabric notebook source", "", "# CELL ********************", "", "SELECT 1", "",
                      "# METADATA ********************", "", "# META {", '# META   "language": "sparksql"', "# META }", ""])
    assert nbf.parse_source(text)["cells"] == [{"type": "code", "source": "%%sql\nSELECT 1"}]


def test_databricks_py_export_and_import():
    text = nbf.to_databricks_py(CELLS)
    assert text.startswith("# Databricks notebook source\n")
    assert text.count("# COMMAND ----------") == 3
    assert "# MAGIC %md\n# MAGIC # Sales\n# MAGIC Daily load.\n# MAGIC\n# MAGIC Second paragraph." in text
    assert "# MAGIC %sql\n# MAGIC SELECT *" in text
    back = nbf.parse_source(text)
    assert back["format"] == "Databricks notebook source"
    # Databricks has no parameters cell, so only that tag is lost.
    assert _roundtrip(text) == [CELLS[0], {"type": "code", "source": CELLS[1]["source"]}, CELLS[2], CELLS[3]]


def test_databricks_magics_titles_and_sql_source():
    py = "\n".join(["# Databricks notebook source", "# DBTITLE 1,Load", "x = 1", "", "# COMMAND ----------", "",
                    "# MAGIC %scala", "# MAGIC val y = 2", "", "# COMMAND ----------", "", "# MAGIC %run ./helpers",
                    "", "# COMMAND ----------", "", "# MAGIC %md-sandbox <b>hi</b>"])
    assert nbf.parse_source(py)["cells"] == [
        {"type": "code", "source": "x = 1"}, {"type": "code", "source": "%%spark\nval y = 2"},
        {"type": "code", "source": "%run ./helpers"}, {"type": "markdown", "source": "<b>hi</b>"}]
    sql = "-- Databricks notebook source\nSELECT 1\n\n-- COMMAND ----------\n\n-- MAGIC %md\n-- MAGIC Notes\n\n-- COMMAND ----------\n\n-- MAGIC %python\n-- MAGIC print(1)\n"
    out = nbf.parse_source(sql, "q.sql")
    assert out["format"] == "Databricks SQL notebook source"
    assert out["cells"] == [{"type": "code", "source": "%%sql\nSELECT 1"}, {"type": "markdown", "source": "Notes"},
                            {"type": "code", "source": "print(1)"}]


def test_percent_format_with_jupytext_header():
    text = "\n".join(["# ---", "# jupyter:", "#   kernelspec: {}", "# ---", "", "# %% [markdown]", "# # Title", "#", "# Body",
                      "", "# %% tags=[\"parameters\"]", "a = 1", "", "# %%", "print(a)", "", "# %% [markdown]", '"""', "Quoted", '"""'])
    out = nbf.parse_source(text, "x.py")
    assert out["format"].startswith("Percent-format")
    assert out["cells"] == [{"type": "markdown", "source": "# Title\n\nBody"},
                            {"type": "code", "source": "a = 1", "meta": {"tags": ["parameters"]}},
                            {"type": "code", "source": "print(a)"}, {"type": "markdown", "source": "Quoted"}]


def test_plain_script_is_one_cell():
    assert nbf.parse_source("print(1)\n", "a.py")["cells"] == [{"type": "code", "source": "print(1)"}]
    assert nbf.parse_source("SELECT 1", "a.sql")["cells"] == [{"type": "code", "source": "%%sql\nSELECT 1"}]


def test_ipynb_export_with_and_without_outputs():
    cells = [dict(CELLS[2], count=3, outputs=[
        {"type": "text", "text": "42\n"}, {"type": "table", "columns": ["a"], "rows": [[1], ["<b>"]]},
        {"type": "error", "ename": "ValueError", "evalue": "bad", "traceback": "line 1\nline 2"},
        {"type": "image", "mime": "image/png", "data": "aGk="}, {"type": "html", "html": "<p>x</p>"}])]
    bare = json.loads(nbf.export_text("ipynb", "n", cells))
    assert bare["cells"][0]["outputs"] == [] and bare["cells"][0]["execution_count"] is None
    full = json.loads(nbf.export_text("ipynb_outputs", "n", cells))
    outs = full["cells"][0]["outputs"]
    assert full["cells"][0]["execution_count"] == 3
    assert [o["output_type"] for o in outs] == ["stream", "display_data", "error", "display_data", "display_data"]
    assert outs[0]["text"] == ["42\n"]
    assert "&lt;b&gt;" in outs[1]["data"]["text/html"][0]
    assert outs[2]["traceback"] == ["line 1", "line 2"]
    assert outs[3]["data"]["image/png"] == "aGk="


def test_html_export_escapes_everything_and_has_no_script():
    cells = [{"type": "markdown", "source": "<script>alert(1)</script>"},
             {"type": "code", "source": "print('<img onerror=x>')", "count": 1, "outputs": [
                 {"type": "text", "text": "<script>bad()</script>"}, {"type": "html", "html": "<script>bad()</script>"},
                 {"type": "image", "mime": "image/png", "data": "\"><script>"},
                 {"type": "table", "columns": ["<c>"], "rows": [["<v>"]], "total_rows": 10}]}]
    page = nbf.to_html("<Sales>", cells)
    assert "<script" not in page.lower()
    assert "default-src 'none'" in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;img onerror=x&gt;" in page
    assert "<title>&lt;Sales&gt;</title>" in page and "HTML output isn" in page
    assert "<th>&lt;c&gt;</th>" in page and "1 of 10 rows" in page
    assert "data:image/png" not in page  # data that isn't base64 is left out


def test_unknown_export_format():
    with pytest.raises(ValueError, match="format is one of"):
        nbf.export_text("docx", "n", [])


def _dbc(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def _dbc_doc(name, language, commands):
    return json.dumps({"name": name, "language": language,
                       "commands": [{"command": c, "position": i} for i, c in enumerate(commands)]})


def test_dbc_reads_every_notebook_in_memory():
    data = _dbc([("proj/Load.python", _dbc_doc("Load", "python", ["%md # Load", "x = 1", "%sql SELECT 1"])),
                 ("proj/Report.sql", _dbc_doc("Report", "sql", ["SELECT 2", "%python\nprint(2)"])),
                 ("manifest.mf", "not json"), ("proj/", "")])
    nbs = nbf.parse_dbc(data)
    assert [(n["name"], n["folder"]) for n in nbs] == [("Load", "proj"), ("Report", "proj")]
    assert nbs[0]["cells"] == [{"type": "markdown", "source": "# Load"}, {"type": "code", "source": "x = 1"},
                               {"type": "code", "source": "%%sql\nSELECT 1"}]
    assert nbs[1]["cells"] == [{"type": "code", "source": "%%sql\nSELECT 2"}, {"type": "code", "source": "print(2)"}]


@pytest.mark.parametrize("name", ["../evil.python", "/abs.python", "a\\b.python", "C:/x.python", "a/../../b.python"])
def test_dbc_rejects_path_tricks(name):
    with pytest.raises(ValueError, match="unsafe path"):
        nbf.parse_dbc(_dbc([(name, _dbc_doc("x", "python", ["1"]))]))


def test_dbc_limits(monkeypatch):
    with pytest.raises(ValueError, match="isn't a Databricks archive"):
        nbf.parse_dbc(b"not a zip")
    with pytest.raises(ValueError, match="no notebooks"):
        nbf.parse_dbc(_dbc([("a.txt", "x")]))
    monkeypatch.setattr(nbf, "MAX_DBC_ENTRIES", 2)
    with pytest.raises(ValueError, match="at most 2"):
        nbf.parse_dbc(_dbc([(f"n{i}.python", _dbc_doc("n", "python", ["1"])) for i in range(3)]))
    monkeypatch.setattr(nbf, "MAX_DBC_ENTRY", 50)
    with pytest.raises(ValueError, match="larger than 16 MB"):
        nbf.parse_dbc(_dbc([("big.python", _dbc_doc("n", "python", ["x" * 100]))]))


def test_notebook_name_from_file_names():
    assert nbf.notebook_name("a/b:c") == "a-b-c"
    assert nbf.notebook_name("  ...  ") == "Imported notebook"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return Service(client_id="x", root=tmp_path / "app")


def test_import_never_replaces_a_notebook_and_reports_free_names(svc):
    svc.notebooks.save("Sales", [{"type": "code", "source": "keep me"}])
    text = nbf.to_fabric_py(CELLS)
    check = svc.notebook_import_check("Sales.py", text)
    assert check["format"] == "Fabric notebook source"
    assert check["notebooks"] == [{"name": "Sales (2)", "source_name": "Sales", "folder": "", "cells": 4, "markdown": 1,
                                   "languages": ["%%sql", "python"]}]
    assert not (svc.notebooks.folder / "Sales (2).ipynb").exists()  # the check writes nothing
    out = svc.notebook_import("Sales.py", text)
    assert out["created"] == ["Sales (2)"]
    assert svc.notebooks.load("Sales")["cells"][0]["source"] == "keep me"
    assert [c["source"] for c in svc.notebooks.load("Sales (2)")["cells"]] == [c["source"] for c in CELLS]
    assert svc.notebook_import("Sales.py", text)["created"] == ["Sales (3)"]


def test_import_dbc_creates_one_notebook_each(svc):
    data = _dbc([("a/Load.python", _dbc_doc("Load", "python", ["x = 1"])), ("b/Load.python", _dbc_doc("Load", "python", ["y = 2"]))])
    content = base64.b64encode(data).decode()
    assert [n["name"] for n in svc.notebook_import_check("w.dbc", content, "base64")["notebooks"]] == ["Load", "Load (2)"]
    assert svc.notebook_import("w.dbc", content, "base64")["created"] == ["Load", "Load (2)"]
    assert svc.notebooks.load("Load (2)")["cells"][0]["source"] == "y = 2"
    with pytest.raises(ValueError, match="base64"):
        svc.notebook_import("w.dbc", content, "text")
    with pytest.raises(ValueError, match="didn't arrive whole"):
        svc.notebook_import("w.dbc", "@@@", "base64")


def test_import_ipynb_and_unknown_types(svc):
    nb = {"cells": [{"cell_type": "markdown", "source": ["# T"]}, {"cell_type": "code", "source": "1", "outputs": []}]}
    assert svc.notebook_import("Jup.ipynb", json.dumps(nb))["created"] == ["Jup"]
    assert [c["type"] for c in svc.notebooks.load("Jup")["cells"]] == ["markdown", "code"]
    with pytest.raises(ValueError, match="isn't valid JSON"):
        svc.notebook_import_check("x.ipynb", "{")
    with pytest.raises(ValueError, match="imports .ipynb"):
        svc.notebook_import_check("x.docx", "hi")


def test_export_writes_a_new_file_in_downloads_each_time(svc, tmp_path):
    first = svc.notebook_export("Daily sales", CELLS, "fabric_py")
    second = svc.notebook_export("Daily sales", CELLS, "fabric_py")
    assert Path(first["path"]) == tmp_path / "home" / "Downloads" / "Daily sales.py"
    assert Path(second["path"]).name == "Daily sales (2).py"
    assert Path(first["path"]).read_text(encoding="utf-8").startswith("# Fabric notebook source")
    assert Path(svc.notebook_export("../x", CELLS, "ipynb_outputs")["path"]).name == "x with outputs.ipynb"
    assert Path(svc.notebook_export("n", CELLS, "html")["path"]).suffix == ".html"
    assert not svc.notebooks.list()  # exporting doesn't save the notebook
