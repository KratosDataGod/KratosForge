"""Mutation batch 273: fabric_git.py's field limits, link file reads, three-commit comparison and
Get Status ordering, where the earlier tests didn't look. No Fabric and no Git: a stand-in
repository answers what the comparison asks."""

from __future__ import annotations

import os

import pytest

from fabric_desktop import fabric_git as fg

G1 = "11111111-1111-1111-1111-111111111111"
SHA_A = "a" * 40
SHA_B = "b" * 40


def test_a_delete_character_makes_a_field_empty():
    assert fg._text("ab\x7fcd") == ""
    assert fg._text("ab~cd") == "ab~cd"


def _github(**details):
    return {"gitConnectionState": "ConnectedAndInitialized",
            "gitProviderDetails": {"gitProviderType": "GitHub", **details},
            "gitSyncDetails": {"head": SHA_A, "lastSyncTime": "x" * 60}}


def test_each_connection_field_is_cut_at_its_own_length():
    out = fg.connection(_github(ownerName="o" * 300, repositoryName="r" * 300, branchName="b" * 300,
                                directoryName="d" * 300, customDomainName="h" * 300))
    assert len(out["owner"]) == 100 and len(out["repository"]) == 128 and len(out["branch"]) == 250
    assert len(out["folder"]) == 256 and len(out["host"]) == 100 and len(out["last_sync"]) == 40
    ado = fg.connection({"gitConnectionState": "Connected",
                         "gitProviderDetails": {"gitProviderType": "AzureDevOps", "organizationName": "g" * 300,
                                                "projectName": "p" * 300}})
    assert len(ado["owner"]) == 100 and len(ado["project"]) == 100


def test_a_link_file_that_reads_or_is_a_link_exists(tmp_path):
    (tmp_path / fg.LINK_FILE).write_text(f'[workspaces.dev]\nworkspace_id = "{G1}"\n', encoding="utf-8")
    assert fg.read_links(tmp_path)["exists"] is True
    other = tmp_path / "other"
    other.mkdir()
    try:
        os.symlink(tmp_path / fg.LINK_FILE, other / fg.LINK_FILE)
    except OSError:
        pytest.skip("this PC can't make links")
    got = fg.read_links(other)
    assert got["exists"] is True and got["problem"]


class Repo:
    """What alignment() reads from a repository."""

    def __init__(self, status, head, answers):
        self._status, self._head, self._answers = status, head, answers

    def status(self):
        return self._status

    def head(self):
        return self._head

    def _try(self, *args, **kw):
        return self._answers(args)


def _conn(**kw):
    return {"connected": True, "branch": "main", "synced_head": "", "last_sync": "", "folder": ""} | kw


def test_a_count_git_couldnt_give_is_unknown():
    assert fg._count(Repo({}, None, lambda a: None), "a..b") is None


def test_uncommitted_counts_every_kind_of_change():
    repo = Repo({"branch": "main", "staged": [1, 2], "unstaged": [1], "untracked": [1, 2, 3], "conflicts": [1]},
                None, lambda a: None)
    assert fg.alignment(repo, {"connected": False}, None)["local"]["uncommitted"] == 7


def test_no_synced_commit_is_never_known_here():
    repo = Repo({"branch": "main"}, None, lambda a: "anything")
    out = fg.alignment(repo, _conn(), "origin")
    assert out["workspace"]["known_here"] is False
    assert out["workspace"]["commit"] is None and out["workspace"]["last_sync"] is None


def test_no_local_commit_or_branch_compares_nothing_and_notes_no_branch():
    repo = Repo({"branch": None}, None, lambda a: SHA_B if a[0] == "rev-parse" else "3")
    out = fg.alignment(repo, _conn(), "origin")
    assert out["remote"]["commit"] == SHA_B
    assert "ahead_of_remote" not in out["local"] and "behind_remote" not in out["local"]
    assert not any(n.startswith("You're on") for n in out["notes"])


def _change(name, ws="", remote="", conflict=""):
    return {"itemMetadata": {"itemIdentifier": {"logicalId": G1}, "itemType": "Notebook", "displayName": name},
            "workspaceChange": ws, "remoteChange": remote, "conflictType": conflict}


def test_items_come_in_the_order_a_person_acts_on_them_whatever_their_names():
    out = fg.status({"changes": [_change("a"), _change("b", conflict="SameChanges"),
                                 _change("c", ws="Modified"), _change("d", remote="Modified"),
                                 _change("e", ws="Modified", remote="Modified"),
                                 _change("f", ws="Modified", remote="Modified", conflict="Conflict")]})
    assert [i["state"] for i in out["items"]] == ["conflict", "both_changed", "incoming", "outgoing",
                                                  "same_change", "unchanged"]
    assert out["items"][0]["logical_id"] == G1


def test_an_item_type_is_cut_at_eighty_characters():
    change = _change("x", ws="Added")
    change["itemMetadata"]["itemType"] = "T" * 200
    assert len(fg.status({"changes": [change]})["items"][0]["item_type"]) == 80
