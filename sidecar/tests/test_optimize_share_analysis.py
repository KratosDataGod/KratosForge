"""Another feature's analysis shared through Optimize's writer, with a confirm bound to the rows shown (OE-004)."""

import pytest

from fabric_desktop.optimize.shared import Shared
from test_optimize_service import svc  # noqa: F401 - the fixture is used by name

SAS = "https://acct.blob.core.windows.net/c/f?sv=2024&sig=abcdefSECRET123"


def records(detail="Two notebooks do the same thing.", run_id="oe-1"):
    where = {"workspace_id": "w1", "item_id": "nb1"}
    return {"run": {"run_id": run_id, "finished_at": "2026-11-13T10:00:00Z", "status": "complete",
                    "scope": "{}", "rule_set_version": "opportunities-1"},
            "artifacts": [where | {"item_type": "Notebook", "display_name": "Load sales"}],
            "findings": [where | {"rule_id": "oe.dup-nb", "target": "opp-1", "severity": "high",
                                  "title": "Duplicate notebooks", "detail": detail, "evidence": f'{{"url": "{SAS}"}}'}],
            "recommendations": [where | {"rule_id": "oe.dup-nb", "target": "opp-1", "kind": "consolidate",
                                         "reason": f"Keep one; see {SAS}", "impact": '{"runs": 2}',
                                         "effort": "Medium", "confidence": "high"}]}


def ask(svc, rows):  # noqa: F811
    with pytest.raises(PermissionError) as asked:
        svc._optimize_share_analysis(rows, "this assessment")
    return asked.value


def test_it_asks_first_then_writes_exactly_what_was_shown_without_secrets(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    shown = ask(svc, records())
    assert "Adds 1 finding and 1 recommendation from this assessment" in str(shown)
    assert "1 item in 1 workspace" in str(shown) and "Confirm" in str(shown)
    assert svc.optimize_findings() == []  # asking wrote nothing
    # Without the confirm, or with one for other rows, it asks again and writes nothing.
    with pytest.raises(PermissionError, match="^Adds"):
        svc._optimize_share_analysis(records(), "this assessment", approve=True)
    with pytest.raises(PermissionError, match="changed since you looked"):
        svc._optimize_share_analysis(records(detail="Changed."), "this assessment", approve=True,
                                     confirm=shown.would["confirm"])
    assert svc.optimize_findings() == []
    out = svc._optimize_share_analysis(records(), "this assessment", approve=True, confirm=shown.would["confirm"])
    assert out["shared"] == 1
    shared = svc._optimize_shared()
    for table, column in (("findings", "evidence"), ("recommendations", "reason")):
        assert not any("abcdefSECRET123" in (v or "") for v in shared.rows(table)[column].to_list()), table
    [rec] = svc.optimize_recommendations()
    assert rec["kind"] == "consolidate" and rec["confidence"] == "high"
    # The same run shared again adds nothing.
    again = ask(svc, records())
    svc._optimize_share_analysis(records(), "this assessment", approve=True, confirm=again.would["confirm"])
    assert len(shared.rows("findings")) == 1


def test_nothing_found_asks_nothing_and_a_run_needs_its_id(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    assert svc._optimize_share_analysis(records() | {"findings": [], "recommendations": []}, "x")["shared"] == 0
    with pytest.raises(ValueError, match="run id"):
        svc._optimize_share_analysis(records(run_id=""), "x")


def test_a_lakehouse_out_of_reach_keeps_it_on_this_pc(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    shown = ask(svc, records())
    monkeypatch.setattr(Shared, "record_analysis", lambda self, *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    out = svc._optimize_share_analysis(records(), "this assessment", approve=True, confirm=shown.would["confirm"])
    assert out["queued"] == 1 and "down" in out["error"]
    assert svc.optimize_status()["outbox"]["pending"] == 1
