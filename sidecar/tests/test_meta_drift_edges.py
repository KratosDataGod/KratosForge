"""Schema drift review: the type names, config edits and widening rules the dataops tests don't
reach. Pure functions on text and Polars types, plus stand-in entities; nothing is read or written."""

import tomllib
from types import SimpleNamespace

import polars as pl

from fabric_desktop.meta import drift


def test_type_names_and_kinds_for_times_and_flags():
    assert drift.type_name(pl.Datetime("us", "UTC")) == "timestamp"
    assert drift.type_name(pl.List(pl.Int64)) == "string"
    assert drift._kind(pl.Boolean) == "bool"
    assert drift._kind(pl.Datetime("us")) == "date" and drift._kind(pl.String) == "text"


def test_an_entity_read_from_another_expects_its_derived_and_mode_columns_too():
    up = SimpleNamespace(columns={"id": "int64"}, derived=["full_name"], hook_adds=["score"],
                         mode_columns=lambda: {"_valid_from", "_current"})
    project = SimpleNamespace(entities={"silver.customers": up})
    e = SimpleNamespace(spec={"source": {"entity": "silver.customers"}})
    assert drift.expected(project, e) == {"id": "int64", "full_name": "", "score": "", "_current": "",
                                          "_valid_from": ""}


def test_a_section_that_isnt_there_is_none():
    assert drift._section(["[source]", "a = 1"], "[contract]") is None


def test_a_file_with_no_columns_section_gets_one_after_its_contract():
    text = '[source]\nfrom = "crm.customers"\n\n[contract]\ndrift = "stop"\n\n[load]\nmode = "merge"\n'
    out = drift.apply(text, add={"email": "string"}, retype={"id": "int64"})
    parsed = tomllib.loads(out)
    assert parsed["contract"]["columns"] == {"email": "string"}
    assert parsed["contract"]["drift"] == "stop" and parsed["load"]["mode"] == "merge"
    assert drift.apply('[source]\nfrom = "x"\n', add={"a": "int64"}).endswith('[contract.columns]\na = "int64"\n')


def test_retype_rewrites_the_line_and_add_skips_a_column_already_there():
    text = '[contract.columns]\nid = "int32"\n"name" = "string"\n'
    out = tomllib.loads(drift.apply(text, retype={"id": "int64"}, add={"name": "int64", "city": "string"}))
    assert out["contract"]["columns"] == {"id": "int64", "name": "string", "city": "string"}


def test_a_policy_is_written_into_a_new_contract_section_and_a_blank_one_clears_it():
    text = '[source]\nfrom = "crm.customers"\n'
    out = drift.apply(text, policy="keep")
    assert tomllib.loads(out)["contract"] == {"drift": "keep"}
    cleared = drift.apply(out, policy="")
    assert tomllib.loads(cleared)["contract"] == {}


def test_a_rename_with_no_source_section_starts_one_and_keeps_earlier_renames():
    out = drift.apply('[contract.columns]\nid = "int64"\n', rename={"name": "full_name"})
    assert tomllib.loads(out)["source"]["rename"] == {"full_name": "name"}
    again = drift.apply(out, rename={"city": "town"})
    assert tomllib.loads(again)["source"]["rename"] == {"full_name": "name", "town": "city"}


def test_trailing_blank_lines_are_trimmed():
    assert drift.apply("[contract]\n\n\n\n") == "[contract]\n"


def test_exact_type_text_for_decimals_and_times():
    assert drift.type_text(pl.Decimal(10, 2)) == "decimal(10,2)"
    assert drift.type_text(pl.Datetime("us", "UTC")) == "timestamp"
    assert drift.type_text(pl.Datetime("us")) == "timestamp_ntz"
    assert drift.type_text(pl.Int16) == "int16"


def test_widening_follows_deltas_rules():
    assert drift.widens(pl.Int32, pl.Int32) is False
    assert drift.widens(pl.Int32, pl.Int64) and not drift.widens(pl.Int64, pl.Int32)
    assert drift.widens(pl.UInt8, pl.Int16) and not drift.widens(pl.Int8, pl.UInt16)
    assert drift.widens(pl.Int32, pl.Float64) and not drift.widens(pl.Int64, pl.Float64)
    assert drift.widens(pl.Float32, pl.Float64)
    assert drift.widens(pl.Int32, pl.Decimal(12, 2)) and not drift.widens(pl.Int64, pl.Decimal(12, 2))
    assert drift.widens(pl.Decimal(10, 2), pl.Decimal(12, 2)) and not drift.widens(pl.Decimal(10, 2), pl.Decimal(10, 1))
    assert drift.widens(pl.Date, pl.Datetime("us")) and not drift.widens(pl.Date, pl.Datetime("us", "UTC"))
    assert not drift.widens(pl.String, pl.Int64)
