"""PAR-002 first increment: parity cases, the local runner and its run records."""

from __future__ import annotations

import copy
import datetime as dt
import json
import socket
from decimal import Decimal

import duckdb
import pytest
import sqlglot

from fabric_desktop import cli, parity_harness as h

CASE = {"id": "dec", "title": "A decimal", "dataset": {"table": "t", "columns": [["d", "decimal(10,2)"]], "rows": [["1.50"]]},
        "dialects": {"duckdb": {"statement": "SELECT d FROM t", "schema": [["d", "decimal(10,2)"]], "rows": [["1.50"]],
                                "source": "test"}}}


def _case(**over):
    c = copy.deepcopy(CASE)
    c["dialects"]["duckdb"] |= over
    return h.validate(c)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the parity harness opened a network connection")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_the_seeded_cases_load_and_cover_the_list():
    cases = h.load()
    ids = {c["id"] for c in cases}
    assert 30 <= len(cases) <= 35
    assert {"int_division", "decimal_division", "null_ordering", "null_aggregation", "concat_null", "date_trunc_month",
            "timestamp_no_zone", "coalesce_iff_case", "group_having", "row_number", "boolean_logic"} <= ids
    assert {"substring_positions", "string_length_bytes", "trim_case_replace", "like_case_sensitivity",
            "date_add_diff_month_end", "day_of_week_week_start", "cast_invalid_string_to_int", "float_to_int",
            "boolean_cast", "null_in_not_in", "integer_overflow", "modulo_negative", "round_half", "distinct_nulls",
            "union_vs_union_all", "lag_lead_default", "rank_dense_rank_ties", "running_sum_frame", "case_with_null",
            "empty_string_vs_null", "division_by_zero"} <= ids
    assert all(e["source"].strip() for c in cases for e in c["dialects"].values())
    assert all(set(c["dialects"]) <= set(h.DIALECTS) for c in cases)


@pytest.mark.parametrize("change, words", [
    (lambda c: c.update(extra=1), "unknown key extra"),
    (lambda c: c["dialects"]["duckdb"].update(normalize=["trim_spaces"]), "normalize names"),
    (lambda c: c["dialects"]["duckdb"].update(rounding=2), "unknown key rounding"),
    (lambda c: c["dialects"].update(oracle=c["dialects"]["duckdb"]), "unknown dialect"),
    (lambda c: c["dataset"]["columns"].__setitem__(0, ["d", "money"]), "isn't a type"),
    (lambda c: c["dataset"]["rows"].__setitem__(0, [1.5]), "isn't a decimal"),
    (lambda c: c["dialects"]["duckdb"].pop("source"), "source missing"),
    (lambda c: c["dialects"]["duckdb"].update(unconfirmed="yes"), "true or false"),
    (lambda c: c.update(id="Bad Id"), "case id"),
    (lambda c: c["dialects"]["duckdb"].update(expect_error=False), "expect_error must be true"),
    (lambda c: c["dialects"]["duckdb"].update(expect_error="yes"), "expect_error must be true"),
    (lambda c: c["dialects"]["duckdb"].update(expect_error=True), "no schema, rows or normalize"),
    (lambda c: c["dialects"]["duckdb"].update(error_like="overflow"), "error_like goes with expect_error"),
    (lambda c: c["dialects"]["duckdb"].pop("rows"), "rows missing"),
], ids=["key", "norm", "dkey", "dialect", "type", "value", "source", "flag", "id", "err-false", "err-text", "err-schema", "err-like",
        "rows"])
def test_a_case_that_breaks_the_format_is_refused(change, words):
    c = copy.deepcopy(CASE)
    change(c)
    with pytest.raises(h.CaseError, match=words):
        h.validate(c)


def test_the_normalizations_are_a_closed_list():
    assert set(h.NORMALIZATIONS) == {"row_order", "column_name_case", "integer_width", "decimal_scale_display"}


