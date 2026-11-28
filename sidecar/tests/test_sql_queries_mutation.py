"""Mutation-testing gaps in sql_queries_service.py: header reading at its edges, the length limits at
exactly their size, the fingerprint and the size messages. Every file is in a temporary folder;
nothing runs SQL or calls Fabric."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fabric_desktop import sql_queries_service as sq
from fabric_desktop.service import Service
from fabric_desktop.sql_templates_service import MAX_BYTES


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return Service(client_id="x", root=tmp_path / "app")


def test_a_file_of_only_header_lines_has_no_sql():
    assert sq.parse("-- query: A", "x") == {"title": "A", "warehouse": "", "description": "", "sql": ""}
    assert sq.parse("-- query: A\n-- warehouse: W", "x")["sql"] == ""


def test_a_line_of_spaces_after_the_header_is_the_header_gap_not_sql():
    assert sq.parse("-- query: A\n   \n  SELECT 1", "x")["sql"] == "  SELECT 1"


def test_only_one_gap_line_is_dropped():
    assert sq.parse("-- query: A\n   \n   \nSELECT 1", "x")["sql"] == "   \nSELECT 1"


def test_a_line_of_spaces_with_no_header_stays_sql():
    assert sq.parse("   \nSELECT 1", "stem") == {"title": "stem", "warehouse": "", "description": "",
                                                    "sql": "   \nSELECT 1"}


def test_a_good_query_has_an_empty_problem():
    assert sq.problem({"title": "A", "sql": "SELECT 1"}) == ""


def test_the_name_and_lines_may_be_exactly_their_limit():
    ok = {"title": "t" * sq.MAX_TITLE, "warehouse": "w" * sq.MAX_LINE, "description": "d" * sq.MAX_LINE,
          "sql": "SELECT 1"}
    assert sq.problem(ok) == ""
    assert sq.problem(ok | {"description": "d" * (sq.MAX_LINE + 1)}) == "Use a description of up to 200 characters on one line."
    assert sq.problem(ok | {"title": "t" * (sq.MAX_TITLE + 1)}) == "Use a name of up to 100 characters on one line."


def test_the_fingerprint_is_the_first_16_hex_digits_of_sha256():
    data = b"-- query: A\n\nSELECT 1\n"
    assert sq.version(data) == hashlib.sha256(data).hexdigest()[:16]
    assert len(sq.version(b"")) == 16


def _sql_for(total: int) -> str:
    head = len(sq.render({"title": "Edge", "sql": ""}).encode("utf-8")) - 1  # render adds "\n" after the SQL
    return "S" * (total - head - 1)


def test_a_query_of_exactly_the_size_limit_is_saved_and_one_byte_more_is_refused(svc):
    sql = _sql_for(MAX_BYTES)
    assert len(sq.render({"title": "Edge", "sql": sql}).encode("utf-8")) == MAX_BYTES
    saved = svc.sql_query_save("mine", "Edge", sql)
    assert saved["name"].endswith(".sql")
    with pytest.raises(ValueError) as raised:
        svc.sql_query_save("mine", "Edge", sql + "S")
    assert str(raised.value) == "A saved query can be up to 64 KB."


def test_a_listed_file_of_exactly_the_size_limit_is_read_and_a_larger_one_says_why(svc, tmp_path):
    svc.sql_query_save("mine", "Seed", "SELECT 1")
    folder = next(p for p in (tmp_path / "app").rglob("sql-queries") if p.is_dir())
    (folder / "edge.sql").write_bytes(b"S" * MAX_BYTES)
    (folder / "big.sql").write_bytes(b"S" * (MAX_BYTES + 1))
    items = {q["name"]: q for q in svc.sql_queries_list()["queries"]}
    assert items["edge.sql"]["problem"] == "" and items["edge.sql"]["sql"] == "S" * MAX_BYTES
    assert items["big.sql"]["problem"] == "It is larger than 64 KB."
    assert items["big.sql"]["sql"] == "" and items["big.sql"]["title"] == "big"
