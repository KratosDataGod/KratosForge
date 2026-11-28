"""Mutation batch 45: time_zones sorts Windows zones by the UTC offset in their display name."""

from __future__ import annotations

from fabric_desktop import time_zones as tz


def test_an_offset_is_hours_times_sixty_plus_minutes():
    assert tz._offset_minutes("(UTC+05:30) Chennai, Kolkata") == 330
    assert tz._offset_minutes("(UTC-03:30) Newfoundland") == -210
    assert tz._offset_minutes("(UTC+12:45) Chatham Islands") == 765


def test_plain_utc_and_an_unreadable_name_are_offset_zero():
    assert tz._offset_minutes("(UTC) Coordinated Universal Time") == 0
    assert tz._offset_minutes("Somewhere") == 0


def test_zones_sort_by_offset_and_utc_sits_between_minus_and_plus():
    pairs = [("India Standard Time", "(UTC+05:30) Chennai"), ("UTC", "(UTC) Coordinated Universal Time"),
             ("Nepal Standard Time", "(UTC+05:45) Kathmandu"), ("Morocco Standard Time", "(UTC+01:00) Casablanca"),
             ("Azores Standard Time", "(UTC-01:00) Azores"), ("Sri Lanka Standard Time", "(UTC+05:31) Test")]
    got = tz.time_zones(lambda: (pairs, "UTC"))
    assert [z["id"] for z in got["zones"]] == ["Azores Standard Time", "UTC", "Morocco Standard Time",
                                              "India Standard Time", "Sri Lanka Standard Time",
                                              "Nepal Standard Time"]
    assert got["here"] == "UTC"
