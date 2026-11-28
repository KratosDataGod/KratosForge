"""WR-136: a seeded fuzz of the .ipynb round trip through NotebookStore and merge_saved. Where
test_ipynb_round_trip.py opens five files shaped like real tools' and test_notebook_reader_fuzz.py feeds
the readers junk, this writes well-formed notebooks with every kind of metadata other tools leave in a
file (Jupyter, VS Code, Colab, Fabric, Databricks, papermill, widgets, attachments, KratosForge's own
slide marks and another tool's fabric_desktop keys) in the ways tools write them (indent 1, 2 or a tab,
escaped or plain non-ASCII, CRLF, sources as one string or a list of lines, ids missing, empty or
repeated), and checks for each:

- opening and saving with no change leaves the file byte for byte;
- editing one cell's source changes that cell (outputs and count dropped, its metadata kept) and
  nothing else in the file;
- marking a cell "Starts a slide" and unmarking it changes only that one key;
- what was saved opens again to the same cells.

The generator is random.Random with fixed seeds, so a failure names a case that can be run again; it
also prints the smallest notebook, found by dropping cells and metadata keys, that still fails.
"""

from __future__ import annotations

import copy
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fabric_desktop.notebook import NotebookStore

CASES = 300
SEEDS = range(3)

TEXT = ["x = 1", "print('é 日本 🚀')", "", "  indented\n", "a\nb\n", "# heading\n\nbody", "df = spark.sql('SELECT 1')",
        "%%sql\nSELECT * FROM t", "مرحبا", "tab\there", "trailing  \n", "\\n literal", 'quote "q"']
CELL_META = [
    {"tags": ["parameters"]}, {"tags": []}, {"tags": ["x", "y"]}, {"collapsed": True}, {"scrolled": "auto"},
    {"jupyter": {"source_hidden": True}}, {"jupyter": {"outputs_hidden": True}},
    {"vscode": {"languageId": "sql"}}, {"id": "ZWhWniBGu3_Y"}, {"colab": {"base_uri": "https://localhost:8080/"}},
    {"papermill": {"duration": 0.5, "status": "completed"}}, {"execution": {"iopub.status.busy": "2026-01-01T00:00:00Z"}},
    {"application/vnd.databricks.v1+cell": {"nuid": "n1", "title": "Load", "showTitle": True}},
    {"microsoft": {"language": "python", "language_group": "synapse_pyspark"}},
    {"fabric_desktop": {"slide_start": True}}, {"fabric_desktop": {"other_tool_key": [1, 2]}},
    {"editable": False}, {"deletable": False}, {"editable": True},
    {"run_control": {"frozen": True}, "editable": False, "deletable": False}, {"nested": {"deep": {"deeper": [None, 1.5, "s"]}}},
]
NB_META = [
    {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
    {"kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark"}},
    {"language_info": {"name": "python", "version": "3.12.4"}},
    {"colab": {"provenance": [], "toc_visible": True}}, {"accelerator": "GPU"},
    {"widgets": {"application/vnd.jupyter.widget-state+json": {"state": {"ab12": {"model_name": "IntSliderModel", "state": {"value": 3}}},
                                                               "version_major": 2, "version_minor": 0}}},
    {"vscode": {"interpreter": {"hash": "0f"}}},
    {"dependencies": {"lakehouse": {"default_lakehouse": "1111", "default_lakehouse_name": "lh",
                                    "known_lakehouses": [{"id": "1111"}, {"id": "2222"}]}}},
    {"dependencies": {"environment": {"environmentId": "e1", "workspaceId": "w1"}}},
    {"microsoft": {"language": "python", "ms_spell_check": {"ms_spell_check_language": "en"}}},
    {"synapse_widget": {"version": "0.1", "state": {}}}, {"a11y": {"x": True}},
    {"fabric_desktop": {"other_tool_key": "kept"}}, {"title": "Notebook é"},
]
OUTPUTS = [
    {"output_type": "stream", "name": "stdout", "text": ["a\n", "b"]},
    {"output_type": "execute_result", "execution_count": 3, "data": {"text/plain": ["3"]}, "metadata": {}},
    {"output_type": "display_data", "data": {"image/png": "iVBORw0KGgo=", "text/plain": ["<Figure>"]}, "metadata": {"needs_background": "light"}},
    {"output_type": "error", "ename": "ValueError", "evalue": "bad", "traceback": ["Traceback", "ValueError: bad"]},
]
PNG = {"image/png": "iVBORw0KGgo="}


def _source(r: random.Random, kind: str) -> Any:
    text = "\n".join(r.choice(TEXT) for _ in range(r.randint(1, 3)))
    if kind == "markdown" and r.random() < 0.3:
        text += "\n\n![pic](attachment:pic.png)"
    if r.random() < 0.5:  # a list of lines, as nbformat writes it
        lines = text.splitlines(keepends=True)
        return lines or [""]
    return text


