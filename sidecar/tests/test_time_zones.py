"""PB-048: every Windows time zone for Fabric schedules, read from this PC's registry."""

import sys

import pytest

from fabric_desktop import time_zones as tz


def test_zones_are_sorted_by_utc_offset_with_the_pcs_zone_named():
    pairs = [("Tasmania Standard Time", "(UTC+10:00) Hobart"), ("UTC", "(UTC) Coordinated Universal Time"),
             ("Pacific Standard Time", "(UTC-08:00) Pacific Time (US & Canada)"),
             ("India Standard Time", "(UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi"),
             ("Dateline Standard Time", "(UTC-12:00) International Date Line West"),
             ("AUS Eastern Standard Time", "(UTC+10:00) Canberra, Melbourne, Sydney")]
    out = tz.time_zones(lambda: (pairs, "India Standard Time"))
    assert [z["id"] for z in out["zones"]] == ["Dateline Standard Time", "Pacific Standard Time", "UTC", "India Standard Time",
                                              "AUS Eastern Standard Time", "Tasmania Standard Time"]
    assert out["zones"][0] == {"id": "Dateline Standard Time", "name": "(UTC-12:00) International Date Line West"}
    assert out["here"] == "India Standard Time"


def test_an_unknown_pc_zone_or_unreadable_registry_gives_the_short_list():
    assert tz.time_zones(lambda: ([("UTC", "(UTC) Coordinated Universal Time")], "Gone Standard Time"))["here"] is None
    assert tz.time_zones(lambda: ([("", "x"), ("Odd", "")], None)) == {"zones": [], "here": None}

    def fails():
        raise OSError("access denied")
    assert tz.time_zones(fails) == {"zones": [], "here": None}


def test_off_windows_the_list_is_empty(monkeypatch):
    monkeypatch.setattr(tz.sys, "platform", "linux")
    assert tz.time_zones() == {"zones": [], "here": None}


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows registry")
def test_this_windows_pc_lists_its_zones():
    out = tz.time_zones()
    assert len(out["zones"]) > 100 and any(z["id"] == "UTC" for z in out["zones"])
    assert out["here"] in {z["id"] for z in out["zones"]}


def test_the_registry_reader_says_why_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(OSError, match="only on Windows"):
        tz._registry()
