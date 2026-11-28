"""Mutation-testing follow-ups for parity_harness.py (batch 461): each refusal of a case that breaks
the format, the decimal and value edges, the logical names of Arrow types, the type rules each
normalization allows, the error a dataset, an empty statement or a T-SQL run gives, a T-SQL batch's
earlier statements, the engine a record names, the row limit, and writing into a new folder."""
from __future__ import annotations

import copy
import datetime as dt
import json
import socket

import pyarrow as pa
import pytest

from fabric_desktop import parity_harness as h

CASE = {"id": "dec", "title": "A decimal", "dataset": {"table": "t", "columns": [["d", "decimal(10,2)"]], "rows": [["1.50"]]},
        "dialects": {"duckdb": {"statement": "SELECT d FROM t", "schema": [["d", "decimal(10,2)"]], "rows": [["1.50"]],
                                "source": "test"}}}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the parity harness opened a network connection")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _int_case(statement, dialect="duckdb", schema=(("v", "int"),), rows=((1,),), columns=(("a", "int"),),
              data=((1,),)):
    return h.validate({"id": "c", "title": "c",
                       "dataset": {"table": "t", "columns": [list(c) for c in columns], "rows": [list(r) for r in data]},
                       "dialects": {dialect: {"statement": statement, "schema": [list(c) for c in schema],
                                              "rows": [list(r) for r in rows], "source": "test"}}})


# ---- the case format


@pytest.mark.parametrize("change, words", [
    (lambda c: c["dataset"].update(table="1t"), "table is a plain name"),
    (lambda c: c["dataset"].update(table=5), "table is a plain name"),
    (lambda c: c["dataset"]["columns"].__setitem__(0, ["1d", "int"]), "plain column name"),
    (lambda c: c["dataset"]["columns"].__setitem__(0, ("d", "int")), "plain column name"),
    (lambda c: c["dataset"]["columns"].__setitem__(0, [5, "int"]), "plain column name"),
    (lambda c: c["dataset"]["columns"].__setitem__(0, ["d", "int", "x"]), "plain column name"),
    (lambda c: c["dataset"].update(rows=5), "rows are lists of 1 values"),
    (lambda c: c["dataset"].update(rows=[["1.50", "2"]]), "rows are lists of 1 values"),
    (lambda c: c["dialects"]["duckdb"].update(source="  "), "statement and source are text"),
    (lambda c: c["dialects"]["duckdb"].update(source=5), "statement and source are text"),
    (lambda c: c["dialects"]["duckdb"].update(statement=5), "statement and source are text"),
    (lambda c: c.update(dataset=[1]), "dataset is an object"),
], ids=["table", "table-type", "col-name", "col-tuple", "col-type", "col-len", "rows", "row-len", "source",
        "source-type", "statement-type", "dataset"])
def test_a_case_that_breaks_the_format_is_refused(change, words):
    c = copy.deepcopy(CASE)
    change(c)
    with pytest.raises(h.CaseError, match=words):
        h.validate(c)


def test_a_case_that_isnt_an_object_is_refused():
    with pytest.raises(h.CaseError, match="A case is an object"):
        h.validate("case")


def test_repeated_case_ids_name_only_the_repeats(tmp_path):
    other = copy.deepcopy(CASE) | {"id": "other"}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [CASE, CASE, other]}), encoding="utf-8")
    with pytest.raises(h.CaseError, match=r"Case ids repeat: \['dec'\]\."):
        h.load(path)


@pytest.mark.parametrize("name", ["decimal(0,0)", "decimal(39,0)", "decimal(3,4)"])
def test_a_decimal_outside_its_limits_isnt_a_type(name):
    with pytest.raises(h.CaseError, match="isn't a type"):
        h._type(name, "x")


@pytest.mark.parametrize("name, want", [("decimal(1,0)", pa.decimal128(1, 0)), ("decimal(38,2)", pa.decimal128(38, 2)),
                                        ("decimal(2,2)", pa.decimal128(2, 2))])
def test_a_decimal_at_its_limits_is_a_type(name, want):
    assert h._type(name, "x")[0] == want


@pytest.mark.parametrize("v, kind", [(True, "int"), (5, "boolean"), (1, "string")])
def test_a_value_of_another_kind_is_refused(v, kind):
    with pytest.raises(h.CaseError, match=f"isn't a {kind} value"):
        h._value(v, kind, "x")


def test_strings_and_dates_read_as_their_values():
    assert h._value("a b", "string", "x") == "a b"
    assert h._value("2026-01-02", "date", "x") == dt.date(2026, 1, 2)


