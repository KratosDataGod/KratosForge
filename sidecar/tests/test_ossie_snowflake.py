"""Snowflake export profile (OSSIE-010a, ossie_snowflake): an Ossie 0.2.0.dev0 document as Snowflake's 0.1.1 file,
with a named report row for everything Snowflake would skip or Ossie 0.1.1 can't hold. Fixtures are synthetic and
small; nothing calls Snowflake."""

import copy
import logging

import pytest
import yaml

from fabric_desktop import model_ossie, ossie_profiles, ossie_snowflake as osf


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _expr(*dialects):
    return {"dialects": [{"dialect": d, "expression": e} for d, e in dialects]}


def _doc(**extra):
    """A flat 0.2.0.dev0 model Snowflake reads in full: a SNOWFLAKE field and an ANSI_SQL metric."""
    doc = {"version": "0.2.0.dev0", "name": "orders", "datasets": [
        {"name": "sales", "source": "db.s.sales",
         "fields": [{"name": "amount", "expression": _expr(("SNOWFLAKE", "amount"))}]}],
        "metrics": [{"name": "revenue", "expression": _expr(("ANSI_SQL", "SUM(sales.amount)"))}]}
    doc.update(extra)
    return doc


def _rows(out, cls=None, rule=None):
    return [r for r in out["report"]["rows"] if (cls is None or r["class"] == cls) and (rule is None or r["rule"] == rule)]


def _model(out):
    return yaml.safe_load(out["yaml"])["semantic_model"][0]


def _metric_doc(*dialects, at="model"):
    doc = _doc()
    metric = {"name": "m", "expression": _expr(*dialects)}
    if at == "model":
        doc["metrics"].append(metric)
    else:
        doc["datasets"][0]["metrics"] = [metric]
    return doc


# ---- the wrapper and the whole-file row

def test_output_is_the_0_1_1_wrapper_with_a_list():
    out = osf.to_snowflake(_doc())
    doc = yaml.safe_load(out["yaml"])
    assert list(doc) == ["version", "semantic_model"] and doc["version"] == "0.1.1"
    assert isinstance(doc["semantic_model"], list) and len(doc["semantic_model"]) == 1
    assert doc["semantic_model"][0]["name"] == "orders" and "version" not in doc["semantic_model"][0]
    assert ossie_profiles.detect_profile(doc) == "snowflake-ossie"


def test_output_has_the_shape_of_the_read_ossie_yaml_fixture():
    """The same layout as test_ossie_profiles._v011, the shape SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW writes."""
    from test_ossie_profiles import _v011
    doc = yaml.safe_load(osf.to_snowflake(_doc())["yaml"])
    fixture = _v011()
    assert type(doc["semantic_model"]) is type(fixture["semantic_model"])
    assert doc["semantic_model"][0]["datasets"][0]["fields"] == fixture["semantic_model"][0]["datasets"][0]["fields"]


def test_whole_file_is_one_unverified_row_first():
    rows = osf.to_snowflake(_doc())["report"]["rows"]
    assert rows[0]["class"] == "unverified" and rows[0]["label"] == "Unverified" and rows[0]["path"] == "$"
    assert rows[0]["kind"] == "model" and rows[0]["remediation"]
    assert len(rows) == 1  # a model Snowflake reads in full has nothing else to report


def test_rows_have_the_documented_keys():
    out = osf.to_snowflake(_metric_doc(("DAX", "1")))
    for r in out["report"]["rows"]:
        assert {"class", "label", "path", "message", "remediation"} <= set(r)
        assert r["label"] == model_ossie.CLASSES[r["class"]]


def test_validation_reads_back_as_snowflake_with_no_errors():
    v = osf.to_snowflake(_metric_doc(("DAX", "1")))["report"]["validation"]
    assert v["profile"] == "snowflake-ossie" and v["status"] == "Unverified" and v["diagnostics"] == []
    assert [w["rule"] for w in v["warnings"]] == ["snowflake/dialect"]


def test_report_names_source_and_destination():
    report = osf.to_snowflake(_doc())["report"]
    assert report["source"]["version"] == "0.2.0.dev0" and report["destination"]["version"] == "0.1.1"
    assert report["counts"]["unverified"] == 1


