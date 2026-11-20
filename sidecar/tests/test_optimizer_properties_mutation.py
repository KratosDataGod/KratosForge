"""Edges the property tests and earlier tests leave open (mutation batches 198 to 201,
docs/quality/mutation-testing.md)."""

import datetime as dt

from fabric_desktop import measurement as ms
from fabric_desktop.opportunities import analyzers

AT = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)


def test_a_collection_time_given_as_a_datetime_is_stored_as_iso_text():
    m = ms.make(ms.MEASURED, "cu", ms.CU_SECONDS, 1.0, source="job api", collected_at=AT)
    assert m["collected_at"] == AT.isoformat()


def test_a_period_that_starts_and_ends_at_the_same_moment_is_valid():
    m = ms.make(ms.MEASURED, "cu", ms.CU_SECONDS, 1.0, source="job api", collected_at=AT,
                period_start=AT, period_end=AT)
    assert m["period"] == {"start": AT.isoformat(), "end": AT.isoformat()}


def test_a_currency_given_for_a_cu_total_is_ignored_not_refused():
    parts = [ms.make(ms.MEASURED, "cu", ms.CU_SECONDS, 2.0, source="s", collected_at=AT)]
    got = ms.total(parts, "cu", ms.CU_SECONDS, currency="USD")
    assert got["value"] == 2.0 and got["currency"] is None


def test_a_whole_table_rewrite_is_quoted_to_120_characters():
    cols = ", ".join(f"column_{n}" for n in range(40))
    [(cell, text)] = analyzers._sql_full([f"%%sql\nCREATE OR REPLACE TABLE silver.sales USING DELTA PARTITIONED BY ({cols}) AS SELECT 1"])
    assert cell == 1 and len(text) == 120 and text.startswith("CREATE OR REPLACE TABLE silver.sales")


def test_copies_with_no_known_lakehouse_name_their_code_by_a_16_character_fingerprint():
    fp = "ab" * 32
    opp = analyzers._suspected([{"item_id": "a", "name": "A", "type": "Notebook", "workspace_id": "w",
                                 "runs": {"read": False, "runs": 0, "scheduled": 0, "days": 30, "seconds_total": None}},
                                {"item_id": "b", "name": "B", "type": "Notebook", "workspace_id": "w",
                                 "runs": {"read": False, "runs": 0, "scheduled": 0, "days": 30, "seconds_total": None}}], fp)
    [code] = [e for e in opp["evidence"] if e["kind"] == "identical_code"]
    assert code["detail"]["fingerprint"] == fp[:16]