def test_a_dataset_writes_booleans_as_sql_keywords():
    sql = h.dataset_sql({"table": "t", "columns": [("b", "boolean")], "rows": [[True], [False], [None]]})
    assert sql[1] == "INSERT INTO t VALUES (CAST(TRUE AS BOOLEAN)), (CAST(FALSE AS BOOLEAN)), (NULL)"


def test_a_dataset_row_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError):
        h.dataset_sql({"table": "t", "columns": [("a", "int")], "rows": [[1, 2]]})


# ---- types


@pytest.mark.parametrize("t, want", [
    (pa.timestamp("ns"), "timestamp"), (pa.timestamp("ns", tz="UTC"), "timestamp_tz"),
    (pa.timestamp("ms", tz="Europe/Oslo"), "timestamp_tz"), (pa.large_string(), "string"),
    (pa.time64("us"), "time64[us]"),
])
def test_arrow_types_have_their_logical_names(t, want):
    assert h._logical(t) == want


@pytest.mark.parametrize("want, got, norms, same", [
    ("int", "string", ["integer_width"], False),
    ("decimal(10,2)", "int", ["integer_width"], False),
    ("decimal(10,0)", "bigint", ["integer_width"], True),
    ("decimal(10,2)", "int", ["decimal_scale_display"], False),
    ("int", "decimal(10,2)", ["decimal_scale_display"], False),
    ("decimal(10,2)", "decimal(12,4)", ["decimal_scale_display"], True),
])
def test_normalizations_allow_only_their_own_types(want, got, norms, same):
    assert h._same_type(want, got, norms) is same


def test_renamed_words_change_only_text():
    assert h._renamed({"pc": "pc", "n": 5, "rows": [{"fabric": "x"}]}) == {"actual": "actual", "n": 5,
                                                                         "rows": [{"expected": "x"}]}


# ---- running


def test_a_dataset_that_cant_be_made_is_an_error_with_duckdbs_reason():
    case = _int_case("SELECT 1 AS v", columns=(("select", "int"),))
    rec = h.run_one(case, "duckdb")
    assert rec["outcome"] == "error" and rec["detail"].startswith("The dataset wasn't made. ")
    assert "syntax error" in rec["detail"].lower() and rec["detail"].endswith(".")


def test_a_statement_with_no_result_says_so():
    rec = h.run_one(_int_case("-- nothing to run"), "duckdb")
    assert rec["outcome"] == "error" and rec["detail"] == "The statement returned no result."


def test_more_than_ten_thousand_rows_is_an_error_and_ten_thousand_are_compared():
    over = h.run_one(_int_case("SELECT 1 AS v FROM range(10001)"), "duckdb")
    assert over["outcome"] == "error"
    at = h.run_one(_int_case("SELECT 1 AS v FROM range(10000)", rows=[(1,)] * 10_000), "duckdb")
    assert at["outcome"] == "match"


def test_a_tsql_statement_that_fails_on_duckdb_is_an_error_naming_the_exception():
    # A column that isn't there fails to bind, so it stays an error (#3245 makes a failure on the data a
    # difference instead).
    rec = h.run_one(_int_case("SELECT nope AS v", dialect="tsql"), "tsql")
    assert rec["outcome"] == "error" and rec["detail"].split(":")[0].endswith("Exception")
    assert "\n" not in rec["detail"]


def test_a_tsql_batch_runs_its_earlier_statements_first():
    rec = h.run_one(_int_case("CREATE TABLE u (a INT); INSERT INTO u VALUES (5); SELECT a AS v FROM u",
                              dialect="tsql", rows=[(5,)]), "tsql")
    assert rec["outcome"] == "match", rec


@pytest.mark.parametrize("dialect, translation", [("duckdb", "none"), ("spark", "engines/local_sql.py"),
                                                  ("tsql", "localdata/tsql.py")])
def test_a_record_names_the_translation_it_used(dialect, translation):
    rec = h.run_one(_int_case("SELECT a AS v FROM t", dialect=dialect), dialect)
    assert rec["engine"]["translation"] == translation and rec["outcome"] == "match"


def test_records_are_written_into_a_folder_that_doesnt_exist_yet(tmp_path):
    rec = h.run_one(_int_case("SELECT a AS v FROM t"), "duckdb")
    path = h.write([rec], tmp_path / "a" / "b")
    assert path.parent == tmp_path / "a" / "b" and json.loads(path.read_text(encoding="utf-8"))["records"][0]["case"] == "c"
