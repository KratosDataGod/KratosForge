"""Boundary (off-by-one) mutants that the mutation tool didn't make before batch 36
(docs/quality/mutation-testing.md): a comparison's < moved to <= or the other way round. Each test
pins a value that sits exactly on an edge."""

from __future__ import annotations

import datetime as dt
import ipaddress

import pytest

from fabric_desktop import databricks_ip as dip
from fabric_desktop import databricks_variant as dv
from fabric_desktop import measurement as ms
from fabric_desktop.localdata import LocalData
from fabric_desktop.nbsymbols import find_symbol
from fabric_desktop.orchestration import expressions as ex
from fabric_desktop.usage import reconcile as rc

UNTIL = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)
SINCE = UNTIL - dt.timedelta(days=30)


def _cu(value, start, end):
    return {"value": value, "unit": ms.CU_SECONDS, "source": "export.csv", "uncertainty": "sampled",
            "classification": ms.MEASURED, "metric": "fabric_operation_cu", "period": {"start": start, "end": end}}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def _imp(*rows, id="u-1", imported_at=None):
    return {"id": id, "imported_at": imported_at or UNTIL.isoformat(), "rows": list(rows)}


def _recon(*rows, runs=()):
    return rc.reconcile([_imp(*rows)], list(runs), {}, SINCE, UNTIL, None, now=UNTIL)


def _op(start, minutes=5, item="nb_a"):
    end = (start + dt.timedelta(minutes=minutes)).isoformat()
    return {"level": "operation", "workspace": "Sales Dev", "item": item, "start": start.isoformat(),
            "end": end, "cu": _cu(100.0, start.isoformat(), end), "cost": None}


def _total(start, end):
    return {"level": "item", "workspace": "Sales Dev", "item": "nb_a", "start": None,
            "cu": _cu(500, start.isoformat(), end.isoformat())}


def _run(job, start, end, item="nb_a"):
    return {"kind": "Notebook run", "name": item, "workspace_name": "Sales Dev", "workspace_id": "w1",
            "job_id": job, "started_at": start.isoformat(), "finished_at": end.isoformat()}


# ---- usage/reconcile.py: the period is [since, until)

def test_a_timed_row_starting_exactly_at_since_is_counted_and_one_at_until_isnt():
    assert _recon(_op(SINCE))["rows"] == 1
    assert _recon(_op(UNTIL))["rows"] == 0


def test_an_item_total_ending_exactly_at_until_and_starting_exactly_at_since_is_counted():
    total = _total
    r = _recon(total(SINCE, UNTIL))
    assert r["rows"] == 1 and r["rows_outside_period"] == 0
    # one second past either edge only partly falls in the period, so it's counted as outside instead
    late = _recon(total(SINCE, UNTIL + dt.timedelta(seconds=1)))
    early = _recon(total(SINCE - dt.timedelta(seconds=1), UNTIL))
    assert (late["rows"], late["rows_outside_period"]) == (0, 1)
    assert (early["rows"], early["rows_outside_period"]) == (0, 1)


# ---- databricks_ip.py: a network BLOB is the address bytes then the prefix length

def test_a_network_blob_may_have_the_longest_prefix_but_not_one_longer():
    for text, longest in (("192.0.2.7", 32), ("2001:db8::7", 128)):
        packed = ipaddress.ip_address(text).packed
        assert dip.ip("ip_prefix_length", "BLOB", (packed + bytes([longest])).hex()) == str(longest)
        assert dip.ip("try_ip_prefix_length", "BLOB", (packed + bytes([longest + 1])).hex()) is None


def test_an_item_total_that_only_touches_the_period_isnt_counted_as_partly_inside():
    day = dt.timedelta(days=1)
    for start, end in ((UNTIL, UNTIL + day), (SINCE - day, SINCE)):
        r = _recon(_total(start, end))
        assert (r["rows"], r["rows_outside_period"]) == (0, 0)


def test_a_run_starting_exactly_at_until_isnt_in_the_period():
    r = _recon(runs=[_run("j", UNTIL, UNTIL + dt.timedelta(minutes=5))])
    assert r["runs"]["total"] == 0


def test_a_row_starting_exactly_when_a_run_starts_or_finishes_matches_it():
    start, end = UNTIL - dt.timedelta(hours=2), UNTIL - dt.timedelta(hours=1)
    for at in (start, end):
        r = _recon(_op(at, minutes=1), runs=[_run("j", start, end)])
        assert r["matched"]["runs"] == 1


def test_of_two_bills_on_a_full_tie_the_first_one_seen_is_kept():
    def bill(value):
        period = {"start": "2026-10-10T00:00:00+00:00", "end": "2026-10-11T00:00:00+00:00"}
        return {"level": "capacity", "workspace": None, "item": None, "key": f"k{value}", "slot": "s",
                "capacity_id": "f64", "capacity": "F64", "meter": "CU", "start": period["start"], "end": period["end"],
                "cu": {"value": value, "unit": ms.CU_HOURS, "classification": ms.MEASURED,
                       "metric": "billed_capacity_cu", "period": period, "source": "cost.csv"},
                "cost": {"value": 1.0, "unit": "currency", "currency": "EUR", "classification": ms.MEASURED,
                         "metric": "billed_cost", "period": period, "source": "cost.csv"}}
    same = "2026-10-16T09:00:00+00:00"
    imports = [_imp(bill(40.0), id="a", imported_at=same), _imp(bill(96.0), id="b", imported_at=same)]
    [cap] = rc.reconcile(imports, [], {}, SINCE, UNTIL, None, now=UNTIL)["billed"]
    assert cap["cu"]["value"] == pytest.approx(40.0)


