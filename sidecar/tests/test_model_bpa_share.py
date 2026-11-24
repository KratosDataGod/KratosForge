"""A model's last best practices check as rows for the shared Optimize lakehouse (model_bpa_share.py)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fabric_desktop import audit, model_bpa_share, server
from fabric_desktop.optimize import setup
from fabric_desktop.optimize.installation import Installation
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.optimize.shared import Shared
from test_optimize_service import svc  # noqa: F401 - the fixture is used by name

TOKEN = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"  # pragma: allowlist secret (a made-up token for the redaction test)


def _status(request_id="bpa-1", **last):
    return {"model": "SM_Gold", "model_id": "m1", "env": "test", "workspace_id": "w1", "workspace_name": "Sales",
            "last": {"request_id": request_id, "checked_at": "2026-11-23T10:00:00+00:00", "package": "semantic-link-labs",
                     "version": "0.17.1", "no_tables": False, "counts": {},
                     "findings": [
                         {"severity": "Error", "rule": "Hide foreign keys", "category": "Formatting", "object_type": "Column",
                          "object": "'FactSales'[customer_sk]", "description": "Foreign keys should be hidden.", "url": ""},
                         {"severity": "Warning", "rule": "Mark primary keys", "category": "Formatting", "object_type": "Column",
                          "object": "'DimCustomer'[customer_sk]", "description": f"Set IsKey. {TOKEN}", "url": ""},
                         {"severity": "Info", "rule": "Add descriptions", "category": "Maintenance", "object_type": "Table",
                          "object": "'FactSales'", "description": "Describe it.", "url": ""}],
                     **last}}


def test_errors_and_warnings_become_findings_keyed_by_rule_model_and_object():
    rows = model_bpa_share.analysis_rows(_status())
    assert [(f["rule_id"], f["target"], f["severity"]) for f in rows["findings"]] == [
        ("bpa.hide-foreign-keys", "'FactSales'[customer_sk]", "high"),
        ("bpa.mark-primary-keys", "'DimCustomer'[customer_sk]", "medium")], "Info stays on this PC"
    assert rows["artifacts"] == [{"workspace_id": "w1", "item_id": "m1", "item_type": "SemanticModel", "display_name": "SM_Gold"}]
    rec = rows["recommendations"][0]
    assert rec["kind"] == "optimize_in_place" and json.loads(rec["impact"]) == {"basis": "not estimated"}
    assert "Update model fixes it" in rec["reason"]
    assert rows["run"]["status"] == "complete" and rows["run"]["rule_set_version"] == "model-bpa-1"


def test_the_check_id_is_in_the_evidence_never_the_key():
    first, later = model_bpa_share.analysis_rows(_status("bpa-1")), model_bpa_share.analysis_rows(_status("bpa-2"))
    key = lambda rows: [(f["rule_id"], f["item_id"], f["target"]) for f in rows["findings"]]  # noqa: E731
    assert key(first) == key(later), "a dismissed finding stays dismissed when the next check finds it"
    assert first["run"]["run_id"] != later["run"]["run_id"]
    assert first["run"]["run_id"] == model_bpa_share.analysis_rows(_status("bpa-1"))["run"]["run_id"]
    assert json.loads(first["findings"][0]["evidence"])[0]["request_id"] == "bpa-1"
    assert all("bpa-1" not in f["rule_id"] + f["target"] for f in first["findings"])


def test_secrets_are_removed_and_a_check_of_no_tables_is_partial():
    rows = model_bpa_share.analysis_rows(_status(no_tables=True))
    assert TOKEN.split()[1] not in json.dumps(rows)
    assert rows["run"]["status"] == "partial"


def test_one_finding_per_rule_and_object_and_nothing_without_a_finished_check():
    status = _status()
    status["last"]["findings"].append(copy.deepcopy(status["last"]["findings"][0]))
    assert len(model_bpa_share.analysis_rows(status)["findings"]) == 2
    with pytest.raises(ValueError, match="no finished check"):
        model_bpa_share.analysis_rows({"model": "SM_Gold", "model_id": "m1", "workspace_id": "w1", "last": None})


def test_rule_ids_are_slugs():
    assert model_bpa_share.rule_id("Avoid using '1-(x/y)' syntax") == "bpa.avoid-using-1-x-y-syntax"
    assert model_bpa_share.rule_id("Ünïcode rule") == "bpa.ünïcode-rule", "letters of any script are kept"
    assert model_bpa_share.rule_id("日本") != model_bpa_share.rule_id("中国")
    assert model_bpa_share.rule_id("!!").startswith("bpa.r-"), "a name with no letters is hashed"


def test_two_rule_names_with_one_key_keep_the_error():
    status = _status()
    warning = copy.deepcopy(status["last"]["findings"][0]) | {"severity": "Warning", "rule": "hide foreign keys!"}
    status["last"]["findings"].insert(0, warning)
    found = [f for f in model_bpa_share.analysis_rows(status)["findings"] if f["rule_id"] == "bpa.hide-foreign-keys"]
    assert [f["severity"] for f in found] == ["high"]


@pytest.mark.parametrize("checked_at", ["", "2026-11-23T10:00:00", "not a time"])
def test_a_check_with_no_zoned_finish_time_isnt_shared(checked_at):
    with pytest.raises(ValueError, match="no finish time with its time zone"):
        model_bpa_share.analysis_rows(_status(checked_at=checked_at))


def test_effort_isnt_claimed():
    assert all(r["effort"] is None for r in model_bpa_share.analysis_rows(_status())["recommendations"])


def test_recorded_twice_it_adds_nothing_and_a_dismissal_holds(tmp_path: Path):
    lake = LocalLakehouse(tmp_path / "lakehouse")
    setup.initialize(lake, Installation(tmp_path / "setup" / "optimize"), approve=True)
    me = Shared(lake, Installation(tmp_path / "pc1" / "optimize"))
    rows = model_bpa_share.analysis_rows(_status("bpa-1"))
    me.record_analysis(rows["run"], rows["artifacts"], rows["findings"], rows["recommendations"])
    me.record_analysis(rows["run"], rows["artifacts"], rows["findings"], rows["recommendations"])
    assert len(me.findings()) == 2
    later = model_bpa_share.analysis_rows(_status("bpa-2"))
    me.record_analysis(later["run"], later["artifacts"], later["findings"], later["recommendations"])
    assert len(me.findings()) == 2, "the next check's findings are the same findings"


def test_sharing_is_an_audited_call_that_asks_first_and_fails_closed(svc, monkeypatch):  # noqa: F811
    assert "model_bpa_share" in server.METHODS and audit.audited("model_bpa_share")
    assert audit.fails_closed("model_bpa_share", {"env": "test", "approve": True, "confirm": "abc"})
    monkeypatch.setattr(type(svc), "model_bpa_status", lambda self, env="test", layer=None, name=None: _status(), raising=False)
    svc.optimize_create("w1", workspace_name="Platform")
    with pytest.raises(PermissionError, match="from this model's best practices check") as asked:
        svc.model_bpa_share("test")
    would = asked.value.would
    assert would["findings"] == 2 and would["shared"] == 0
    assert len(svc._optimize_shared().rows("findings")) == 0, "asking wrote nothing"
    out = svc.model_bpa_share("test", approve=True, confirm=would["confirm"])
    assert out["shared"] == 2
    assert set(svc._optimize_shared().rows("findings")["rule_id"].to_list()) == {"bpa.hide-foreign-keys", "bpa.mark-primary-keys"}
