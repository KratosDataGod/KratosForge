"""Notebook import and export formats (notebook_formats.py), the paths the main tests don't reach:
outputs a .ipynb or the HTML page leaves out or shows plainly, SVG charts that can't be decoded, Fabric
cell metadata that isn't JSON, Databricks cells that aren't magics, files over the size limit, and
Databricks archives with documents that aren't notebooks. All in memory."""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest

from fabric_desktop import notebook_formats as nbf


def code(*outputs, count=None):
    cell = {"type": "code", "source": "x = 1", "outputs": list(outputs)}
    if count is not None:
        cell["count"] = count
    return cell


def ipynb_outputs(*outputs):
    nb = json.loads(nbf.to_ipynb_text([code(*outputs)], outputs=True))
    return nb["cells"][0]["outputs"]


# ---- export: .ipynb with outputs


def test_an_output_a_notebook_file_cant_hold_is_left_out():
    assert ipynb_outputs({"type": "audio", "data": "x"}, {"type": "vegalite", "spec": "not a dict"}) == []


def test_an_svg_chart_is_written_as_its_text_and_one_that_cant_be_decoded_as_empty():
    svg = "<svg xmlns='http://www.w3.org/2000/svg'/>"
    good = {"type": "image", "mime": "image/svg+xml", "data": base64.b64encode(svg.encode()).decode()}
    bad = {"type": "image", "mime": "image/svg+xml", "data": base64.b64encode(b"\xff\xfe").decode()}
    got = ipynb_outputs(good, bad)
    assert [o["data"]["image/svg+xml"] for o in got] == [svg, ""]
    assert nbf._svg_text("a") == ""


# ---- export: the HTML page


def test_the_page_shows_errors_with_their_traceback_images_and_says_what_it_leaves_out():
    png = base64.b64encode(b"\x89PNG").decode()
    page = nbf.to_html("Sales", [code(
        {"type": "error", "ename": "ValueError", "evalue": "bad <value>", "traceback": "line 1"},
        {"type": "image", "mime": "image/png", "data": png},
        {"type": "image", "mime": "image/png", "data": "not base64 <script>"},
        {"type": "vegalite", "spec": {}},
        {"type": "unknown"},
        count=3)])
    assert '<pre class="out err">ValueError: bad &lt;value&gt;\n\nline 1</pre>' in page
    assert f'src="data:image/png;base64,{png}"' in page and "<script>" not in page
    assert "Altair charts aren't included in the page" in page
    assert '<div class="label">Cell 1 [3]</div>' in page


# ---- import: source files


def test_fabric_cell_metadata_that_isnt_json_is_ignored():
    text = "\n".join([nbf.FABRIC_HEADER, nbf.FABRIC_CELL, "SELECT 1", nbf.FABRIC_METADATA, "# META {not json"])
    cells = nbf.parse_source(text)["cells"]
    assert [c["source"] for c in cells] == ["SELECT 1"]
    assert nbf._fabric_meta(["# META [1, 2]"]) == {}


def test_a_databricks_cell_of_magic_lines_without_a_magic_is_code():
    text = "\n".join([f"# {nbf.DATABRICKS_HEADER}", "# MAGIC print('hello')"])
    assert [c["source"] for c in nbf.parse_source(text)["cells"]] == ["print('hello')"]


def test_files_over_the_size_limit_arent_read(monkeypatch):
    monkeypatch.setattr(nbf, "MAX_TEXT", 10)
    with pytest.raises(ValueError, match="larger than 32 MB"):
        nbf.parse_source("print('a long line')")
    with pytest.raises(ValueError, match="larger than 32 MB"):
        nbf.parse_ipynb('{"cells": [], "pad": "xxxx"}')


@pytest.mark.parametrize("text", ['[1, 2]', '{"cells": "none"}'])
def test_a_json_file_without_a_cells_list_isnt_a_notebook(text):
    with pytest.raises(ValueError, match="has no cells list"):
        nbf.parse_ipynb(text)


# ---- import: Databricks archives


def dbc(**entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name.replace("__", "/"), text)
    return buf.getvalue()


def test_archive_documents_that_arent_notebooks_and_empty_commands_are_skipped():
    notebook = json.dumps({"name": "load", "language": "python",
                           "commands": [{"command": "   ", "position": 1}, {"command": "print(1)", "position": 2}]})
    data = dbc(**{"manifest.json": "not json", "other.json": json.dumps({"name": "x"}),
                  "list.json": "[1]", "etl__load.python": notebook})
    out = nbf.parse_dbc(data)
    assert [(n["name"], n["folder"], [c["source"] for c in n["cells"]]) for n in out] == [
        ("load", "etl", ["print(1)"])]


def test_an_archive_of_only_documents_that_arent_notebooks_has_none():
    with pytest.raises(ValueError, match="no notebooks in it"):
        nbf.parse_dbc(dbc(**{"manifest.json": "{}"}))
