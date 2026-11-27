"""Mutation batch 261: excellence/catalog_devops.py (docs/quality/mutation-testing.md). The survivors were
which characters make a Git value unsafe to keep (control characters and DEL, not a space) and the
256-character cut, a workspace named only by ``name``, the record's source naming Get Status only when
the status was read, a provider the app has no display name for, and the conflict count on a workspace
that is behind its branch."""

from __future__ import annotations

from fabric_desktop.excellence import catalog_devops as dv
from fabric_desktop.excellence import controls as ct

AT = "2026-11-19T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
RID = "ev-0000000000000001"


def conn(provider: str = "GitHub", branch: str = "main", state: str = "ConnectedAndInitialized") -> dict:
    return {"gitConnectionState": state, "gitProviderDetails": {"gitProviderType": provider, "branchName": branch}}


def ctx(payload: dict) -> ct.Context:
    return ct.Context({"type": "workspace", "id": WSID}, {dv.KIND: [{"id": RID, "payload": payload}]})


def test_a_space_is_kept_but_a_control_character_or_del_empties_the_value():
    assert dv._short(" release 2026 ") == "release 2026"
    assert dv._short("a!b") == "a!b"  # 33, the first character after the space
    assert dv._short("a\x1fb") == ""  # 31, the last control character
    assert dv._short("a\x7fb") == ""  # DEL
    assert dv._short("a\x80b") == "a\x80b"  # the first character after DEL


def test_a_value_is_cut_to_256_characters():
    assert dv._short("b" * 300) == "b" * 256
    rec = dv.evidence({"id": WSID}, conn(branch="b" * 300), None, collected_at=AT)[0]
    assert rec["payload"]["branch"] == "b" * 256


def test_a_workspace_without_a_display_name_is_named_by_its_name():
    rec = dv.evidence({"id": WSID, "name": "Sales"}, conn(), None, collected_at=AT)[0]
    assert rec["subject"]["name"] == "Sales"
    rec = dv.evidence({"id": WSID, "displayName": "Shown", "name": "Sales"}, conn(), None, collected_at=AT)[0]
    assert rec["subject"]["name"] == "Shown"


def test_the_source_names_get_status_only_when_the_status_was_read():
    rec = dv.evidence({"id": WSID}, conn(), {"changes": []}, collected_at=AT)[0]
    assert rec["source"] == dv.GET_CONNECTION + " and " + dv.GET_STATUS
    rec = dv.evidence({"id": WSID}, conn(), None, collected_at=AT)[0]
    assert rec["source"] == dv.GET_CONNECTION
    rec = dv.evidence({"id": WSID}, conn(state="Connected"), {"changes": []}, collected_at=AT)[0]
    assert rec["source"] == dv.GET_CONNECTION  # no first sync: the status isn't kept


def test_a_provider_without_a_display_name_is_shown_as_fabric_gives_it():
    got = dv._connected(ctx({"state": "ConnectedAndInitialized", "provider": "Bitbucket", "branch": "dev"}))
    assert got["text"] == "Connected to Bitbucket branch dev and synced."
    got = dv._connected(ctx({"state": "ConnectedAndInitialized", "provider": "AzureDevOps", "branch": ""}))
    assert got["text"] == "Connected to Azure DevOps and synced."


def test_a_workspace_behind_its_branch_names_its_conflicts():
    payload = {"state": "ConnectedAndInitialized", "provider": "GitHub", "branch": "main",
               "status": {"uncommitted": 0, "incoming": 3, "conflicts": 2}}
    got = dv._behind(ctx(payload))
    assert got["outcome"] == "fail"
    assert got["text"] == ("3 items have changes in GitHub branch main that aren't in the workspace yet, "
                           "2 of them in conflict.")
    assert got["detail"] == {"incoming": 3, "conflicts": 2}
