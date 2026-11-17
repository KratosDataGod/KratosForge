"""Comments on notebook cells (comments_service.py): the exact limits, line ranges at their edges,
who "you" are when signed out, and this PC's id when two first comments race."""

from __future__ import annotations

import threading

import pytest

from fabric_desktop import comments_service as cs
from tests.test_notebook_comments import Auth, svc  # noqa: F401  (svc is a fixture)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _thread(n: int, replies: int = 1) -> dict:
    return {"id": f"t{n}", "cell_id": "c1", "lines": None, "quote": "", "resolved": False,
            "comments": [{"id": f"t{n}-{r}", "author": "a", "author_id": "aad:x", "at": "", "text": "x"}
                         for r in range(replies)]}


def _seed(s, threads: list[dict]) -> None:
    s.notebooks.update_extra("NB", cs.KEY, lambda _saved, _nb: threads)


# ---- the limits, exactly

def test_a_comment_of_exactly_4000_characters_is_taken_and_4001_refused(svc):
    out = svc.notebook_comment_add("NB", "c1", "x" * 4000)
    assert len(out["threads"][0]["comments"][0]["text"]) == 4000
    with pytest.raises(ValueError) as e:
        svc.notebook_comment_add("NB", "c1", "x" * 4001)
    assert str(e.value) == "A comment can be up to 4000 characters."


def test_a_notebook_takes_500_threads_and_no_more(svc):
    _seed(svc, [_thread(n) for n in range(499)])
    assert len(svc.notebook_comment_add("NB", "c1", "the 500th")["threads"]) == 500
    with pytest.raises(ValueError) as e:
        svc.notebook_comment_add("NB", "c1", "the 501st")
    assert str(e.value) == "A notebook can have up to 500 comment threads. Delete resolved ones first."


def test_a_thread_takes_200_comments_and_no_more(svc):
    _seed(svc, [_thread(0, replies=199)])
    assert len(svc.notebook_comment_reply("NB", "t0", "the 200th")["threads"][0]["comments"]) == 200
    with pytest.raises(ValueError) as e:
        svc.notebook_comment_reply("NB", "t0", "the 201st")
    assert str(e.value) == "A thread can have up to 200 comments. Start a new thread."


def test_the_quote_kept_with_a_thread_is_the_first_500_characters_of_its_lines(svc):
    svc.notebooks.save("NB", [{"type": "code", "source": "q" * 700 + "\nb = 2", "cell_id": "c1"}])
    t = svc.notebook_comment_add("NB", "c1", "long line", lines=[1, 2])["threads"][0]
    assert t["quote"] == "q" * 500 and t["lines"] == [1, 2]


def test_ids_are_twelve_hex_characters(svc):
    t = svc.notebook_comment_add("NB", "c1", "hi")["threads"][0]
    for i in (t["id"], t["comments"][0]["id"]):
        assert len(i) == 12 and int(i, 16) >= 0


# ---- line ranges at their edges

@pytest.mark.parametrize("lines", [[1, 1], [2, 2], [3, 3], [1, 3]])
def test_a_range_of_one_line_or_all_of_them_is_taken(svc, lines):
    t = svc.notebook_comment_add("NB", "c1", "ok", lines=lines)["threads"][0]
    code = ["a = 1", "b = 2", "c = 3"]
    assert t["lines"] == lines and t["quote"] == "\n".join(code[lines[0] - 1:lines[1]])


@pytest.mark.parametrize("lines", [[0, 1], [0, 0], [3, 2], [2, 1], [True, True], [1, True], ["1", "2"]])
def test_a_range_from_line_0_backwards_or_not_of_whole_numbers_is_refused(svc, lines):
    with pytest.raises(ValueError) as e:
        svc.notebook_comment_add("NB", "c1", "no", lines=lines)
    assert str(e.value) == "lines is [first, last], counted from 1."


# ---- who "you" are

class NoOne:
    """Signed out: an auth that knows no one."""

    def signed_in_user(self):
        return None


def test_an_auth_with_no_one_signed_in_comments_as_this_pc(svc):
    svc._auth = NoOne()
    out = svc.notebook_comment_add("NB", "c1", "hi")
    c = out["threads"][0]["comments"][0]
    assert c["author"] == "" and c["author_id"].startswith("pc:") and out["me"] == {"name": "", "id": c["author_id"]}


def test_resolve_resolves_unless_told_otherwise(svc):
    tid = svc.notebook_comment_add("NB", "c1", "hi")["threads"][0]["id"]
    t = svc.notebook_comment_resolve("NB", tid)["threads"][0]
    assert t["resolved"] is True and t["resolved_by"] == "ann@contoso.com"


def test_this_pcs_id_is_made_with_its_folder(tmp_path):
    path = tmp_path / "a" / "b" / cs.PC_ID_FILE
    made = cs._make_pc_id(path)
    assert len(made) == 32 and path.read_text(encoding="utf-8") == made


def test_a_second_first_comment_waits_for_the_first_ones_id(tmp_path):
    """The other writer has made the file but not written to it yet: this one waits for its id."""
    path = tmp_path / cs.PC_ID_FILE
    path.write_text("", encoding="utf-8")
    timer = threading.Timer(0.2, lambda: path.write_text("theirs", encoding="utf-8"))
    timer.start()
    try:
        assert cs._make_pc_id(path) == "theirs"
    finally:
        timer.cancel()
        timer.join()
