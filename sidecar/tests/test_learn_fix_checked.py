"""GD-06: a best-practice fix applied after Compare found the same rows is recorded in Profile, with
what was checked and measured, never the SQL; nothing else is."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_desktop import app_settings, audit, progress, server
from fabric_desktop.learn_service import METHODS, LearnMixin
from fabric_desktop.progress_service import ProgressMixin

T = pa.table({"region": ["n", "s", "e", "w"], "revenue": [1.5, 2.0, 3.0, 4.0]})


BEFORE = "SELECT region, SUM(amount) AS revenue FROM dbo.sales WHERE YEAR(order_date) = 2026 GROUP BY region"
AFTER = BEFORE.replace("YEAR(order_date) = 2026", "(order_date >= '2026-01-01' AND order_date < '2027-01-01')")
QUERIES = {"a": BEFORE, "b": AFTER, "c": "SELECT region, SUM(amount) AS revenue FROM dbo.sales GROUP BY region",
           "d": "SELECT region, revenue FROM sales", "e": "SELECT region, revenue FROM dbo.sales"}


class _Runs:
    def __init__(self, runs):
        self.runs = runs

    def table(self, run_id):
        return self.runs[run_id]

    def query_of(self, run_id):
        return QUERIES[run_id]


class _Svc(LearnMixin, ProgressMixin):
    def __init__(self, root, runs):
        self.root = root
        self.sql_runs = _Runs(runs)

    def _profile_user(self):
        return "dev@contoso.com"


def _runs(after=T, **extra):
    st = {"seconds": 1.84, "target": "warehouse", "warehouse": "WH_Gold"}
    return {"a": (st, T), "b": ({**st, "seconds": 0.21, **extra}, after), "c": (st, T), "d": (st, T),
            "e": ({**st, "seconds": 0.21}, T)}


@pytest.fixture(autouse=True)
def _preview(monkeypatch):
    real = app_settings.value
    monkeypatch.setattr(app_settings, "value", lambda key: True if key == "show_preview" else real(key))


def test_a_matching_compare_records_the_fix_with_what_was_checked_and_measured(tmp_path):
    svc = _Svc(tmp_path, _runs())
    assert svc.learn_fix_checked("a", "b", "SQ002") == {"recorded": True}
    (row,) = progress.load(svc._profile_path())["evidence"]
    assert row["type"] == "fix.checked" and row["verdict"] == "same" and row["rules_version"] == progress.RULES_VERSION == 3
    assert row["artifact"] == {"kind": "rule", "name": "SQ002", "where": "WH_Gold"}
    assert row["ref"] == {"source": "compare", "id": "b", "revision": "a"}
    assert row["detail"] == {"rows": 4, "level": "complete", "target": "warehouse"}
    assert row["measurement"]["before_seconds"] == 1.84 and row["measurement"]["after_seconds"] == 0.21
    assert row["measurement"]["kind"] == "measured" and "one run each" in row["measurement"]["source"]
    assert row["dimensions"] == ["Engineering Quality", "Performance & Optimization"]   # SQ002 is a performance rule
    assert "SELECT" not in str(row)
    assert svc.learn_fix_checked("a", "b", "SQ002") == {"recorded": True}   # the same check again adds nothing
    assert len(progress.load(svc._profile_path())["evidence"]) == 1


def test_different_rows_a_write_or_an_unknown_rule_record_nothing(tmp_path):
    assert _Svc(tmp_path, _runs(after=T.slice(0, 3))).learn_fix_checked("a", "b", "SQ002") == {"recorded": False}
    with pytest.raises(ValueError, match="only read"):
        _Svc(tmp_path, _runs(change=True)).learn_fix_checked("a", "b", "SQ002")
    with pytest.raises(ValueError, match="no fix"):
        _Svc(tmp_path, _runs()).learn_fix_checked("a", "b", "NB001")
    assert progress.load(progress.path_for(tmp_path, "dev@contoso.com"))["evidence"] == []


def test_only_the_runs_of_the_finding_and_its_fix_count(tmp_path):
    """The same run twice, the same SQL twice, an unrelated query, or the fix and the original swapped:
    rows that match prove nothing about the fix, so nothing is recorded."""
    svc = _Svc(tmp_path, _runs())
    assert svc.learn_fix_checked("a", "a", "SQ002") == {"recorded": False}      # one run against itself
    assert svc.learn_fix_checked("c", "c", "SQ002") == {"recorded": False}
    assert svc.learn_fix_checked("c", "b", "SQ002") == {"recorded": False}      # the first never had SQ002
    assert svc.learn_fix_checked("b", "a", "SQ002") == {"recorded": False}      # the second still has it
    assert svc.learn_fix_checked("a", "b", "SQ005") == {"recorded": False}      # no SQ005 in either
    assert progress.load(svc._profile_path())["evidence"] == []


def test_recording_off_records_nothing(tmp_path):
    svc = _Svc(tmp_path, _runs())
    profile = progress.load(svc._profile_path())
    profile["settings"]["record"] = False
    progress.save(svc._profile_path(), profile)
    assert svc.learn_fix_checked("a", "b", "SQ002") == {"recorded": False}


def test_a_checked_fix_earns_no_xp_and_the_report_says_what_was_checked(tmp_path):
    svc = _Svc(tmp_path, _runs())
    assert svc.learn_fix_checked("d", "e", "SQ005") == {"recorded": True}
    profile = progress.load(svc._profile_path())
    p = progress.progression(profile)
    assert p["xp"] == 0 and p["achievements"] == []
    (row,) = profile["evidence"]
    assert row["dimensions"] == ["Engineering Quality"]   # SQ005 is a maintenance rule: no skill for its area
    md = progress.report(profile)["markdown"]
    assert "1 best-practice fix (SQ005) applied after running the query as written and as fixed and finding the same rows. *(Verified)*" in md
    assert "SQ005 fix on WH\\_Gold: 1.84 s as written, 0.21 s fixed, one run each, measured. *(Verified, E1)*" in md
    assert "E1 · " in md and "Compare for SQ005 · runs d and e · same" in md
    assert "master" not in md.lower()


def test_served_and_listed():
    assert "learn_fix_checked" in METHODS <= server.METHODS and not audit.audited("learn_fix_checked")
