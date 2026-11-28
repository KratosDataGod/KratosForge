"""Mutation batch 25: the shortcut command center's rules give exact answers. Counts, CSV cells,
needs, risks and environment twins are what an admin acts on, so each value is pinned."""

from __future__ import annotations

import csv
import io

import pytest

from fabric_desktop import shortcut_center as center


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


LAKEHOUSES = [
    {"workspace_id": "ws-dev", "workspace_name": "Sales Dev", "lakehouse_id": "lh-1", "lakehouse_name": "LH_Silver"},
    {"workspace_id": "ws-test", "workspace_name": "Sales Test", "lakehouse_id": "lh-1t", "lakehouse_name": "LH_Silver"},
    {"workspace_id": "ws-prod1", "workspace_name": "Sales Prod", "lakehouse_id": "lh-1p", "lakehouse_name": "LH_Silver"},
    {"workspace_id": "ws-dev2", "workspace_name": "Finance Dev", "lakehouse_id": "lh-gold-dev", "lakehouse_name": "LH_Gold"},
    {"workspace_id": "ws-prod", "workspace_name": "Finance Prod", "lakehouse_id": "lh-gold", "lakehouse_name": "LH_Gold"},
    {"workspace_id": "ws-shared", "workspace_name": "Platform Shared", "lakehouse_id": "lh-cal", "lakehouse_name": "LH_Calendar"},
]
SRC = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/supplier"}
DETAIL = {"workspace_id": "ws-dev", "item_id": "lh-1", "where": "Tables/dbo", "name": "supplier", "source": SRC,
          "lakehouse_name": "LH_Silver", "workspace_name": "Sales Dev"}


# ---- keys, probes and rules


def test_a_storage_target_key_is_its_type_and_location_lowercased():
    assert center.target_key({"target_type": "AdlsGen2", "target": "https://Acct.dfs/C/"}) == "adlsgen2:https://acct.dfs/c"


@pytest.mark.parametrize("host_status", [401, 403])
def test_a_lakehouse_you_cant_read_isnt_checked(host_status):
    got = center.health_from_probe({"target_type": "OneLake"}, host_status, 200)
    assert got == {"health": "not_checked", "detail": "You can't read the lakehouse it's in, so it can't be checked"}


def test_no_allowed_connection_list_blocks_no_connection():
    rules = {"connections": "block", "storage_role": "off"}
    got = center.check_rules(rules, {"target_type": "AdlsGen2", "connection_id": "c1"}, is_admin=True)
    assert got == {"blocked": [], "warnings": []}


def test_a_lakehouse_without_a_schema_is_found_by_both_table_paths():
    idx = center.port_index([{"id": "p1", "name": "Sales", "ports": [{"lakehouse_id": "LH", "workspace_id": "WS",
                                                                       "table_name": "orders"}]}])
    assert sorted(idx) == ["onelake:ws/lh/tables/dbo/orders", "onelake:ws/lh/tables/orders"]


def test_a_storage_shortcut_has_no_same_environment_target():
    s = {"host_env": "dev", "target_lakehouse_name": "LH_Gold", "target_type": "AdlsGen2", "item_id": "x"}
    assert center.same_env_target(s, {"ws-dev2": "dev"}, [{"workspace_id": "ws-dev2", "name": "LH_Gold",
                                                           "lakehouse_id": "lh-gold-dev"}]) is None


# ---- risks, acceptance and needs


def test_cross_environment_risk_text_says_which_way_it_goes():
    rows = [{"lakehouse_id": "a", "full_path": "Tables/x", "host_env": "prod", "target_env": "dev", "target_type": "S3",
             "target": "s3://one"},
            {"lakehouse_id": "b", "full_path": "Tables/y", "host_env": "dev", "target_env": "test", "target_type": "S3",
             "target": "s3://two"}]
    got = center.risks(rows, {})
    assert got["a|Tables/x"] == [{"kind": "cross_env", "text": "Prod reads Dev: Prod depends on data that isn't released"}]
    assert got["b|Tables/y"] == [{"kind": "cross_env", "text": "Dev reads Test"}]


def test_an_accepted_risk_records_who_why_and_when_and_blanks_what_is_missing():
    rows = [{"id": "s1", "risks": [{"kind": "duplicate"}, {"kind": "cross_env"}]}]
    center.accept_risks(rows, [{"shortcut_id": "s1", "kind": "duplicate", "by": "ana", "reason": "ok", "at": "t1"},
                               {"shortcut_id": "s1", "kind": "cross_env"}])
    assert rows[0]["risks"][0]["accepted"] == {"by": "ana", "reason": "ok", "at": "t1"}
    assert rows[0]["risks"][1]["accepted"] == {"by": "", "reason": "", "at": ""}


def test_only_waiting_shortcut_requests_you_can_decide_are_needs():
    reqs = [{"id": "r1", "kind": "shortcut", "status": "waiting", "can_decide": True, "reason": "for the report",
             "detail": {"name": "sup", "workspace_name": "W", "lakehouse_name": "L"}},
            {"id": "r2", "kind": "shortcut", "status": "waiting", "can_decide": False, "detail": {}},
            {"id": "r3", "kind": "shortcut", "status": "approved", "can_decide": True, "detail": {}},
            {"id": "r4", "kind": "workspace", "status": "waiting", "can_decide": True, "detail": {}}]
    assert center.needs([], reqs) == [{"kind": "request", "id": "r1", "title": "Someone asks for shortcut sup",
                                      "detail": "In W / L: for the report"}]


