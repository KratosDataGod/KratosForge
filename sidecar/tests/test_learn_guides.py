"""LX-09: Explain, Show example and Dismiss beside a best-practice finding. The guides are the docs'
rule sections, shipped with the app; a dismissal hides a finding only in this person's panels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import audit, notebook_practices, server
from fabric_desktop.learn import dismissals, guides
from fabric_desktop.learn_service import METHODS, LearnMixin
from fabric_desktop.sqlcompat import practices

DOCS = Path(__file__).resolve().parents[2] / "docs"
LINE = "a" * 64


class _Svc(LearnMixin):
    def __init__(self, root, named=None):
        self.root = root
        self.named = named or {}

    def governance_practice_settings(self, kind):
        return {"settings": self.named, "policies": [], "note": ""}


# ---- the guides


def test_the_shipped_guides_are_the_docs():
    assert guides.load() == guides.build(DOCS), "run: python -m fabric_desktop.learn.guides ../docs"


def test_every_rule_has_a_guide_with_a_why():
    ids = {r.id for r in notebook_practices.RULES} | {r.id for r in practices.RULES}
    got = guides.load()
    assert ids <= set(got)
    for rule_id in ids:
        assert got[rule_id]["why"] and not got[rule_id]["why"][0].islower(), rule_id
        assert bool(got[rule_id]["before"]) == bool(got[rule_id]["after"]), rule_id


def test_a_section_reads_into_fields():
    md = """### SQ999

**Thing** · Warning · Performance · Advanced

Why: one line
and the next.

```sql
-- Before
SELECT 1
-- After
SELECT 2
```

When it's fine: rarely.
Principle: say it once.
Impact: Performance. Slower.
How to check: run it twice.
Source: [x](https://example.com).

## Next part

Why: not this.
"""
    assert guides.parse(md) == {"SQ999": {"importance": "Advanced", "area": "Performance", "why": "One line and the next.",
                                          "fine": "Rarely.", "instead": "", "principle": "Say it once.",
                                          "impact": "Performance. Slower.", "check": "Run it twice.",
                                          "language": "sql", "before": "SELECT 1", "after": "SELECT 2"}}


def test_every_rule_has_an_importance_principle_impact_and_check():
    got = guides.load()
    for rule in [*notebook_practices.RULES, *practices.RULES]:
        g = got[rule.id]
        assert g["importance"] in guides.IMPORTANCE and g["area"] == rule.category, rule.id
        assert g["principle"] and g["impact"] and g["check"], rule.id
    assert got["NB001"]["importance"] == "Critical" and got["SQ010"]["importance"] == "Critical"


def test_importance_only_explains_and_never_changes_a_rule():
    """Importance is a label in the guide; the rules' severities stay what the checkers say."""
    sev = {r.id: r.severity for r in notebook_practices.RULES} | {r.id: r.severity for r in practices.RULES}
    assert sev["NB001"] == "Error" and sev["SQ010"] == "Warning" and sev["NB020"] == "Warning"


# ---- dismissals


def test_dismiss_each_scope_once_and_show_again(tmp_path):
    rows = dismissals.add(tmp_path, "SQ002", "line", "WH_Sales/weekly.sql", LINE)
    rows = dismissals.add(tmp_path, "SQ002", "line", "WH_Sales/weekly.sql", LINE)
    assert len(rows) == 1 and rows[0]["line"] == LINE
    rows = dismissals.add(tmp_path, "SQ005", "never", "ignored", "ignored")
    assert rows[-1]["where"] == "" and rows[-1]["line"] == ""
    rows = dismissals.add(tmp_path, "NB003", "file", "load_orders", LINE)
    assert rows[-1]["line"] == "" and len(rows) == 3
    assert len(dismissals.remove(tmp_path, rows[0]["id"])) == 2
    assert dismissals.remove(tmp_path) == [] and dismissals.load(tmp_path) == []


@pytest.mark.parametrize("args, says", [
    (("XX001", "never"), "isn't a notebook or SQL"),
    (("SQ002", "forever"), "scope must be"),
    (("SQ002", "file", ""), "where must name"),
    (("SQ002", "file", "x" * 513), "where must name"),
    (("SQ002", "line", "tab", "SELECT secret"), "SHA-256"),
])
def test_dismiss_refuses_what_it_cant_keep(tmp_path, args, says):
    with pytest.raises(ValueError, match=says):
        dismissals.add(tmp_path, *args)
    assert not dismissals.path(tmp_path).exists()


def test_a_broken_file_reads_as_none_and_the_oldest_go_past_the_limit(tmp_path, monkeypatch):
    dismissals.path(tmp_path).parent.mkdir(parents=True)
    dismissals.path(tmp_path).write_text("{not json", encoding="utf-8")
    assert dismissals.load(tmp_path) == []
    monkeypatch.setattr(dismissals, "MAX", 2)
    for n in range(3):
        dismissals.add(tmp_path, "SQ002", "file", f"tab {n}")
    assert [r["where"] for r in dismissals.load(tmp_path)] == ["tab 1", "tab 2"]
    assert json.loads(dismissals.path(tmp_path).read_text(encoding="utf-8"))[0]["where"] == "tab 1"


# ---- the service


def test_methods_are_served_and_only_the_reads_skip_the_audit():
    assert {"learn_rule_guides", "learn_dismissals", "learn_dismiss", "learn_undismiss"} <= METHODS <= server.METHODS
    assert not audit.audited("learn_rule_guides") and not audit.audited("learn_dismissals")
    assert audit.audited("learn_dismiss") and audit.audited("learn_undismiss")


def test_a_rule_a_policy_names_cant_be_dismissed(tmp_path):
    svc = _Svc(tmp_path, named={"NB001": {"enabled": True, "severity": "Error"}, "SQ005": {"enabled": False, "severity": "Info"}})
    with pytest.raises(ValueError, match="policy checks NB001"):
        svc.learn_dismiss("NB001", "never")
    assert len(svc.learn_dismiss("SQ005", "never")) == 1   # a policy that turns it off doesn't require it
    assert svc.learn_rule_guides()["SQ002"]["after"].startswith("WHERE order_date")
    assert svc.learn_undismiss() == [] and svc.learn_dismissals() == []
