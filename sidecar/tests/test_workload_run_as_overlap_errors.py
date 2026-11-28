"""Run as (workloads/run_as.py) and tables scheduled twice (workloads/overlap.py) on input the other
tests don't send: credential and schedule fields of the wrong type, unknown credential kinds, times
that can't be read or have no zone, expiry on either side of EXPIRING_DAYS, entities that are empty,
missing or not a list, and the same agent in several records."""

import datetime as dt
import json
import time
import types

import pytest

from fabric_desktop.workloads import overlap, run_as
from fabric_desktop.workloads.agents import read_status

UTC = dt.timezone.utc
NOW = dt.datetime(2027, 1, 15, 8, 0, tzinfo=UTC)
BEAT = "2027-01-15T07:59:30Z"
STALE = "2027-01-15T08:01:30Z"


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


@pytest.fixture
def away_from_utc(monkeypatch):
    """Run in a zone that isn't UTC, so reading a time with no zone as local time would show."""
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset to set this process's time zone")
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def agent(name, schedules=None, beat=BEAT, **kw):
    return {
        "kind": "agent_status",
        "agent": name,
        "state": "running",
        "heartbeat_at": beat,
        "stale_after": STALE,
        "host": "vm",
        "schedules": schedules,
        **kw,
    }


def agent_row(credential, identity="service_principal"):
    out = run_as.build(
        "",
        {"prod": [agent("a", identity=identity, credential=credential)]},
        [],
        {},
        NOW,
    )
    [row] = [r for r in out["rows"] if r["place"]["kind"] == "agent"]
    return row, out["attention"]


def pc(sid, env, entities):
    return {
        "id": sid,
        "enabled": True,
        "target": {"kind": "meta", "env": env, "entities": entities},
        "text": "Daily",
    }


# ---- run_as: credentials


@pytest.mark.parametrize("credential", [["certificate"], "certificate", 42, None])
def test_a_credential_that_isnt_an_object_is_read_as_an_older_agent_whose_state_is_unknown(
    credential,
):
    row, attention = agent_row(credential)
    assert row["credential"] == {
        "kind": "",
        "state": "unknown",
        "expires_at": None,
        "last_token_at": None,
        "error": "",
    }
    assert attention == []
    row, _ = agent_row(credential, identity="managed_identity")
    assert (
        row["credential"]["kind"] == "managed_identity"
        and row["credential"]["state"] == "unknown"
    )


def test_an_unknown_credential_kind_is_kept_and_is_ok_only_once_a_token_request_has_worked():
    far = iso(NOW + dt.timedelta(days=200))
    row, _ = agent_row({"kind": "hardware_key", "expires_at": far})
    assert (
        row["credential"]["kind"] == "hardware_key"
        and row["credential"]["state"] == "unknown"
    )
    row, _ = agent_row(
        {"kind": "hardware_key", "expires_at": far, "last_token_at": iso(NOW)}
    )
    assert row["credential"]["state"] == "ok"


def test_an_unknown_credential_kind_still_warns_when_its_expiry_is_near_or_past():
    row, attention = agent_row(
        {"kind": "hardware_key", "expires_at": iso(NOW + dt.timedelta(days=3))}
    )
    assert row["credential"]["state"] == "expiring" and [
        a["state"] for a in attention
    ] == ["expiring"]
    row, attention = agent_row(
        {"kind": "hardware_key", "expires_at": iso(NOW - dt.timedelta(days=3))}
    )
    assert row["credential"]["state"] == "expired" and [
        a["state"] for a in attention
    ] == ["expired"]


@pytest.mark.parametrize(
    "expires", ["soon", "2027-13-40T00:00:00Z", "", [2027, 2, 1], {"at": "2027-02-01"}]
)
def test_a_certificate_expiry_that_cant_be_read_is_unknown_and_kept_as_given(expires):
    row, attention = agent_row(
        {"kind": "certificate", "expires_at": expires, "last_token_at": iso(NOW)}
    )
    assert (
        row["credential"]["state"] == "unknown"
        and row["credential"]["expires_at"] == expires
    )
    assert attention == []


