"""Notebook debugger (#2362): breakpoints kept in the notebook file, and a multi-cell debug run.

Breakpoints are a cell setting like the parameters tag: saved in the .ipynb under the cell's
``fabric_desktop.breakpoints``, kept by a save that changes nothing else, carried by the Fabric
.py source format in the cell's METADATA block, and listed as dropped by a Databricks export.

The multi-cell test drives the kernel the way "Debug from here" does: after a step in one cell,
the next cell starts paused at its first line; after Continue, it runs to its breakpoints only.
"""

from __future__ import annotations

import json
import time

import pytest

from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel, NotebookStore, breakpoints_meta, from_ipynb, to_ipynb
from fabric_desktop.notebook_formats import databricks_export_notes, export_text, parse_ipynb, parse_source

CELLS = [
    {"type": "markdown", "source": "# Orders"},
    {"type": "code", "source": "rows = [3, 1, 2]\ntotal = 0", "meta": {"tags": ["parameters"], "breakpoints": [2]}},
    {"type": "code", "source": "for r in rows:\n    total += r\nprint(total)", "meta": {"breakpoints": [3, 2]}},
    {"type": "code", "source": "print('no breakpoints')"},
]


# ---------- saved with the notebook ----------

def test_breakpoints_are_saved_in_the_ipynb_and_read_back():
    nb = to_ipynb(CELLS, None)
    assert nb["cells"][2]["metadata"]["fabric_desktop"] == {"breakpoints": [2, 3]}
    assert nb["cells"][1]["metadata"]["tags"] == ["parameters"]
    assert "fabric_desktop" not in nb["cells"][3]["metadata"]
    back = from_ipynb(json.loads(json.dumps(nb)))["cells"]
    assert [(c.get("meta") or {}).get("breakpoints") for c in back] == [None, [2], [2, 3], None]


