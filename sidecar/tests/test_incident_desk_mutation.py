"""The incident desk (incident_desk.py) as its callers see it: the board's counts and defaults, the bell's
order, who an incident reaches on an older or narrower platform, and the incident history the central
lakehouse keeps. Each test pins a behaviour that mutation testing (batch 109) found unchecked."""

import datetime as dt
import json
import types

import polars as pl
import pytest

from fabric_desktop.central import core
from fabric_desktop.service import Service
from fabric_desktop.workloads import evaluate
from fabric_desktop.workloads.incident_history import FAMILY
from fabric_desktop.workloads.incidents import KEY

ME = "chris@contoso.com"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    s = Service(root=tmp_path / "app")
    s._auth = types.SimpleNamespace(signed_in_user=lambda: ME, silent_token=lambda scopes: None,
                                    fabric_token=lambda: None, storage_token=lambda: None)
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def _iso(t):
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


def _edit(svc, env, change):
    """Change the stored incidents as time passing would, then write them back."""
    log = svc._incident_log(env)
    doc = json.loads(log.backend.read(KEY).data)
    change({i["title"]: i for i in doc["incidents"]})
    log.backend.replace(KEY, json.dumps(doc).encode(), log.backend.read(KEY).etag)


# ---------------------------------------------------------------------------- the board


def test_the_board_shows_resolved_incidents_unless_asked_not_to(svc):
    inc = svc.incident_report("dev", "Fixed already", priority="P2")
    svc.incident_resolve("dev", inc["id"], "fixed")
    board = svc.incidents_board("dev")
    assert [i["title"] for i in board["incidents"]] == ["Fixed already"]
    assert board["counts"]["open"] == 0
    assert svc.incidents_board("dev", include_resolved=False)["incidents"] == []


def test_the_board_says_which_incidents_were_detected_and_which_were_reported(svc):
    log = svc._incident_log("dev")
    run = {"entity": "silver.orders", "status": "failed", "run_id": "r1", "started_at": "2026-10-16T10:00:00+00:00",
           "message": "boom"}
    log.sync(evaluate.signals("sales", {"silver.orders": {}}, [run], "dev"), evaluated=True, by="agent")
    svc.incident_report("dev", "Slow page")
    sources = {i["kind"]: i["source"] for i in svc.incidents_board("dev")["incidents"]}
    assert sources == {"run_failed": "detected", "reported": "reported"}


def test_mine_counts_only_my_incidents_not_someone_elses(svc):
    mine = svc.incident_report("dev", "Mine")
    theirs = svc.incident_report("dev", "Theirs")
    svc.incident_report("dev", "Nobody's")
    svc.incident_assign("dev", mine["id"], "Chris@Contoso.com")
    svc.incident_assign("dev", theirs["id"], "dana@contoso.com")
    counts = svc.incidents_board("dev")["counts"]
    assert (counts["open"], counts["mine"], counts["unassigned"]) == (3, 1, 1)


# ---------------------------------------------------------------------------- the bell


def test_the_bell_puts_breached_before_at_risk_before_due_whatever_was_seen_last(svc):
    svc.workload_store.assign("meta:sales:gold.revenue", {"technical_owner": ME})
    for title in ("Breached", "At risk", "Due", "Missed", "Resolve at risk"):
        svc.incident_report("dev", title, priority="P3", workload="meta:sales:gold.revenue")
    now = dt.datetime.now(dt.UTC)
    ago = lambda **kw: _iso(now - dt.timedelta(**kw))  # noqa: E731

    def age(by):  # P3: acknowledge within 4 h, resolve within 2 days; at risk in the last quarter
        by["Breached"].update(opened_at=ago(hours=5), last_seen_at="2026-01-03T00:00:00Z")
        by["At risk"].update(opened_at=ago(hours=3, minutes=30), last_seen_at="2026-01-02T00:00:00Z")
        by["Due"].update(opened_at=ago(minutes=10), last_seen_at="2026-01-01T00:00:00Z")
        by["Missed"].update(opened_at=ago(hours=5), state="acknowledged", acknowledged_at=ago(minutes=50),
                            last_seen_at="2026-01-05T00:00:00Z")
        by["Resolve at risk"].update(opened_at=ago(hours=40), state="acknowledged", acknowledged_at=ago(hours=39),
                                     last_seen_at="2026-01-04T00:00:00Z")
    _edit(svc, "dev", age)
    out = svc.incidents_for_me()["incidents"]
    assert [(i["title"], i["sla"]["state"]) for i in out] == [
        ("Breached", "breached"), ("At risk", "at_risk"), ("Due", "due"),
        ("Missed", "missed"), ("Resolve at risk", "at_risk")]
    assert [i["at"] for i in out] == ["2026-01-03T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z",
                                      "2026-01-05T00:00:00Z", "2026-01-04T00:00:00Z"]  # when it was last seen


# ---------------------------------------------------------------------------- who an incident reaches


