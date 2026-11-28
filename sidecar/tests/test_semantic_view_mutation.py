"""Mutation-testing kill tests for fabric_desktop/semantic_view.py (batch 426): the exact report rows, paths,
reasons and notes the emitter writes, the Ossie checks it makes itself, what lives only in the POWER_BI extension,
the converter failing, and the file-count limit at exactly twenty datasets. Pure: nothing is fetched or written."""

from __future__ import annotations

import json

import pytest
import yaml

from fabric_desktop import model_ossie as mo, ossie_profiles as op, semantic_view as sv
from test_model_ossie import _bim, _ossie

DAX = {"dialects": [{"dialect": "DAX", "expression": "COUNTROWS('fact_sales')"}]}


def _doc(**extra):
    doc = yaml.safe_load(_ossie())
    doc.update(extra)
    return doc


def _row(report, ident):
    return next(o for o in report["objects"] if o["object"] == ident)


def _idents(report):
    return [o["object"] for o in report["objects"]]


# ---- what the emitter checks itself

@pytest.mark.parametrize("change", [{"name": ""}, {"datasets": []}, {"datasets": "fact_sales"}])
def test_an_ossie_document_with_no_name_or_no_datasets_is_refused(change):
    doc = _doc(**change)
    with pytest.raises(ValueError, match="needs a name and at least one dataset"):
        sv._read(doc, "V")


def test_ossie_json_text_is_read_with_the_ossie_size_limit(monkeypatch):
    text = json.dumps(_doc())
    assert sv._read(text, "V")[1] == "ossie"
    monkeypatch.setattr(mo, "MAX_CHARS", 100)  # JSON with no model object is read as Ossie text, with its limit
    with pytest.raises(ValueError, match="an Ossie model is at most"):
        sv._read(text, "V")


def test_json_nested_too_deeply_is_refused_with_a_plain_reason():
    with pytest.raises(ValueError, match="nested too deeply to be a semantic model"):
        sv._read('{"a":' * 100_000 + "1" + "}" * 100_000, "V")


def test_more_files_than_fabric_takes_is_refused_before_anything_is_returned(monkeypatch):
    many = {f"{i:02d}.yaml": "x" for i in range(op.FABRIC_MAX_FILES + 1)}
    monkeypatch.setattr(sv, "_files", lambda *a: (many, {"requested": "single", "used": "single", "message": ""}))
    with pytest.raises(RuntimeError, match="came to 21 files, but Fabric takes at most 20"):
        sv.semantic_view(_doc(), view_name="V")


def test_merged_refuses_a_file_that_isnt_one_object():
    with pytest.raises(ValueError, match="b.yaml isn't one object with version and name"):
        sv.merged({"a.yaml": "version: 0.2.0.dev0\nname: V\n", "b.yaml": "- 1\n"})


# ---- the report's rows: exact paths, reasons, kinds and notes

def test_a_clean_ossie_document_excludes_nothing_and_says_it_is_a_preview():
    report = sv.semantic_view(_doc(), view_name="V")["report"]
    assert report["excluded"] == [] and report["preview"] is True
    assert all(o["excluded"] is False for o in report["objects"])


def test_each_stripped_construct_has_its_exact_path_kind_and_reason():
    report = sv.semantic_view(_doc(), view_name="V")["report"]
    field = _row(report, "dataset 'fact_sales' field 'amount'")
    assert field["path"] == "$.datasets[0].fields[1]" and field["kind"] == "column"
    assert field["reason"] == ("left out of the view: Dataset fields are omitted with warnings. Field 'amount'. "
                               "Fabric reads the columns from the lakehouse table.")
    key = _row(report, "dataset 'dim_channel' primary_key")
    assert key["path"] == "$.datasets[1].primary_key" and key["kind"] == "primary_key"
    assert key["reason"] == "left out of the view: Dataset primary_key is omitted with warnings."


def test_a_dataset_row_notes_only_what_was_left_out_of_it():
    doc = _doc()
    doc["datasets"][1].pop("fields")
    doc["datasets"][1].pop("primary_key")
    report = sv.semantic_view(doc, view_name="V")["report"]
    assert _row(report, "dataset 'fact_sales'")["notes"] == ["fields left out"]
    assert _row(report, "dataset 'dim_channel'")["notes"] == []
    assert _row(report, "relationship 'sales_channel'")["notes"] == []


