"""Data health from the Delta log: freshness, volume against the usual, schema changes, and never a guessed number."""

import datetime as dt
import shutil

import polars as pl
import pytest
from deltalake import DeltaTable

from fabric_desktop.errors import NotSetUp
from fabric_desktop.meta import data_health as dh
from fabric_desktop.service import Service

NOW = dt.datetime(2026, 11, 17, 12, tzinfo=dt.timezone.utc)


def _ms(hours_ago: float) -> int:
    return int((NOW - dt.timedelta(hours=hours_ago)).timestamp() * 1000)


def _log(rows: list[int | None], op: str = "WRITE", name: str = "numOutputRows") -> list[dict]:
    """A Delta history, newest first, as delta-rs returns it: one load per count, an hour apart."""
    out = []
    for v, n in enumerate(rows):
        metrics = {} if n is None else {name: str(n)}
        out.append({"version": v, "timestamp": _ms(len(rows) - v), "operation": op,
                    "operationParameters": {"mode": "Append"}, "operationMetrics": metrics})
    return out[::-1]


def test_rows_written_reads_spark_and_delta_rs_names():
    assert dh.rows_written({"operation": "WRITE", "operationMetrics": {"numOutputRows": "12"}}) == 12
    assert dh.rows_written({"operation": "WRITE", "operationMetrics": {"num_added_rows": 7}}) == 7
    assert dh.rows_written({"operation": "MERGE", "operationMetrics": {
        "numTargetRowsInserted": "3", "numTargetRowsUpdated": "4"}}) == 7
    assert dh.rows_written({"operation": "MERGE", "operationMetrics": {
        "num_target_rows_inserted": 1, "num_target_rows_updated": 1}}) == 2
    # Half a MERGE's counts, a negative, a boolean or nothing at all: not recorded, never a guess.
    assert dh.rows_written({"operation": "MERGE", "operationMetrics": {"numTargetRowsInserted": "3"}}) is None
    assert dh.rows_written({"operation": "WRITE", "operationMetrics": {"numOutputRows": -1}}) is None
    assert dh.rows_written({"operation": "WRITE", "operationMetrics": {"numOutputRows": True}}) is None
    assert dh.rows_written({"operation": "WRITE"}) is None


def test_a_load_far_from_the_usual_is_unusual_and_the_rule_comes_with_it():
    history = _log([500] * 9 + [480, 520, 510, 490] + [100])
    v = dh.volume(history)
    assert v["state"] == "low" and v["usual"]["median"] == 500 and v["latest"]["rows"] == 100
    assert "median" in v["rule"]["text"] and v["rule"]["minimum"] == dh.MINIMUM
    result = {"freshness": {"state": "fresh"}, "volume": v, "schema": {"state": "same"}}
    [alert] = dh.alerts(result)
    assert alert["kind"] == "volume" and alert["version"] == 13 and alert["rule"] == v["rule"]["text"]


def test_a_load_inside_the_band_is_usual_and_the_floor_covers_loads_that_never_vary():
    assert dh.volume(_log([500] * 12 + [530]))["state"] == "usual"  # within 10% of 500
    assert dh.volume(_log([500] * 12 + [600]))["state"] == "high"  # spread 0, beyond the 10% floor
    assert dh.volume(_log([0] * 12 + [0]))["state"] == "usual"


def test_too_little_history_says_so_instead_of_alerting():
    v = dh.volume(_log([500] * 6 + [1]))
    assert v["state"] == "not_enough_history" and v["usual"] == {"enough": False, "have": 6, "need": 10}
    assert dh.alerts({"freshness": {"state": "fresh"}, "volume": v, "schema": {"state": "same"}}) == []


def test_unrecorded_loads_are_left_out_of_the_usual_and_counted():
    v = dh.volume(_log([500] * 9 + [None] * 3 + [500]))
    assert v["state"] == "not_enough_history" and v["unread"] == 3 and v["usual"]["have"] == 9
    latest_unread = dh.volume(_log([500] * 12 + [None]))
    assert latest_unread["state"] == "unread" and "didn't record" in latest_unread["why"]