def test_a_matching_answer_is_a_match_with_versions_and_local_evidence():
    r = h.run_one(_case(), "duckdb")
    assert r["outcome"] == "match" and r["differences"] == []
    assert r["platform"] == "local" and r["location"] == "this_pc" and r["evidence_level"] == "local"
    assert r["versions"] == {"duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__,
                             "python": r["versions"]["python"], "connector": "n/a"}
    assert r["engine"]["id"] == "local-duckdb" and r["case"] == "dec" and r["date"].endswith("+00:00")
    assert r["expected"]["rows"] == [["1.50"]] and r["actual"] == {"schema": [["d", "decimal(10,2)"]], "rows": [["1.50"]]}


def test_a_decimal_scale_difference_differs_with_both_values():
    r = h.run_one(_case(statement="SELECT CAST(d AS DECIMAL(10,1)) AS d FROM t", rows=[["1.50"]]), "duckdb")
    assert r["outcome"] == "differs"
    assert {"what": "type", "column": "d", "expected": "decimal(10,2)", "actual": "decimal(10,1)"} in r["differences"]
    # A scale that changes the value is a value difference even when the type is allowed to differ.
    r = h.run_one(_case(statement="SELECT CAST(d AS DECIMAL(10,0)) AS d FROM t", normalize=["decimal_scale_display"]),
                  "duckdb")
    assert r["outcome"] == "differs"
    values = next(d for d in r["differences"] if d["what"] == "values")
    assert values["rows"][0]["columns"]["d"] == {"actual": "2", "expected": "1.50"}


def test_a_named_normalization_is_the_only_one_applied():
    same_value = "SELECT CAST(d AS DECIMAL(12,4)) AS D FROM t"
    assert h.run_one(_case(statement=same_value), "duckdb")["outcome"] == "differs"
    assert h.run_one(_case(statement=same_value, normalize=["decimal_scale_display"]), "duckdb")["outcome"] == "differs"
    assert h.run_one(_case(statement=same_value, normalize=["decimal_scale_display", "column_name_case"]),
                     "duckdb")["outcome"] == "match"


def test_null_where_a_value_was_expected_differs():
    r = h.run_one(_case(statement="SELECT CAST(NULL AS DECIMAL(10,2)) AS d FROM t"), "duckdb")
    assert r["outcome"] == "differs"


def test_row_order_is_compared_unless_named():
    c = {"id": "o", "title": "o", "dataset": {"table": "t", "columns": [["v", "int"]], "rows": [[1], [2]]},
         "dialects": {"duckdb": {"statement": "SELECT v FROM t ORDER BY v DESC", "schema": [["v", "int"]],
                                 "rows": [[1], [2]], "source": "test"}}}
    assert h.run_one(h.validate(c), "duckdb")["outcome"] == "differs"
    c["dialects"]["duckdb"]["normalize"] = ["row_order"]
    assert h.run_one(h.validate(c), "duckdb")["outcome"] == "match"


def test_unconfirmed_stays_unconfirmed_and_never_match():
    assert h.run_one(_case(unconfirmed=True), "duckdb")["outcome"] == "unconfirmed"
    assert h.run_one(_case(unconfirmed=True, rows=[["1.51"]]), "duckdb")["outcome"] == "differs"


def _error_case(statement, **over):
    c = copy.deepcopy(CASE)
    c["dialects"]["duckdb"] = {"statement": statement, "expect_error": True, "source": "test"} | over
    return h.validate(c)


def test_a_case_may_expect_an_error_without_schema_or_rows():
    c = _error_case("SELECT CAST('x' AS INTEGER) AS i")
    assert c["dialects"]["duckdb"]["expect_error"] is True and c["dialects"]["duckdb"]["schema"] == []
    assert h.validate(copy.deepcopy(CASE))["dialects"]["duckdb"]["expect_error"] is False
    with pytest.raises(h.CaseError, match="no schema"):
        _error_case("SELECT 1", normalize=["row_order"])