def test_a_dax_only_metric_has_no_other_dialects_note():
    doc = _doc(metrics=[{"name": "orders", "expression": DAX}])
    row = _row(sv.semantic_view(doc, view_name="V")["report"], "metric 'orders'")
    assert row["class"] == "fully_preserved" and row["notes"] == []


def test_a_dataset_or_metric_with_an_empty_name_is_excluded_by_position():
    doc = _doc(metrics=[{"name": "", "expression": DAX}])
    doc["datasets"].append({"name": "", "source": "dbo.x"})
    report = sv.semantic_view(doc, view_name="V")["report"]
    assert _row(report, "dataset #3")["excluded"] and _row(report, "metric #1")["excluded"]
    assert _row(report, "metric #1")["path"] == "$.metrics[0]"
    assert "dataset ''" not in _idents(report) and "metric ''" not in _idents(report)


def test_what_was_lost_converting_the_model_is_a_row_of_the_view(monkeypatch):
    real = mo.export

    def export(*a, **kw):
        out = real(*a, **kw)
        out["fidelity"]["objects"].append({"class": "unsupported", "kind": "table", "object": "table 'Lost'",
                                           "remediation": "rebuild it"})
        return out

    monkeypatch.setattr(mo, "export", export)
    row = _row(sv.semantic_view(_bim(), view_name="V")["report"], "table 'Lost'")
    assert row["excluded"] and row["class"] == "unsupported"
    assert row["reason"] == "lost converting the model to Ossie, before the view: rebuild it"


def test_a_converter_that_cant_read_the_document_says_why_on_each_sql_metric(monkeypatch):
    def refuse(doc):
        raise ValueError("no tables")

    monkeypatch.setattr(mo, "to_tmsl", refuse)
    assert sv._translations(_doc()) == ({}, "the converter couldn't read the document (ValueError: no tables)")
    row = _row(sv.semantic_view(_doc(), view_name="V")["report"], "dataset 'fact_sales' metric 'net revenue'")
    assert row["excluded"] and "(the converter couldn't read the document (ValueError: no tables))" in row["reason"]


# ---- what lives only in the POWER_BI extension

def test_excluded_measures_and_relationships_in_the_extension_are_listed_one_by_one():
    stash = {"excludedMeasures": [{"table": "t", "measure": {"name": "kept m"}},
                                  {"table": "t", "measure": {"name": 5}}, {"table": "t", "measure": "x"}],
             "excludedRelationships": [
                 {"name": "r9", "fromTable": "a", "fromColumn": "x", "toTable": "b", "toColumn": "y"},
                 {"name": "", "fromTable": "a", "fromColumn": "x", "toTable": "b", "toColumn": "z"}]}
    doc = _doc(custom_extensions=[{"vendor_name": "POWER_BI", "data": json.dumps(stash)}])
    report = sv.semantic_view(doc, view_name="V")["report"]
    ext = [o for o in report["objects"] if o["path"] == "$.custom_extensions"]
    assert [o["object"] for o in ext] == ["model custom_extensions", "table 't' measure 'kept m'",
                                          "relationship 'r9'", "relationship 'a.x -> b.z'"]
    assert ext[0]["reason"].endswith("omitted with warnings. Vendors: POWER_BI.")
    assert ext[1]["key"] == ["measure", "kept m"] and ext[1]["kind"] == "measure"


def test_an_extension_table_lists_its_members_with_their_own_keys():
    stash = {"excludedTables": [
        {"name": "Time calc", "calculationGroup": {"items": []}, "columns": [{"name": "Period"}],
         "measures": [{"name": "ytd"}], "hierarchies": [{"name": "h"}]},
        {"name": "plain", "columns": [{"name": "c"}]}]}
    doc = _doc(custom_extensions=[{"vendor_name": "POWER_BI", "data": json.dumps(stash)}])
    report = sv.semantic_view(doc, view_name="V")["report"]
    assert _row(report, "table 'Time calc' measure 'ytd'")["key"] == ["measure", "ytd"]
    assert _row(report, "table 'Time calc' column 'Period'")["key"] == ["column", "Time calc", "Period"]
    assert _row(report, "table 'Time calc' hierarchy 'h'")["key"] == ["hierarchy", "Time calc", "h"]
    assert _row(report, "table 'Time calc'")["reason"].endswith("; a view has no calculation groups")
    assert _row(report, "table 'plain'")["reason"] == ("can't go into a Semantic View: it lives only in the "
                                                       "POWER_BI extension, which Fabric omits")
    assert _row(report, "table 'plain'")["kind"] == "table"