def test_the_usual_is_taken_from_the_last_twenty_loads_only():
    v = dh.volume(_log([10] * 30 + [1000] * 20 + [1000]))
    assert v["state"] == "usual" and v["usual"]["median"] == 1000 and v["usual"]["have"] == dh.BASELINE


def test_maintenance_isnt_a_load_or_a_change_of_data():
    history = _log([500] * 3)
    history.insert(0, {"version": 3, "timestamp": _ms(0.5), "operation": "OPTIMIZE", "operationMetrics": {}})
    history.insert(0, {"version": 4, "timestamp": _ms(0.2), "operation": "VACUUM END", "operationMetrics": {}})
    assert [x["version"] for x in dh.loads(history)] == [0, 1, 2]
    f = dh.freshness(history, 24, NOW)
    assert f["last_version"] == 2 and f["state"] == "fresh" and f["age_hours"] == 1


def test_freshness_against_the_promise():
    history = _log([500])
    history[0]["timestamp"] = _ms(26)
    late = dh.freshness(history, 24, NOW)
    assert late["state"] == "late" and late["age_hours"] == 26 and late["last_change"].endswith("Z")
    assert dh.freshness(history, None, NOW)["state"] == "no_promise"
    history[0]["timestamp"] = None
    assert dh.freshness(history, 24, NOW)["state"] == "unread" and dh.freshness(history, 24, NOW)["age_hours"] is None
    assert dh.freshness([], 24, NOW)["state"] == "unread"
    assert "last 200 commits" in dh.freshness([], 24, NOW)["why"]
    assert dh.loads([{"version": True, "operation": "WRITE", "operationMetrics": {"numOutputRows": 1}}]) == []
    [alert] = dh.alerts({"freshness": late, "volume": {"state": "usual"}, "schema": {"state": "same"}})
    assert alert["kind"] == "freshness" and "promise 24 h" in alert["title"]


def test_schema_change_names_columns_added_removed_and_retyped():
    a = [{"name": "id", "type": "long"}, {"name": "x", "type": "string"}]
    b = [{"name": "id", "type": "integer"}, {"name": "y", "type": "string"}]
    assert dh.schema_change(a, b) == {"state": "changed", "added": ["y"], "removed": ["x"],
                                      "retyped": [{"name": "id", "from": "long", "to": "integer"}]}
    assert dh.schema_change(a, a)["state"] == "same"
    assert dh.schema_change(None, a) == {"state": "unread"}


def test_check_reads_a_real_delta_table(tmp_path):
    uri = str(tmp_path / "t")
    for _ in range(12):
        pl.DataFrame({"a": list(range(100))}).write_delta(uri, mode="append")
    DeltaTable(uri).optimize.compact()
    pl.DataFrame({"a": [1], "b": ["new"]}).write_delta(uri, mode="append", delta_write_options={"schema_mode": "merge"})
    out = dh.check(lambda v: DeltaTable(uri, version=v), within_hours=24)
    assert out["freshness"]["state"] == "fresh" and out["freshness"]["last_version"] == 13
    assert out["volume"]["state"] == "low" and out["volume"]["latest"]["rows"] == 1
    assert out["volume"]["usual"]["median"] == 100
    assert out["schema"]["state"] == "changed" and out["schema"]["added"] == ["b"] and out["schema"]["since_version"] == 11
    assert {a["kind"] for a in out["alerts"]} == {"volume", "schema"}


def test_a_schema_that_cant_be_read_says_so_and_the_rest_still_reports(tmp_path):
    uri = str(tmp_path / "t")
    for _ in range(2):
        pl.DataFrame({"a": [1]}).write_delta(uri, mode="append")

    def open_at(v):
        if v == 0:
            raise OSError("log entry gone")
        return DeltaTable(uri, version=v)

    out = dh.check(open_at)
    assert out["schema"]["state"] == "unread" and "version 0" in out["schema"]["why"]
    assert "log entry gone" not in out["schema"]["why"]  # the raw error (often a path) stays in the log
    assert out["freshness"]["state"] == "no_promise" and out["volume"]["state"] == "not_enough_history"


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    return s


