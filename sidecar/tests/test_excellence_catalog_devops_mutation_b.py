"""excellence/catalog_devops at its edges, from the mutation run (batch 468): which text the evidence
keeps, the workspace's name and the record's source, a provider the app doesn't name, and conflicts
counted only when there are some."""

from __future__ import annotations

from fabric_desktop.excellence import catalog_devops as dv
from test_excellence_devops import AT, WS, WSID, change, conn, outcome_of, run, status


def payload(connection, st=None, ws=WS):
    return dv.evidence(ws, connection, st, collected_at=AT)[0]


def test_text_is_cut_at_256_characters_and_refused_with_a_control_character():
    assert payload(conn(branch="b" * 300))["payload"]["branch"] == "b" * 256
    assert payload(conn(branch="b" * 256))["payload"]["branch"] == "b" * 256
    assert payload(conn(branch="my branch"))["payload"]["branch"] == "my branch"  # a space is text
    assert payload(conn(branch="café\u0080x"))["payload"]["branch"] == "café\u0080x"  # 128 is past DEL
    for bad in ("a\x1fb", "a\x7fb", "a\x00b"):
        assert payload(conn(branch=bad))["payload"]["branch"] == ""


def test_the_record_names_the_workspace_and_says_what_was_read():
    with_status = payload(conn(), status())
    assert with_status["subject"] == {"type": "workspace", "id": WSID, "name": "Sales Prod"}
    assert with_status["source"] == f"{dv.GET_CONNECTION} and {dv.GET_STATUS}"
    assert payload(conn(), None)["source"] == dv.GET_CONNECTION
    assert payload(conn("Connected"), status())["source"] == dv.GET_CONNECTION  # never synced: status not kept
    assert payload(conn(), status(), ws={"id": WSID, "name": "Old name"})["subject"]["name"] == "Old name"


def test_a_provider_the_app_doesnt_name_is_shown_as_fabric_gives_it():
    o = outcome_of(run(conn(provider="GitLab", branch="main"), status()), "FEA-DEVOPS-003")
    assert o["text"] == "Connected to GitLab branch main and synced."
    o = outcome_of(run(conn(provider="", branch=""), status()), "FEA-DEVOPS-003")
    assert o["text"] == "Connected to a Git branch and synced."


def test_conflicts_are_mentioned_only_when_there_are_some():
    dirty = outcome_of(run(conn(), status(change("Modified", None))), "FEA-DEVOPS-004")
    assert dirty["outcome"] == "fail" and "conflict" not in dirty["text"]
    assert dirty["detail"] == {"uncommitted": 1, "conflicts": 0}
    behind = outcome_of(run(conn(), status(change(None, "Modified"), change("Modified", "Modified", "Conflict"))),
                        "FEA-DEVOPS-005")
    assert behind["text"].endswith(", 1 of them in conflict.") and behind["detail"] == {"incoming": 2, "conflicts": 1}
