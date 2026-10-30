"""LX-01: every notebook, SQL and semantic model best-practice rule links to its own section of the docs,
and the section exists, so About NB003 (and every finding's url) never lands on a missing anchor."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fabric_desktop import model_practices, notebook_practices
from fabric_desktop.sqlcompat import practices as sql_practices

DOCS = Path(__file__).resolve().parents[2] / "docs"
CHECKERS = [notebook_practices, sql_practices, model_practices]


def _anchors(page: str) -> set[str]:
    return {m.group(1).lower() for m in re.finditer(r"^### ([A-Z]{2}\d{3})\s*$", (DOCS / page).read_text(encoding="utf-8"), re.M)}


@pytest.mark.parametrize("checker", CHECKERS, ids=lambda c: c.__name__.rsplit(".", 1)[-1])
def test_every_rule_has_a_docs_section(checker):
    anchors = _anchors(checker.DOCS)
    for rule in checker.rules():
        page, _, anchor = rule["url"].partition("#")
        assert page == checker.DOCS
        assert anchor == rule["id"].lower()
        assert anchor in anchors, f"{checker.DOCS} has no '### {rule['id']}' section"


@pytest.mark.parametrize("checker", CHECKERS, ids=lambda c: c.__name__.rsplit(".", 1)[-1])
def test_no_docs_section_for_a_rule_that_doesnt_exist(checker):
    assert _anchors(checker.DOCS) == {r["id"].lower() for r in checker.rules()}


def test_findings_carry_the_rule_url():
    nb = notebook_practices.check(["try:\n    x = 1\nexcept Exception:\n    pass\n"])
    assert nb["findings"] and all(f["url"] == f"notebook-best-practices.md#{f['rule_id'].lower()}" for f in nb["findings"])
    sql = sql_practices.check("SELECT * FROM a CROSS JOIN b")
    assert sql["findings"] and all(f["url"] == f"sql-best-practices.md#{f['rule_id'].lower()}" for f in sql["findings"])
