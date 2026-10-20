"""Comments on notebook cells (comments_service.py): kept in the notebook file, survive the notebook
screen's saves, stay out of what is published to Fabric and out of the Git notebook diff."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit
from fabric_desktop.fabric_run import fabric_ipynb
from fabric_desktop.git.repo import notebook_text
from fabric_desktop.server import METHODS, handle
from fabric_desktop.service import Service


class Auth:
    def __init__(self, user):
        self.user = user

    def signed_in_user(self):
        return self.user


def call(s, method, **params):
    out = handle(s, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def error(s, method, **params):
    out = handle(s, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" in out, out
    return out["error"]


@pytest.fixture
def svc(tmp_path):
    s = Service(client_id="x", root=tmp_path)
    s._auth = Auth("ann@contoso.com")
    s.notebooks.save("NB", [{"type": "code", "source": "a = 1\nb = 2\nc = 3", "cell_id": "c1"},
                            {"type": "markdown", "source": "# Notes", "cell_id": "c2"}])
    return s


def saved(s):
    return json.loads(s.notebooks.path("NB").read_text(encoding="utf-8"))


def test_methods_are_registered_and_only_list_is_read_only():
    names = {"notebook_comments_list", "notebook_comment_add", "notebook_comment_reply", "notebook_comment_edit",
             "notebook_comment_delete", "notebook_comment_resolve"}
    assert names <= METHODS
    assert [n for n in sorted(names) if not audit.audited(n)] == ["notebook_comments_list"]
    assert audit.target({"name": "NB", "text": "secret plan"})["text"] == "(given)"


def test_add_reply_resolve_reopen_are_kept_in_the_file(svc):
    out = call(svc, "notebook_comment_add", name="NB", cell_id="c1", text="Why 2? @bob", lines=[2, 3])
    assert out["me"] == "ann@contoso.com"
    t = out["threads"][0]
    assert t["cell_id"] == "c1" and t["lines"] == [2, 3] and t["quote"] == "b = 2\nc = 3" and not t["resolved"]
    assert t["comments"][0]["author"] == "ann@contoso.com" and t["comments"][0]["text"] == "Why 2? @bob"
    svc._auth = Auth("bob@contoso.com")
    out = call(svc, "notebook_comment_reply", name="NB", thread_id=t["id"], text="  It's the default.  ")
    assert [c["text"] for c in out["threads"][0]["comments"]] == ["Why 2? @bob", "It's the default."]
    out = call(svc, "notebook_comment_resolve", name="NB", thread_id=t["id"], resolved=True)
    assert out["threads"][0]["resolved"] and out["threads"][0]["resolved_by"] == "bob@contoso.com"
    out = call(svc, "notebook_comment_resolve", name="NB", thread_id=t["id"], resolved=False)
    assert not out["threads"][0]["resolved"] and "resolved_by" not in out["threads"][0]
    assert saved(svc)["metadata"]["fabric_desktop"]["comments"] == out["threads"]
    assert call(svc, "notebook_comments_list", name="NB") == out


def test_signed_out_author_is_you(svc):
    svc._auth = None
    out = call(svc, "notebook_comment_add", name="NB", cell_id="c2", text="hi")
    assert out["me"] == "You" and out["threads"][0]["comments"][0]["author"] == "You"
    assert out["threads"][0]["lines"] is None


def test_edit_and_delete_only_own(svc):
    t = call(svc, "notebook_comment_add", name="NB", cell_id="c1", text="first")["threads"][0]
    first = t["comments"][0]["id"]
    svc._auth = Auth("bob@contoso.com")
    reply = call(svc, "notebook_comment_reply", name="NB", thread_id=t["id"], text="bob's")["threads"][0]["comments"][1]["id"]
    assert "only edit your own" in error(svc, "notebook_comment_edit", name="NB", thread_id=t["id"], comment_id=first, text="x")
    assert "only delete your own" in error(svc, "notebook_comment_delete", name="NB", thread_id=t["id"], comment_id=first)
    out = call(svc, "notebook_comment_edit", name="NB", thread_id=t["id"], comment_id=reply, text="bob's, edited")
    assert out["threads"][0]["comments"][1]["text"] == "bob's, edited" and out["threads"][0]["comments"][1]["edited_at"]
    svc._auth = Auth("ann@contoso.com")
    # Ann can't delete the thread while Bob's reply is in it
    assert "Resolve it instead" in error(svc, "notebook_comment_delete", name="NB", thread_id=t["id"], comment_id=first)
    svc._auth = Auth("bob@contoso.com")
    out = call(svc, "notebook_comment_delete", name="NB", thread_id=t["id"], comment_id=reply)
    assert len(out["threads"][0]["comments"]) == 1
    svc._auth = Auth("ann@contoso.com")
    out = call(svc, "notebook_comment_delete", name="NB", thread_id=t["id"], comment_id=first)
    assert out["threads"] == []
    assert "comments" not in saved(svc)["metadata"].get("fabric_desktop", {})


def test_bad_input_changes_nothing(svc):
    before = svc.notebooks.path("NB").read_text(encoding="utf-8")
    assert "Write a comment" in error(svc, "notebook_comment_add", name="NB", cell_id="c1", text="   ")
    assert "isn't saved yet" in error(svc, "notebook_comment_add", name="NB", cell_id="nope", text="x")
    assert "has 3 lines" in error(svc, "notebook_comment_add", name="NB", cell_id="c1", text="x", lines=[2, 9])
    assert "[first, last]" in error(svc, "notebook_comment_add", name="NB", cell_id="c1", text="x", lines=[3, 2])
    assert "up to 4000" in error(svc, "notebook_comment_add", name="NB", cell_id="c1", text="x" * 4001)
    assert "no longer in the notebook" in error(svc, "notebook_comment_reply", name="NB", thread_id="gone", text="x")
    assert svc.notebooks.path("NB").read_text(encoding="utf-8") == before


def test_a_save_from_the_notebook_screen_keeps_comments(svc):
    call(svc, "notebook_comment_add", name="NB", cell_id="c1", text="keep me")
    # The screen sends only cells (it never holds the comments); the second cell is deleted.
    call(svc, "save_notebook", name="NB", cells=[{"type": "code", "source": "a = 10", "cell_id": "c1"}])
    threads = call(svc, "notebook_comments_list", name="NB")["threads"]
    assert [t["comments"][0]["text"] for t in threads] == ["keep me"]
    # A thread on a cell that's gone stays until it's resolved or deleted
    svc.notebooks.save("NB", [{"type": "code", "source": "x", "cell_id": "c9"}])
    assert call(svc, "notebook_comments_list", name="NB")["threads"][0]["cell_id"] == "c1"
    assert "comments" not in json.dumps(svc.notebooks.load("NB"))


def test_comments_stay_out_of_fabric_and_the_git_notebook_diff(svc):
    nb = svc.notebooks.load("NB")
    plain = notebook_text(svc.notebooks.path("NB").read_bytes())
    call(svc, "notebook_comment_add", name="NB", cell_id="c1", text="review note")
    assert notebook_text(svc.notebooks.path("NB").read_bytes()) == plain
    published = json.dumps(fabric_ipynb(svc.notebooks.load("NB")["cells"], None))
    assert "review note" not in published
    assert svc.notebooks.load("NB")["cells"] == nb["cells"]


def test_malformed_saved_comments_are_ignored(svc):
    svc.notebooks.set_extra("NB", "comments", [{"id": 1}, "x", {"id": "t", "cell_id": "c1", "comments": []}])
    assert [t["id"] for t in call(svc, "notebook_comments_list", name="NB")["threads"]] == ["t"]
