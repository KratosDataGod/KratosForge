"""Variable history, watches and hidden state (Prove stage 5): the engine's part is the watches,
evaluated after each cell, and the notebook's saved watch list. History and the hidden-state
check are worked out by the notebook screen from kernel_variables and notebook_dependencies."""

from __future__ import annotations

import json
import time

import pytest

from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel, check_watches
from fabric_desktop.server import handle
from fabric_desktop.service import Service


def call(s, method, **params):
    out = handle(s, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def finished(k, timeout=30):
    deadline = time.monotonic() + timeout
    snap = k.poll(-1, 0.5)
    while snap["status"] == "running" and time.monotonic() < deadline:
        snap = k.poll(-1, 0.5)
    return snap


def by_expr(snap):
    return {w["expr"]: w for w in snap["watches"]}


@pytest.mark.parametrize("make", [Kernel, ProcessKernel], ids=["in engine", "own process"])
def test_watches_are_evaluated_after_each_cell(make):
    k = make()
    try:
        k.start("rows = [1, 2, 3]\nprint('cell output')", watches=["len(rows)", "sum(rows) / 0", "print('noise') or 7",
                                                            "api_key_value"])
        snap = finished(k)
        assert snap["status"] == "ok"
        w = by_expr(snap)
        assert w["len(rows)"]["value"] == "3" and w["len(rows)"]["type"] == "int"
        assert w["sum(rows) / 0"]["error"].startswith("ZeroDivisionError")
        assert w["print('noise') or 7"]["value"] == "7"
        assert "NameError" in w["api_key_value"]["error"]
        assert all(isinstance(x["seconds"], float) for x in snap["watches"])
        # What a watch prints doesn't land in the cell's output.
        assert "".join(o.get("text", "") for o in snap["outputs"]) == "cell output\n"
        # A failed cell still has its watches; a cell without watches has none.
        k.start("rows.append(4)\nraise ValueError('no')", watches=["len(rows)"])
        snap = finished(k)
        assert snap["status"] == "error" and by_expr(snap)["len(rows)"]["value"] == "4"
        k.start("1")
        assert "watches" not in finished(k)
    finally:
        if isinstance(k, ProcessKernel):
            k.close()


def test_secrets_in_watches_are_hidden():
    k = Kernel()
    k.namespace["password"] = "hunter2-very-secret"
    k.namespace["url"] = "https://x.blob.core.windows.net/c?sv=1&sig=abcdefSECRET"
    k.start("pass", watches=["password", "url"])
    w = by_expr(finished(k))
    assert w["password"]["value"] == "hidden"
    assert "abcdefSECRET" not in w["url"]["value"] and "[removed]" in w["url"]["value"]


def test_cancel_skips_the_rest_of_the_watches():
    k = Kernel()
    k.start("x = 1", watches=["x", "__import__('time').sleep(30)", "x + 1"])
    time.sleep(0.5)
    assert k.interrupt()["interrupted"] is True
    snap = finished(k)
    w = by_expr(snap)
    assert w["x"]["value"] == "1"
    assert w["__import__('time').sleep(30)"]["skipped"] and w["x + 1"]["skipped"]
    assert k.run("x")["outputs"] == [{"type": "text", "text": "1"}]


def test_only_expressions_can_be_watches():
    assert check_watches([" df.height ", "df.height", "", "len(x)"]) == ["df.height", "len(x)"]
    assert check_watches(None) == []
    for bad in (["x = 1"], ["import os"], ["x" * 201], [f"a{i}" for i in range(11)], "x", [1]):
        with pytest.raises(ValueError):
            check_watches(bad)


def test_watches_are_saved_with_the_notebook_and_cells_use_them(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    call(s, "save_notebook", name="nb", cells=[{"type": "code", "source": "n = 2"}])
    assert call(s, "notebook_debug_get", name="nb") == {"watches": [], "ignore_hidden": []}
    saved = call(s, "notebook_debug_set", name="nb", watches=["n * 21"], ignore_hidden=["total", "total"])
    assert saved == {"watches": ["n * 21"], "ignore_hidden": ["total"]}
    # A save from the notebook screen keeps them; only the expressions are in the file.
    call(s, "save_notebook", name="nb", cells=[{"type": "code", "source": "n = 2"}])
    assert call(s, "notebook_debug_get", name="nb")["watches"] == ["n * 21"]
    out = handle(s, json.dumps({"id": 1, "method": "notebook_debug_set", "params": {"name": "nb", "watches": ["n = 3"]}}))
    assert "isn't an expression" in json.dumps(out["error"])
    call(s, "start_cell", code="n = 2", notebook="nb", watches=["n * 21"])
    snap = call(s, "poll_cell", version=-1, wait=2)
    while snap["status"] == "running":
        snap = call(s, "poll_cell", version=-1, wait=2)
    assert snap["watches"][0]["value"] == "42"
    call(s, "notebook_debug_set", name="nb", watches=[], ignore_hidden=[])
    nb = json.loads(s.notebooks.path("nb").read_text(encoding="utf-8"))
    assert "debug" not in nb["metadata"].get("fabric_desktop", {})