# ---- databricks_variant.py

def test_a_variant_double_of_exactly_a_thousandth_prints_plain():
    assert dv._java_double(0.001) == "0.001"
    assert dv._java_double(1e7) == "1.0E7"


def test_an_index_one_past_the_end_of_an_array_isnt_found():
    assert dv._at([10, 20], [1]) == (True, 20)
    assert dv._at([10, 20], [2]) == (False, None)


# ---- orchestration/expressions.py

def test_an_interpolation_at_the_start_is_read_even_when_the_value_ends_with_an_at_sign():
    assert [(e.text, e.interpolated) for e in ex.scan("@{pipeline().RunId} costs @")] == [("pipeline().RunId", True)]


@pytest.mark.parametrize("text, refs", [
    ("activity('a').output[", [("activity", "a", "output")]),
    ("activity('a').output['x'", [("activity", "a", "output")]),
    ("concat(", []),
])
def test_an_expression_cut_short_says_so_and_keeps_what_it_could_read(text, refs):
    [e] = list(ex.scan("@" + text))
    assert (e.text, e.problem) == (text, "a bracket isn't closed")
    assert [(r.kind, r.name, r.member) for r in e.references] == refs


# ---- localdata/tsql_format.py

@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    return local.engine(local.create("TSQL")["id"])


def _format(engine, value: str, pattern: str):
    return engine.execute(f"SELECT FORMAT({value}, '{pattern}') AS f", dialect="tsql")["rows"][0][0]


def test_format_takes_exactly_38_decimal_places_and_seven_fractions_of_a_second(engine):
    assert _format(engine, "CAST(0.5 AS DECIMAL(10, 1))", "N38") == "0.5" + "0" * 37
    # The local engine keeps microseconds, so the seventh digit is 0; text with a 7th digit that isn't 0 is
    # refused by name (test_tsql_rules_differential's DATETIME2_N cases).
    day = "CAST('2024-03-05 14:07:09.1234560' AS DATETIME2)"
    seven = _format(engine, day, "fffffff")
    assert seven == "1234560"


def test_format_yy_is_the_two_digit_year(engine):
    assert _format(engine, "CAST('2024-03-05' AS DATETIME2)", "yy") == "24"


# ---- nbsymbols.py: a name on a cell's last line (no newline after it)

def _cells(source):
    return [{"type": "code", "source": source}]


@pytest.mark.parametrize("source, name", [
    ("def total(): pass", "total"),
    ("import os", "os"),
    ("try:\n    pass\nexcept ValueError as err: pass", "err"),
    ("match v:\n    case [first]: pass", "first"),
])
def test_a_definition_on_the_last_line_is_found(source, name):
    places = find_symbol(_cells(source), 0, 0, name)["places"]
    assert [(p["kind"], source[p["from"]:p["to"]]) for p in places if p["kind"] == "definition"] == [("definition", name)]


def test_a_case_name_on_the_last_line_is_found_where_it_is_bound_not_where_its_text_first_appears():
    source = "match v:\n    case {'first': first}: pass"
    [place] = find_symbol(_cells(source), 0, 0, "first")["places"]
    assert (place["kind"], place["from"]) == ("definition", source.rindex("first"))


def test_a_name_on_the_last_line_after_a_wide_character_is_found_where_it_is_written():
    source = "é = 1; total = é"
    assert find_symbol(_cells(source), 0, source.index("total"))["name"] == "total"


def test_the_cursor_right_after_a_name_is_on_that_name():
    assert find_symbol(_cells("total = 1"), 0, len("total"))["name"] == "total"


def test_a_cell_one_past_the_last_has_no_name():
    assert find_symbol(_cells("x = 1"), 1, 0) == {"name": None, "places": [], "skipped": []}


def test_format_n38_of_a_value_with_an_integer_part(engine):
    assert _format(engine, "1.5", "N38") == "1.5" + "0" * 37


@pytest.mark.parametrize("value, pattern, text", [
    ("1.5", "F38", "1.5" + "0" * 37),
    ("CAST(12345678901234567890123456789012345678 AS DECIMAL(38, 0))", "N2",
     "12,345,678,901,234,567,890,123,456,789,012,345,678.00"),
    ("9.995", "N2", "10.00"),
    ("-9.995", "F2", "-10.00"),
    ("CAST(1.255 AS DECIMAL(5, 3))", "F2", "1.26"),
    ("CAST(-0.5 AS DECIMAL(3, 1))", "F2", "-0.50"),
    ("CAST(42 AS INT)", "N3", "42.000"),
])
def test_format_writes_the_whole_part_and_the_decimals_of_any_size(engine, value, pattern, text):
    """#2691: the whole part and the decimals are written separately, so a high precision leaves room."""
    assert _format(engine, value, pattern) == text
