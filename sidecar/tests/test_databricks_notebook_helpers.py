"""The generated list of Databricks notebook helpers (dbutils and % magics) on this PC: it covers every
call the kernel's dbutils has, its statuses match what a notebook gets, and the docs page is current."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import databricks_notebook_helpers as helpers
from fabric_desktop.notebook import Kernel

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows():
    return helpers.rows()


def test_the_docs_page_is_up_to_date(rows):
    page = (ROOT / "docs" / "databricks-notebook-helpers.md").read_text(encoding="utf-8")
    assert page == helpers.markdown(rows), (
        "docs/databricks-notebook-helpers.md is stale: run `python -m fabric_desktop.databricks_notebook_helpers` "
        "in sidecar/ and commit it")


def test_every_dbutils_call_on_this_pc_is_listed():
    listed = set(helpers.load()["dbutils"])
    assert sorted(helpers.public_calls() - listed) == [], "add the new dbutils call to databricks_notebook_helpers.json"


def test_nothing_listed_is_missing_and_every_status_appears(rows):
    assert [r["name"] for r in rows if r["status"] == "missing"] == []
    assert {r["status"] for r in rows} == {"runs", "differs", "refused"}
    assert all(r["note"] for r in rows if r["status"] in ("differs", "refused"))


def test_a_refused_dbutils_call_raises_its_note_in_a_notebook(rows):
    k = Kernel()
    for r in (r for r in rows if r["group"] == "dbutils" and r["status"] == "refused"):
        out = k.run(f"dbutils.{r['name']}('x', 'y')")
        assert out["status"] == "error", r["name"]
        assert out["outputs"][-1]["ename"] == "NotImplementedError", r["name"]
        assert out["outputs"][-1]["evalue"] == r["note"], r["name"]


@pytest.mark.parametrize("header", ["%python", "%sql", "%md", "%scala", "%r"])
def test_a_cell_magic_runs_or_is_refused_as_listed(rows, header):
    status = next(r["status"] for r in rows if r["group"] == "cells" and r["name"] == header)
    body = {"%python": "1 + 1", "%sql": "SELECT 1 AS a", "%md": "# Title"}.get(header, "1")
    out = Kernel().run(f"{header}\n{body}")
    if status == "refused":
        assert out["status"] == "error" and out["outputs"][-1]["ename"] == "RunsInFabric"
    else:
        assert out["status"] == "ok", out


@pytest.mark.parametrize("path", ["/tmp", "/mnt/raw", "/FileStore/tables", "dbfs:/tmp", "/Volumes/main/raw/x"])
def test_fs_magic_refuses_databricks_storage_like_dbutils_fs(path):
    """In Databricks, %fs ls /tmp lists DBFS. Here it stops and says so, instead of listing this PC's /tmp."""
    out = Kernel().run(f"%fs ls {path}")
    assert out["status"] == "error"
    assert "Databricks storage" in out["outputs"][-1]["evalue"] or "DBFS path" in out["outputs"][-1]["evalue"]


def test_fs_magic_still_takes_file_and_lakehouse_paths(tmp_path):
    k = Kernel()
    (tmp_path / "a.txt").write_text("hi")
    out = k.run(f"%fs ls file:{tmp_path.as_posix()}")
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["rows"][0][0] == "a.txt"