def test_input_is_not_changed():
    doc = _metric_doc(("OSSIE_SQL_2026", "SUM(a)"))
    doc["custom_extensions"] = [{"vendor_name": "V", "data": {"a": 1}}]
    before = copy.deepcopy(doc)
    osf.to_snowflake(doc)
    assert doc == before


def test_text_input_is_read():
    out = osf.to_snowflake(yaml.safe_dump(_doc(), sort_keys=False))
    assert _model(out)["name"] == "orders"


def test_shared_objects_are_written_out_never_as_aliases():
    shared = {"dialects": [{"dialect": "SNOWFLAKE", "expression": "a"}]}
    doc = _doc()
    doc["datasets"][0]["fields"] = [{"name": "a", "expression": shared}, {"name": "b", "expression": shared}]
    out = osf.to_snowflake(doc)
    assert "&" not in out["yaml"] and "*" not in out["yaml"]
    assert _model(out)["datasets"][0]["fields"][1]["expression"] == shared


# ---- bad input: a plain ValueError

@pytest.mark.parametrize("bad", [
    pytest.param("version: [", id="yaml"),
    pytest.param("- a\n- b\n", id="list"),
    pytest.param(42, id="int"),
    pytest.param({"version": "0.9", "name": "x", "datasets": [{"name": "a"}]}, id="unknown-version"),
    pytest.param({"version": "0.2.0.dev0", "name": "x", "datasets": []}, id="no-datasets"),
    pytest.param({"version": "0.1.1", "semantic_model": [{"name": "x"}]}, id="already-0.1.1"),
    pytest.param({"version": "0.3.0.dev0", "name": "x", "datasets": [{"name": "a"}]}, id="v0.3"),
    pytest.param({"version": "0.2.0.dev0", "name": "x", "datasets": [{"name": "a"}], 1: "k"}, id="int-key"),
])
def test_bad_input_is_a_plain_value_error(bad):
    with pytest.raises(ValueError) as caught:
        osf.to_snowflake(bad)
    assert type(caught.value) is ValueError


# ---- custom_extensions

def test_extension_data_that_isnt_text_becomes_json_text():
    out = osf.to_snowflake(_doc(custom_extensions=[{"vendor_name": "POWER_BI", "data": {"b": [1, 2], "a": "x"}}]))
    ext = _model(out)["custom_extensions"][0]
    assert ext == {"vendor_name": "POWER_BI", "data": '{"a": "x", "b": [1, 2]}'}
    [row] = _rows(out, "preserved_through_extensions")
    assert row["path"] == "$.semantic_model[0].custom_extensions[0].data"


def test_extension_text_data_is_left_as_it_is_with_no_row():
    out = osf.to_snowflake(_doc(custom_extensions=[{"vendor_name": "COMMON", "data": "{\"a\": 1}"}]))
    assert _model(out)["custom_extensions"] == [{"vendor_name": "COMMON", "data": "{\"a\": 1}"}]
    assert not _rows(out, rule="snowflake/extensions")


def test_extensions_on_a_field_are_handled_too():
    doc = _doc()
    doc["datasets"][0]["fields"][0]["custom_extensions"] = [{"vendor_name": "V", "data": [1]}]
    out = osf.to_snowflake(doc)
    assert _model(out)["datasets"][0]["fields"][0]["custom_extensions"][0]["data"] == "[1]"
    assert _rows(out, "preserved_through_extensions")[0]["object"] == "sales.amount"


@pytest.mark.parametrize("ext, why", [
    pytest.param([{"data": "x"}], "no vendor_name", id="no-vendor"),
    pytest.param([{"vendor_name": "", "data": "x"}], "no vendor_name", id="empty-vendor"),
    pytest.param([{"vendor_name": "V"}], "no data", id="no-data"),
    pytest.param([{"vendor_name": "V", "data": None}], "no data", id="null-data"),
    pytest.param(["text"], "isn't an object", id="not-object"),
    pytest.param({"vendor_name": "V", "data": "x"}, "isn't a list", id="not-a-list"),
])
def test_unusable_extensions_are_unsupported_and_dropped(ext, why):
    out = osf.to_snowflake(_doc(custom_extensions=ext))
    assert "custom_extensions" not in _model(out)
    [row] = _rows(out, "unsupported", "snowflake/extensions")
    assert why in row["message"]


