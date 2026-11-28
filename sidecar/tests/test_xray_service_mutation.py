"""Estate X-ray service paths that mutation testing (batch 15, docs/quality/mutation-testing.md) showed
no test checked: entity names and keys, a scan's pause flag and cost findings, the central lakehouse
phase, which lakehouses the project manages, fix jobs that ended in the scan's own second, the risk
facts and refresh flags of Optimize and Vacuum, the size of what a vacuum deletes, adopting with picked
keys, and a staging lakehouse's wording and margin. Fabric and OneLake are fakes; nothing reaches a tenant."""

from __future__ import annotations

import datetime as dt
import json
import logging
from types import SimpleNamespace

import polars as pl
import pytest
from deltalake import DeltaTable, write_deltalake

import fabric_desktop.xray_service as xs
from fabric_desktop.service import Service

from test_xray import vacuum_svc  # noqa: F401 - the fixture is used by name
from test_xray_service_edges import adopt_svc, scan_svc  # noqa: F401 - the fixtures are used by name
from test_xray_staging import OLD, WS, Fabric, svc  # noqa: F401 - the fixture is used by name


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """App data (Databricks sync pairs, settings) goes to this test's folder, never the real one."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


# ---- names, keys and the adopted entity

def test_a_table_named_with_a_leading_digit_gets_a_letter_first():
    assert xs._entity_name("2024_sales") == "t_2024_sales"
    assert xs._entity_name("Orders") == "orders"


def test_a_table_with_no_id_column_is_keyed_by_its_first_column():
    assert xs.guess_keys(["name", "amount"]) == ["name"]


def test_an_adopted_entity_loads_new_rows_by_its_timestamp_and_has_an_owner():
    text = xs.adopted_entity_text("bronze.orders", "", "abfss://x", "Sales / LH / orders",
                                  {"id": pl.Int64, "updated_at": pl.Datetime}, ["id"])
    assert 'mode = "incremental"\nwatermark = "updated_at"' in text
    assert 'owner = "me"' in text
    full = xs.adopted_entity_text("bronze.orders", "chris", "abfss://x", "L", {"id": pl.Int64}, ["id"])
    assert 'mode = "full"' in full and 'owner = "chris"' in full


# ---- dismiss

def test_a_dismissed_id_may_be_1024_characters_and_its_reason_500(tmp_path):
    s = Service(root=tmp_path / "app")
    assert s.xray_dismiss("x" * 1024)["dismissed"] is not None
    with pytest.raises(ValueError, match="too long"):
        s.xray_dismiss("x" * 1025)
    assert len(s.xray_dismiss("y", "r" * 600)["dismissed"]["reason"]) == 500


# ---- a scan

@pytest.fixture
def quiet(tmp_path, monkeypatch):
    """A Service whose scan thread does nothing, so the state xray() sets can be read."""
    monkeypatch.setattr(Service, "_xray_run", lambda self, *a: None)
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(fabric_token=lambda: "f",
                                                                                storage_token=lambda: "s")), raising=False)
    return Service(root=tmp_path / "app")


def test_a_scan_starts_unpaused_and_pause_with_no_argument_pauses_it(quiet):
    out = quiet.xray(refresh=True)
    assert out["running"] is True and out["paused"] is False
    assert quiet.xray_pause()["paused"] is True and not quiet._xray_gate.is_set()


def test_a_scan_adds_the_last_30_days_cost_findings(scan_svc, monkeypatch):
    asked = []
    monkeypatch.setattr(scan_svc, "cost_report", lambda days: asked.append(days) or {"by_item": []})
    monkeypatch.setattr(xs.xray, "cost_findings",
                        lambda report, local: [{"kind": "cost", "severity": "low", "title": "NB cost about $1"}])
    scan_svc._xray_run("f", "s")
    assert asked == [30]
    saved = json.loads(scan_svc._xray_file.read_text(encoding="utf-8"))
    assert saved["counts"].get("cost") == 1


def test_a_second_scan_writes_over_the_first(scan_svc):
    for _ in range(2):
        scan_svc._xray_run("f", "s")
        assert scan_svc._xray_state == {"running": False}


def test_sharing_with_an_unnamed_central_lakehouse_says_the_central_lakehouse(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    monkeypatch.setattr(s, "central_lakehouse", lambda: {"access": "write"}, raising=False)
    monkeypatch.setattr(s, "_optimize_write", lambda kind, records, token: {"queued": False, "sent": 0}, raising=False)
    s._xray_state = {"running": True}
    assert s._xray_share({"scanned": "2026-11-01T00:00:00+00:00", "findings": [], "lakehouse_rows": []}, lambda: "s") == {"lakehouse": None, "findings": 0}
    assert s._xray_state["progress"]["phase"] == "Adding findings to the central lakehouse"


# ---- what a scan's result is shown with

def test_annotate_reads_the_runs_log_fresh_by_default(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s._save_settings(xray_submitted={"f": {"job_id": "j"}})
    asked = []
    monkeypatch.setattr(s, "fabric_runs", lambda limit, refresh: asked.append((limit, refresh)) or [])
    s._xray_annotate({"findings": []})
    assert asked == [(500, True)]


def test_managed_and_adopted_count_only_the_lakehouses_own_tables(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    layer = SimpleNamespace(workspace=None, lakehouse="LH_A")
    project = SimpleNamespace(environments={"dev": {"workspace_id": "ws1"}}, layer_names=lambda: ["bronze"],
                              layer=lambda name: layer,
                              entities={"a": SimpleNamespace(spec={"source": {"table": "abfss://ws1@onelake/lh1/Tables/x"}}),
                                        "b": SimpleNamespace(spec={"source": {"table": "abfss://ws2@onelake/lh2/Tables/y"}})})
    monkeypatch.setattr(s, "_project", lambda: project, raising=False)
    rows = [{"workspace_id": "ws1", "lakehouse_id": "lh1", "lakehouse_name": "LH_A"},
            {"workspace_id": "ws1", "lakehouse_id": "lh2", "lakehouse_name": "LH_B"}]
    out = s._xray_annotate({"lakehouse_rows": rows, "findings": []}, refresh=False)["lakehouse_rows"]
    assert [(r["managed"], r["adopted"]) for r in out] == [(True, 1), (False, 0)]


def test_a_fix_that_ended_in_the_second_the_scan_began_still_counts(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s._save_settings(xray_submitted={"f": {"job_id": "j", "submitted_at": "2026-11-01T09:00:00+00:00"}})
    monkeypatch.setattr(s, "fabric_runs", lambda limit, refresh: [
        {"job_id": "j", "status": "Completed", "finished_at": "2026-11-01T10:00:00.500000+00:00"}])
    assert s._xray_fixes("2026-11-01T10:00:00+00:00", refresh=False)["f"]["state"] == "fixed"


# ---- Optimize and Vacuum

def test_optimize_names_the_table_with_its_schema(vacuum_svc, monkeypatch):
    s, _, _ = vacuum_svc
    monkeypatch.setattr(s, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(s, "_risk_capacity", lambda *a: None, raising=False)
    assert s.xray_optimize("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev")["table"] == "dbo.orders"


def test_vacuum_and_optimize_are_assessed_as_themselves_without_a_fresh_capacity_read(vacuum_svc, monkeypatch):
    s, _, _ = vacuum_svc
    facts, refreshes = [], []
    monkeypatch.setattr(s, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(s, "_risk_capacity", lambda ws, name, refresh, reads: refreshes.append(refresh), raising=False)
    monkeypatch.setattr(xs.risk, "analyze", lambda f, settings: facts.append(f) or {"result": "pass"})
    args = ("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev")
    s.xray_vacuum_preview(*args)
    s.xray_vacuum(*args)
    s.xray_optimize_check(*args)
    assert refreshes == [False, False, False]
    assert [(f["label"], f["workload_id"]) for f in facts] == [
        ("Vacuum", "xray-vacuum:ws-dev/lh-silver/dbo.orders"), ("Vacuum", "xray-vacuum:ws-dev/lh-silver/dbo.orders"),
        ("Optimize", "xray:ws-dev/lh-silver/dbo.orders")]


def test_a_risk_check_that_cannot_run_is_unknown_and_asks_nothing(vacuum_svc, monkeypatch):
    s, _, _ = vacuum_svc
    monkeypatch.setattr(s, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(s, "_risk_capacity", lambda *a: (_ for _ in ()).throw(RuntimeError("no network")), raising=False)
    got = s.xray_vacuum_preview("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver")["risk"]
    assert got == {"result": xs.risk.UNKNOWN, "label": xs.risk.LABELS[xs.risk.UNKNOWN], "findings": [], "can_proceed": True,
                   "needs_acknowledgement": False, "enforced": False, "summary": "The risk check couldn't run: no network"}


def test_a_vacuum_is_sized_from_a_recursive_listing_and_nothing_listed_is_zero(vacuum_svc, monkeypatch):
    s, _, _ = vacuum_svc
    monkeypatch.setattr(s, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(s, "_risk_capacity", lambda *a: None, raising=False)
    listed = []

    def files(ws, lh, folder, recursive=False):
        assert recursive is True
        return listed
    monkeypatch.setattr(xs, "FileStore", lambda _t: SimpleNamespace(list=files))
    out = s.xray_vacuum_preview("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver")
    assert out["files"] == 2 and out["bytes"] == 0  # nothing listed: no size to add up


# ---- adopt

def test_adopt_keeps_the_picked_keys_says_where_from_and_who_owns_it(adopt_svc, tmp_path, monkeypatch):
    write_deltalake(str(tmp_path / "t"), pl.DataFrame({"id": [1], "customer_id": [2], "name": ["a"]}).to_arrow())
    monkeypatch.setattr(adopt_svc, "_source_table", lambda ws, lh, schema, table: ("abfss://t", DeltaTable(str(tmp_path / "t"))))
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(signed_in_user=lambda: "chris@contoso.com")),
                        raising=False)
    out = adopt_svc.xray_adopt("ws", "lh", "LH", "Sales", [{"schema": "dbo", "table": "invoices", "keys": ["customer_id"]}])
    assert out["adopted"] == ["bronze.invoices"], out
    text = (adopt_svc._project().entities_dir / "bronze.invoices.toml").read_text(encoding="utf-8")
    assert 'keys = ["customer_id"]' in text
    assert 'description = "Adopted from Sales / LH / dbo.invoices"' in text
    assert 'owner = "chris"' in text


# ---- leftover staging lakehouses

def _named(when):
    return "StagingLakehouseForDataflows_" + when.strftime("%Y%m%d%H%M%S")


@pytest.mark.parametrize("days", [1, 2])
@pytest.mark.parametrize("written", [True, False])
def test_a_young_staging_lakehouse_says_how_long_ago(svc, monkeypatch, days, written):
    s, deleted, _ = svc
    now = dt.datetime.now(dt.timezone.utc)
    made = now - dt.timedelta(days=days, hours=1)
    monkeypatch.setattr(Fabric, "lakehouses", {WS: [{"id": "y", "displayName": _named(made)},
                                                    {"id": "z", "displayName": _named(now - dt.timedelta(hours=1))}]})
    entries = [{"path": "Files/x.parquet", "is_dir": False, "size": 1, "modified": s.ago(days)}] if written else []
    monkeypatch.setattr(xs, "FileStore", lambda _t: SimpleNamespace(
        list=lambda ws, lh, folder, recursive=False: [e for e in entries if e["path"].startswith(folder)]))
    unit = "day" if days == 1 else "days"
    start = f"Something was written to it {days} {unit} ago" if written else f"It was made {days} {unit} ago"
    why = s.xray_staging_preview(WS, "y")["why"]
    assert why[0] == f"{start}, less than 30 days, so a dataflow may still be using it."
    assert deleted == []


def test_a_refresh_within_an_hour_of_the_last_write_does_not_show_it_unused(svc):
    s, deleted, _ = svc
    s.flows["df1"]["jobs"] = [{"status": "Completed", "startTimeUtc": s.ago(40 - 1 / 48)}]  # 30 minutes after it
    p = s.xray_staging_preview(WS, "a")
    assert not p["ok"]
    assert "Sales load last refreshed before the lakehouse was last written, so it may stage there." in p["why"]


def test_remove_logs_the_workspace_name_and_keeps_removals_from_the_last_90_days(svc, caplog):
    s, deleted, _ = svc
    now = dt.datetime.now(dt.timezone.utc)
    s._save_settings(xray_removed={"recent": (now - dt.timedelta(days=10)).isoformat(timespec="seconds"),
                                   "old": (now - dt.timedelta(days=90, hours=12)).isoformat(timespec="seconds")})
    with caplog.at_level(logging.INFO, logger=xs._log.name):
        s.xray_remove_staging(WS, "a", OLD, "Sales", finding_id="f1")
    assert "in workspace Sales;" in caplog.text
    assert set(s._settings()["xray_removed"]) == {"recent", "f1"}
    assert deleted == [(WS, "a")]