def _orders(svc):
    svc._catalog().call("register_product", product={"project": "sales", "name": "orders", "owner": "olu@contoso.com",
                                                     "access": "public", "status": "published", "domain": "sales"},
                        ports=[{"port": "daily", "entity": "silver.orders"}], env="dev")
    return svc.incident_report("dev", "Orders late", workload="meta:sales:silver.orders")


def test_an_incident_that_is_gone_says_so(svc):
    with pytest.raises(LookupError, match="isn't in the list any more"):
        svc.incident_impact("dev", "inc-gone")


def test_a_platform_that_keeps_no_followers_still_names_the_owners(svc, monkeypatch):
    inc = _orders(svc)
    monkeypatch.setattr(core, "OPERATIONS", {k: v for k, v in core.OPERATIONS.items() if k != "product_followers"})
    out = svc.incident_impact("dev", inc["id"])
    assert [p["upn"] for p in out["people"]] == ["olu@contoso.com"] and out["followers_hidden"] == []


def test_an_older_platform_without_the_followers_call_still_names_the_owners(svc, monkeypatch):
    inc = _orders(svc)
    real = type(svc._catalog()).call

    def older(self, op, **kw):
        if op == "product_followers":
            raise LookupError("no such function")
        return real(self, op, **kw)
    monkeypatch.setattr(type(svc._catalog()), "call", older)
    out = svc.incident_impact("dev", inc["id"])
    assert [p["upn"] for p in out["people"]] == ["olu@contoso.com"] and out["followers_hidden"] == []


def test_impact_shows_the_latest_time_people_were_told(svc):
    inc = _orders(svc)
    svc.incident_note("dev", inc["id"], "Told downstream in Teams: ana@contoso.com")
    svc.incident_note("dev", inc["id"], "Told downstream in Teams: olu@contoso.com")
    assert svc.incident_impact("dev", inc["id"])["told"]["text"] == "Told downstream in Teams: olu@contoso.com"


def test_telling_says_which_routes_it_went_to(svc, monkeypatch):
    from fabric_desktop import platform_notify_service as pns
    monkeypatch.setattr(pns, "post_teams", lambda url, title, lines: None)
    inc = _orders(svc)
    svc.platform_notify_save([{"name": "default", "url": "https://contoso.webhook.office.com/x"}])
    told = svc.incident_tell_impacted("dev", inc["id"], ["olu@contoso.com"])
    assert told["told"] is True and told["routes"] == ["default"]


def test_a_long_list_of_people_already_told_is_noted_once(svc, monkeypatch):
    inc = svc.incident_report("dev", "Orders late", workload="meta:sales:silver.orders")
    people = [{"upn": f"person{n:03d}.with.a.long.name@contoso.example.com"} for n in range(60)]
    monkeypatch.setattr(svc, "incident_impact", lambda env, id: {"available": True, "people": people,
                                                                 "products": [{"name": "orders"}]})
    answers = iter([{"events": [{"result": "sent", "routes": ["default"]}]},
                    {"events": [{"result": "skipped", "reason": "already posted"}]}])
    monkeypatch.setattr(svc, "platform_notify", lambda events: next(answers))
    upns = [p["upn"] for p in people]
    assert svc.incident_tell_impacted("dev", inc["id"], upns)["told"] is True
    again = svc.incident_tell_impacted("dev", inc["id"], upns)
    assert again["told"] is False and again["reason"] == "already posted"
    notes = [n["text"] for n in svc._incident("dev", inc["id"])["notes"]]
    assert len(notes) == 1 and len(notes[0]) == 2000  # the note is kept to 2,000 characters, and written once


# ---------------------------------------------------------------------------- history in the central lakehouse


class Lake:
    """A central lakehouse holding these incident snapshots, or failing to read them."""

    def __init__(self, df=None, error=None):
        self.df, self.error, self.read = df, error, []

    def rows(self, table):
        self.read.append(table)
        if self.error is not None:
            raise self.error
        return self.df


@pytest.fixture
def lake(svc, monkeypatch):
    holder = {"lake": Lake(), "conn": {"workspace_id": "w1", "lakehouse_id": "l1", "lakehouse_name": "Central"}}
    monkeypatch.setattr(svc, "central_lakehouse", lambda: holder["conn"])
    asked = holder.setdefault("schemas", [])

    def open_lake(self, ws, lh, schemas=None):
        asked.append(schemas)
        return holder["lake"]
    monkeypatch.setattr(Service, "_optimize_lake", open_lake)
    return holder


def _snap(n, state="open", as_of="2026-11-01T10:00:00Z", created="2026-11-01T10:00:01Z", opened=None, **kw):
    return {"incident_id": f"inc-{n}", "env": "dev", "state": state, "as_of": as_of, "created_at": created,
            "opened_at": opened or f"2026-11-{1 + n % 28:02d}T00:00:00Z", "record_id": f"r-{n}-{state}",
            "installation_id": "pc", "app_version": "1", "schema_version": 1, **kw}


def _df(rows):
    return pl.DataFrame(rows, schema_overrides={"as_of": pl.String, "opened_at": pl.String, "created_at": pl.String})


