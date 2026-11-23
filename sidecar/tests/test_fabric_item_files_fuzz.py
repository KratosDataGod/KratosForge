"""Seeded, hostile inputs for the readers of Fabric Git-format item files: notebook-content.py, .sql and .scala
(notebook_formats.parse_source and to_fabric_py), a notebook definition read for the workspace inventory
(opportunities/inventory.notebook_content) and a warehouse project's ``.platform`` (wh_projects_list).

The property: every file is read, or refused with a ValueError that says why. Never another exception, never a
hang, and what KratosForge writes as Fabric source reads back as the same cells. Each loop uses fixed seeds, so a
failure names its seed and reproduces exactly."""

import base64
import datetime as dt
import json
import os
import random
import shutil
import sys
from pathlib import Path

import pytest

from fabric_desktop import notebook_formats as nbf
from fabric_desktop.opportunities import inventory as inv
from fabric_desktop.service import Service
from settings_fuzz import mutations

# Lines a Fabric source file is built from: every marker (and near misses), META lines whole and broken, MAGIC
# lines, other formats' headers, and unicode that str.splitlines (but not the reader) treats as a line break.
LINES = ["# CELL ********************", "# PARAMETERS CELL ********************", "# MARKDOWN ********************",
         "# METADATA ********************", "# METADATA ********************  ", "# CELL ********************\t",
         "# META {", "# META }", '# META   "language": "sparksql",', '# META   "language": "scala"',
         '# META "language_group": "synapse_pyspark"', '# META {"fabric_desktop": {"breakpoints": [1, 2]}}',
         '# META {"language": "python"}', "# META", "# META x", "# META " + "[" * 5000, "# MAGIC %%sql",
         "# MAGIC SELECT 1", "# MAGIC", "# MAGIC  x", "# MAGIC %%spark", "# MAGIC # CELL ********************",
         "x = 1", "", "  ", "# a", "#", "# # CELL ********************", "%%sql", "SELECT 2", "print(1)", "\ufeff",
         "# Fabric notebook source", "# METADATA", "CELL ********************", "METADATA ********************",
         "# COMMAND ----------", "# %%", "\ud800", "\x00", "\u2028", "\x85", "\x0c", "é", "x" * 2000]
# Pieces of cell text for the write-then-read loop.
PIECES = ["CELL ********************", "METADATA ********************", "PARAMETERS CELL ********************",
          "MARKDOWN ********************", "# CELL ********************", "# METADATA ********************",
          "# META {", "# MAGIC x", "%%sql", "%%spark", "%%sparkr", "%%md", "%%html", "%run x", "x = 1", "  ", "\t",
          "", "#", "# ", "é", "\ufeff", "\x00", "\u2028", "\x85", "\r", "\r\n", "# %%", "# DBTITLE 1,x", "# MAGIC",
          "# MAGIC  ", "-- MAGIC", "// MAGIC", "*" * 20]
COMMENTS = ["#", "--", "//"]


def _key(cells):
    """Cells as compared after a round trip: line breaks as \\n and blank edge lines don't count."""
    return [(c["type"], nbf._trim(nbf._lines(c["source"])), nbf._is_params(c), (c.get("meta") or {}).get("breakpoints"))
            for c in cells]


def _read(text, name="notebook-content.py"):
    try:
        return nbf.parse_source(text, name)["cells"]
    except ValueError as exc:
        assert "32 MB" in str(exc)
        return None


def _write_then_read(cells, seed):
    """What to_fabric_py writes reads back as the same cells, or it refuses, naming the markdown cell."""
    try:
        text = nbf.to_fabric_py(cells)
    except ValueError as exc:
        assert "would read back as a cell marker" in str(exc), seed
        n = int(str(exc).split("in cell ", 1)[1].split(",", 1)[0])
        assert cells[n - 1]["type"] == "markdown", seed
        return None
    back = nbf.parse_source(text, "notebook-content.py")
    assert back["format"] == "Fabric notebook source", seed
    return back["cells"]


@pytest.mark.parametrize("seed", range(12))
def test_a_hostile_fabric_source_file_reads_and_writes_back_as_the_same_cells(seed):
    r = random.Random(seed)
    for _ in range(300):
        comment = r.choice(COMMENTS)
        lines = [f"{comment} Fabric notebook source"]
        lines += [ln.replace("#", comment, 1) if ln.startswith("#") and r.random() < .9 else ln
                  for ln in (r.choice(LINES) for _ in range(r.randint(0, 40)))]
        text = r.choice(["\n", "\r\n", "\r"]).join(lines) if r.random() < .3 else "\n".join(lines)
        if r.random() < .1:
            text = "\ufeff" + text
        cells = _read(text, "notebook-content" + {"#": ".py", "--": ".sql", "//": ".scala"}[comment])
        assert cells is not None, (seed, text[:200])
        back = _write_then_read(cells, seed)
        if back is not None:
            assert _key(back) == _key(cells), (seed, text[:400])