def test_save_and_reopen_keeps_breakpoints_and_clearing_them_removes_them(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("Orders", CELLS)
    cells = store.load("Orders")["cells"]
    assert cells[2]["meta"]["breakpoints"] == [2, 3]
    assert cells[1]["meta"] == {"tags": ["parameters"], "breakpoints": [2]}

    del cells[2]["meta"]["breakpoints"]
    store.save("Orders", cells)
    saved = json.loads(store.path("Orders").read_text(encoding="utf-8"))
    assert "fabric_desktop" not in saved["cells"][2]["metadata"]  # the old value isn't merged back
    assert saved["cells"][1]["metadata"]["fabric_desktop"] == {"breakpoints": [2]}


def test_other_tools_keys_next_to_breakpoints_are_kept(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("Orders", CELLS)
    path = store.path("Orders")
    nb = json.loads(path.read_text(encoding="utf-8"))
    nb["cells"][2]["metadata"]["fabric_desktop"]["someone_else"] = {"keep": True}
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    cells = store.load("Orders")["cells"]
    cells[2]["meta"]["breakpoints"] = [1]
    store.save("Orders", cells)
    meta = json.loads(path.read_text(encoding="utf-8"))["cells"][2]["metadata"]["fabric_desktop"]
    assert meta == {"someone_else": {"keep": True}, "breakpoints": [1]}


@pytest.mark.parametrize("value, kept", [
    ([5, 1, 5, 3], [1, 3, 5]),
    ([0, -2, "4", 2.0, True, None, 7], [7]),
    ("3", []),
    ({"line": 3}, []),
    (None, []),
    (list(range(1, 500)), list(range(1, 101))),
])
def test_a_hand_edited_value_is_cleaned_not_refused(value, kept):
    assert breakpoints_meta(value) == kept
    nb = {"cells": [{"cell_type": "code", "source": "x = 1", "metadata": {"fabric_desktop": {"breakpoints": value}}}]}
    assert (from_ipynb(nb)["cells"][0].get("meta") or {}).get("breakpoints") == (kept or None)


def test_a_markdown_cell_never_carries_breakpoints_into_formats_that_only_have_code():
    cells = [{"type": "markdown", "source": "# T", "meta": {"breakpoints": [1]}}]
    assert databricks_export_notes(cells) == []


# ---------- the other formats KratosForge writes ----------

def test_ipynb_exports_keep_breakpoints():
    for fmt in ("ipynb", "ipynb_outputs"):
        cells = parse_ipynb(export_text(fmt, "Orders", CELLS))["cells"]
        assert [(c.get("meta") or {}).get("breakpoints") for c in cells] == [None, [2], [2, 3], None]


def test_fabric_source_carries_breakpoints_in_the_cell_metadata_block():
    text = export_text("fabric_py", "Orders", CELLS)
    assert '"breakpoints": [' in text and "# META" in text
    cells = parse_source(text)["cells"]
    assert [c["source"] for c in cells] == [c["source"] for c in CELLS]
    assert [(c.get("meta") or {}).get("breakpoints") for c in cells] == [None, [2], [2, 3], None]
    assert cells[1]["meta"]["tags"] == ["parameters"]


def test_fabric_source_without_our_key_reads_as_before():
    text = export_text("fabric_py", "Orders", [{"type": "code", "source": "x = 1"}])
    assert "fabric_desktop" not in text
    assert parse_source(text)["cells"] == [{"type": "code", "source": "x = 1"}]


def test_databricks_export_says_breakpoints_are_dropped():
    notes = databricks_export_notes(CELLS)
    dropped = [n for n in notes if "breakpoints" in n["what"]]
    assert [(n["cell"], n["kind"]) for n in dropped] == [(2, "dropped"), (3, "dropped")]
    assert "\"breakpoints\"" not in export_text("databricks_py", "Orders", CELLS)


# ---------- a multi-cell debug run (the Academy-style verification) ----------

NOTEBOOK = [
    "orders = [{'id': 1, 'qty': 2}, {'id': 2, 'qty': 0}, {'id': 3, 'qty': 5}]\nkept = []",
    "def keep(o):\n    ok = o['qty'] > 0\n    return ok\nfor o in orders:\n    if keep(o):\n        kept.append(o['id'])",
    "count = len(kept)\nprint(count)",
]


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    k = Kernel() if request.param == "engine" else ProcessKernel()
    yield k
    if request.param == "process":
        k.close()


def wait(k, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


def test_stepping_carries_from_cell_to_cell(kernel):
    # Debug from here on cell 1: it starts paused at its first line.
    kernel.start(NOTEBOOK[0], step=True)
    assert wait(kernel)["paused"]["line"] == 1
    kernel.debug("next")
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("next")  # past the cell's last line: the cell ends
    assert wait(kernel)["status"] == "ok"

    # The last action was a step, so cell 2 starts paused too; step into keep().
    kernel.start(NOTEBOOK[1], step=True)
    assert wait(kernel)["paused"]["line"] == 1
    for line in (4, 5, 2):
        kernel.debug("step")
        p = wait(kernel)["paused"]
        assert p["line"] == line
    assert [f["function"] for f in p["stack"]] == ["(cell)", "keep"]
    assert [(v["name"], v["type"]) for v in p["variables"]] == [("o", "dict")]
    assert kernel.debug("eval", "o['qty']")["value"] == "2"
    kernel.debug("out")
    p = wait(kernel)["paused"]
    assert (p["line"], len(p["stack"])) == (6, 1)
    assert kernel.debug("eval", "kept")["value"] == "[]"
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"

    # After Continue, cell 3 runs with its breakpoints only.
    kernel.start(NOTEBOOK[2], breakpoints=[2])
    p = wait(kernel)["paused"]
    assert (p["line"], p["reason"]) == (2, "breakpoint")
    assert {v["name"]: v["value"] for v in p["variables"]}["count"] == "2"
    kernel.debug("continue")
    snap = wait(kernel)
    assert snap["status"] == "ok" and snap["outputs"][-1]["text"].strip() == "2"


def test_stop_while_paused_leaves_no_debugger_behind(kernel):
    kernel.start(NOTEBOOK[0])
    assert wait(kernel)["status"] == "ok"
    kernel.start(NOTEBOOK[1], breakpoints=[6])
    assert wait(kernel)["paused"]["line"] == 6
    assert kernel.interrupt()["interrupted"] is True
    assert wait(kernel)["status"] == "cancelled"
    with pytest.raises(RuntimeError):
        kernel.debug("continue")  # nothing is paused any more
    # The next cell runs untraced, at full speed, with the variables the stopped cell left.
    kernel.start("import sys\nprint(sys.gettrace() is None, kept)")
    snap = wait(kernel)
    assert snap["status"] == "ok" and snap.get("paused") is None
    assert snap["outputs"][-1]["text"].strip() == "True []"