def test_one_second_past_the_warning_days_a_certificate_is_ok_and_not_listed_for_attention():
    edge = NOW + dt.timedelta(days=run_as.EXPIRING_DAYS)
    row, attention = agent_row(
        {"kind": "certificate", "expires_at": iso(edge + dt.timedelta(seconds=1))}
    )
    assert row["credential"]["state"] == "ok" and attention == []
    row, attention = agent_row(
        {"kind": "certificate", "expires_at": iso(edge - dt.timedelta(seconds=1))}
    )
    assert row["credential"]["state"] == "expiring" and attention[0][
        "expires_at"
    ] == iso(edge - dt.timedelta(seconds=1))


def test_a_certificate_expiring_years_from_now_is_ok():
    row, attention = agent_row(
        {"kind": "certificate", "expires_at": "2099-12-31T23:59:59Z"}
    )
    assert row["credential"]["state"] == "ok" and attention == []


def test_an_expiry_with_no_zone_is_read_as_utc_even_when_this_pc_is_in_another_zone(
    away_from_utc,
):
    # 07:30 UTC is half an hour ago; read as Chicago time it would be 13:30 UTC, still to come.
    row, _ = agent_row({"kind": "certificate", "expires_at": "2027-01-15T07:30:00"})
    assert row["credential"]["state"] == "expired"


def test_an_expired_certificate_is_expired_even_when_its_last_token_request_also_failed():
    row, _ = agent_row(
        {
            "kind": "certificate",
            "expires_at": iso(NOW - dt.timedelta(hours=1)),
            "last_token_error": {"at": iso(NOW), "error": "AADSTS700027"},
        }
    )
    assert row["credential"]["state"] == "expired" and row["credential"]["error"] == ""


def test_a_failed_token_request_outranks_an_expiry_that_is_only_near():
    row, _ = agent_row(
        {
            "kind": "certificate",
            "expires_at": iso(NOW + dt.timedelta(days=2)),
            "last_token_error": {"at": iso(NOW), "error": 7000222},
        }
    )
    assert row["credential"]["state"] == "failed"
    assert row["credential"]["error"] == "7000222" and row["credential"][
        "error_at"
    ] == iso(NOW)


def test_a_failed_token_request_that_names_no_error_is_still_failed_with_empty_text():
    row, attention = agent_row({"kind": "secret", "last_token_error": {"error": None}})
    assert row["credential"]["state"] == "failed" and row["credential"]["error"] == ""
    assert row["credential"]["error_at"] is None and [
        a["state"] for a in attention
    ] == ["failed"]


# ---- run_as: agent records


@pytest.mark.parametrize("identity", ["workload_identity", 7, None])
def test_an_identity_this_app_doesnt_know_has_no_label_and_none_at_all_is_unknown(
    identity,
):
    row, _ = agent_row(None, identity=identity)
    assert row["runs_as"] == {"kind": identity or "unknown", "name": ""}


def test_a_record_without_an_agent_name_has_no_row_and_a_numeric_name_is_read_as_text():
    records = [
        agent(None),
        agent(""),
        {k: v for k, v in agent("x").items() if k != "agent"},
        agent(42),
    ]
    rows = [
        r
        for r in run_as.build("", {"prod": records}, [], {}, NOW)["rows"]
        if r["place"]["kind"] == "agent"
    ]
    assert [r["place"]["name"] for r in rows] == ["42"]


# ---- run_as: Fabric schedules


@pytest.mark.parametrize("owner", [["someone"], "chris@contoso.com", 5, {}])
def test_a_fabric_schedule_owner_that_isnt_an_object_runs_as_unknown(owner):
    [row] = run_as.fabric_rows([{"enabled": True, "owner": owner}], {})
    assert row["runs_as"] == {"kind": "unknown", "name": ""}


def test_a_numeric_workspace_id_finds_its_environment_and_a_schedule_without_enabled_is_left_out():
    rows = run_as.fabric_rows(
        [
            {"enabled": True, "workspace_id": 5, "name": "on"},
            {"workspace_id": 5, "name": "off"},
        ],
        {"5": "prod"},
    )
    assert [(r["place"]["name"], r["env"]) for r in rows] == [("Fabric · on", "prod")]


# ---- overlap: entities and schedules


