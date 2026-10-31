"""GD-04: notes under a Query profile, each about something the profile measured. Recorded profiles
(the shape spark_local.query_profile returns), including ones that must show nothing, and one run
against DuckDB itself."""

from __future__ import annotations

import pytest

from fabric_desktop import spark_local
from fabric_desktop.learn import evidence


def _scan(read, kept, filters, share=0.8, name="SEQ_SCAN", depth=2):
    return {"depth": depth, "name": name, "rows": kept, "rows_scanned": read, "seconds": share, "share": share,
            "details": {"Table": "memory.main.sales", "Filters": filters}}


def _profile(*steps):
    return {"seconds": 1.0, "rows": 7, "rows_scanned": 0, "peak_memory": 0, "more_steps": 0,
            "steps": [{"depth": 0, "name": "HASH_GROUP_BY", "rows": 7, "rows_scanned": 0, "share": 0.1, "details": {}},
                      {"depth": 1, "name": "PROJECTION", "rows": 1, "rows_scanned": 0, "share": 0.1, "details": {}}, *steps]}


def test_a_scan_that_kept_few_rows_through_a_wrapped_column_gets_a_note():
    (note,) = evidence.notes(_profile(_scan(3_000_000, 369_600, '("year"(order_date) = 2026)', 0.84)))
    assert note["rule_id"] == "SQ002" and note["step"] == 2
    assert note["text"] == ("The scan of sales read 3,000,000 rows and kept 369,600 (12%), 84% of the query's time. "
                            "Its filter wraps order_date in year(), so the engine couldn't skip data by order_date's values.")
    assert note["measured"] == {"rows_read": 3_000_000, "rows_kept": 369_600, "share": 0.84,
                                "filter": '("year"(order_date) = 2026)'}


@pytest.mark.parametrize("scan", [
    _scan(3_000_000, 369_600, "order_date>='2026-01-01'::DATE AND order_date<'2027-01-01'::DATE"),  # a range: fine
    _scan(3_000_000, 2_000_000, '("year"(order_date) = 2026)'),     # kept most rows: skipping saves little
    _scan(50_000, 100, '("year"(order_date) = 2026)'),              # too few rows to matter
    _scan(3_000_000, 369_600, ""),                                  # no filter on the scan
    _scan(3_000_000, 369_600, "(lower(CAST(region AS VARCHAR)) = '3')"),  # can't tell which column: say nothing
], ids=["range", "kept-most", "small", "no-filter", "unclear"])
def test_no_note_where_the_profile_does_not_show_it(scan):
    assert evidence.notes(_profile(scan)) == []


def test_a_cross_join_that_multiplied_rows_gets_a_note():
    p = _profile({"depth": 2, "name": "CROSS_PRODUCT", "rows": 150_000_000, "share": 0.7, "details": {}},
                 {"depth": 3, "name": "SEQ_SCAN", "rows": 3_000_000, "share": 0.03, "details": {}},
                 {"depth": 3, "name": "PROJECTION", "rows": 50, "share": 0, "details": {}},
                 {"depth": 4, "name": "RANGE", "rows": 50, "share": 0, "details": {}})
    (note,) = evidence.notes(p)
    assert note["rule_id"] == "SQ006" and note["measured"]["inputs"] == [3_000_000, 50]
    assert note["text"].startswith("A cross join matched every row with every row: 3,000,000 × 50 made 150,000,000 rows, 70%")


def test_a_join_that_made_many_more_rows_than_either_side_gets_a_note_and_an_ordinary_join_none():
    def join(made):
        return _profile({"depth": 2, "name": "HASH_JOIN", "rows": made, "share": 0.9, "details": {}},
                        {"depth": 3, "name": "SEQ_SCAN", "rows": 20_000, "share": 0, "details": {}},
                        {"depth": 3, "name": "SEQ_SCAN", "rows": 30_000, "share": 0, "details": {}})
    (note,) = evidence.notes(join(85_714_286))
    assert note["rule_id"] == "SQ006" and "made 85,714,286 rows from 20,000 and 30,000" in note["text"]
    assert evidence.notes(join(30_000)) == [] and evidence.notes(join(150_000)) == []  # under ten times


def test_nothing_odd_in_a_profile_breaks_it():
    for p in ({}, {"steps": None}, {"steps": [None, {}, {"name": "SEQ_SCAN"}]}, None):
        assert evidence.notes(p) == []


def test_against_duckdb_a_wrapped_filter_gets_a_note_and_the_bare_one_none():
    # trim() hides the column's values from row-group skipping in DuckDB 1.x and 2.0 alike. (DuckDB 2.0
    # skips through year() and other monotonic date functions itself, so those get no note there.)
    s = spark_local.session()
    s.conn.execute("CREATE OR REPLACE TABLE gd04_codes AS SELECT range AS id, 'c' || lpad(range::VARCHAR, 7, '0') "
                   "AS code, range % 7 AS region, 1.0 AS amount FROM range(3000000)")
    try:
        wrapped = spark_local.query_profile(s.sql("SELECT region, sum(amount) FROM gd04_codes "
                                                  "WHERE trim(code) >= 'c2600000' GROUP BY region"))
        bare = spark_local.query_profile(s.sql("SELECT region, sum(amount) FROM gd04_codes "
                                               "WHERE code >= 'c2600000' GROUP BY region"))
    finally:
        s.conn.execute("DROP TABLE gd04_codes")
    (note,) = evidence.notes(wrapped)
    assert note["rule_id"] == "SQ002" and "wraps code in trim()" in note["text"]
    assert evidence.notes(bare) == []
    scanned = lambda p: next(st["rows_scanned"] for st in p["steps"] if st["name"] in {"SEQ_SCAN", "TABLE_SCAN"})
    assert scanned(bare) < scanned(wrapped) == 3_000_000   # the bare filter let DuckDB skip; the measured proof


def test_the_service_adds_the_notes_to_a_profile_and_leaves_a_gone_one_alone():
    from fabric_desktop.service import Service

    class _Kernel:
        def __init__(self, out):
            self.out = out

        def frame_query_profile(self, frame):
            return dict(self.out)

    class _Svc:
        notebook_frame_query_profile = Service.notebook_frame_query_profile

    svc = _Svc()
    svc.kernel = _Kernel(_profile(_scan(3_000_000, 369_600, '("year"(order_date) = 2026)')))
    (note,) = svc.notebook_frame_query_profile("f1")["notes"]
    assert note["url"] == "sql-best-practices.md#sq002" and note["principle"].startswith("Compare the column itself")
    svc.kernel = _Kernel({"frame": "f1", "gone": True})
    assert "notes" not in svc.notebook_frame_query_profile("f1")