def test_a_dropped_extension_keeps_the_good_ones():
    out = osf.to_snowflake(_doc(custom_extensions=[{"data": "x"}, {"vendor_name": "V", "data": "y"}]))
    assert _model(out)["custom_extensions"] == [{"vendor_name": "V", "data": "y"}]


def test_vendor_names_are_left_unchanged():
    out = osf.to_snowflake(_doc(custom_extensions=[{"vendor_name": "POWER_BI", "data": "x"},
                                                   {"vendor_name": "my vendor", "data": "y"}]))
    assert [e["vendor_name"] for e in _model(out)["custom_extensions"]] == ["POWER_BI", "my vendor"]


# ---- fields and metrics Snowflake skips

def test_dax_only_metric_always_gets_a_row_and_stays():
    out = osf.to_snowflake(_metric_doc(("DAX", "SUM('sales'[amount])")))
    [row] = _rows(out, "unsupported", "snowflake/dialect")
    assert row["object"] == "m" and "DAX" in row["message"] and row["path"] == "$.semantic_model[0].metrics[1].expression"
    assert out["report"]["skipped_by_snowflake"] == [{"object": "m", "kind": "metric", "path": row["path"]}]
    assert _model(out)["metrics"][1]["expression"] == _expr(("DAX", "SUM('sales'[amount])"))


def test_dax_only_metric_gets_a_row_with_translation_off():
    out = osf.to_snowflake(_metric_doc(("DAX", "1")), translate_ossie_sql=False)
    assert len(_rows(out, "unsupported", "snowflake/dialect")) == 1


def test_field_without_sql_is_skipped_and_kept():
    doc = _doc()
    doc["datasets"][0]["fields"].append({"name": "label", "expression": _expr(("DAX", "x"))})
    out = osf.to_snowflake(doc)
    [entry] = out["report"]["skipped_by_snowflake"]
    assert entry["kind"] == "field" and entry["object"] == "sales.label"
    assert _model(out)["datasets"][0]["fields"][1]["name"] == "label"


def test_field_with_no_expression_at_all_is_skipped():
    doc = _doc()
    doc["datasets"][0]["fields"].append({"name": "odd"})
    assert osf.to_snowflake(doc)["report"]["skipped_by_snowflake"][0]["object"] == "sales.odd"


@pytest.mark.parametrize("dialect", ["SNOWFLAKE", "ANSI_SQL"])
def test_snowflake_or_ansi_metric_has_no_row(dialect):
    out = osf.to_snowflake(_metric_doc(("DAX", "1"), (dialect, "SUM(a)")))
    assert not _rows(out, rule="snowflake/dialect") and out["report"]["skipped_by_snowflake"] == []


def test_dataset_metrics_are_checked_and_left_in_their_dataset():
    out = osf.to_snowflake(_metric_doc(("DAX", "1"), at="dataset"))
    assert _model(out)["datasets"][0]["metrics"][0]["name"] == "m"
    assert out["report"]["skipped_by_snowflake"][0]["object"] == "sales.m"


# ---- OSSIE_SQL_2026 translated

def test_ossie_sql_is_translated_beside_the_original():
    out = osf.to_snowflake(_metric_doc(("OSSIE_SQL_2026", "COUNT(DISTINCT customer_id)")))
    assert _model(out)["metrics"][1]["expression"]["dialects"] == [
        {"dialect": "OSSIE_SQL_2026", "expression": "COUNT(DISTINCT customer_id)"},
        {"dialect": "SNOWFLAKE", "expression": "COUNT(DISTINCT customer_id)"}]
    [row] = _rows(out, rule="snowflake/translated")
    assert row["class"] == "unverified" and "Snowflake hasn't run it" in row["message"]
    assert out["report"]["skipped_by_snowflake"] == []


def test_translation_off_leaves_ossie_sql_unsupported():
    out = osf.to_snowflake(_metric_doc(("OSSIE_SQL_2026", "SUM(a)")), translate_ossie_sql=False)
    [row] = _rows(out, "unsupported", "snowflake/dialect")
    assert "translation is off" in row["message"]
    assert [e["dialect"] for e in _model(out)["metrics"][1]["expression"]["dialects"]] == ["OSSIE_SQL_2026"]
    assert out["report"]["translate_ossie_sql"] is False


