"""Mutation batch 242: where an import binds a name, and how often a name is written."""

from __future__ import annotations

from fabric_desktop import nbsymbols


def _cell(source: str) -> list[dict]:
    return [{"type": "code", "source": source}]


def test_a_second_import_as_the_same_name_is_found_at_its_own_as():
    found = nbsymbols.find_symbol(_cell("import os as p, collections as p\np\n"), 0, 0, "p")
    defs = [(p["from"], p["to"]) for p in found["places"] if p["kind"] == "definition"]
    assert defs == [(13, 14), (31, 32)]


def test_an_import_alias_counts_once_as_a_name_written_and_others_dont():
    assert nbsymbols.find_symbol(_cell("import pandas as pd\npd.x\n"), 0, 0, "pd")["anywhere"] == 2
    assert nbsymbols.find_symbol(_cell("import os, sys\nq = 1\n"), 0, 0, "q")["anywhere"] == 1