def test_an_expected_error_matches_only_when_the_statement_fails():
    r = h.run_one(_error_case("SELECT CAST('x' AS INTEGER) AS i"), "duckdb")
    assert r["outcome"] == "match" and "Conversion" in r["detail"] and r["expected"]["expect_error"] is True
    assert h.run_one(_error_case("SELECT CAST('x' AS INTEGER) AS i", unconfirmed=True), "duckdb")["outcome"] == "unconfirmed"
    r = h.run_one(_error_case("SELECT 1 AS i"), "duckdb")
    assert r["outcome"] == "differs" and r["differences"][0]["what"] == "error"
    assert r["actual"] == {"schema": [["i", "int"]], "rows": [[1]]}


def test_error_like_names_the_expected_error():
    assert h.run_one(_error_case("SELECT CAST('x' AS INTEGER) AS i", error_like="conver"), "duckdb")["outcome"] == "match"
    assert h.run_one(_error_case("SELECT CAST('x' AS INTEGER) AS i", error_like="CONVERSION"), "duckdb")["outcome"] == "match"
    r = h.run_one(_error_case("SELECT nope FROM t", error_like="conver"), "duckdb")   # fails, but on a name
    assert r["outcome"] == "differs" and r["differences"][0]["expected"] == "an error like conver"
    assert "nope" in r["differences"][0]["actual"]
    assert h.run_one(_error_case("SELECT nope FROM t"), "duckdb")["outcome"] == "match"   # without it, any failure
    for bad, msg in [("", "as text"), (3, "as text"), ("(", "isn't a regular expression")]:
        with pytest.raises(h.CaseError, match=msg):
            _error_case("SELECT 1", error_like=bad)


def test_a_failure_on_the_data_is_a_difference_when_a_result_is_expected():
    c = copy.deepcopy(CASE)
    c["dialects"]["duckdb"]["statement"] = "SELECT CAST('x' AS INTEGER) AS d FROM t"
    r = h.run_one(h.validate(c), "duckdb")
    assert r["outcome"] == "differs" and r["differences"][0]["what"] == "error"
    assert r["differences"][0]["expected"] == "a result" and "Conversion" in r["differences"][0]["actual"]


def test_an_expected_error_is_still_refused_or_an_error_when_the_run_breaks_elsewhere():
    c = copy.deepcopy(CASE)
    c["dialects"] = {"snowflake": {"statement": "PUT file:///tmp/x @stage", "expect_error": True, "source": "test"}}
    assert h.run_one(h.validate(c), "snowflake")["outcome"] == "refused"
    c = _error_case("SELECT 1")
    c["dataset"]["rows"] = [[Decimal("123456789012")]]  # doesn't fit decimal(10,2): the dataset isn't made
    r = h.run_one(c, "duckdb")
    assert r["outcome"] == "error" and "dataset" in r["detail"]


def test_refusals_and_errors_are_named():
    c = copy.deepcopy(CASE)
    c["dialects"] = {"snowflake": {"statement": "PUT file:///tmp/x @stage", "schema": [["d", "int"]], "rows": [],
                                   "source": "test"},
                     "duckdb": {"statement": "SELECT nope FROM t", "schema": [["d", "int"]], "rows": [], "source": "test"},
                     "tsql": {"statement": "DECLARE @x int", "schema": [["d", "int"]], "rows": [], "source": "test"}}
    out = {r["engine"]["dialect"]: r for r in h.run([h.validate(c)])}
    assert out["snowflake"]["outcome"] == "refused" and out["tsql"]["outcome"] == "refused"
    assert out["duckdb"]["outcome"] == "error" and "nope" in out["duckdb"]["detail"]