def test_untranslatable_ossie_sql_is_unsupported_with_the_reason():
    out = osf.to_snowflake(_metric_doc(("OSSIE_SQL_2026", "FOO_BAR(a)")))
    [row] = _rows(out, "unsupported", "snowflake/dialect")
    assert "calls FOO_BAR, which sqlglot doesn't know" in row["message"]
    assert len(_model(out)["metrics"][1]["expression"]["dialects"]) == 1


def test_extra_dax_entries_are_left_unchanged():
    out = osf.to_snowflake(_metric_doc(("DAX", "SUM(x)"), ("OSSIE_SQL_2026", "SUM(a)"), ("DAX", "1")))
    dialects = [e["dialect"] for e in _model(out)["metrics"][1]["expression"]["dialects"]]
    assert dialects == ["DAX", "OSSIE_SQL_2026", "DAX", "SNOWFLAKE"]


@pytest.mark.parametrize("sql, expected", [
    pytest.param("SUM(amount)", "SUM(amount)", id="sum"),
    pytest.param("COUNT(DISTINCT customer_id)", "COUNT(DISTINCT customer_id)", id="distinct"),
    pytest.param("TRUNC(order_date)", "TRUNC(order_date)", id="trunc"),
])
def test_translate_ok(sql, expected):
    assert osf.translate(sql) == (expected, "")


@pytest.mark.parametrize("sql, reason", [
    pytest.param("FOO_BAR(x)", "calls FOO_BAR, which sqlglot doesn't know", id="anon"),
    pytest.param("FOO(x) + BAR(y)", "calls FOO and BAR, which sqlglot doesn't know", id="two-anon"),
    pytest.param("SUM(amount", "couldn't be read", id="paren"),
    pytest.param("amount +", "couldn't be read", id="dangling"),
    pytest.param("SELECT amount FROM t", "isn't one expression", id="select"),
    pytest.param("a; b", "isn't one expression", id="two-statements"),
    pytest.param("amount + (SELECT 1)", "isn't one expression", id="nested-select"),
    pytest.param("DROP TABLE t", "isn't one expression", id="ddl"),
    pytest.param("", "is empty", id="empty"),
    pytest.param("   ", "is empty", id="blank"),
    pytest.param("MAP(1, 2)", "can't write as Snowflake SQL", id="unsupported"),
])
def test_translate_refused(sql, reason):
    out, why = osf.translate(sql)
    assert out is None and reason in why and len(why) <= osf.MAX_REASON


def test_long_unreadable_expression_is_never_quoted():
    private = "customer_private_value_" * 40
    for sql in (f"SUM({private}", f"{private}_FN({private})", f"{private}; {private}"):
        _, why = osf.translate(sql)
        assert len(why) <= osf.MAX_REASON and private[:30] not in why
    out = osf.to_snowflake(_metric_doc(("OSSIE_SQL_2026", f"SUM({private}")))
    assert all(private[:30] not in r["message"] for r in out["report"]["rows"])


def test_long_function_names_keep_the_reason_short():
    _, why = osf.translate("A" * 300 + "(x) + " + "B" * 300 + "(y)")
    assert len(why) <= osf.MAX_REASON and why.startswith("calls ")



@pytest.mark.parametrize("sql", ["a -- x */ ; DROP TABLE t", "a /* note */ + b", "/* x */ SUM(a)"], ids=["line", "inline", "lead"])
def test_comments_never_reach_the_snowflake_sql(sql):
    out, why = osf.translate(sql)
    assert why == "" and out is not None
    assert "/*" not in out and "*/" not in out and "--" not in out and "DROP" not in out.upper()

# ---- left unchanged

def test_datatype_and_ai_context_are_left_unchanged():
    doc = _doc(ai_context={"synonyms": ["sales"]})
    doc["datasets"][0]["fields"][0]["datatype"] = "Decimal"
    doc["datasets"][0]["ai_context"] = "the sales table"
    model = _model(osf.to_snowflake(doc))
    assert model["ai_context"] == {"synonyms": ["sales"]} and model["datasets"][0]["ai_context"] == "the sales table"
    assert model["datasets"][0]["fields"][0]["datatype"] == "Decimal"


def test_relationships_and_descriptions_are_left_unchanged():
    doc = _doc(description="Orders", relationships=[{"name": "r", "from": "sales", "to": "sales",
                                                      "from_columns": ["a"], "to_columns": ["b"]}])
    model = _model(osf.to_snowflake(doc))
    assert model["description"] == "Orders" and model["relationships"] == doc["relationships"]
