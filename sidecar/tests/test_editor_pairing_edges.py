"""Ending a VS Code pairing that was kept on this PC: its record is removed from the file and its token
stops working, and an id that isn't paired changes nothing."""

import json

from fabric_desktop.editor_pairing import Pairings


def _pair_for_seven_days(p):
    req = p.request("C:/work/repo", "KratosForge for VS Code")
    pid = p.decide(req["id"], True, keep="7d")["id"]
    return pid, p.collect(req["id"])["token"]


def test_ending_a_saved_pairing_removes_it_from_the_file_and_its_token_stops_working(tmp_path):
    p = Pairings(tmp_path)
    pid, token = _pair_for_seven_days(p)
    other, _ = _pair_for_seven_days(p)
    assert p.check(token)["id"] == pid
    assert p.end(pid) is True
    assert p.check(token) is None
    saved = json.loads((tmp_path / "pairings.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in saved] == [other]


def test_ending_an_id_that_isnt_paired_changes_nothing(tmp_path):
    p = Pairings(tmp_path)
    _pair_for_seven_days(p)
    before = (tmp_path / "pairings.json").read_bytes()
    assert p.end("pw_not_there") is False
    assert (tmp_path / "pairings.json").read_bytes() == before