# ---- counts, readers and CSV


def test_counts_are_exact():
    rows = [{"lakehouse_id": "L1", "host_workspace_id": "W1", "health": "broken", "target_type": "OneLake",
             "risks": [{"kind": "duplicate"}]},
            {"lakehouse_id": "L1", "host_workspace_id": "W1", "health": "unreachable", "target_type": "AdlsGen2",
             "risks": [{"kind": "duplicate", "accepted": {"by": "a"}}]},
            {"lakehouse_id": "L2", "host_workspace_id": "W2", "health": None, "target_type": "OneLake"},
            {"lakehouse_id": "L2", "host_workspace_id": "W2", "health": "healthy", "target_type": "OneLake"},
            {"lakehouse_id": "L3", "host_workspace_id": "W2", "health": "not_checked", "target_type": "S3"}]
    reqs = [{"kind": "shortcut", "status": "waiting", "can_decide": True},
            {"kind": "shortcut", "status": "waiting", "can_decide": False},
            {"kind": "shortcut", "status": "approved", "can_decide": True},
            {"kind": "shortcut", "status": "rejected", "can_decide": True},
            {"kind": "other", "status": "waiting", "can_decide": True},
            {"kind": "workspace", "status": "waiting", "can_decide": True}]
    assert center.counts(rows, [{}], reqs) == {"total": 5, "lakehouses": 3, "workspaces": 2, "broken": 1,
                                               "unreachable": 1, "not_checked": 2, "connection": 2, "risky": 1,
                                               "changed_7d": 1, "waiting": 1}


def test_readers_missing_from_the_target_are_listed_up_to_fifty():
    here = [{"id": str(i)} for i in range(51)]
    got = center.readers_gap(here, [], "identity")
    assert got["gap"] == 51 and len(got["missing"]) == 50 and got["target"] == 0


def _csv(rows):
    return list(csv.reader(io.StringIO(center.to_csv(rows))))[1:]


def test_csv_rows_show_onelake_and_storage_targets_in_their_own_columns():
    onelake = {"name": "=evil", "path": "Tables/x", "workspace_name": "W", "lakehouse_name": "L", "host_env": "dev",
               "target_type": "OneLake", "target": "not shown", "target_path": "Tables/y", "target_workspace_name": "TW",
               "target_lakehouse_name": "TL", "target_env": "prod", "product": {"name": "P", "status": "deprecated"},
               "health": "broken", "health_detail": "gone", "checked_at": "t1",
               "risks": [{"kind": "cross_env", "text": "Dev reads Prod"}], "used_by": [{"entity": "a"}, {"entity": "b"}],
               "last_change": {"kind": "created", "at": "t0", "how": "app", "by_user": "ana"}}
    storage = {"name": "s", "path": "Files/s", "target_type": "AdlsGen2", "target": "https://acct.example/c",
               "target_path": "hidden", "target_workspace_name": "hidden", "target_lakehouse_name": "hidden",
               "product": {"name": "Q", "status": "active"}, "last_change": {"how": "app"}}
    checked = {"name": "c", "last_change": {"how": "check", "kind": "health"}}
    seen = {"name": "d", "last_change": {"kind": "seen"}}
    assert _csv([onelake, storage, checked, seen]) == [
        ["'=evil", "Tables/x", "W", "L", "Dev", "OneLake", "", "Tables/y", "TW", "TL", "Prod", "P (deprecated)", "Broken",
         "gone", "t1", "Dev reads Prod", "a; b", "created", "t0", "ana"],
        ["s", "Files/s", "", "", "", "AdlsGen2", "https://acct.example/c", "", "", "", "", "Q", "Not checked", "", "",
         "", "", "", "", "KratosForge"],
        ["c", "", "", "", "", "", "", "", "", "", "", "", "Not checked", "", "", "", "", "health", "", "Health check"],
        ["d", "", "", "", "", "", "", "", "", "", "", "", "Not checked", "", "", "", "", "seen", "", "Seen at scan"],
    ]


# ---- environment twins


def test_a_workspace_the_platform_names_an_environment_has_twins_whatever_its_name():
    plan = center.env_twins(DETAIL | {"workspace_id": "ws-x", "workspace_name": "Sales"}, {"ws-x": "dev"}, LAKEHOUSES, [])
    assert plan["env"] == "dev"


def test_a_shared_target_keeps_its_workspace_name():
    shared = DETAIL | {"source": {"type": "oneLake", "workspace_id": "ws-shared", "item_id": "lh-cal", "path": "Tables/cal"}}
    places = center.env_twins(shared, {}, LAKEHOUSES, [])["places"]
    assert [p["target_workspace_name"] for p in places] == ["Platform Shared", "Platform Shared"]


def test_a_missing_target_twin_names_the_targets_workspace():
    test, prod = center.env_twins(DETAIL, {}, LAKEHOUSES, [])["places"]
    assert test == {"env": "test", "why": "No Test match for its target LH_Gold in Finance Dev"}
    assert prod["source"]["item_id"] == "lh-gold"