def test_a_central_lakehouse_that_doesnt_say_is_read_with_schemas(svc, lake):
    lake["lake"] = Lake(_df([_snap(1)]))
    svc.incidents_history()
    lake["conn"] = lake["conn"] | {"schemas": False}
    svc.incidents_history()
    assert lake["schemas"] == [True, False]


@pytest.mark.parametrize("error", [ValueError("Table incident_snapshot not found"),
                                   type("TableNotFoundError", (Exception,), {})("missing")])
def test_history_before_any_copy_is_empty(svc, lake, error):
    lake["lake"] = Lake(error=error)
    assert svc.incidents_history() == {"state": "empty", "lakehouse": "Central", "incidents": []}


def test_history_that_cant_be_read_for_another_reason_is_an_error(svc, lake):
    lake["lake"] = Lake(error=OSError("the disk is full"))
    with pytest.raises(OSError, match="disk is full"):
        svc.incidents_history()


def test_within_one_second_the_later_stage_of_handling_wins(svc, lake):
    lake["lake"] = Lake(_df([
        _snap(1, "acknowledged", created="2026-11-01T10:00:01Z"), _snap(1, "open", created="2026-11-01T10:00:02Z"),
        _snap(2, "resolved", created="2026-11-01T10:00:01Z"), _snap(2, "acknowledged", created="2026-11-01T10:00:02Z"),
        _snap(3, "acknowledged", created="2026-11-01T10:00:01Z"), _snap(3, "paused", created="2026-11-01T10:00:02Z"),
    ]))
    states = {r["incident_id"]: r["state"] for r in svc.incidents_history()["incidents"]}
    assert states == {"inc-1": "acknowledged", "inc-2": "resolved", "inc-3": "acknowledged"}  # unknown: as open


def test_a_snapshot_with_no_time_never_replaces_a_dated_one(svc, lake):
    lake["lake"] = Lake(_df([_snap(1, "open"), _snap(1, "resolved", as_of=None)]))
    [row] = svc.incidents_history()["incidents"]
    assert row["state"] == "open"


def test_history_is_newest_first_with_undated_incidents_last_and_no_bookkeeping(svc, lake):
    lake["lake"] = Lake(_df([_snap(1, opened="2026-11-01T00:00:00Z"), _snap(2, opened=None) | {"opened_at": None},
                             _snap(3, opened="2026-11-03T00:00:00Z")]))
    rows = svc.incidents_history()["incidents"]
    assert [r["incident_id"] for r in rows] == ["inc-3", "inc-1", "inc-2"]
    assert not {"record_id", "installation_id", "app_version", "schema_version"} & set(rows[0])


def test_history_shows_500_by_default_at_least_one_and_at_most_5000(svc, lake):
    lake["lake"] = Lake(_df([_snap(n) for n in range(5001)]))
    assert len(svc.incidents_history()["incidents"]) == 500
    assert len(svc.incidents_history(limit=0)["incidents"]) == 1
    assert len(svc.incidents_history(limit=6000)["incidents"]) == 5000


def test_history_of_one_environment(svc, lake):
    lake["lake"] = Lake(_df([_snap(1), _snap(2, env="prod")]))
    assert [r["incident_id"] for r in svc.incidents_history("prod")["incidents"]] == ["inc-2"]


@pytest.mark.parametrize("count, capped", [(4999, False), (5000, True), (5001, True)])
def test_measures_read_up_to_5000_incidents_and_say_when_that_is_all_they_read(svc, lake, monkeypatch, count, capped):
    monkeypatch.setattr(svc, "incidents_history_sync", lambda approve=False: {"state": "copied", "envs": []})
    opened = _iso(dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
    lake["lake"] = Lake(_df([_snap(n, opened=opened, priority="P3") for n in range(count)]))
    out = svc.incidents_measures("dev", 7)
    assert out["capped"] is capped and out["total"]["opened"] == min(count, 5000)


def test_the_history_copy_keeps_every_timeline_entry_and_where_it_came_from(svc, tmp_path, monkeypatch):
    from test_optimize_service import FakeFabric

    from fabric_desktop.leases import LocalBackend
    from fabric_desktop.optimize.lakehouse import LocalLakehouse
    fabric = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fabric))
    lakes = tmp_path / "onelake"
    monkeypatch.setattr(Service, "_optimize_lake",
                        lambda self, ws, lh, schemas=None: LocalLakehouse(lakes / ws / lh, schemas is not False))
    monkeypatch.setattr(LocalBackend, "workspace_id", "ws-dev", raising=False)
    svc.optimize_create("w1", workspace_name="Platform")
    inc = svc.incident_report("dev", "Revenue looks doubled", priority="P2")
    svc.incident_note("dev", inc["id"], "looking")
    assert svc.incidents_history_sync(approve=True)["state"] == "copied"
    conn = svc.central_lakehouse()
    lake = LocalLakehouse(lakes / conn["workspace_id"] / conn["lakehouse_id"], conn.get("schemas", True) is not False)
    events = lake.rows(FAMILY.table("incident_event"))
    assert sorted(events["what"].to_list()) == ["note", "reported"]
    [row] = svc.incidents_history("dev")["incidents"]
    assert row["workspace_id"] == "ws-dev"

