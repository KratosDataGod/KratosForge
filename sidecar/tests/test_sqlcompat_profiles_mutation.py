"""Compatibility profile facts that mutation testing found untested (sqlcompat/profiles.py)."""

from __future__ import annotations

import importlib

profiles = importlib.import_module("fabric_desktop.sqlcompat.profiles")


def test_only_spark_sql_and_duckdb_run_on_this_pc():
    assert {p["id"]: p["runs_here"] for p in profiles.profiles()} == {
        "spark": True, "warehouse": False, "endpoint": False, "sqldb": False, "duckdb": True}


def test_each_doc_link_carries_its_last_updated_date():
    by_id = {p["id"]: p for p in profiles.profiles()}
    assert by_id["sqldb"]["docs"] == [{"url": profiles.DOCS["sqldb"][0], "updated": "2026-08-24"}]
    assert by_id["warehouse"]["docs"][0] == {"url": profiles.doc("surface"), "updated": "2026-09-29"}
    assert all(d["updated"] == "" or d["updated"][:2] == "20" for p in by_id.values() for d in p["docs"])
