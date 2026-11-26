"""Snowflake export profile (ossie_snowflake): the edges the earlier tests left open (mutation batch 483). How a
reason is cut, the reasons translate gives when sqlglot can't help, which input is refused, where the report puts
its rows, and how the YAML and extension data are written. Fixtures are synthetic and small; nothing calls
Snowflake."""

import logging

import pytest
import yaml
from sqlglot import exp

from fabric_desktop import ossie_snowflake as osf


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _expr(*dialects):
    return {"dialects": [{"dialect": d, "expression": e} for d, e in dialects]}


def _doc(**extra):
    doc = {"version": "0.2.0.dev0", "name": "orders", "datasets": [
        {"name": "sales", "source": "db.s.sales",
         "fields": [{"name": "amount", "expression": _expr(("SNOWFLAKE", "amount"))}]}],
        "metrics": [{"name": "revenue", "expression": _expr(("ANSI_SQL", "SUM(sales.amount)"))}]}
    doc.update(extra)
    return doc


# ---- translate

def test_a_text_exactly_at_its_limit_isnt_cut():
    assert osf._cut("abc", 3) == "abc" and osf._cut("abcd", 3) == "ab…"


def test_three_unknown_functions_name_two_and_count_the_third():
    assert osf.translate("a1(b1(c1(1)))") == (None, "calls a1 and b1 and 1 more, which sqlglot doesn't know")


def test_long_function_names_are_cut_to_24_and_the_reason_to_80():
    sql, reason = osf.translate("f" * 40 + "(" + "g" * 40 + "(h(1)))")
    assert sql is None and len(reason) == osf.MAX_REASON == 80
    assert reason == ("calls " + "f" * 23 + "… and " + "g" * 23 + "… and 1 more, which sqlglot doesn't know")[:79] + "…"


def test_no_text_is_empty():
    assert osf.translate("   ") == (None, "is empty") and osf.translate(5) == (None, "is empty")


def test_a_translation_that_writes_nothing_is_empty(monkeypatch):
    monkeypatch.setattr(exp.Expression, "sql", lambda self, **kw: "  ")
    assert osf.translate("1 + 1") == (None, "is empty")


def test_a_translation_too_deep_to_write_says_so(monkeypatch):
    def deep(self, **kw):
        raise RecursionError

    monkeypatch.setattr(exp.Expression, "sql", deep)
    assert osf.translate("1 + 1") == (None, "is nested too deeply to translate on this PC")


# ---- the input

def test_bytes_that_arent_utf8_and_text_that_isnt_one_object_are_refused():
    with pytest.raises(ValueError, match="This file isn't UTF-8 text"):
        osf.to_snowflake(b"\xff\xfe")
    with pytest.raises(ValueError):
        osf.to_snowflake("- a\n- b\n")


# ---- the report and the file

def test_an_owner_is_named_by_the_objects_in_its_path_only():
    model = {"name": "m", "datasets": [{"name": "ds", "ai_context": {"x": 1}}]}
    assert osf._owner(("datasets", 0, "ai_context"), model) == "ds"


def test_the_whole_file_row_comes_first():
    doc = _doc()
    doc["datasets"][0]["fields"].append({"name": "dax", "expression": _expr(("DAX", "SUM(x)"))})
    rows = osf.to_snowflake(doc)["report"]["rows"]
    assert len(rows) > 1 and rows[0]["kind"] == "model" and rows[0]["rule"] == "unverified"


def test_a_translated_row_points_at_the_entry_it_added():
    doc = _doc()
    doc["metrics"].append({"name": "m", "expression": _expr(("DAX", "1"), ("OSSIE_SQL_2026", "1 + 1"))})
    out = osf.to_snowflake(doc)
    [row] = [r for r in out["report"]["rows"] if r["rule"] == "snowflake/translated"]
    assert row["path"] == "$.semantic_model[0].metrics[1].expression.dialects[2]"
    assert yaml.safe_load(out["yaml"])["semantic_model"][0]["metrics"][1]["expression"]["dialects"][2]["dialect"] == \
        "SNOWFLAKE"


def test_a_skipped_field_names_its_first_three_dialects():
    doc = _doc()
    doc["datasets"][0]["fields"].append({"name": "x", "expression": _expr(("A", "1"), ("B", "1"), ("C", "1"),
                                                                           ("D", "1"))})
    [row] = [r for r in osf.to_snowflake(doc)["report"]["rows"] if r["rule"] == "snowflake/dialect"]
    assert "(it has A, B, C)," in row["message"]


def test_the_yaml_is_block_style_and_keeps_non_ascii_text():
    doc = _doc(description="Umsätze")
    doc["custom_extensions"] = [{"vendor_name": "v", "data": {"k": "Größe"}}]
    text = osf.to_snowflake(doc)["yaml"]
    assert "Umsätze" in text and text.startswith("version: 0.1.1\nsemantic_model:\n- ")
    data = yaml.safe_load(text)["semantic_model"][0]["custom_extensions"][0]["data"]
    assert data == '{"k": "Größe"}'


def test_sql_that_reads_as_nothing_is_empty(monkeypatch):
    from fabric_desktop import sql_dialects

    monkeypatch.setattr(sql_dialects, "parse_one", lambda sql, **kw: None)
    assert osf.translate("1 + 1") == (None, "is empty")


def test_a_type_refusal_from_the_reader_is_a_plain_value_error(monkeypatch):
    from fabric_desktop import model_ossie

    def parse(text):
        raise TypeError("This isn't one object")

    monkeypatch.setattr(model_ossie, "parse", parse)
    with pytest.raises(ValueError, match="^This isn't one object$"):
        osf.to_snowflake("name: x\n")
