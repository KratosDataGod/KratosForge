"""WR-132: a .ipynb written by another tool comes back from KratosForge as that tool wrote it.

The notebooks are built here, in the shape and byte layout their tools write: JupyterLab (sorted keys,
indent 1, a final newline, cell attachments, ipywidgets state and widget-view outputs, a mime type
nobody knows), VS Code on Windows (CRLF, no final newline, vscode metadata, non-ASCII as is) and a
Fabric export (all on one line, kernel_info, a default lakehouse and environment in dependencies).
Each is opened with NotebookStore.load and saved with NotebookStore.save, the methods behind the
open_notebook and save_notebook RPCs, with the cells passed through JSON as the app sends them.

test_ipynb_round_trip.py covers the same rule on the fixture files in tests/fixtures/ipynb and the app's own
keys (lakehouse, settings, attachments); this file adds other tools' layouts, key order and line endings."""
import json
from pathlib import Path

import pytest

from fabric_desktop.notebook import NotebookStore

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
WIDGET_MODEL = "8f3c0d2e5b1a4c6f9e7d2b1a0c3e5f7a"  # pragma: allowlist secret (made up)


def _jupyter() -> tuple[dict, bytes]:
    nb = {
        "cells": [
            {"attachments": {"chart.png": {"image/png": PNG}, "unused.png": {"image/png": PNG}},
             "cell_type": "markdown", "id": "1f0c6a3e-7d55-4b8e-9a41-0c2f3b9d6e11",
             "metadata": {"editable": True, "slideshow": {"slide_type": "slide"}},
             "source": ["# Sales\n", "\n", "![chart](attachment:chart.png)"]},
            {"cell_type": "code", "execution_count": 3, "id": "2a7b9c0d-1e2f-4a5b-8c6d-7e8f9a0b1c2d",
             "metadata": {"collapsed": False, "jupyter": {"outputs_hidden": False}, "scrolled": "auto",
                          "papermill": {"duration": 0.12, "status": "completed"}},
             "outputs": [
                 {"data": {"application/vnd.jupyter.widget-view+json":
                           {"model_id": WIDGET_MODEL, "version_major": 2, "version_minor": 0},
                           "text/plain": ["IntSlider(value=5)"]},
                  "metadata": {}, "output_type": "display_data"},
                 {"data": {"application/vnd.acme.unknown+json": {"nested": [1, 2.5, None, True]},
                           "text/html": ["<b>5</b>"], "text/plain": ["5"]},
                  "execution_count": 3, "metadata": {"application/vnd.acme.unknown+json": {"expanded": False}},
                  "output_type": "execute_result"},
                 {"name": "stderr", "output_type": "stream", "text": ["warning: x\n", "done"]},
             ],
             "source": ["import ipywidgets as w\n", "s = w.IntSlider(value=5)\n", "display(s)\n", "s.value"]},
            {"cell_type": "code", "execution_count": 4, "id": "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f",
             "metadata": {"tags": ["parameters"]},
             "outputs": [{"ename": "ZeroDivisionError", "evalue": "division by zero", "output_type": "error",
                          "traceback": ["\u001b[0;31mZeroDivisionError\u001b[0m"]}],
             "source": ["x = 1\n", "1 / 0"]},
            {"cell_type": "raw", "id": "4d5e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f7a",
             "metadata": {"format": "text/restructuredtext"}, "source": [".. note:: raw"]},
        ],
        "metadata": {
            "acme_extension": {"theme": "dark", "pinned": [3, 1]},
            "kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"},
            "language_info": {"codemirror_mode": {"name": "ipython", "version": 3}, "file_extension": ".py",
                              "mimetype": "text/x-python", "name": "python", "nbconvert_exporter": "python",
                              "pygments_lexer": "ipython3", "version": "3.11.6"},
            "widgets": {"application/vnd.jupyter.widget-state+json": {
                "state": {WIDGET_MODEL: {"model_module": "@jupyter-widgets/controls", "model_module_version": "2.0.0",
                                         "model_name": "IntSliderModel", "state": {"value": 5}}},
                "version_major": 2, "version_minor": 0}},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    # As nbformat.write does: sorted keys, indent 1, non-ASCII as is, a final newline.
    return nb, (json.dumps(nb, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _vscode() -> tuple[dict, bytes]:
    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["## Café résumé ✓"]},
            {"cell_type": "code", "execution_count": 1,
             "metadata": {"vscode": {"languageId": "sql"}},
             "outputs": [{"data": {"application/vnd.code.notebook.stdout": ["ok"], "text/plain": ["ok"]},
                          "metadata": {"transient": {"display_id": "abc"}}, "output_type": "display_data"}],
             "source": ["%%sql\n", "SELECT 1"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["print('hi')"]},
        ],
        "metadata": {
            "kernelspec": {"display_name": ".venv", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.12.1"},
            "orig_nbformat": 4,
            "vscode": {"interpreter": {"hash": "4f2a1c9b0e7d6a5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f"}},  # pragma: allowlist secret (made up)
        },
        "nbformat": 4,
        "nbformat_minor": 2,
    }
    # VS Code on Windows: CRLF, indent 1, no final newline, non-ASCII as is, written without cell ids (4.2).
    text = json.dumps(nb, indent=1, sort_keys=True, ensure_ascii=False).replace("\n", "\r\n")
    return nb, text.encode()


def _fabric() -> tuple[dict, bytes]:
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "notebookName": "Load sales",
        "cells": [
            {"cell_type": "code", "source": ["df = spark.read.table('sales')\n", "display(df)"],
             "outputs": [{"output_type": "display_data", "metadata": {},
                          "data": {"application/vnd.synapse.widget-view+json":
                                   {"widget_id": "5b6c-77", "widget_type": "Synapse.DataFrame"}}}],
             "execution_count": 7, "metadata": {"microsoft": {"language": "python", "language_group": "synapse_pyspark"},
                                                "advisor": {"adviceMetadata": "{\"artifactId\":\"x\"}"}},
             "id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"},
            {"cell_type": "code", "source": ["print('two')"], "outputs": [], "execution_count": None,
             "metadata": {"microsoft": {"language": "python", "language_group": "synapse_pyspark"}},
             "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"},
        ],
        "metadata": {
            "kernel_info": {"name": "synapse_pyspark"},
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
            "language_info": {"name": "python"},
            "microsoft": {"language": "python", "language_group": "synapse_pyspark", "ms_spell_check": {"ms_spell_check_language": "en"}},
            "widgets": {},
            "spark_compute": {"compute_id": "/trident/default", "session_options": {"conf": {"spark.x": "1"}}},
            "dependencies": {
                "lakehouse": {"default_lakehouse": "11111111-2222-3333-4444-555555555555",
                              "default_lakehouse_name": "Sales", "default_lakehouse_workspace_id": "66666666-7777-8888-9999-000000000000",
                              "known_lakehouses": [{"id": "11111111-2222-3333-4444-555555555555"}]},
                "environment": {"environmentId": "e-1", "workspaceId": "w-1"},
            },
        },
    }
    # A Fabric export: the notebook's definition on one line, Fabric's own key order, no final newline.
    return nb, json.dumps(nb, ensure_ascii=False, separators=(",", ":")).encode()


FIXTURES = {"jupyter": _jupyter, "vscode": _vscode, "fabric": _fabric}


def _open(tmp_path: Path, raw: bytes) -> tuple[NotebookStore, dict]:
    (tmp_path / "nb.ipynb").write_bytes(raw)
    store = NotebookStore(tmp_path)
    return store, json.loads(json.dumps(store.load("nb")))  # through JSON, as the app's RPC carries it


def _save(store: NotebookStore, nb: dict) -> bytes:
    out = store.save("nb", nb["cells"], nb["lakehouse"], nb["compute"], nb["workspace"], nb["disk_stamp"])
    assert not out.get("changed_on_disk")
    return (store.folder / "nb.ipynb").read_bytes()


def _line_style(raw: bytes) -> tuple[str, bool]:
    crlf = b"\r\n" in raw
    bare_lf = raw.replace(b"\r\n", b"").count(b"\n")
    bare_cr = raw.replace(b"\r\n", b"").count(b"\r")
    assert not bare_cr and not (crlf and bare_lf), "mixed line endings"
    return ("crlf" if crlf else "lf"), raw.endswith(b"\n")


def _no_id(cell: dict) -> dict:
    return {k: v for k, v in cell.items() if k != "id"}


@pytest.mark.parametrize("name", FIXTURES)
def test_open_then_save_without_edits_is_byte_for_byte(tmp_path, name):
    _, raw = FIXTURES[name]()
    store, nb = _open(tmp_path, raw)
    assert _save(store, nb) == raw
    assert _save(store, json.loads(json.dumps(store.load("nb")))) == raw  # and again


@pytest.mark.parametrize("name", FIXTURES)
def test_editing_one_cell_keeps_everything_else(tmp_path, name):
    original, raw = FIXTURES[name]()
    store, nb = _open(tmp_path, raw)
    k = len(nb["cells"]) - 1 if name != "jupyter" else 2  # the last code cell
    assert nb["cells"][k]["type"] == "code"
    nb["cells"][k]["source"] += "\nprint('edited')"
    after_raw = _save(store, nb)
    after = json.loads(after_raw)

    # Bytes can't match: the edited cell's lines change, and a file without cell ids (VS Code's 4.2)
    # gets ids and nbformat_minor 5 so the edit can be found again. The rest is compared as JSON,
    # and the layout (line ending, final newline) is compared on its own.
    assert _line_style(after_raw) == _line_style(raw)
    assert after["nbformat"] == original["nbformat"]
    assert after["nbformat_minor"] == max(original["nbformat_minor"], 5)
    assert {x: v for x, v in after.items() if x not in ("cells", "nbformat_minor")} == \
        {x: v for x, v in original.items() if x not in ("cells", "nbformat_minor")}
    assert list(after) == list(json.loads(raw))  # the file's key order, so the rest of the file doesn't move
    assert len(after["cells"]) == len(original["cells"])
    for i, (old, new) in enumerate(zip(original["cells"], after["cells"], strict=True)):
        if i == k:
            assert "".join(new["source"]).endswith("print('edited')")
            assert new["metadata"] == old["metadata"]  # every tool's cell settings kept
            assert new["outputs"] == [] and new["execution_count"] is None  # from code that isn't there now
            assert [x for x in json.loads(after_raw.decode())["cells"][i] if x in old] == \
                [x for x in json.loads(raw.decode())["cells"][i]]  # the cell's keys in the file's order
        else:
            assert _no_id(new) == _no_id(old)  # outputs, widget views, unknown mime, attachments, metadata
            if "id" in old:
                assert new["id"] == old["id"]

    # The next save, with nothing changed, leaves the edited file byte for byte.
    assert _save(store, json.loads(json.dumps(store.load("nb")))) == after_raw


def test_editing_a_markdown_cell_keeps_its_attachments_and_metadata(tmp_path):
    original, raw = _jupyter()
    store, nb = _open(tmp_path, raw)
    nb["cells"][0]["source"] += "\n\nMore text."
    after = json.loads(_save(store, nb))
    cell = after["cells"][0]
    assert cell["attachments"] == {"chart.png": {"image/png": PNG}}  # the picture it still shows
    assert cell["metadata"] == original["cells"][0]["metadata"]
    assert after["metadata"]["widgets"] == original["metadata"]["widgets"]
    assert [_no_id(c) for c in after["cells"][1:]] == [_no_id(c) for c in original["cells"][1:]]


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
@pytest.mark.parametrize("name", FIXTURES)
def test_an_edit_keeps_the_files_final_newline_and_line_ending(tmp_path, name, ending):
    _, raw = FIXTURES[name]()
    raw = raw.rstrip(b"\r\n")
    if ending == b"\r\n":
        raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    elif b"\r\n" in raw:
        raw = raw.replace(b"\r\n", b"\n")
    raw += ending
    store, nb = _open(tmp_path, raw)
    assert _save(store, nb) == raw
    nb["cells"][-1]["source"] = "x = 2"
    assert _line_style(_save(store, nb)) == _line_style(raw)