@pytest.mark.parametrize(
    "entities", [None, "silver_orders", {"silver_orders": True}, 3]
)
def test_entities_that_arent_a_list_are_unknown_rather_than_every_table(entities):
    known, unknown = overlap.placements(
        {"prod": [agent("a", [{"id": "s", "entities": entities}])]}, [], NOW
    )
    assert known == [] and [u["schedule"] for u in unknown] == ["s"]


def test_an_empty_entities_list_is_every_table_and_a_missing_one_is_unknown():
    known, unknown = overlap.placements(
        {"prod": [agent("a", [{"id": "all", "entities": []}, {"id": "old"}])]}, [], NOW
    )
    assert [(k["schedule"], k["entities"]) for k in known] == [("all", None)]
    assert [u["schedule"] for u in unknown] == ["old"]


@pytest.mark.parametrize("schedules", [None, [], "missing"])
def test_an_agent_with_no_schedules_or_none_named_runs_nothing(schedules):
    record = agent("a", schedules)
    if schedules == "missing":
        del record["schedules"]
    assert overlap.placements({"prod": [record]}, [], NOW) == ([], [])


def test_entities_that_arent_text_are_compared_as_text():
    out = overlap.overlaps(
        {"prod": [agent("a", [{"id": "s", "entities": [1, "x"]}])]},
        [pc("p", "prod", ["1"])],
        NOW,
    )
    [o] = out["overlaps"]
    assert o["entities"] == ["1"]


def test_the_same_agent_in_two_environments_never_overlaps_with_itself():
    s = [{"id": "s", "entities": ["x"]}]
    out = overlap.overlaps({"prod": [agent("a", s)], "test": [agent("a", s)]}, [], NOW)
    assert out["overlaps"] == []


def test_two_records_of_the_same_agent_in_one_environment_are_one_place_and_dont_overlap():
    older = agent("a", [{"id": "s1", "entities": ["x"]}], beat="2027-01-15T07:00:00Z")
    newer = agent("a", [{"id": "s2", "entities": ["x"]}])
    known, _ = overlap.placements({"prod": [older, newer]}, [], NOW)
    assert [k["schedule"] for k in known] == ["s2"]
    assert overlap.overlaps({"prod": [older, newer]}, [], NOW)["overlaps"] == []


@pytest.mark.parametrize(
    "when",
    [
        {"kind": "cron", "every_minutes": "hourly"},
        {"kind": "cron", "every_minutes": [5]},
        {"kind": ["cron"]},
        {"kind": "weekly", "weekdays": 5, "times": ["06:00"]},
        {"kind": "daily", "times": "06:00"},
        "every hour",
        ["cron"],
    ],
)
def test_a_schedule_time_that_cant_be_described_has_empty_text_and_is_still_compared(
    when,
):
    out = overlap.overlaps(
        {"prod": [agent("a", [{"id": "s", "entities": ["x"], "when": when}])]},
        [pc("p", "prod", ["x"])],
        NOW,
    )
    [o] = out["overlaps"]
    assert o["schedules"][0]["text"] == ""


def test_a_pc_schedule_without_a_target_or_environment_is_left_out_and_no_entities_means_every_table():
    schedules = [
        {"id": "none"},
        {"id": "noenv", "target": {"kind": "meta"}},
        {"id": "all", "target": {"kind": "meta", "env": "prod", "entities": None}},
    ]
    known, _ = overlap.placements({"prod": []}, schedules, NOW)
    assert [(k["schedule"], k["entities"]) for k in known] == [("all", None)]


def test_a_status_file_that_isnt_an_object_never_reaches_run_as_or_overlap():
    files = {
        "a.json": json.dumps(["agent_status"]).encode(),
        "b.json": b"not json",
        "c.json": json.dumps(agent("etl-01", [])).encode(),
    }
    backend = types.SimpleNamespace(
        list=lambda prefix: sorted(files),
        read=lambda path: types.SimpleNamespace(data=files[path.split("/", 1)[1]]),
    )
    records = read_status(backend)
    assert [r["agent"] for r in records] == ["etl-01"]
    assert overlap.placements({"prod": records}, [], NOW) == ([], [])
    assert [r["place"]["name"] for r in run_as.agent_rows({"prod": records}, NOW)] == [
        "etl-01"
    ]