@pytest.mark.parametrize("seed", range(12))
def test_cells_written_as_fabric_source_read_back_whole(seed):
    r = random.Random(1000 + seed)
    for _ in range(300):
        cells = []
        for _ in range(r.randint(0, 6)):
            source = "\n".join("".join(r.choice(PIECES) for _ in range(r.randint(0, 2))) for _ in range(r.randint(0, 6)))
            cell = {"type": r.choice(["code", "code", "markdown"]), "source": source}
            if cell["type"] == "code" and r.random() < .2:
                cell["meta"] = {"tags": ["parameters"]}
            if cell["type"] == "code" and r.random() < .2:
                cell.setdefault("meta", {})["breakpoints"] = sorted(r.sample(range(1, 9), r.randint(1, 3)))
            cells.append(cell)
        back = _write_then_read(cells, seed)
        if back is None:
            continue
        want = [(c["type"], nbf._trim(nbf._lines(c["source"] if c["type"] == "markdown" else nbf._magic_first(c["source"]))),
                 nbf._is_params(c)) for c in cells]
        assert [k[:3] for k in _key(back)] == want, (seed, cells)
        assert _key(nbf.parse_source(nbf.to_fabric_py(back), "x.py")["cells"]) == _key(back), seed


def test_a_markdown_line_that_would_be_a_marker_is_refused_not_lost():
    for marker in ("CELL", "METADATA", "MARKDOWN", "PARAMETERS CELL"):
        cells = [{"type": "code", "source": "x = 1"},
                 {"type": "markdown", "source": f"Intro\n{marker} ********************  \nThe rest"}]
        with pytest.raises(ValueError, match=r"^The notebook wasn't exported as Fabric source, because a line in cell 2"):
            nbf.to_fabric_py(cells)


def test_a_meta_block_nested_too_deep_is_ignored_like_any_broken_one():
    for deep in ("[" * 100_000, "[" * 50_000 + "]" * 50_000, '{"a":' * 50_000 + "1" + "}" * 50_000):
        text = f"# Fabric notebook source\n# CELL ********************\nx = 1\n# METADATA ********************\n# META {deep}\n"
        assert nbf.parse_source(text, "notebook-content.py")["cells"] == [{"type": "code", "source": "x = 1"}]


def test_a_fabric_sql_notebook_reads_as_its_cells_not_one_cell_of_markers():
    text = "\r\n".join([
        "\ufeff-- Fabric notebook source", "", "-- METADATA ********************", "", '-- META {"kernel_info": {}}', "",
        "-- MARKDOWN ********************", "", "-- # Daily load", "-- Notes", "",
        "-- CELL ********************", "", "SELECT * FROM sales", "", "-- METADATA ********************", "",
        "-- META {", '-- META   "language": "sparksql"', "-- META }", "",
        "-- PARAMETERS CELL ********************", "", "-- MAGIC %%pyspark", '-- MAGIC run_date = ""', "",
        "-- METADATA ********************", "", '-- META {"language": "python"}', "",
        "-- CELL ********************", "", "SELECT 2", ""])
    cells = nbf.parse_source(text, "notebook-content.sql")["cells"]
    assert cells == [{"type": "markdown", "source": "# Daily load\nNotes"},
                     {"type": "code", "source": "%%sql\nSELECT * FROM sales"},
                     {"type": "code", "source": '%%pyspark\nrun_date = ""', "meta": {"tags": ["parameters"]}},
                     {"type": "code", "source": "%%sql\nSELECT 2"}]
    assert nbf.parse_source(nbf.to_fabric_py(cells), "x.py")["cells"] == cells
    scala = "// Fabric notebook source\n// CELL ********************\nval x = 1\n// MARKDOWN ********************\n// Hi\n"
    assert nbf.parse_source(scala, "notebook-content.scala")["cells"] == [
        {"type": "code", "source": "%%spark\nval x = 1"}, {"type": "markdown", "source": "Hi"}]


def test_big_fabric_files_read_in_time():
    many = "# Fabric notebook source\n" + "# CELL ********************\nx = 1\n" * 100_000
    assert len(nbf.parse_source(many, "notebook-content.py")["cells"]) == 100_000
    huge = "# Fabric notebook source\n# CELL ********************\n" + "# MAGIC y = 2\n" * 200_000
    assert nbf.parse_source(huge, "notebook-content.py")["cells"][0]["source"].count("\n") == 199_999
    with pytest.raises(ValueError, match="larger than 32 MB"):
        nbf.parse_source("# Fabric notebook source\n" + "x" * (nbf.MAX_TEXT + 1))