def test_an_empty_extension_list_names_no_vendor():
    report = sv.semantic_view(_doc(custom_extensions=[]), view_name="V")["report"]
    assert _row(report, "model custom_extensions")["reason"].endswith("omitted with warnings. Vendors: none.")


def test_a_field_with_no_name_is_reported_by_position_with_no_key():
    doc = _doc()
    doc["datasets"][0]["fields"].append({})
    row = _row(sv.semantic_view(doc, view_name="V")["report"], "dataset 'fact_sales' field #4")
    assert row["key"] is None and row["path"] == "$.datasets[0].fields[3]"
    assert row["reason"] == ("left out of the view: Dataset fields are omitted with warnings. Fabric reads the "
                             "columns from the lakehouse table.")


def test_a_dataset_metric_with_an_empty_name_is_excluded_by_position():
    doc = _doc()
    doc["datasets"][0]["metrics"].append({"name": "", "expression": DAX})
    row = _row(sv.semantic_view(doc, view_name="V")["report"], "dataset 'fact_sales' metric #2")
    assert row["excluded"] and row["path"] == "$.datasets[0].metrics[1]"


def test_a_query_source_metric_is_excluded():
    doc = _doc()
    doc["datasets"][0]["source"] = "SELECT * FROM dbo.fact_sales"
    row = _row(sv.semantic_view(doc, view_name="V")["report"], "dataset 'fact_sales' metric 'net revenue'")
    assert row["excluded"] is True and row["rule"] == "semantic-view/query-source"


def test_relationships_that_cant_be_written_are_named_as_unnamed_then_by_position():
    doc = _doc()
    doc["relationships"] = [{}, {"name": "", "from": "fact_sales", "to": "dim_channel", "from_columns": ["a", "b"]}]
    report = sv.semantic_view(doc, view_name="V")["report"]
    first, second = _row(report, "relationship '<unnamed>'"), _row(report, "relationship '<unnamed>' #2")
    assert first["excluded"] and first["path"] == "$.relationships[0]"
    assert first["reason"].startswith("excluded: it joins ? column(s) to ?, and Fabric")
    assert second["excluded"] and second["path"] == "$.relationships[1]"
    assert second["reason"].startswith("excluded: it joins 2 column(s) to ?, and Fabric")


def test_a_relationship_that_isnt_an_object_is_refused_by_path():
    doc = _doc()
    doc["relationships"] = ["junk"]
    with pytest.raises(ValueError, match=r"\$\.relationships\[0\] is text where it should be an object"):
        sv.semantic_view(doc, view_name="V")


def test_translations_give_the_converters_own_reason_and_only_translated_metrics():
    untranslatable = {"name": "median", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "MEDIAN(amount) OVER (PARTITION BY channel_sk)"}]}}
    assert sv._translations(_doc(metrics=[untranslatable])) == (
        {"median": (None, "only a single aggregate over one column translates unambiguously to DAX"),
         "net revenue": ("SUM('fact_sales'[amount])", "")}, "")


def test_translations_skip_a_dax_metric_whatever_else_it_is_annotated_with():
    note = json.dumps({"annotations": [{"name": "Note", "value": "x"}]})
    orders = {"name": "orders", "expression": DAX, "custom_extensions": [{"vendor_name": "POWER_BI", "data": note}]}
    assert sv._translations(_doc(metrics=[orders])) == ({"net revenue": ("SUM('fact_sales'[amount])", "")}, "")


def test_a_metric_the_converter_doesnt_return_gets_the_generic_reason(monkeypatch):
    monkeypatch.setattr(sv, "_translations", lambda doc: ({}, ""))
    row = _row(sv.semantic_view(_doc(), view_name="V")["report"], "dataset 'fact_sales' metric 'net revenue'")
    assert "(the vendored converter can't translate it to DAX)" in row["reason"]