def test_meta_data_health_reads_an_entity_table(svc):
    out = svc.meta_data_health("silver.customers")
    assert out["entity"] == "silver.customers" and out["env"] == "dev" and out["version"] >= 0
    assert out["volume"]["state"] == "not_enough_history" and out["freshness"]["state"] in ("no_promise", "fresh")
    with pytest.raises(ValueError, match="isn't an entity"):
        svc.meta_data_health("silver.nope")
    with pytest.raises(ValueError, match="env is one of"):
        svc.meta_data_health("silver.customers", "staging")
    with pytest.raises(NotSetUp):
        svc.meta_data_health("silver.customers", "test")  # no workspace for Test: nothing is read
    project = svc._project()
    e = project.entities["silver.customers"]
    shutil.rmtree(svc._store(project, "dev").uri(project.layer(e.layer).lakehouse, e.schema, e.table))
    assert svc.meta_data_health("silver.customers")["state"] == "no_table"


def test_watch_list_quiet_and_stop_watching(svc):
    assert svc.meta_data_health_list()["tables"] == []
    svc.meta_data_health_watch("silver.customers", "dev")
    svc.meta_data_health_watch("silver.customers", "dev")  # watching twice keeps one row
    out = svc.meta_data_health_list()
    assert [t["entity"] for t in out["tables"]] == ["silver.customers"] and "silver.customers" in out["entities"]
    assert out["rule"]["minimum"] == dh.MINIMUM and svc.meta_data_health_list("prod")["tables"] == []
    with pytest.raises(ValueError, match="isn't an entity"):
        svc.meta_data_health_watch("silver.nope", "dev")
    with pytest.raises(ValueError, match="kind is one of"):
        svc.meta_data_health_quiet("silver.customers", "dev", "cost", 1)
    with pytest.raises(ValueError, match="snooze_days"):
        svc.meta_data_health_quiet("silver.customers", "dev", "volume", 1, 7)
    with pytest.raises(ValueError, match="isn't on the Data health list"):
        svc.meta_data_health_quiet("gold.dim_customer", "dev", "volume", 1)
    acked = svc.meta_data_health_quiet("silver.customers", "dev", "schema", 3)
    assert acked["until"] is None and acked["version"] == 3
    snoozed = svc.meta_data_health_quiet("silver.customers", "dev", "volume", 3, 1)
    assert snoozed["until"] > acked["at"]
    svc.meta_data_health_watch("silver.customers", "dev", watch=False)
    assert svc.meta_data_health_list()["tables"] == [] and svc._dh_read()["quiet"] == {}


def test_a_quieted_alert_is_marked_until_its_snooze_ends(svc, monkeypatch):
    from fabric_desktop import dataops_service
    svc.meta_data_health_watch("silver.customers", "dev")
    alert = {"kind": "volume", "version": 5, "title": "x"}
    monkeypatch.setattr(svc, "meta_data_health", lambda name, env: {"entity": name, "env": env, "alerts": [dict(alert)]})
    assert "quiet" not in svc.meta_data_health_list()["tables"][0]["alerts"][0]
    svc.meta_data_health_quiet("silver.customers", "dev", "volume", 5, 1)
    assert svc.meta_data_health_list()["tables"][0]["alerts"][0]["quiet"]["until"]
    state = svc._dh_read()
    state["quiet"][dataops_service._dh_key("dev", "silver.customers", "volume", 5)]["until"] = "2000-01-01T00:00:00+00:00"
    svc._dh_write(state)
    assert "quiet" not in svc.meta_data_health_list()["tables"][0]["alerts"][0]  # the snooze ended
    # A newer version alerting again isn't covered by an older acknowledgement.
    svc.meta_data_health_quiet("silver.customers", "dev", "volume", 5)
    alert["version"] = 6
    assert "quiet" not in svc.meta_data_health_list()["tables"][0]["alerts"][0]