def _cell(r: random.Random, i: int, ids: str) -> dict[str, Any]:
    kind = r.choices(["code", "markdown", "raw"], [6, 3, 1])[0]
    c: dict[str, Any] = {"cell_type": kind}
    if ids == "unique":
        c["id"] = f"cell-{i}-{r.randint(0, 9999)}"
    elif ids == "messy" and r.random() < 0.8:
        c["id"] = r.choice(["", "dup", f"c{i}"])
    c["metadata"] = {}
    for m in r.sample(CELL_META, r.randint(0, 3)):
        for k, v in m.items():
            c["metadata"][k] = copy.deepcopy(v) if k not in c["metadata"] else c["metadata"][k]
    if "run_control" in c["metadata"]:  # a frozen cell, as Jupyter writes one: not editable, not deletable
        c["metadata"].update(editable=False, deletable=False)
    c["source"] = _source(r, kind)
    if kind == "code":
        c["execution_count"] = r.choice([None, r.randint(1, 99)])
        c["outputs"] = copy.deepcopy(r.sample(OUTPUTS, r.randint(0, 2)))
    elif kind == "markdown" and r.random() < 0.3:
        c["attachments"] = {"pic.png": dict(PNG)} if r.random() < 0.7 else {"unused.png": dict(PNG)}
    if r.random() < 0.3:  # tools write keys in their own order
        c = dict(r.sample(list(c.items()), len(c)))
    return c


def _notebook(r: random.Random) -> dict[str, Any]:
    ids = r.choice(["unique", "none", "messy"])
    meta: dict[str, Any] = {}
    for m in r.sample(NB_META, r.randint(0, 5)):
        for k, v in m.items():
            meta.setdefault(k, copy.deepcopy(v))
    nb = {"cells": [_cell(r, i, ids) for i in range(r.randint(1, 8))], "metadata": meta, "nbformat": 4,
          "nbformat_minor": r.choice([0, 2, 4, 5, 10]) if ids == "none" else r.choice([5, 10])}
    if r.random() < 0.3:
        nb = dict(r.sample(list(nb.items()), len(nb)))
    return nb


def _dumps(r: random.Random, nb: dict[str, Any]) -> bytes:
    text = json.dumps(nb, indent=r.choice([1, 2, "\t", None]), ensure_ascii=r.random() < 0.5,
                      sort_keys=r.random() < 0.3)
    if r.random() < 0.8:
        text += "\n"
    if r.random() < 0.3:
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


def _store(tmp: Path, raw: bytes) -> NotebookStore:
    for p in tmp.glob("*"):
        if p.is_file():
            p.unlink()
    (tmp / "nb.ipynb").write_bytes(raw)
    return NotebookStore(tmp)


def _save(store: NotebookStore, nb: dict[str, Any]) -> bytes:
    store.save("nb", nb["cells"], nb["lakehouse"], nb["compute"], nb["workspace"], nb["disk_stamp"])
    return (store.folder / "nb.ipynb").read_bytes()


def _no_id(c: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in c.items() if k != "id"}


def _src(c: dict[str, Any]) -> str:
    s = c.get("source", "")
    return "".join(s) if isinstance(s, list) else s


# ---------- the properties; each returns None when it holds, or what went wrong ----------

def _unchanged(tmp: Path, raw: bytes, r: random.Random) -> str | None:
    store = _store(tmp, raw)
    nb = store.load("nb")
    out = _save(store, nb)
    if out != raw:
        return "opening and saving with no change rewrote the file"
    again = store.load("nb")
    if [(c["type"], c["source"]) for c in again["cells"]] != [(c["type"], c["source"]) for c in nb["cells"]]:
        return "the saved file opened to different cells"
    return None


def _one_edit(tmp: Path, raw: bytes, r: random.Random) -> str | None:
    store = _store(tmp, raw)
    before = json.loads(raw)
    nb = store.load("nb")
    k = r.randrange(len(nb["cells"]))
    nb["cells"][k]["source"] += "\nedited = True"
    after = json.loads(_save(store, nb))
    if len(after["cells"]) != len(before["cells"]):
        return f"{len(before['cells'])} cells became {len(after['cells'])}"
    for i, (old, new) in enumerate(zip(before["cells"], after["cells"], strict=True)):
        if i == k:
            if not _src(new).endswith("edited = True"):
                return f"cell {i} lost the edit"
            if new.get("outputs") not in (None, []) or new.get("execution_count") is not None:
                return f"cell {i} kept outputs from code that isn't there any more"
            if new["metadata"] != old.get("metadata", {}):
                return f"cell {i}'s metadata changed: {old.get('metadata')} -> {new['metadata']}"
            refs = "attachment:pic.png" in _src(new)
            if refs and new.get("attachments", {}).get("pic.png") != old.get("attachments", {}).get("pic.png"):
                return f"cell {i} lost a picture its source still shows"
        elif _no_id(new) != _no_id(old):
            return f"cell {i}, which wasn't edited, changed"
    rest = lambda d: {k: v for k, v in d.items() if k not in ("cells", "nbformat_minor")}  # noqa: E731
    if rest(after) != rest(before):
        return f"the notebook's metadata changed: {rest(before)} -> {rest(after)}"
    again = store.load("nb")
    if again["cells"][k]["source"] != nb["cells"][k]["source"]:
        return "the edit didn't open again as saved"
    return None


