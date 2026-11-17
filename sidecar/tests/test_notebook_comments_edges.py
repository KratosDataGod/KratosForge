"""Comments on notebook cells: the refusals and the PC-id race the tests in test_notebook_comments don't reach."""

import pytest

from fabric_desktop import comments_service
from tests.test_notebook_comments import Auth, call, error, saved, svc  # noqa: F401  (svc is a fixture)


def test_a_cell_id_that_is_not_one_is_refused_before_the_file_is_read(svc):
    before = saved(svc)
    assert "isn't a cell id" in error(svc, "notebook_comment_add", name="NB", cell_id="../c1", text="hi")
    assert saved(svc) == before


def test_a_notebook_with_the_most_threads_takes_no_more(svc, monkeypatch):
    monkeypatch.setattr(comments_service, "MAX_THREADS", 1)
    call(svc, "notebook_comment_add", name="NB", cell_id="c1", text="first")
    assert "up to 1 comment threads" in error(svc, "notebook_comment_add", name="NB", cell_id="c1", text="second")
    assert len(saved(svc)["metadata"]["fabric_desktop"]["comments"]) == 1


def test_a_thread_with_the_most_comments_takes_no_more_replies(svc, monkeypatch):
    monkeypatch.setattr(comments_service, "MAX_REPLIES", 2)
    t = call(svc, "notebook_comment_add", name="NB", cell_id="c1", text="first")["threads"][0]
    call(svc, "notebook_comment_reply", name="NB", thread_id=t["id"], text="second")
    assert "Start a new thread" in error(svc, "notebook_comment_reply", name="NB", thread_id=t["id"], text="third")


def test_editing_a_comment_that_is_gone_says_to_reopen(svc):
    t = call(svc, "notebook_comment_add", name="NB", cell_id="c1", text="first")["threads"][0]
    out = error(svc, "notebook_comment_edit", name="NB", thread_id=t["id"], comment_id="nope", text="changed")
    assert "That comment is no longer in the notebook" in out


def test_a_pc_id_file_left_empty_by_another_writer_is_refused_after_the_wait(tmp_path, monkeypatch):
    path = tmp_path / "comment-author-id"
    path.write_text("", encoding="utf-8")  # the other writer created it and never wrote
    clock = iter([0.0, 0.0, 5.0])
    monkeypatch.setattr(comments_service.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(comments_service.time, "sleep", lambda s: None)
    with pytest.raises(ValueError, match="is empty. Delete it and try again"):
        comments_service._make_pc_id(path)
