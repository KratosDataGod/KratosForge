"""Set-asides made within one clock tick each keep their own copy.

Windows' clock can return the same time for calls made close together, and the copies are named by
that time. A second set-aside in the same tick used to replace the first copy, silently losing a
damaged file that was meant to be kept (seen on windows-latest: 7 set-asides left 3 copies)."""

from __future__ import annotations

import datetime as dt
import types

from fabric_desktop import jsonfile

TICK = dt.datetime(2026, 11, 12, 12, 0, 0, 500_000, tzinfo=dt.timezone.utc)


class _FrozenClock(dt.datetime):
    @classmethod
    def now(cls, tz=None):  # every call lands in the same tick
        return TICK


def _freeze(monkeypatch):
    monkeypatch.setattr(jsonfile, "dt", types.SimpleNamespace(
        datetime=_FrozenClock, timezone=dt.timezone, timedelta=dt.timedelta))


def test_set_asides_in_one_tick_each_keep_their_own_copy(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    f = tmp_path / "s.json"
    made = []
    for i in range(jsonfile.KEEP_SET_ASIDE):
        f.write_text(f'{{"torn": {i}', encoding="utf-8")
        made.append(jsonfile.set_aside(f))
    assert len(set(made)) == jsonfile.KEEP_SET_ASIDE
    assert sorted(p.read_text(encoding="utf-8") for p in made) == [f'{{"torn": {i}' for i in range(5)]
    assert made == sorted(made)  # later copies still sort later, so pruning keeps the newest


def test_pruning_after_many_set_asides_in_one_tick_keeps_the_newest(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    f = tmp_path / "s.json"
    made = []
    for i in range(jsonfile.KEEP_SET_ASIDE + 3):
        f.write_text(f'{{"torn": {i}', encoding="utf-8")
        made.append(jsonfile.set_aside(f))
    left = sorted(tmp_path.glob("s.json.corrupt-*"))
    assert left == made[-jsonfile.KEEP_SET_ASIDE:]


def test_keep_copies_in_one_tick_dont_replace_each_other(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    f = tmp_path / "s.json"
    f.write_text("{first", encoding="utf-8")
    one = jsonfile.set_aside(f, keep=True)
    f.write_text("{second", encoding="utf-8")
    two = jsonfile.set_aside(f, keep=True)
    assert one != two
    assert (one.read_text(encoding="utf-8"), two.read_text(encoding="utf-8")) == ("{first", "{second")


def test_a_hand_named_copy_with_an_impossible_date_doesnt_stop_a_set_aside(tmp_path):
    f = tmp_path / "s.json"
    odd = tmp_path / "s.json.corrupt-99991399T999999999999Z"
    odd.write_text("kept by hand", encoding="utf-8")
    f.write_text("{torn", encoding="utf-8")
    dest = jsonfile.set_aside(f)
    assert dest.read_text(encoding="utf-8") == "{torn" and odd.read_text(encoding="utf-8") == "kept by hand"
