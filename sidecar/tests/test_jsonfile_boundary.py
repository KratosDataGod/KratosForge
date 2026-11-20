"""Set-aside copies at their boundary (mutation batch 207, a boundary re-score of batch 3,
docs/quality/mutation-testing.md): a copy stamped exactly a day ahead still counts in the ordering."""

from __future__ import annotations

import datetime as dt
import types

from fabric_desktop import jsonfile

NOW = dt.datetime(2026, 11, 19, 12, 0, tzinfo=dt.timezone.utc)


class _Clock(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def _stamp(when: dt.datetime) -> str:
    return f"{when:%Y%m%dT%H%M%S%f}"


def test_a_copy_stamped_exactly_a_day_ahead_still_orders_the_next_one(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonfile, "dt", types.SimpleNamespace(datetime=_Clock, timezone=dt.timezone,
                                                              timedelta=dt.timedelta))
    path = tmp_path / "state.json"
    ahead = NOW + jsonfile._SET_ASIDE_AHEAD
    (tmp_path / f"state.json.corrupt-{_stamp(ahead)}Z").write_text("x")
    assert jsonfile._next_stamp(path) == ahead + dt.timedelta(microseconds=1)
    (tmp_path / f"state.json.corrupt-{_stamp(ahead)}Z").unlink()
    further = ahead + dt.timedelta(microseconds=1)
    (tmp_path / f"state.json.corrupt-{_stamp(further)}Z").write_text("x")
    assert jsonfile._next_stamp(path) == NOW  # beyond a day ahead: not ours, left out