def test_a_metric_keeps_only_its_first_dax_expression():
    doc = _doc(metrics=[{"name": "m", "expression": {"dialects": [
        {"dialect": "DAX", "expression": "1"}, {"dialect": "DAX", "expression": "2"}]}}])
    files = sv.semantic_view(doc, view_name="V")["files"]
    metric = next(m for m in yaml.safe_load(files["V.yaml"])["metrics"] if m["name"] == "m")
    assert metric["expression"]["dialects"] == [{"dialect": "DAX", "expression": "1"}]


@pytest.mark.parametrize("source", ["tmsl", "tmdl"])
def test_a_model_s_dax_measures_keep_their_sql_translation(source):
    from fabric_desktop import tmdl

    src = _bim() if source == "tmsl" else tmdl.from_bim(_bim())[0]
    doc = yaml.safe_load(sv.semantic_view(src, view_name="V")["files"]["V.yaml"])
    assert [(m["name"], [e["dialect"] for e in m["expression"]["dialects"]]) for m in doc["metrics"]] == [
        ("fact_sales rows", ["DAX"]), ("fact_sales total amount", ["DAX", "OSSIE_SQL_2026"])]


def test_a_view_file_is_block_yaml_in_document_order_with_unicode_as_written():
    doc = {"version": "0.2.0.dev0", "name": "n", "description": "Ventes é",
           "datasets": [{"name": "a", "source": "dbo.a"}],
           "metrics": [{"name": "m", "expression": {"dialects": [{"dialect": "DAX", "expression": "1"}]}}]}
    assert sv.semantic_view(doc, view_name="V")["files"]["V.yaml"] == (
        "version: 0.2.0.dev0\nname: V\ndescription: Ventes é\ndatasets:\n- name: a\n  source: dbo.a\nmetrics:\n"
        "- name: m\n  expression:\n    dialects:\n    - dialect: DAX\n      expression: '1'\n")


def test_twenty_datasets_by_dataset_are_merged_two_a_file():
    doc = _doc(relationships=[])
    doc["datasets"] = [{"name": f"t{i:02d}", "source": f"dbo.t{i:02d}"} for i in range(20)]
    out = sv.semantic_view(doc, view_name="Wide", split="by_dataset")
    assert out["report"]["split"] == {
        "requested": "by_dataset", "used": "by_dataset (merged)",
        "message": "20 datasets don't fit one file each in Fabric's 20 files, so they're merged, up to 2 datasets a "
                   "file, into 10 dataset files"}
    assert len(out["files"]) == 11


def test_bad_json_text_is_refused_with_a_plain_reason():
    with pytest.raises(ValueError, match="This isn't valid JSON"):
        sv._read("{bad", "V")


def test_merged_takes_other_keys_from_the_first_file_only():
    first = "version: 0.2.0.dev0\nname: V\ntags: [a]\ndatasets:\n- name: x\n"
    second = "version: 0.2.0.dev0\nname: V\ntags: [b]\ndatasets:\n- name: y\n"
    out = sv.merged({"a.yaml": first, "b.yaml": second, "c.txt": "- not read"})
    assert out["tags"] == ["a"] and out["datasets"] == [{"name": "x"}, {"name": "y"}]


def test_an_ossie_document_with_a_tmdl_named_key_is_still_read_as_ossie():
    out = sv.semantic_view(_doc(**{"notes.tmdl": 1}), view_name="V")
    assert out["report"]["source_kind"] == "ossie"
    assert _row(out["report"], "model key 'notes.tmdl'")["path"] == "$.notes.tmdl"


def test_the_files_summary_counts_only_metrics_with_a_dax_expression():
    text = yaml.safe_dump({"version": "0.2.0.dev0", "name": "V", "metrics": [
        {"name": "d", "expression": {"dialects": [{"dialect": "DAX", "expression": "1"}]}},
        {"name": "s", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(a)"}]}},
        {"name": "d2", "expression": {"dialects": [{"dialect": "DAX", "expression": "2"}]}},
        {"name": "e", "expression": {"dialects": [{"dialect": "DAX", "expression": ""}]}}, "junk"]})
    assert sv._summary({"V.yaml": text}) == [{"path": "V.yaml", "bytes": len(text.encode("utf-8")), "datasets": 0,
                                              "relationships": 0, "metrics": 4, "dax_metrics": 2}]
