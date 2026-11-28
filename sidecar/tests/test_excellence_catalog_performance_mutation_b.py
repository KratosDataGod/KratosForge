"""excellence/catalog_performance at its edges, from the mutation run (batch 467): a table's facts when
the log gives no number, the exact wording for one table and for several, the thresholds at their
edge, how many names a message lists, and the size in MB."""

from __future__ import annotations

from fabric_desktop import delta_profile as dp
from fabric_desktop.excellence import catalog_performance as perf
from test_excellence_performance import LH0, MB, outcome_of, run, table, unread

SMALL, VACUUM, FEAT = "FEA-PERF-003", "FEA-PERF-004", "FEA-PERF-005"


def text(read, cid):
    return outcome_of(run({LH0: read}), cid)["text"]


# ---- table_facts


def test_a_table_with_an_error_is_unread_even_with_its_health():
    got = perf.table_facts("dbo.t", {"exists": True, "files": 3}, [], error="403 Forbidden")
    assert got == {"name": "dbo.t", "read": False, "problem": "403 Forbidden"}
    assert perf.table_facts("dbo.t", None, None) == {"name": "dbo.t", "read": False,
                                                    "problem": "Its Delta log wasn't read."}


def test_a_log_without_numbers_counts_zero_and_numbers_are_kept():
    got = perf.table_facts("dbo.t", {"exists": True}, None)
    assert got == {"name": "dbo.t", "read": True, "delta": True, "files": 0, "bytes": 0, "avg_file_bytes": 0,
                   "versions_since_vacuum": 0, "last_vacuum": None, "features": []}
    got = perf.table_facts("dbo.t", {"exists": True, "files": 7, "bytes": 70, "avg_file_bytes": 10,
                                     "versions_since_vacuum": 4, "last_vacuum": "x"}, ["b", "a"])
    assert (got["files"], got["bytes"], got["avg_file_bytes"], got["versions_since_vacuum"], got["features"]) == \
        (7, 70, 10, 4, ["a", "b"])


# ---- wording for one table and for several


def test_small_files_wording_for_one_and_several():
    one = text({"tables": [table("dbo.a", files=40, avg_mb=1)]}, SMALL)
    assert one.startswith("1 table has many small files: ")
    two = text({"tables": [table("dbo.a", files=40, avg_mb=1), table("dbo.b", files=50, avg_mb=1)]}, SMALL)
    assert two.startswith("2 tables have many small files: dbo.b (50 files")  # most files first
    assert text({"tables": [table()]}, SMALL) == "None of the 1 table has 20 or more files averaging under 32 MB."
    assert text({"tables": [table(), table("dbo.u")]}, SMALL) == \
        "None of the 2 tables has 20 or more files averaging under 32 MB."


def test_vacuum_wording_for_one_and_several():
    assert text({"tables": [table(since_vacuum=40)]}, VACUUM).startswith("1 table has 30 or more versions")
    assert text({"tables": [table(since_vacuum=40), table("dbo.u", since_vacuum=50)]}, VACUUM).startswith(
        "2 tables have 30 or more versions since the last VACUUM: dbo.u (50 versions)")
    assert text({"tables": [table()]}, VACUUM) == \
        "Every one of the 1 table has fewer than 30 versions since its last VACUUM."
    assert text({"tables": [table(), table("dbo.u")]}, VACUUM) == \
        "Every one of the 2 tables has fewer than 30 versions since its last VACUUM."


def test_feature_wording_for_one_and_several():
    readers = " or ".join(dp.READERS[r] for r in perf.READERS)
    assert text({"tables": [table(features=[dp.CM_ID])]}, FEAT).startswith(f"1 table uses a Delta feature {readers}")
    assert text({"tables": [table(features=[dp.CM_ID]), table("dbo.u", features=[dp.CM_ID])]}, FEAT).startswith(
        f"2 tables use a Delta feature {readers}")
    assert text({"tables": [table()]}, FEAT) == f"None of the 1 table uses a Delta feature {readers} can't read."
    assert text({"tables": [table(), table("dbo.u")]}, FEAT) == \
        f"None of the 2 tables uses a Delta feature {readers} can't read."


def test_unread_wording_for_one_and_several():
    one = text({"tables": [table(), unread("dbo.x")]}, VACUUM)
    assert "but 1 table couldn't be read (dbo.x)." in one
    two = text({"tables": [table(), unread("dbo.x"), unread("dbo.y")]}, VACUUM)
    assert "but 2 tables couldn't be read (dbo.x, dbo.y)." in two
    assert "but 1 table past the assessment's read budget weren't read." in text(
        {"tables": [table()], "past_budget": 1}, VACUUM)
    assert "but 2 tables past the assessment's read budget weren't read." in text(
        {"tables": [table()], "past_budget": 2}, VACUUM)


# ---- how many names are listed


def test_five_names_are_listed_in_full_and_more_are_counted():
    five = [unread(f"dbo.t{i}") for i in range(5)]
    got = text({"tables": [table(), *five]}, VACUUM)
    assert "(dbo.t0, dbo.t1, dbo.t2, dbo.t3, dbo.t4)." in got and "more" not in got
    six = [unread(f"dbo.t{i}") for i in range(6)]
    assert "(dbo.t0, dbo.t1, dbo.t2, dbo.t3, dbo.t4 and 1 more)." in text({"tables": [table(), *six]}, VACUUM)
    seven = [unread(f"dbo.t{i}") for i in range(7)]
    got = text({"tables": [table(), *seven]}, VACUUM)
    assert "(dbo.t0, dbo.t1, dbo.t2, dbo.t3, dbo.t4 and 2 more)." in got


# ---- thresholds at their edge


def test_the_thresholds_at_their_edge():
    assert outcome_of(run({LH0: {"tables": [table(files=20, avg_mb=1)]}}), SMALL)["outcome"] == "fail"
    assert outcome_of(run({LH0: {"tables": [table(files=19, avg_mb=1)]}}), SMALL)["outcome"] == "pass"
    edge = perf.table_facts("dbo.t", {"exists": True, "files": 20, "avg_file_bytes": perf.SMALL_FILE_BYTES}, [])
    assert outcome_of(run({LH0: {"tables": [edge]}}), SMALL)["outcome"] == "pass"
    under = perf.table_facts("dbo.t", {"exists": True, "files": 20, "avg_file_bytes": perf.SMALL_FILE_BYTES - 1}, [])
    assert outcome_of(run({LH0: {"tables": [under]}}), SMALL)["outcome"] == "fail"
    assert outcome_of(run({LH0: {"tables": [table(since_vacuum=30)]}}), VACUUM)["outcome"] == "fail"
    assert outcome_of(run({LH0: {"tables": [table(since_vacuum=29)]}}), VACUUM)["outcome"] == "pass"


# ---- sizes


def test_sizes_are_in_mebibytes():
    t = perf.table_facts("dbo.t", {"exists": True, "files": 30, "avg_file_bytes": int(10.055 * MB)}, [])
    assert "dbo.t (30 files averaging 10.1 MB)" in text({"tables": [t]}, SMALL)


def test_descriptions_name_the_thresholds():
    by = {c.id: c for c in perf.CONTROLS}
    assert by[SMALL].description.startswith("A table in the lakehouse has 20 or more data files averaging under 32 MB,")
    assert by[VACUUM].description.startswith("A table in the lakehouse has 30 or more versions since its last VACUUM")
