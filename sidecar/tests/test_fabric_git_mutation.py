"""Mutation tests for a workspace's Git connection (fabric_git.py): the lengths Fabric's fields are cut
to, the link file, the three commits side by side with a fake repository, and the order of Get Status
rows. Found by tools/mutate.py."""

import os

from fabric_desktop import fabric_git as fg

WS = "0f8fad5b-d9cb-469f-a165-70867728950e"
A, B, C = "a" * 40, "b" * 40, "c" * 40


def test_text_with_a_control_character_is_dropped_and_others_are_kept():
    assert fg._text("a\x7fb") == "" and fg._text("a\x80b") == "a\x80b"


def test_each_field_fabric_gives_is_cut_to_its_length():
    def conn(provider, **details):
        return fg.connection({"gitConnectionState": "Connected", "gitProviderDetails": {"gitProviderType": provider, **details},
                              "gitSyncDetails": {"head": A, "lastSyncTime": "t" * 60}})

    gh = conn("GitHub", ownerName="o" * 200, repositoryName="r" * 200, branchName="b" * 300, directoryName="d" * 300,
              customDomainName="h" * 200)
    assert [len(gh[k]) for k in ("owner", "repository", "branch", "folder", "host", "last_sync")] == [100, 128, 250, 256, 100, 40]
    ado = conn("AzureDevOps", organizationName="o" * 200, projectName="p" * 200, repositoryName="r")
    assert (len(ado["owner"]), len(ado["project"])) == (100, 100)


def test_a_link_file_that_is_there_says_so_even_when_it_is_a_link(tmp_path):
    (tmp_path / fg.LINK_FILE).write_text(f'[workspaces.dev]\nworkspace_id = "{WS}"\n', encoding="utf-8")
    got = fg.read_links(tmp_path)
    assert got["exists"] is True and got["envs"]["dev"] == {"workspace_id": WS}
    if os.name != "nt":
        other = tmp_path / "other"
        other.mkdir()
        (other / fg.LINK_FILE).symlink_to(tmp_path / fg.LINK_FILE)
        assert fg.read_links(other)["exists"] is True


class Repo:
    def __init__(self, branch="main", head=A, tracking=B, known=True, counts=None, **status):
        self._branch, self._head, self._tracking, self._known = branch, head, tracking, known
        self._counts, self._status = counts or {}, status

    def status(self):
        return {"branch": self._branch, **self._status}

    def head(self):
        return self._head

    def _try(self, *args):
        if args[0] == "rev-parse":
            return self._tracking
        if args[0] == "cat-file":
            return "" if self._known else None
        return self._counts.get(args[-1], "1")


CONN = {"connected": True, "branch": "main", "synced_head": C, "last_sync": "2026-11-01T10:00:00Z", "folder": "fabric"}


def test_three_commits_line_up_with_counts_only_where_both_ends_are_known():
    out = fg.alignment(Repo(staged=[1], unstaged=[1, 2], untracked=[1], conflicts=[1]), CONN, "origin")
    assert out["local"]["uncommitted"] == 5
    assert out["workspace"] == {"commit": C, "last_sync": "2026-11-01T10:00:00Z", "known_here": True, "folder": "fabric"}
    assert "ahead_of_remote" in out["local"]
    out = fg.alignment(Repo(tracking=""), CONN, "origin")
    assert "ahead_of_remote" not in out["local"] and out["remote"]["commit"] is None


def test_the_branch_note_is_only_for_a_different_branch():
    notes = fg.alignment(Repo(branch="main"), CONN, "origin")["notes"]
    assert not any(n.startswith("You're on") for n in notes)
    assert not any(n.startswith("You're on") for n in fg.alignment(Repo(branch=None), CONN, "origin")["notes"])
    assert any(n.startswith("You're on feature") for n in fg.alignment(Repo(branch="feature"), CONN, "origin")["notes"])


def test_a_count_git_cant_give_is_none():
    assert fg._count(Repo(counts={"x": "3\n"}), "x") == 3
    assert fg._count(Repo(counts={"x": "lots"}), "x") is None
    assert fg._count(Repo(counts={"x": None}), "x") is None


def _change(n, name, ws="", remote="", conflict="", item_type="Notebook"):
    return {"itemMetadata": {"itemIdentifier": {"logicalId": f"00000000-0000-0000-0000-{n:012d}"},
                             "displayName": name, "itemType": item_type},
            "workspaceChange": ws, "remoteChange": remote, "conflictType": conflict}


def test_status_rows_go_conflicts_first_then_both_incoming_outgoing_same_unchanged():
    changes = [_change(1, "a"), _change(2, "b", conflict="SameChanges"), _change(3, "c", ws="Modified"),
               _change(4, "d", remote="Modified"), _change(5, "e", ws="Modified", remote="Modified"),
               _change(6, "f", conflict="Conflict", item_type="x" * 100)]
    st = fg.status({"changes": changes})
    assert [(r["name"], r["state"]) for r in st["items"]] == [
        ("f", "conflict"), ("e", "both_changed"), ("d", "incoming"), ("c", "outgoing"), ("b", "same_change"), ("a", "unchanged")]
    assert st["items"][0]["logical_id"] == "00000000-0000-0000-0000-000000000006"
    assert len(st["items"][0]["item_type"]) == 80

