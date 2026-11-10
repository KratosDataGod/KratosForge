"""A platform repository's files (git/platform_files.py), the paths the main tests don't reach: ids
kept as written, values TOML can't hold, names that can't be file names, files that are links or
aren't text, both in the folder and in a commit, and a domain loop. Pure functions and local files."""

from __future__ import annotations

import os

import pytest

from fabric_desktop.git import platform_files as pf
from fabric_desktop.git.cli import GitError


def _defs(**over):
    defs = {k: {} for k in pf.FOLDERS} | {"admins": [], "glossary": {}, "shortcut_rules": {}}
    return defs | over


def test_a_source_id_that_isnt_a_number_is_kept_as_written():
    assert pf.source_record({"source_id": "first", "code": "CRM"})["source_id"] == "first"
    assert pf.source_record({"code": "CRM"})["source_id"] == ""


def test_a_value_toml_cant_hold_is_refused():
    with pytest.raises(ValueError, match="can't write object to a file"):
        pf.dumps({"x": object()})
    assert pf.dumps({"a": [1, None, 2], "b": [{"c": None, "d": True}, 3]}) == "a = [1, 2]\nb = [{ d = true }, 3]\n"


def test_an_object_whose_name_cant_be_a_file_name_is_refused():
    files, refused = pf.to_files(_defs(domains={"../up": {"id": "../up"}, "Bad Name": {"id": "x"}}))
    assert {r["id"] for r in refused} == {"../up", "Bad Name"} and not any(f.startswith("domains/") for f in files)


# ---- reading the folder


@pytest.mark.skipif(os.name == "nt", reason="links need privileges on Windows")
def test_a_file_that_is_a_link_or_isnt_toml_is_reported(tmp_path):
    (tmp_path / "domains").mkdir()
    (tmp_path / "domains" / "sales.toml").write_text("id = ")
    (tmp_path / "elsewhere.toml").write_text("admins = []\n")
    (tmp_path / "admins.toml").symlink_to(tmp_path / "elsewhere.toml")
    out = pf.read_repo(tmp_path)
    assert out["domains"] == {} and out["admins"] is None
    assert any(p.startswith("domains/sales.toml can't be read") for p in out["problems"])
    assert "admins.toml is a link, so it isn't read." in out["problems"]


def test_committed_text_that_isnt_toml_is_reported():
    out = pf.read_repo("unused", ({"glossary.toml": "term = ["}, ["earlier problem"]))
    assert out["glossary"] is None and out["problems"][0] == "earlier problem"
    assert out["problems"][1].startswith("glossary.toml can't be read")


# ---- reading a commit


class FakeRepo:
    """Git's answers for ls-tree and cat-file."""

    def __init__(self, entries, unreadable=()):
        self.entries, self.unreadable = entries, set(unreadable)

    def git(self, *args, read_only=False):
        if args[0] == "ls-tree":
            return "".join(f"{mode} {kind} {sha}\t{rel}\0" for mode, kind, sha, rel in self.entries)
        if args[2] in self.unreadable:
            raise GitError("bad object", kind="unknown")
        return f"text of {args[2]}"


def test_a_commit_with_a_link_a_file_it_cant_read_and_files_the_reader_skips():
    repo = FakeRepo([("120000", "blob", "s1", "admins.toml"), ("100644", "blob", "s2", "glossary.toml"),
                     ("100644", "blob", "s3", "domains/sales.toml"), ("100644", "blob", "s4", "domains/.gitkeep"),
                     ("040000", "tree", "s5", "domains/deeper")], unreadable={"s2"})
    texts, problems = pf.committed_files(repo)
    assert texts == {"domains/sales.toml": "text of s3"}
    assert problems[0] == "admins.toml is a link, so it isn't read."
    assert problems[1].startswith("glossary.toml can't be read")


# ---- export and plan helpers


def test_an_existing_file_that_isnt_text_is_written_over(tmp_path):
    (tmp_path / "admins.toml").write_bytes(b"\xff\xfe not utf-8")
    out = pf.export(tmp_path, _defs(admins=["admin@contoso.com"]), set())
    assert "admins.toml" in out["written"] and "admin@contoso.com" in (tmp_path / "admins.toml").read_text()


def test_a_domain_loop_counts_as_deep_and_ends():
    assert pf._depth("a", {"a": "b", "b": "a"}) == 2
    assert pf._depth("top", {}) == 0