def _slide_mark(tmp: Path, raw: bytes, r: random.Random) -> str | None:
    store = _store(tmp, raw)
    before = json.loads(raw)
    nb = store.load("nb")
    k = r.randrange(len(nb["cells"]))
    was = bool(nb["cells"][k].get("meta", {}).get("slide_start"))
    nb["cells"][k].setdefault("meta", {})["slide_start"] = not was
    after = json.loads(_save(store, nb))
    old_md, new_md = before["cells"][k].get("metadata", {}), after["cells"][k]["metadata"]
    strip = lambda md: {**md, "fabric_desktop": {kk: v for kk, v in md.get("fabric_desktop", {}).items() if kk != "slide_start"}}  # noqa: E731
    clean = lambda md: {kk: v for kk, v in strip(md).items() if not (kk == "fabric_desktop" and v == {})}  # noqa: E731
    if clean(new_md) != clean(old_md):
        return f"marking cell {k} changed its other metadata: {old_md} -> {new_md}"
    if bool(new_md.get("fabric_desktop", {}).get("slide_start")) == was:
        return f"cell {k}'s mark wasn't saved"
    for i, (old, new) in enumerate(zip(before["cells"], after["cells"], strict=True)):
        if i != k and _no_id(new) != _no_id(old):
            return f"marking cell {k} changed cell {i}"
    again = store.load("nb")
    if bool(again["cells"][k].get("meta", {}).get("slide_start")) == was:
        return "the mark didn't open again"
    again["cells"][k].setdefault("meta", {})["slide_start"] = was
    if not was:
        del again["cells"][k]["meta"]["slide_start"]
    back = json.loads(_save(store, again))
    if clean(back["cells"][k]["metadata"]) != clean(old_md) or \
            bool(back["cells"][k]["metadata"].get("fabric_desktop", {}).get("slide_start")) != was:
        return f"undoing the mark on cell {k} didn't put its metadata back: {old_md} -> {back['cells'][k]['metadata']}"
    return None


PROPERTIES: dict[str, Callable[[Path, bytes, random.Random], str | None]] = {
    "unchanged": _unchanged, "one_edit": _one_edit, "slide_mark": _slide_mark}


# ---------- the smallest failing notebook ----------

def _smaller(nb: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i in range(len(nb["cells"])):
        if len(nb["cells"]) > 1:
            out.append({**nb, "cells": nb["cells"][:i] + nb["cells"][i + 1:]})
    for k in list(nb.get("metadata", {})):
        out.append({**nb, "metadata": {kk: v for kk, v in nb["metadata"].items() if kk != k}})
    for i, c in enumerate(nb["cells"]):
        for k in list(c.get("metadata", {})):
            cell = {**c, "metadata": {kk: v for kk, v in c["metadata"].items() if kk != k}}
            out.append({**nb, "cells": nb["cells"][:i] + [cell] + nb["cells"][i + 1:]})
        for k in ("outputs", "attachments"):
            if c.get(k):
                out.append({**nb, "cells": nb["cells"][:i] + [{**c, k: [] if k == "outputs" else {}}] + nb["cells"][i + 1:]})
    return out


def _shrink(tmp: Path, nb: dict[str, Any], dump: Callable[[dict[str, Any]], bytes], prop: Callable[..., str | None],
            seed: int) -> dict[str, Any]:
    fails = lambda cand: prop(tmp, dump(cand), random.Random(seed)) is not None  # noqa: E731
    for _ in range(200):
        for cand in _smaller(nb):
            try:
                bad = fails(cand)
            except Exception:  # noqa: BLE001 - while shrinking, a crash counts as still failing
                bad = True
            if bad:
                nb = cand
                break
        else:
            return nb
    return nb


@pytest.mark.parametrize("name", sorted(PROPERTIES))
@pytest.mark.parametrize("seed", SEEDS)
def test_the_round_trip_holds_on_generated_notebooks(tmp_path, name, seed):
    prop = PROPERTIES[name]
    for case in range(CASES):
        r = random.Random(f"{name}-{seed}-{case}")
        nb = _notebook(r)
        style = f"{name}-{seed}-{case}-style"
        dump = lambda d, style=style: _dumps(random.Random(style), d)  # noqa: E731  # the same writing style each time
        raw = dump(nb)
        check = random.Random(f"{name}-{seed}-{case}-check")
        try:
            problem = prop(tmp_path, raw, check)
        except Exception as e:  # noqa: BLE001 - any crash is a finding, reported with its smallest notebook
            problem = f"{type(e).__name__}: {e}"
        if problem:
            small = _shrink(tmp_path, nb, dump, prop, hash((name, seed, case)) & 0xFFFF)
            pytest.fail(f"{name}, seed {seed}, case {case}: {problem}\nsmallest notebook that fails:\n"
                        f"{dump(small).decode('utf-8')}")