def test_a_table_that_cant_be_read_doesnt_hide_the_others(svc, monkeypatch):
    svc.meta_data_health_watch("silver.customers", "dev")
    svc.meta_data_health_watch("gold.dim_customer", "dev")

    def check(name, env):
        if name == "gold.dim_customer":
            raise OSError("OneLake said no")
        return {"entity": name, "env": env, "alerts": []}

    monkeypatch.setattr(svc, "meta_data_health", check)
    tables = {t["entity"]: t for t in svc.meta_data_health_list()["tables"]}
    assert tables["gold.dim_customer"]["state"] == "error" and "OneLake said no" in tables["gold.dim_customer"]["why"]
    assert tables["silver.customers"]["alerts"] == []


def test_loads_carry_the_run_that_wrote_them():
    history = _log([5, 5])
    history[0]["fabric_desktop_run_id"] = "r-20261117-120000-0123456789ab"
    history[1]["fabric_desktop_run_id"] = "not ours"
    assert [x["run_id"] for x in dh.loads(history)] == [None, "r-20261117-120000-0123456789ab"]


def test_acknowledgements_no_alert_matches_are_forgotten(svc, monkeypatch):
    svc.meta_data_health_watch("silver.customers", "dev")
    svc.meta_data_health_watch("gold.dim_customer", "dev")
    alerts = {"silver.customers": [{"kind": "volume", "version": 6, "title": "x"}]}

    def check(name, env):
        if name == "gold.dim_customer":
            raise OSError("can't read it now")
        return {"entity": name, "env": env, "alerts": [dict(a) for a in alerts[name]]}

    monkeypatch.setattr(svc, "meta_data_health", check)
    for kind, version, name in (("volume", 5, "silver.customers"), ("volume", 6, "silver.customers"),
                                ("schema", 6, "silver.customers"), ("volume", 9, "gold.dim_customer")):
        svc.meta_data_health_quiet(name, "dev", kind, version)
    svc.meta_data_health_watch("silver.customers", "prod")
    svc.meta_data_health_quiet("silver.customers", "prod", "volume", 1)
    svc.meta_data_health_list()
    # Kept: the one the current alert matches, the unreadable table's, and another environment's.
    assert sorted(svc._dh_read()["quiet"]) == ["dev|gold.dim_customer|volume|9", "dev|silver.customers|volume|6",
                                               "prod|silver.customers|volume|1"]


def test_notify_sends_open_alerts_once_each_for_the_projects_team(svc, monkeypatch):
    svc.meta_data_health_watch("silver.customers", "dev")
    alerts = [{"kind": "volume", "version": 6, "title": "Latest load wrote 1 rows; usual is 500 ± 0", "rule": "Usual is the median."},
              {"kind": "schema", "version": 6, "title": "Schema changed: 1 column added"}]
    monkeypatch.setattr(svc, "meta_data_health", lambda name, env: {"entity": name, "env": env, "alerts": [dict(a) for a in alerts]})
    sent = []
    monkeypatch.setattr(svc, "platform_notify", lambda events: sent.append(events) or {"sent": len(events)})
    svc.meta_data_health_quiet("silver.customers", "dev", "schema", 6)
    svc.meta_data_health_notify("dev")
    [events] = sent
    [e] = events  # the acknowledged schema alert isn't sent
    assert e["kind"] == "data.health" and e["severity"] == "warn" and e["dedupe"] == "data.health:dev|silver.customers|volume|6"
    assert e["to"]["roles"][0].startswith("project:") and e["open"] == {"view": "monitor", "tab": "data_health", "target": "silver.customers"}
    assert e["title"] == "silver.customers: Latest load wrote 1 rows; usual is 500 ± 0" and e["detail"] == "Usual is the median."
    from fabric_desktop.workloads.platform_notify import validate
    validate(e)  # the shape platform_notify takes
    # Nothing open: nothing is sent.
    alerts.clear()
    svc.meta_data_health_list("dev")
    assert svc.meta_data_health_notify("dev") == {"sent": 0, "events": []} and len(sent) == 1


def test_notify_reuses_the_list_the_page_just_read(svc, monkeypatch):
    svc.meta_data_health_watch("silver.customers", "dev")
    reads = []
    monkeypatch.setattr(svc, "meta_data_health", lambda name, env: reads.append(name) or {"entity": name, "env": env, "alerts": []})
    svc.meta_data_health_list("dev")
    svc.meta_data_health_notify("dev")
    assert reads == ["silver.customers"]