def test_every_seeded_case_runs_locally_and_unconfirmed_ones_say_so():
    records = h.run()
    assert {r["outcome"] for r in records} <= {"match", "unconfirmed", "differs", "refused", "error"}
    by = {(r["case"], r["engine"]["dialect"]): r for r in records}
    # INT is NUMBER(38,0) on Snowflake; on this PC the translation keeps a 32-bit INTEGER and overflows.
    assert [k for k, r in by.items() if r["outcome"] == "error"] == []
    overflow = by[("integer_overflow", "snowflake")]   # INT kept 32-bit here; NUMBER(38,0) on Snowflake
    assert overflow["outcome"] == "differs" and "INT32" in overflow["differences"][0]["actual"]
    assert all(r["outcome"] != "match" for r in records if r["expected"]["unconfirmed"])
    # Known gaps on this PC, reported, not normalized away.
    assert by[("decimal_division", "snowflake")]["outcome"] == "differs"
    assert by[("decimal_division", "tsql")]["outcome"] == "match"
    assert by[("null_ordering", "snowflake")]["outcome"] == "match"
    assert by[("substring_positions", "snowflake")]["outcome"] == "differs"   # out-of-range start isn't empty
    assert by[("date_add_diff_month_end", "snowflake")]["outcome"] == "differs"   # DATEADD gives a timestamp
    assert by[("day_of_week_week_start", "snowflake")]["outcome"] == "differs"   # DATE_TRUNC gives a timestamp
    assert by[("division_by_zero", "databricks")]["outcome"] == "match"   # expected errors
    assert by[("division_by_zero", "tsql")]["outcome"] == "match"
    assert by[("integer_overflow", "spark")]["outcome"] == "match"
    assert not any(h.can_verify(r) for r in records)


@pytest.mark.parametrize("record, ok", [
    ({"evidence_level": "local", "platform": "local", "location": "this_pc", "outcome": "match"}, False),
    ({"evidence_level": "fake", "platform": "snowflake", "location": "fake_api", "outcome": "match"}, False),
    ({"evidence_level": "live", "platform": "local", "location": "this_pc", "outcome": "match"}, False),
    ({"evidence_level": "live", "platform": "fabric", "location": "this_pc", "outcome": "match"}, False),
    ({"evidence_level": "live", "platform": "snowflake", "location": "account", "outcome": "unconfirmed"}, False),
    ({"evidence_level": "live", "platform": "snowflake", "location": "account", "outcome": "match"}, True),
], ids=["local", "fake", "live-local", "live-this-pc", "live-unconf", "live"])
def test_only_a_matching_live_platform_run_can_verify(record, ok):
    assert h.can_verify(record) is ok


def test_records_are_written_with_their_path(tmp_path):
    records = h.run(case_id="null_ordering", dialect="duckdb")
    path = h.write(records, tmp_path / "runs")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema"] == h.SCHEMA and saved["records"][0]["written_to"] == str(path)
    assert saved["records"][0]["evidence_level"] == "local"


def test_the_cli_runs_locally(tmp_path, capsys):
    with pytest.raises(SystemExit) as done:
        cli.main(["parity-check", "--case", "boolean_logic", "--out", str(tmp_path)])
    assert done.value.code == 0
    out = capsys.readouterr()
    assert "boolean_logic" in out.out and "Evidence level: local" in out.out
    assert list(tmp_path.glob("parity-run-*.json"))
    with pytest.raises(SystemExit) as done:
        cli.main(["parity-check", "--case", "nope"])
    assert done.value.code == 2


def test_a_timestamp_offset_is_converted_or_refused():
    utc = h._value("2024-01-01T05:00:00+05:00", "timestamp_tz", "t")
    assert utc == dt.datetime(2024, 1, 1, 0, 0, tzinfo=dt.timezone.utc)
    assert h._value("2024-01-01T05:00:00", "timestamp_tz", "t").hour == 5
    with pytest.raises(h.CaseError):
        h._value("2024-01-01T05:00:00+05:00", "timestamp", "t")
