"""usage.read (usage/__init__.py): what mutation testing found unchecked. The notes a preview gives, the
period an untimed file covers and when it overlaps an earlier import, the dates a refusal names, the
skipped reasons' order and sample, rows that replace earlier cost rows, and the overall period."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import usage
from tests.test_usage_import import COST, HEAD, ITEMS, OPS, read

PERIOD = {"period_start": "2026-10-01", "period_end": "2026-10-14"}
UTC = dt.UTC


def _notes(p):
    return [n for n in p["notes"] if not n.startswith(("Times without", "Each row"))]


def test_the_notes_name_private_columns_the_decimal_mark_and_the_date_order():
    assert _notes(read(OPS)) == ["Not stored: the User column. People's names aren't stored.",
                                 "Numbers read with a point as the decimal mark.", "Dates read month first."]
    assert _notes(read(COST)) == ["Numbers read with a point as the decimal mark.", "Dates read month first."]
    comma = ("Workspace;Item kind;Item name;Operation;Start;End;Total CU (s)\n"
             "W;Notebook;n;Run;16/10/2026 10:00;16/10/2026 10:05;1.880,5\n")
    assert _notes(read(comma)) == ["Numbers read with a comma as the decimal mark.", "Dates read day first."]
    plain = ("Workspace,Item kind,Item name,Operation,Start,End,Total CU (s)\n"
             "W,Notebook,n,Run,2026-10-16T10:00:00Z,2026-10-16T10:05:00Z,12\n")
    assert read(plain)["ready"] and _notes(read(plain)) == []


def test_the_time_zone_note_names_the_zone_chosen():
    p = read(OPS, {"tz": "+02:00"})
    assert p["notes"][0].startswith("Times without a time zone are read as +02:00:")


def test_a_time_zone_that_cant_be_used_is_the_problem():
    p = read(OPS, {"tz": "Europe/Paris"})
    assert p is not None and not p["ready"] and p["problem"] and p["needs"] is None


def test_an_items_period_is_read_month_first_unless_told_and_given_back_as_iso():
    p = read(ITEMS, {"period_start": "10/01/2026", "period_end": "10/14/2026"})
    assert p["ready"]
    assert (p["options"]["period_start"], p["options"]["period_end"]) == ("2026-10-01T00:00:00+00:00",
                                                                          "2026-10-15T00:00:00+00:00")


def test_an_items_period_is_read_in_the_date_order_chosen():
    p = read(ITEMS, {"period_start": "02/10/2026", "period_end": "14/10/2026", "date_order": "dmy"})
    assert (p["options"]["period_start"], p["options"]["period_end"]) == ("2026-10-02T00:00:00+00:00",
                                                                          "2026-10-15T00:00:00+00:00")


def test_an_end_with_a_time_isnt_moved_to_the_next_midnight():
    p = read(ITEMS, {"period_start": "2026-10-01", "period_end": "20261014T10"})
    assert p["options"]["period_end"] == "2026-10-14T10:00:00+00:00"


@pytest.mark.parametrize("given", [{"period_start": "2026-10-01"}, {"period_end": "2026-10-14"}])
def test_an_items_file_with_half_a_period_asks_for_it(given):
    p = read(ITEMS, given)
    layout = usage.PROVIDERS["metrics_app"].layouts[1]
    assert not p["ready"] and p["needs"] == {"option": "period", "choices": {}}
    assert p["problem"] == layout.period_hint and layout.id == "items"


def test_a_period_that_ends_where_it_starts_is_refused():
    p = read(ITEMS, {"period_start": "2026-10-14", "period_end": "2026-10-13"})   # the end counts to the 14th
    assert p["problem"] == "The period ends before it starts."


def _earlier(**change):
    return [{"id": "u-2", "provider": "metrics_app", "layout": "items", "workspaces": ["Sales Dev"],
             "file_name": "old.csv", "imported_at": "2026-10-15T09:30:00", "sha256": "x",
             "period": {"start": "2026-10-10T00:00:00+00:00", "end": "2026-10-20T00:00:00+00:00"}} | change]


def test_only_an_overlapping_period_of_the_same_layout_provider_and_workspaces_is_refused():
    assert read(ITEMS, PERIOD, imports=_earlier())["problem"].startswith(
        "Its period overlaps 'old.csv', imported on 2026-10-15, for the same workspaces")
    for change in ({"workspaces": ["Other"]}, {"layout": "operations"}, {"provider": "cost_management"},
                   {"period": {"start": "2026-10-15T00:00:00+00:00", "end": "2026-10-20T00:00:00+00:00"}}):
        assert read(ITEMS, PERIOD, imports=_earlier(**change))["ready"], change


def test_overlaps_needs_both_ends_of_both_periods_and_touching_isnt_overlapping():
    t = [dt.datetime(2026, 10, d, tzinfo=UTC) for d in (1, 5, 10, 15)]
    assert usage._overlaps((t[0], t[2]), (t[1], t[3])) and usage._overlaps((t[1], t[3]), (t[0], t[2]))
    assert usage._overlaps((t[0], t[3]), (t[1], t[2]))
    assert not usage._overlaps((t[0], t[1]), (t[1], t[2])) and not usage._overlaps((t[1], t[2]), (t[0], t[1]))
    assert not usage._overlaps((t[0], t[1]), (t[2], t[3]))
    for i in range(4):
        ends = [t[0], t[2], t[1], t[3]]
        ends[i] = None
        assert usage._overlaps((ends[0], ends[1]), (ends[2], ends[3])) is False


def test_the_same_file_names_the_day_it_was_imported():
    same = read(OPS, imports=[{"id": "u-1", "sha256": read(OPS)["sha256"], "file_name": "old.csv",
                                "imported_at": "2026-10-16T08:00:00"}])
    assert same["problem"] == "This file was already imported on 2026-10-16 as 'old.csv'."


def test_skipped_reasons_come_most_common_first_with_five_lines_at_most():
    rows = ["W,Notebook,a,Run,10/16/2026 2:00 PM,10/16/2026 1:00 PM,Succeeded,,1,5,0,Billable,e1"]
    rows += [f"W,Notebook,n{i},Run,,,Succeeded,,1,5,0,Billable,s{i}" for i in range(7)]
    rows += [f"W,Notebook,k{i},Run,10/16/2026 2:0{i} PM,,Succeeded,,1,5,0,Billable,k{i}" for i in range(7)]
    p = read(HEAD + "\n" + "\n".join(rows) + "\n")
    assert p["skipped"] == [{"reason": "No start time", "count": 7, "lines": [3, 4, 5, 6, 7]},
                            {"reason": "Ends before it starts", "count": 1, "lines": [2]}]
    assert p["to_import"] == 7 and [s["item"] for s in p["sample"]] == ["k0", "k1", "k2", "k3", "k4"]


def test_a_cost_row_for_a_day_already_imported_replaces_it():
    first = read(COST)["rows"][0]
    p = read(COST, known_slots={first["slot"]})
    assert p["to_import"] == 4 and p["replaces_earlier"] == 1


def test_the_files_period_runs_from_its_first_start_to_its_last_end():
    assert read(OPS)["period"] == {"start": "2026-10-16T14:00:05+00:00", "end": "2026-10-16T15:01:00+00:00"}