# ---- a notebook definition read for the workspace inventory

NOW = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _definition(path, data):
    return {"definition": {"parts": [{"path": path, "payload": base64.b64encode(data).decode()}]}}


def _workload(definition):
    w = inv.workloads({"id": "w"}, [{"id": "i", "type": "Notebook", "displayName": "nb"}], {"i": definition}, {},
                      now=NOW, days=7)[0]
    assert w["definition"] in ("read", "not_read")
    assert (w["problem"] is None) == (w["definition"] == "read")
    return w


def test_a_notebook_definition_of_the_wrong_shape_is_not_read_and_the_scan_goes_on():
    for doc in (b"[1, 2]", b"[" * 100_000, b'{"cells": ["x"]}', b'{"cells": {"a": 1}}', b'"cells"', b"null"):
        assert _workload(_definition("notebook-content.ipynb", doc))["definition"] == "not_read", doc[:20]
    for junk in ([1], "x", None, {"definition": [1]}, {"definition": {"parts": "x"}}, {"parts": [None, 5]}):
        assert _workload(junk)["definition"] == "not_read", junk
    bad = {"definition": {"parts": [{"path": "notebook-content.py", "payload": "@@not base64@@"}]}}
    assert _workload(bad)["definition"] == "not_read"
    sql = _definition("notebook-content.sql", b"-- Fabric notebook source\n-- CELL ********************\nSELECT 1\n")
    assert "notebook-content.sql" in _workload(sql)["problem"], "a format it can't read isn't read as no cells"
    odd = b'{"metadata": {"dependencies": [1]}, "cells": [{"cell_type": "code", "source": "x"}]}'
    assert _workload(_definition("notebook-content.ipynb", odd))["cells"] == ["x"]


def test_a_fabric_source_definition_keeps_only_the_code():
    text = nbf.to_fabric_py([{"type": "code", "source": "x = 1"}, {"type": "markdown", "source": "Hi"},
                             {"type": "code", "source": "y = 2"}])
    w = _workload(_definition("notebook-content.py", text.encode()))
    assert w["cells"] == ["x = 1", "y = 2"], "no METADATA marker line is left in a cell"


@pytest.mark.parametrize("seed", range(4))
def test_mutated_notebook_definitions_never_stop_the_scan(seed):
    valid = {"nbformat": 4, "metadata": {"dependencies": {"lakehouse": {"default_lakehouse": "abc"}}},
             "cells": [{"cell_type": "code", "source": ["x=1\n", "y"]}, {"cell_type": "markdown", "source": "t"}]}
    for _label, data in mutations(valid, seed, 120):
        for path in ("notebook-content.ipynb", "notebook-content.py"):
            _workload(_definition(path, data))


# ---- a warehouse project's .platform

@pytest.fixture
def wh(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    svc = Service(client_id="x", root=tmp_path / "app")
    repo = tmp_path / "repo"
    repo.mkdir()
    svc.git_init(str(repo))
    (repo / "Gold.Warehouse").mkdir()
    return svc, repo / "Gold.Warehouse" / ".platform"


def _shown(svc):
    return [p["name"] for p in svc.wh_projects_list()["projects"]]


def test_a_platform_file_is_read_or_the_folder_name_is_used(wh):
    svc, platform = wh
    good = json.dumps({"metadata": {"type": "Warehouse", "displayName": "Sales gold"}}).encode()
    platform.write_bytes(b"\xef\xbb\xbf" + good)
    assert _shown(svc) == ["Sales gold"], "a byte order mark is read past"
    for data in (b"[" * 100_000, b'{"metadata": {"displayName": {"a": 1}}}', b'{"metadata": {"displayName": "a\\nb"}}',
                 b'{"metadata": [1]}', b"\xff" + good, good[:-3], b"", b" " * (2 * 1024 * 1024)):
        platform.write_bytes(data)
        assert _shown(svc) == ["Gold"], data[:30]
    for data in mutations({"metadata": {"type": "Warehouse", "displayName": "Sales gold"}}, 7, 80):
        platform.write_bytes(data[1])
        assert len(_shown(svc)) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="devices and pipes are POSIX")
def test_a_platform_that_is_a_device_or_a_pipe_is_not_read(wh):
    svc, platform = wh
    platform.symlink_to("/dev/zero")
    assert _shown(svc) == ["Gold"], "a link to an endless file would never finish reading"
    platform.unlink()
    os.mkfifo(platform)
    assert _shown(svc) == ["Gold"], "a pipe nobody writes to would never finish reading"
