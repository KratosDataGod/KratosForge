"""VS Code pairing: the request limits at their exact values, what each answer hands back, and when a
pairing stops working. Mutation batch 60."""

from __future__ import annotations

import json
import re

import pytest
from fabric_desktop import editor_pairing as ep
from fabric_desktop.editor_pairing import Pairings


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _p(tmp_path, clock=None):
    return Pairings(tmp_path, clock=clock or Clock())


def test_the_limits_are_as_documented():
    assert ep.KEEP == {"session": None, "8h": 28_800, "7d": 604_800}
    assert ep.REQUEST_SECONDS == 120 and ep.MAX_WAITING == 3


def test_shown_text_drops_control_characters_and_is_cut():
    assert ep._text("Kratos\x07Forge\nfor VS Code", 9) == "KratosFor"
    assert ep._text(None, 10) == "" and ep._text(42, 10) == ""


def test_a_code_is_two_groups_of_three_letters():
    for _ in range(20):
        code = ep.new_code()
        assert re.fullmatch(r"[A-Z2-9]{3}-[A-Z2-9]{3}", code), code
        assert set(code.replace("-", "")) <= set(ep.CODE_LETTERS)


@pytest.mark.parametrize("window", [None, "", 7, "C:/work\x00/repo", "C:/work\t/repo"])
def test_a_window_without_a_clean_folder_path_is_refused(tmp_path, window):
    with pytest.raises(PermissionError):
        _p(tmp_path).request(window, "ext")


def test_a_request_gives_its_id_code_and_lifetime(tmp_path):
    p = _p(tmp_path)
    got = p.request("C:/Users/é/repo", "ext")
    assert re.fullmatch(r"pq_[0-9a-f]{16}", got["id"]) and got["expires_in"] == 120
    assert p.waiting() == [{"id": got["id"], "code": got["code"], "window": "C:/Users/é/repo", "client": "ext",
                            "at": 1000.0}]


def test_the_client_name_is_cleaned_cut_to_80_or_named_for_it(tmp_path):
    p = _p(tmp_path)
    p.request("C:/a", "x" * 81)
    p.request("C:/b", "")
    p.request("C:/c", "Ext\x1bension")
    clients = [r["client"] for r in p.waiting()]
    assert clients == ["x" * 80, "An unnamed VS Code extension", "Extension"]


def test_three_requests_may_wait_and_a_fourth_is_refused(tmp_path):
    p = _p(tmp_path)
    ids = [p.request(f"C:/w{i}", "ext")["id"] for i in range(3)]
    with pytest.raises(PermissionError):
        p.request("C:/w3", "ext")
    # An answered request no longer waits, so it makes room.
    p.decide(ids[0], False)
    assert p.request("C:/w4", "ext")["id"]
    assert len(p.waiting()) == 3


def test_a_request_lasts_exactly_120_seconds(tmp_path):
    clock = Clock()
    p = _p(tmp_path, clock)
    rid = p.request("C:/w", "ext")["id"]
    clock.now += 120
    assert p.collect(rid) == {"state": "waiting"}
    clock.now += 1
    assert p.collect(rid) == {"state": "expired"}


def test_collect_answers_each_state_once(tmp_path):
    p = _p(tmp_path)
    assert p.collect(123) == {"state": "expired"} and p.collect("pq_unknown") == {"state": "expired"}
    denied = p.request("C:/d", "ext")["id"]
    assert p.decide(denied, False) == {"paired": False}
    assert p.collect(denied) == {"state": "denied"}
    assert p.collect(denied) == {"state": "expired"}
    paired = p.request("C:/p", "ext")["id"]
    p.decide(paired, True, keep="8h")
    first = p.collect(paired)
    assert first["state"] == "paired" and first["keep"] == "8h"
    assert re.fullmatch(r"kfp_[A-Za-z0-9_-]{43}", first["token"])
    assert p.collect(paired) == {"state": "expired"}


def test_decide_checks_keep_and_that_the_request_still_waits(tmp_path):
    p = _p(tmp_path)
    rid = p.request("C:/w", "ext")["id"]
    with pytest.raises(ValueError):
        p.decide(rid, True, keep="forever")
    with pytest.raises(LookupError):
        p.decide("pq_unknown", True)
    p.decide(rid, False)
    with pytest.raises(LookupError):
        p.decide(rid, True)
    other = p.request("C:/o", "ext")["id"]
    got = p.decide(other, True)
    assert got["paired"] is True and re.fullmatch(r"pw_[0-9a-f]{16}", got["id"])


@pytest.mark.parametrize("token", [None, 42, "", "abc", "kfx_123"])
def test_check_refuses_anything_that_isnt_a_pairing_token(tmp_path, token):
    assert _p(tmp_path).check(token) is None


def test_a_kept_pairing_ends_exactly_when_it_expires(tmp_path):
    clock = Clock()
    p = _p(tmp_path, clock)
    rid = p.request("C:/w", "ext")["id"]
    p.decide(rid, True, keep="8h")
    token = p.collect(rid)["token"]
    assert p.check(token)["expires"] == 1000.0 + 28_800
    clock.now = 1000.0 + 28_800 - 1
    assert p.check(token) is not None
    clock.now = 1000.0 + 28_800
    assert p.check(token) is None


def test_a_saved_record_with_a_bad_expiry_counts_as_expired(tmp_path):
    p = _p(tmp_path)
    assert p._expired({"expires": None}) is False
    assert p._expired({"expires": "tomorrow"}) is True
    assert p._expired({"expires": 999.0}) is True and p._expired({"expires": 1001.0}) is False


def test_the_saved_file_must_be_a_list_of_records_with_a_hash(tmp_path):
    p = _p(tmp_path)
    (tmp_path / "pairings.json").write_text(json.dumps({"hash": "x"}), encoding="utf-8")
    assert p._saved() == []
    (tmp_path / "pairings.json").write_text(json.dumps([{"hash": "x"}, "y", {"hash": 1}, {"id": "z"}]),
                                            encoding="utf-8")
    assert p._saved() == [{"hash": "x"}]


def test_ending_everything_writes_no_file_that_wasnt_there(tmp_path):
    p = _p(tmp_path)
    p.request("C:/w", "ext")
    p.end_all()
    assert not (tmp_path / "pairings.json").exists() and p.waiting() == []
    rid = p.request("C:/w", "ext")["id"]
    p.decide(rid, True, keep="7d")
    p.end_all()
    assert json.loads((tmp_path / "pairings.json").read_text(encoding="utf-8")) == []
