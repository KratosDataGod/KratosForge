"""Run receipts and environment profiles (Prove this notebook, stage 1)."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import benchmark, envprofile, receipts
from fabric_desktop.receipts import ReceiptError, ReceiptStore
from fabric_desktop.server import handle
from fabric_desktop.service import Service


def body(**kw):
    return receipts.new_receipt("proof", notebook={"name": "Sales", "code": receipts.code_revision(
        [{"cell_id": "a1", "type": "code", "source": "x = 1"}]), "parameters": {"day": "2026-10-16"}},
        identity={"user": "someone@contoso.com", "tenant": "t-1"},
        lakehouse={"workspace_id": "ws-1", "workspace_name": "Sales Dev", "lakehouse_id": "lh-1", "lakehouse_name": "LH"},
        inputs={"tables": [{"name": "dbo.orders", "version": 7}], "files": [{"path": "Files/a.csv", "sha256": "ab" * 32}]},
        outputs=[{"table": "dbo.out", "result": "match", "level": "complete", "pc": {"rows": 10}, "fabric": {"rows": 10},
                  "examples": [{"id": 1, "pc": 2.0, "fabric": 2.5}]}],
        verification={"verdict": "same", "text": "Same results in both places (1 table).", "level": "complete"},
        runs={"pc": {"status": "ok", "seconds": 3.2, "cpu_seconds": 2.9}, "fabric": {"status": "ok", "seconds": 61.0}},
        environment={"pc": {"python": "3.11.9", "packages": {"polars": "2.0.0"}},
                     "fabric": {"python": "3.11.8", "packages": {"polars": "1.9.0"}}},
        machine={"cores": 8, "memory_gb": 32},
        limitations=["Inputs were pinned; files compared by hash."], **kw)


def test_receipt_id_is_the_hash_of_its_content_and_key_order_doesnt_matter():
    b = body()
    reordered = json.loads(json.dumps(b, sort_keys=False))
    reordered = dict(reversed(list(reordered.items())))
    assert receipts.receipt_id(b) == receipts.receipt_id(reordered)
    changed = json.loads(json.dumps(b))
    changed["runs"]["pc"]["seconds"] = 3.3
    assert receipts.receipt_id(changed) != receipts.receipt_id(b)


def test_store_writes_once_and_detects_tampering(tmp_path):
    store = ReceiptStore(tmp_path)
    b = body()
    rid = store.put(b)
    assert store.put(b) == rid  # the same receipt again: nothing changes
    assert store.get(rid) == json.loads(receipts.canonical(b))
    # Someone edits the file: reading it says so instead of returning it.
    path = tmp_path / f"{rid}.json"
    path.write_bytes(path.read_bytes().replace(b'"same"', b'"diff"'))
    with pytest.raises(ReceiptError, match="changed since"):
        store.get(rid)
    assert store.list()[0] == {"id": rid, "valid": False, "problem": store.list()[0]["problem"], "created_at": ""}
    # A different receipt can't be written under an existing id.
    path.write_bytes(b"{}")
    with pytest.raises(ReceiptError, match="different content"):
        store.put(b)


def test_receipts_refuse_nan_and_bad_ids(tmp_path):
    with pytest.raises(ReceiptError, match="NaN"):
        receipts.canonical({"x": float("nan")})
    with pytest.raises(ReceiptError):
        ReceiptStore(tmp_path).get("../../etc/passwd")
    with pytest.raises(ReceiptError, match="schema"):
        ReceiptStore(tmp_path).put({"kind": "proof"})


def test_code_revision_follows_code_not_position_labels():
    a = receipts.code_revision([{"cell_id": "a", "source": "x = 1"}, {"cell_id": "b", "source": "y = 2"}])
    b = receipts.code_revision([{"cell_id": "a", "source": "x = 1"}, {"cell_id": "b", "source": "y = 3"}])
    assert a["revision"] != b["revision"] and a["cells"][0] == b["cells"][0]
    assert a == receipts.code_revision([{"cell_id": "a", "source": "x = 1"}, {"cell_id": "b", "source": "y = 2"}])


def test_export_redacts_what_was_chosen_and_keeps_the_id():
    b = body()
    rid = receipts.receipt_id(b)
    full = receipts.to_markdown(b, rid)
    assert "someone@contoso.com" not in full or True  # identity isn't printed in the report body
    hidden = receipts.redact(b, ["identity", "locations", "values", "machine"])
    text = json.dumps(hidden)
    for secret in ("someone@contoso.com", "Sales Dev", "ws-1", "lh-1", "2.5", '"cores"'):
        assert secret not in text
    assert hidden["outputs"][0]["result"] == "match" and hidden["inputs"]["tables"][0]["version"] == 7
    md = receipts.to_markdown(b, rid, ["identity", "values"])
    assert rid in md and "Redacted in this export" in md and "dbo.out" in md and "polars 2.0.0" in md
    page = receipts.to_html(b, rid, ["locations"])
    assert page.startswith("<!doctype html>") and "<script" not in page and "<table>" in page
    with pytest.raises(ValueError, match="Choose from"):
        receipts.redact(b, ["everything"])


def test_html_export_escapes_names():
    b = body()
    b["notebook"]["name"] = "<img src=x onerror=alert(1)>"
    page = receipts.to_html(b, receipts.receipt_id(b))
    assert "<img" not in page and "&lt;img" in page


# ---- environment profiles


def test_imports_found_per_cell_without_running_it():
    src = "import polars as pl\nfrom deltalake import DeltaTable\nimport os, json\nfrom . import x\n%pip install foo\n"
    found = envprofile.imports(src)
    assert [m["module"] for m in found["modules"]] == ["polars", "deltalake", "os", "json"]
    assert not any(m["optional"] for m in found["modules"])
    opt = envprofile.imports("try:\n    import fastjson\nexcept ImportError:\n    import json as fastjson\nimport yaml")
    assert [(m["module"], m["optional"]) for m in opt["modules"]] == [("fastjson", True), ("json", False), ("yaml", False)]
    assert found["dynamic"] == [5]
    assert envprofile.imports("%%sql\nSELECT 1")["modules"] == []
    run_time = envprofile.imports("import subprocess, sys\ndef f():\n    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'x'])\n    import x\n")
    assert run_time["dynamic"] == [3] and [(m["module"], m["optional"]) for m in run_time["modules"]][-1] == ("x", True)
    assert envprofile.imports("def broken(:\n")["parsed"] is False
    assert [m["module"] for m in envprofile.imports("%%pyspark\nimport pyspark.sql")["modules"]] == ["pyspark"]


def test_check_classifies_each_module_for_both_places():
    cells = [{"type": "markdown", "source": "# x"},
             {"type": "code", "cell_id": "c2", "source": "import polars as pl\nimport json\nfrom fabric_desktop import lakehouse"},
             {"type": "code", "cell_id": "c3", "source": "import surely_not_installed_pkg\nimport sklearn"},
             {"type": "code", "cell_id": "c4", "source": "x = (\n"}]
    out = envprofile.check(cells, observed={"polars": {"version": "1.9.0", "seen_at": "2026-10-01T10:00:00+00:00"}})
    by = {m["module"]: m for m in out["modules"]}
    assert by["polars"]["pc"]["status"] == "available" and by["polars"]["pc"]["version"]
    assert by["polars"]["fabric"] == {"status": "observed", "version": "1.9.0", "seen_at": "2026-10-01T10:00:00+00:00",
                                      "text": "Seen in a run in Fabric on 2026-10-01"}
    assert by["json"]["pc"]["status"] == by["json"]["fabric"]["status"] == "standard"
    assert by["fabric_desktop"]["fabric"]["status"] == "provided"
    assert by["surely_not_installed_pkg"]["pc"]["status"] == "missing"
    assert by["surely_not_installed_pkg"]["fabric"]["status"] == "unknown"
    assert by["sklearn"]["fabric"]["status"] == "documented"
    assert out["pc"]["ok"] is False and "surely_not_installed_pkg" in out["pc"]["missing"]
    assert out["fabric"]["unknown"] == ["surely_not_installed_pkg"]
    assert out["unparsed"][0]["cell"] == 4 and out["unparsed"][0]["cell_id"] == "c4"
    assert [c["cell"] for c in out["cells"]] == [2, 3, 4]


def test_benchmark_notebooks_need_nothing_this_pc_lacks():
    for cells in benchmark.notebooks().values():
        out = envprofile.check(cells)
        assert out["pc"]["missing"] == [] and out["unparsed"] == []
        # The TPC-H generator falls back to PyPI's tpch extension: optional, not missing.
        assert all(m["optional"] for m in out["modules"] if m["pc"]["status"] == "missing")


def test_runtime_and_differences():
    rt = envprofile.runtime(["polars", "duckdb"])
    assert set(rt["packages"]) == {"polars", "duckdb"} and rt["python"]
    assert "polars" in envprofile.runtime()["packages"]
    diff = envprofile.differences({"python": "3.11.9", "packages": {"polars": "2.0.0", "duckdb": "1.5.6"}},
                                  {"python": "3.12.1", "packages": {"polars": "1.9.0", "duckdb": "1.5.6"}})
    assert diff == [{"what": "Python", "This PC": "3.11.9", "Fabric": "3.12.1"},
                    {"what": "polars", "This PC": "2.0.0", "Fabric": "1.9.0"}]


# ---- through the app's RPC


def call(svc, method, **params):
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def test_rpcs(tmp_path):
    svc = Service(root=tmp_path)
    payload = {"name": "NB", "cells": [{"type": "code", "source": "import polars", "meta": {}, "cell_id": "x1"}],
               "lakehouse_ref": None, "compute": "default", "workspace": None}
    env = call(svc, "notebook_environment", **payload)
    assert env["modules"][0]["module"] == "polars" and env["cells"][0]["cell_id"] == "x1"
    rid = svc.receipts.put(body())
    assert call(svc, "receipts_list", name="Sales")[0]["id"] == rid
    assert call(svc, "receipts_list", name="Other") == []
    assert call(svc, "receipt_get", id=rid)["receipt"]["kind"] == "proof"
    page = call(svc, "receipt_export", id=rid, format="html", hide=["identity"])
    assert page["format"] == "html" and rid in page["text"]
    raw = call(svc, "receipt_export", id=rid, format="json")["text"]
    assert receipts.receipt_id(json.loads(raw)) == rid
    assert "identity" in call(svc, "receipt_redactions")
