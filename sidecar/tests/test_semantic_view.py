"""Fabric Semantic View files (OSSIE-005a): the emitter, one fixture per documented rule, both split modes, hostile
view names, roles and calculation groups, the file writer and the parity cross-check. Nothing reaches Fabric."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
import yaml

from fabric_desktop import jsonfile, ossie_profiles as op, semantic_parity as sp, semantic_view as sv, tmdl
from test_model_ossie import _bim, _ossie, svc  # noqa: F401 - svc is a fixture
from test_ossie_fidelity import _with_calculation_group

UNTRANSLATABLE = {"name": "median", "expression": {"dialects": [
    {"dialect": "ANSI_SQL", "expression": "MEDIAN(amount) OVER (PARTITION BY channel_sk)"}]}}

DAX_METRIC = {"name": "orders", "expression": {"dialects": [{"dialect": "DAX", "expression": "COUNTROWS('fact_sales')"}]}}


def _doc(**extra):
    doc = yaml.safe_load(_ossie())
    doc.update(extra)
    return doc


def _files(out):
    return {n: yaml.safe_load(t) for n, t in out["files"].items()}


def _row(report, ident):
    return next(o for o in report["objects"] if o["object"] == ident)


def _walk_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_keys(v)


def _rich_bim():
    bim = _with_calculation_group(_bim())
    bim["model"]["perspectives"] = [{"name": "Sales only", "tables": [{"name": "fact_sales"}]}]
    return bim


# ---- a clean model, both split modes

@pytest.mark.parametrize("split", sv.SPLITS)
def test_a_clean_model_gives_valid_yaml_files_only_each_with_version_and_name(split):
    out = sv.semantic_view(_bim(), view_name="Sales", split=split)
    assert out["files"] and all(n.endswith(".yaml") for n in out["files"])  # fabric/yml-ignored: never .yml
    for doc in _files(out).values():
        assert doc["version"] == op.V02 and doc["name"] == "Sales"  # fabric/version, fabric/fragment
        assert "datasets" not in doc or doc["datasets"]  # fabric/fragment: never an empty list
    report = out["report"]
    assert report["validation"]["status"] == "Valid" and report["valid"]  # fabric/schema and fabric/merged
    assert report["validation"]["merged"]["status"] == "Valid"
    assert report["kind"] == "Semantic View YAML" and report["profile"] == "fabric-semantic-view"
    assert "doesn't publish" in report["publish"]


def test_single_writes_one_file_named_after_the_view():
    out = sv.semantic_view(_bim(), view_name="Sales", split="single")
    assert list(out["files"]) == ["Sales.yaml"]
    doc = _files(out)["Sales.yaml"]
    assert [d["name"] for d in doc["datasets"]] == ["fact_sales", "dim_customer"]
    assert out["report"]["split"] == {"requested": "single", "used": "single", "message": ""}


def test_by_dataset_puts_the_model_parts_first_and_one_dataset_with_its_metrics_in_each_other_file():
    bim = _bim()
    bim["model"]["description"] = "Sales model"
    out = sv.semantic_view(bim, view_name="Sales", split="by_dataset")
    files = _files(out)
    assert list(files) == ["00_model.yaml", "01_fact_sales.yaml", "02_dim_customer.yaml"]
    first = files["00_model.yaml"]
    assert "datasets" not in first and first["relationships"][0]["from"] == "fact_sales"
    assert [d["name"] for d in files["01_fact_sales.yaml"]["datasets"]] == ["fact_sales"]
    assert [m["name"] for m in files["01_fact_sales.yaml"]["metrics"]] == ["fact_sales rows", "fact_sales total amount"]
    assert "metrics" not in files["02_dim_customer.yaml"]
    # fabric/description: the top-level description is in one file only; fabric/conflict: no other shared key
    assert [n for n, d in files.items() if "description" in d] == ["00_model.yaml"]
    assert all(set(d) <= {"version", "name", "datasets", "metrics"} for n, d in files.items() if n != "00_model.yaml")
    assert out["report"]["validation"]["status"] == "Valid"


def test_by_dataset_keeps_the_description_in_the_first_file_only():
    out = sv.semantic_view(_doc(description="Orders view"), view_name="Orders", split="by_dataset")
    files = _files(out)
    assert [n for n, d in files.items() if "description" in d] == ["00_model.yaml"]  # fabric/description
    assert out["report"]["validation"]["status"] == "Valid"


def test_past_twenty_datasets_by_dataset_merges_files_and_says_so():
    doc = _doc()
    doc["datasets"] = [{"name": f"t{i:02d}", "source": f"dbo.t{i:02d}"} for i in range(25)]
    doc["relationships"] = []
    out = sv.semantic_view(doc, view_name="Wide", split="by_dataset")
    assert len(out["files"]) <= op.FABRIC_MAX_FILES  # fabric/file-count
    split = out["report"]["split"]
    assert split["used"] == "by_dataset (merged)" and "25 datasets" in split["message"]
    names = [d["name"] for doc in _files(out).values() for d in doc.get("datasets") or []]
    assert sorted(names) == [f"t{i:02d}" for i in range(25)]
    assert out["report"]["validation"]["status"] == "Valid"


def test_a_twenty_first_file_is_refused_by_the_check():
    doc = _doc()
    doc["datasets"] = [{"name": f"t{i:02d}", "source": f"dbo.t{i:02d}"} for i in range(19)]
    doc["relationships"] = []
    out = sv.semantic_view(doc, view_name="Wide", split="by_dataset")
    assert len(out["files"]) == op.FABRIC_MAX_FILES and out["report"]["split"]["message"] == ""
    files = dict(out["files"]) | {"21st.yaml": "version: 0.2.0.dev0\nname: Wide\n"}
    result = op.validate_files(files, "fabric-semantic-view")
    assert not result["valid"] and [d["rule"] for d in result["diagnostics"]] == ["fabric/file-count"]


@pytest.mark.parametrize("source_kind", ["tmsl", "tmsl-text", "tmdl", "ossie", "ossie-text"])
def test_every_source_kind_is_read(source_kind):
    source = {"tmsl": _bim(), "tmsl-text": json.dumps(_bim()), "tmdl": tmdl.from_bim(_bim())[0],
              "ossie": _doc(), "ossie-text": _ossie()}[source_kind]
    out = sv.semantic_view(source, view_name="V")
    assert out["report"]["source_kind"] == source_kind.split("-")[0]
    assert out["report"]["validation"]["status"] == "Valid"


@pytest.mark.parametrize("source, says", [
    ({"version": "0.3.0.dev0", "name": "x", "datasets": [{"name": "a", "source": "b"}]}, "can't convert it yet"),
    ({"version": "9", "name": "x", "datasets": []}, "doesn't know"),
    ({"name": "x"}, "isn't a semantic model"),
    ([], "made from"),
])
def test_what_isnt_a_convertible_model_is_refused(source, says):
    with pytest.raises((ValueError, TypeError), match=says):
        sv.semantic_view(source, view_name="V")


def test_an_unknown_split_is_refused():
    with pytest.raises(ValueError, match="split is one of"):
        sv.semantic_view(_bim(), view_name="V", split="by_table")


def test_the_emitter_never_writes_a_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sv.semantic_view(_rich_bim(), view_name="V", split="by_dataset")
    assert list(tmp_path.iterdir()) == []


# ---- what Fabric omits: stripped from the files, each reported Unsupported with its rule

def test_custom_extensions_are_stripped_and_reported():  # fabric/extensions-omitted
    out = sv.semantic_view(_bim(), view_name="V")
    assert "custom_extensions" not in set(_walk_keys(_files(out)))
    row = _row(out["report"], "dataset 'fact_sales' custom_extensions")
    assert row["class"] == "unsupported" and row["rule"] == "fabric/extensions-omitted"
    assert row["source"] == op.FABRIC_YAML_REFERENCE and "lineageTag" in row["in_extension"]
    assert out["report"]["validation"]["warnings"] == []  # nothing left for Fabric to warn about


def test_fields_keys_and_ai_context_are_stripped_and_reported():  # fabric/omitted
    doc = _doc(ai_context={"instructions": "sales"})
    doc["datasets"][1]["unique_keys"] = [["channel"]]
    doc["datasets"][1]["ai_context"] = {"synonyms": ["channel"]}
    doc["relationships"][0]["ai_context"] = {"instructions": "join"}
    out = sv.semantic_view(doc, view_name="V")
    keys = set(_walk_keys(_files(out)))
    assert not keys & {"fields", "primary_key", "unique_keys", "ai_context", "custom_extensions"}
    omitted = {(o["object"], o["construct"], o["rule"]) for o in out["report"]["omitted"]}
    assert {("model ai_context", "ai_context", "fabric/omitted"),
            ("dataset 'fact_sales' field 'amount'", "fields", "fabric/omitted"),
            ("dataset 'dim_channel' primary_key", "primary_key", "fabric/omitted"),
            ("dataset 'dim_channel' unique_keys", "unique_keys", "fabric/omitted"),
            ("dataset 'dim_channel' ai_context", "ai_context", "fabric/omitted"),
            ("relationship 'sales_channel' ai_context", "ai_context", "fabric/omitted")} <= omitted
    for o in out["report"]["omitted"]:
        assert _row(out["report"], o["object"])["class"] == "unsupported" and o["source"]
    assert _row(out["report"], "dataset 'dim_channel'")["class"] == "fully_preserved"
    assert out["report"]["validation"]["status"] == "Valid"


def test_metric_datatype_is_stripped_and_reported():  # metric datatype is ignored
    doc = _doc(metrics=[{"name": "rows", "datatype": "Integer",
                         "expression": {"dialects": [{"dialect": "DAX", "expression": "COUNTROWS('fact_sales')"}]}}])
    out = sv.semantic_view(doc, view_name="V")
    metric = next(m for m in _files(out)["V.yaml"]["metrics"] if m["name"] == "rows")
    assert "datatype" not in metric
    assert _row(out["report"], "metric 'rows' datatype")["rule"] == "semantic-view/metric-datatype"


def test_a_key_outside_the_supported_yaml_is_stripped_and_reported():
    doc = _doc(metrics=[{"name": "rows", "owner": "finance",
                         "expression": {"dialects": [{"dialect": "DAX", "expression": "COUNTROWS('fact_sales')"}]}}])
    out = sv.semantic_view(doc, view_name="V")
    row = _row(out["report"], "metric 'rows' key 'owner'")
    assert row["class"] == "unsupported" and row["rule"] == "semantic-view/supported-yaml"


def test_a_query_source_dataset_is_excluded_with_its_metrics_and_relationships():
    doc = _doc()
    doc["datasets"][1]["source"] = "SELECT channel_sk, channel FROM dbo.dim_channel"
    out = sv.semantic_view(doc, view_name="V")
    files = _files(out)["V.yaml"]
    assert [d["name"] for d in files["datasets"]] == ["fact_sales"] and "relationships" not in files
    excluded = {e["object"]: e["rule"] for e in out["report"]["excluded"]}
    assert excluded["dataset 'dim_channel'"] == "semantic-view/query-source"
    assert excluded["relationship 'sales_channel'"] == "semantic-view/query-source"


def test_a_view_whose_datasets_are_all_query_sources_warns_that_fabric_infers_them():
    doc = _doc(relationships=[])
    for d in doc["datasets"]:
        d["source"] = "WITH x AS (SELECT 1) SELECT * FROM x"
    out = sv.semantic_view(doc, view_name="V")
    assert all("datasets" not in d for d in _files(out).values())
    assert [w["rule"] for w in out["report"]["validation"]["warnings"]] == ["fabric/datasets-inferred"]


# ---- metrics: DAX on every one

def test_a_dax_metric_is_fully_preserved():
    out = sv.semantic_view(_bim(), view_name="V")
    row = _row(out["report"], "metric 'fact_sales rows'")
    assert row["class"] == "fully_preserved"
    for m in _files(out)["V.yaml"]["metrics"]:
        assert m["expression"]["dialects"][0]["dialect"] == "DAX"  # fabric/metric-dax


def test_a_translatable_sql_only_metric_gets_dax_and_is_unverified():
    out = sv.semantic_view(_ossie(), view_name="V")
    metric = next(m for m in _files(out)["V.yaml"]["metrics"] if m["name"] == "net revenue")
    assert metric["expression"]["dialects"][0] == {"dialect": "DAX", "expression": "SUM('fact_sales'[amount])"}
    assert {"dialect": "ANSI_SQL", "expression": "SUM(fact_sales.amount)"} in metric["expression"]["dialects"]
    assert _row(out["report"], "dataset 'fact_sales' metric 'net revenue'")["class"] == "unverified"


def test_an_untranslatable_sql_only_metric_is_flagged_before_export_and_excluded():
    out = sv.semantic_view(_ossie(UNTRANSLATABLE), view_name="V")
    names = [m["name"] for d in _files(out).values() for m in d.get("metrics") or []]
    assert names == ["net revenue"]
    excluded = next(e for e in out["report"]["excluded"] if e["object"] == "dataset 'fact_sales' metric 'median'")
    assert excluded["rule"] == "fabric/metric-dax" and "excluded before export" in excluded["reason"]
    assert "couldn't be translated to DAX" in excluded["reason"] and "ANSI_SQL" in excluded["reason"]
    assert _row(out["report"], "dataset 'fact_sales' metric 'median'")["class"] == "unsupported"
    assert out["report"]["validation"]["status"] == "Valid"  # what remains publishes


def test_a_metric_with_no_expression_is_excluded():
    doc = _doc(metrics=[{"name": "empty", "expression": {"dialects": []}}])
    out = sv.semantic_view(doc, view_name="V")
    assert any(e["object"] == "metric 'empty'" and e["rule"] == "fabric/metric-dax" for e in out["report"]["excluded"])


def test_a_dataset_metric_named_like_a_model_metric_is_excluded():  # fabric/duplicate-name
    doc = _doc(metrics=[{"name": "net revenue",
                         "expression": {"dialects": [{"dialect": "DAX", "expression": "SUM('fact_sales'[amount])"}]}}])
    out = sv.semantic_view(doc, view_name="V", split="by_dataset")
    assert [m["name"] for d in _files(out).values() for m in d.get("metrics") or []] == ["net revenue"]
    assert any(e["rule"] == "fabric/duplicate-name" for e in out["report"]["excluded"])
    assert out["report"]["validation"]["status"] == "Valid"


# ---- relationships

def test_a_relationship_that_isnt_single_column_is_excluded():  # fabric/relationship-single-column
    doc = _doc()
    doc["relationships"].append({"name": "two", "from": "fact_sales", "to": "dim_channel",
                                 "from_columns": ["channel_sk", "customer_sk"], "to_columns": ["channel_sk", "x"]})
    out = sv.semantic_view(doc, view_name="V")
    rels = _files(out)["V.yaml"]["relationships"]
    assert [r["name"] for r in rels] == ["sales_channel"]
    row = _row(out["report"], "relationship 'two'")
    assert row["class"] == "unsupported" and row["excluded"] and row["rule"] == "fabric/relationship-single-column"
    assert out["report"]["validation"]["status"] == "Valid"


# ---- the limits the check owns

def test_yaml_over_the_combined_limit_is_invalid(monkeypatch):  # fabric/yaml-size
    monkeypatch.setattr(op, "FABRIC_MAX_YAML_BYTES", 100)
    out = sv.semantic_view(_bim(), view_name="V")
    assert [d["rule"] for d in out["report"]["validation"]["diagnostics"]] == ["fabric/yaml-size"]
    assert not out["report"]["valid"]


def test_generated_tmdl_size_is_not_claimed():  # fabric/tmdl-size: checked only when the TMDL is given
    out = sv.semantic_view(_bim(), view_name="V")
    assert out["report"]["validation"]["counts"]["tmdl_bytes"] is None


# ---- roles and calculation groups

def test_roles_and_calculation_groups_are_reported_never_dropped_silently():
    out = sv.semantic_view(_rich_bim(), view_name="V")
    report = out["report"]
    role = _row(report, "role 'EU only'")
    assert role["class"] == "unsupported" and role["flag"] == "security" and role["key"] == ["role", "EU only"]
    group = _row(report, "table 'Time calc'")
    assert group["kind"] == "calculation_group" and group["class"] == "unsupported"
    assert report["flagged"] == {"roles": ["role 'EU only'"], "calculation_groups": ["table 'Time calc'"]}
    first = report["warnings"][0]
    assert first["severity"] == "security" and first["prominent"] and first["object"] == "role 'EU only'"
    assert "no row-level security" in first["message"]
    assert any(w["object"] == "table 'Time calc'" and w["prominent"] for w in report["warnings"])
    assert _row(report, "perspective 'Sales only'")["class"] == "unsupported"
    assert _row(report, "dataset 'dim_customer' hierarchy 'Geo'")["class"] == "unsupported"
    text = "".join(out["files"].values())
    assert "EU only" not in text and "Time calc" not in text


def test_the_report_lists_every_omitted_construct():
    out = sv.semantic_view(_rich_bim(), view_name="V")
    report = out["report"]
    stripped = {o["object"] for o in report["omitted"]}
    for ident in ("model custom_extensions", "role 'EU only'", "table 'Time calc'", "perspective 'Sales only'",
                  "expression 'DatabaseQuery'", "dataset 'dim_customer' primary_key",
                  "dataset 'dim_customer' hierarchy 'Geo'", "dataset 'fact_sales' field 'amount'"):
        assert ident in stripped, ident
    assert all(_row(report, i)["class"] == "unsupported" for i in stripped)
    assert sum(report["counts"].values()) == len(report["objects"])


# ---- hostile view names

@pytest.mark.parametrize("name", ["../up", "a/b", "a\\b", "..", "x..y", "CON", "con.txt", "LPT1", "", "   ", "...",
                                  None, 7, "Sales.", "Sales ", " Sales", ".Sales", "Sales. ", "Sales\t"])
def test_a_hostile_view_name_is_refused(name):
    with pytest.raises(ValueError):
        sv.semantic_view(_bim(), view_name=name)


@pytest.mark.parametrize("name, folder", [('Sales: "Q1"?', 'Sales_ _Q1__'), ("a\x00b", "a_b"), ("Sales v2", "Sales v2"),
                                          ("Sales.v2", "Sales.v2")])
def test_a_view_name_is_sanitised_like_a_tmdl_file_name(name, folder):
    out = sv.semantic_view(_bim(), view_name=name)
    assert out["report"]["view"] == folder and list(out["files"]) == [f"{folder}.yaml"]


# ---- the parity cross-check

def test_parity_reports_what_the_view_strips_as_unsupported_never_as_a_match():
    bim = _rich_bim()
    out = sv.semantic_view(bim, view_name="V")
    for right in (out, out["files"]):  # with the emitter's report, and from the files alone
        report = sp.parity_l1(bim, right, right_kind="semantic_view")
        assert report["error"] == "" and report["sides"]["right"]["profile"] == "fabric-semantic-view"
        rows = {o["id"]: o for o in report["objects"]}
        for ident in ("role 'EU only'", "table 'Time calc'", "perspective 'Sales only'",
                      "table 'dim_customer' hierarchy 'Geo'", "table 'fact_sales' column 'amount'"):
            assert rows[ident]["result"] == sp.UNSUPPORTED, ident
        assert rows["table 'fact_sales'"]["result"] == sp.COMPATIBLE
        assert report["counts"][sp.VERIFIED] == 0 and report["counts"][sp.DIFFERENT] == 0


def test_parity_reports_an_excluded_metric_as_unsupported():
    source = _ossie(UNTRANSLATABLE)
    out = sv.semantic_view(source, view_name="V")
    rows = {o["id"]: o for o in sp.parity_l1(source, out, right_kind="semantic_view")["objects"]}
    assert rows["table 'fact_sales' measure 'median'"]["result"] == sp.UNSUPPORTED
    assert "excluded before export" in " ".join(rows["table 'fact_sales' measure 'median'"]["notes"])


def test_a_semantic_view_side_that_isnt_ossie_0_2_fails_the_test_not_the_caller():
    report = sp.parity_l1(_bim(), {"v.yaml": "version: 0.3.0.dev0\nname: v\n"}, right_kind="semantic_view")
    assert report["summary"] == sp.TEST_FAILED


def test_merged_joins_the_files_as_publishing_does():
    out = sv.semantic_view(_bim(), view_name="V", split="by_dataset")
    doc = sv.merged(out["files"])
    assert [d["name"] for d in doc["datasets"]] == ["fact_sales", "dim_customer"]
    assert len(doc["metrics"]) == 2 and len(doc["relationships"]) == 1
    with pytest.raises(ValueError):
        sv.merged({"a.yml": "x: 1"})


# ---- the writer (meta_model_semantic_view_save)

def _views(svc):  # noqa: F811
    return Path(svc._project().root) / "semantic-views"


def test_the_writer_previews_then_writes_only_with_the_token(svc):  # noqa: F811
    plan = svc.meta_model_semantic_view_save("Sales", text=json.dumps(_bim()))["confirm"]
    assert plan["folder"] == "semantic-views/Sales" and plan["files"] == ["semantic-views/Sales/Sales.yaml"]
    assert plan["published"] is False and not _views(svc).exists()
    done = svc.meta_model_semantic_view_save("Sales", text=json.dumps(_bim()), confirmed=True, token=plan["token"])
    written = (_views(svc) / "Sales" / "Sales.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(written)["name"] == "Sales" and done["report"]["valid"]


def test_the_writer_refuses_a_token_that_doesnt_match(svc):  # noqa: F811
    with pytest.raises(ValueError, match="changed since the preview"):
        svc.meta_model_semantic_view_save("Sales", text=_ossie(), confirmed=True, token="0" * 64)
    with pytest.raises(ValueError, match="changed since the preview"):
        svc.meta_model_semantic_view_save("Sales", text=_ossie(), confirmed=True)
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie())["confirm"]
    with pytest.raises(ValueError, match="changed since the preview"):  # another definition, the old token
        svc.meta_model_semantic_view_save("Sales", text=_ossie(DAX_METRIC), confirmed=True, token=plan["token"])
    assert not _views(svc).exists()


def test_the_writer_removes_a_stale_yaml_file_only_after_the_confirm(svc):  # noqa: F811
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie(), split="by_dataset")["confirm"]
    svc.meta_model_semantic_view_save("Sales", text=_ossie(), split="by_dataset", confirmed=True, token=plan["token"])
    folder = _views(svc) / "Sales"
    (folder / "notes.md").write_text("mine", encoding="utf-8")
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie())["confirm"]
    assert sorted(plan["removed"]) == ["semantic-views/Sales/00_model.yaml", "semantic-views/Sales/01_fact_sales.yaml",
                                       "semantic-views/Sales/02_dim_channel.yaml"]
    assert (folder / "00_model.yaml").exists()
    svc.meta_model_semantic_view_save("Sales", text=_ossie(), confirmed=True, token=plan["token"])
    assert sorted(p.name for p in folder.iterdir()) == ["Sales.yaml", "notes.md"]


def test_the_writer_writes_each_file_whole(svc, monkeypatch):  # noqa: F811
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie())["confirm"]
    svc.meta_model_semantic_view_save("Sales", text=_ossie(), confirmed=True, token=plan["token"])
    target = _views(svc) / "Sales" / "Sales.yaml"
    before = target.read_text(encoding="utf-8")
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie(DAX_METRIC))["confirm"]

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(jsonfile, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        svc.meta_model_semantic_view_save("Sales", text=_ossie(DAX_METRIC), confirmed=True, token=plan["token"])
    assert target.read_text(encoding="utf-8") == before  # the old file, never half a new one
    assert sorted(p.name for p in target.parent.iterdir()) == ["Sales.yaml"]  # no temporary file left


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symbolic links")
def test_the_writer_never_follows_a_link(svc, tmp_path):  # noqa: F811
    outside = tmp_path / "outside"
    outside.mkdir()
    _views(svc).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="link"):
        svc.meta_model_semantic_view_save("Sales", text=_ossie())
    _views(svc).unlink()
    (_views(svc) / "Sales").mkdir(parents=True)
    (_views(svc) / "Sales" / "Sales.yaml").symlink_to(outside / "x.yaml")
    with pytest.raises(ValueError, match="link"):
        svc.meta_model_semantic_view_save("Sales", text=_ossie())
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("name", ["Context", "context", "SystemContext"])
def test_the_writer_refuses_a_context_folder(svc, name):  # noqa: F811
    with pytest.raises(ValueError, match="never writes there"):
        svc.meta_model_semantic_view_save(name, text=_ossie())
    assert not (_views(svc) / name).exists()


def test_the_writer_refuses_a_project_inside_a_context_folder(svc, monkeypatch, tmp_path):  # noqa: F811
    project = svc._project()
    moved = copy.copy(project)
    moved.root = str(tmp_path / "Lakehouse" / "Files" / "Context" / "proj")
    monkeypatch.setattr(svc, "_project", lambda: moved)
    with pytest.raises(ValueError, match="never writes there"):
        svc.meta_model_semantic_view_save("Sales", text=_ossie())
    assert not (tmp_path / "Lakehouse").exists()


def test_the_writer_never_writes_more_than_twenty_files(svc, monkeypatch):  # noqa: F811
    many = {f"{i:02d}.yaml": "version: 0.2.0.dev0\nname: V\n" for i in range(21)}
    monkeypatch.setattr(sv, "semantic_view", lambda *a, **k: {"files": many, "report": {}})
    with pytest.raises(ValueError, match="21 files"):
        svc.meta_model_semantic_view_save("Sales", text=_ossie())
    assert not _views(svc).exists()


def test_the_writer_reads_a_model_folder_in_the_project(svc):  # noqa: F811
    folder = Path(svc._project().root) / "semantic" / "SM_Gold.SemanticModel"
    folder.mkdir(parents=True)
    (folder / "model.bim").write_text(json.dumps(_rich_bim()), encoding="utf-8")
    plan = svc.meta_model_semantic_view_save("Gold", path="semantic/SM_Gold.SemanticModel")["confirm"]
    assert plan["report"]["flagged"]["roles"] == ["role 'EU only'"]
    with pytest.raises(LookupError):
        svc.meta_model_semantic_view_save("Gold", path="semantic/Nope.SemanticModel")
    with pytest.raises(ValueError, match="either"):
        svc.meta_model_semantic_view_save("Gold", text=_ossie(), path="semantic/SM_Gold.SemanticModel")


@pytest.mark.parametrize("name", ["../x", "CON", "a/b"])
def test_the_writer_refuses_a_hostile_view_name_before_reading(svc, name):  # noqa: F811
    with pytest.raises(ValueError):
        svc.meta_model_semantic_view_save(name, text=_ossie())
    assert not _views(svc).exists()


# ---- review follow-ups

@pytest.mark.parametrize("brk", ["\x85", "\u2028", "\u2029"])
def test_yaml_line_break_characters_are_escaped_and_read_back_exactly(brk):
    doc = _doc()
    doc["datasets"][0]["description"] = f"Orders{brk}by day"
    doc["metrics"] = [{"name": f"rows{brk}today", "description": f"a{brk}b", "expression": {"dialects": [
        {"dialect": "DAX", "expression": "COUNTROWS('fact_sales')"},
        {"dialect": "ANSI_SQL", "expression": f"SUM(CASE WHEN channel = 'a{brk}b' THEN 1 END)"}]}}]
    for split in sv.SPLITS:
        out = sv.semantic_view(doc, view_name="V", split=split)
        text = "".join(out["files"].values())
        assert brk not in text  # escaped (\\N, \\L, \\P), never written raw
        merged = sv.merged(out["files"])
        metric = next(m for m in merged["metrics"] if m["name"] == f"rows{brk}today")
        assert metric["description"] == f"a{brk}b"
        assert metric["expression"]["dialects"][1]["expression"] == f"SUM(CASE WHEN channel = 'a{brk}b' THEN 1 END)"
        assert merged["datasets"][0]["description"] == f"Orders{brk}by day"
        assert out["report"]["validation"]["status"] == "Valid"


def test_a_file_that_wouldnt_read_back_as_written_is_refused(monkeypatch):
    real = sv.mo.yaml.safe_load
    monkeypatch.setattr(sv.mo.yaml, "safe_load", lambda text: {**real(text), "name": "other"})
    with pytest.raises(ValueError, match="wouldn't read back as written"):
        sv._dump({"version": op.V02, "name": "V"})


def test_a_forged_report_cant_hide_a_measure_dropped_from_the_view():
    bim = _bim()
    out = sv.semantic_view(bim, view_name="V")
    doc = yaml.safe_load(out["files"]["V.yaml"])
    doc["metrics"] = [m for m in doc["metrics"] if m["name"] != "fact_sales rows"]  # dropped by hand
    forged = {"files": {"V.yaml": yaml.safe_dump(doc, sort_keys=False)}, "report": copy.deepcopy(out["report"])}
    forged["report"]["objects"].append({"object": "metric 'fact_sales rows'", "kind": "measure",
                                        "class": "unsupported", "key": ["measure", "fact_sales rows"],
                                        "rule": "fabric/metric-dax", "reason": "excluded before export (forged)"})
    rows = {o["id"]: o for o in sp.parity_l1(bim, forged, right_kind="semantic_view")["objects"]}
    dropped = rows["table 'fact_sales' measure 'fact_sales rows'"]
    assert dropped["result"] == sp.DIFFERENT and "forged" not in " ".join(dropped["notes"])
    assert rows["role 'EU only'"]["result"] == sp.UNSUPPORTED  # what the emitter really strips stays Unsupported


def test_schema_metrics_writes_learns_schema_and_metrics_files_that_pass_the_check():
    out = sv.semantic_view(_doc(description="Orders"), view_name="Orders", split="schema_metrics")
    assert sorted(out["files"]) == ["metrics.yaml", "schema.yaml"]
    files = _files(out)
    schema, metrics = files["schema.yaml"], files["metrics.yaml"]
    assert schema["version"] == metrics["version"] == op.V02 and schema["name"] == metrics["name"] == "Orders"
    assert set(schema) == {"version", "name", "description", "datasets", "relationships"}
    assert set(metrics) == {"version", "name", "metrics"} and metrics["metrics"][0]["name"] == "net revenue"
    result = op.validate_files(out["files"], "fabric-semantic-view")
    assert result["valid"] and result["status"] == "Valid" and result["counts"]["yaml_files"] == 2
    assert out["report"]["split"] == {"requested": "schema_metrics", "used": "schema_metrics", "message": ""}


def test_schema_metrics_with_no_metrics_still_gives_a_valid_pair():
    out = sv.semantic_view(_bim(), view_name="V", split="schema_metrics")
    out2 = sv.semantic_view(_doc(), view_name="V", split="schema_metrics")
    for o in (out, out2):
        assert op.validate_files(o["files"], "fabric-semantic-view")["valid"]


@pytest.mark.parametrize("split", sv.SPLITS)
def test_files_summary_says_what_each_file_holds(split):
    out = sv.semantic_view(_ossie(UNTRANSLATABLE), view_name="V", split=split)
    summary = out["report"]["files_summary"]
    assert [f["path"] for f in summary] == sorted(out["files"])
    for f in summary:
        assert f["bytes"] == len(out["files"][f["path"]].encode("utf-8"))
        doc = yaml.safe_load(out["files"][f["path"]])
        assert (f["datasets"], f["relationships"], f["metrics"]) == tuple(
            len(doc.get(k) or []) for k in ("datasets", "relationships", "metrics"))
        assert f["dax_metrics"] == f["metrics"]  # every metric written has DAX
    totals = {k: sum(f[k] for f in summary) for k in ("datasets", "relationships", "metrics", "dax_metrics")}
    assert totals == {"datasets": 2, "relationships": 1, "metrics": 1, "dax_metrics": 1}  # median excluded


def test_the_writer_writes_the_schema_metrics_files_flat(svc):  # noqa: F811
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie(), split="schema_metrics")["confirm"]
    svc.meta_model_semantic_view_save("Sales", text=_ossie(), split="schema_metrics", confirmed=True,
                                      token=plan["token"])
    folder = _views(svc) / "Sales"
    assert sorted(p.name for p in folder.iterdir()) == ["metrics.yaml", "schema.yaml"]
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie())["confirm"]
    assert sorted(plan["removed"]) == ["semantic-views/Sales/metrics.yaml", "semantic-views/Sales/schema.yaml"]


def test_a_yaml_file_in_a_subfolder_of_the_view_is_listed_as_removed(svc):  # noqa: F811
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie())["confirm"]
    svc.meta_model_semantic_view_save("Sales", text=_ossie(), confirmed=True, token=plan["token"])
    stray = _views(svc) / "Sales" / "schema" / "schema.yaml"  # e.g. made by hand for a shortcut
    stray.parent.mkdir()
    stray.write_text("version: 0.2.0.dev0\nname: Sales\n", encoding="utf-8")
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie())["confirm"]
    assert plan["removed"] == ["semantic-views/Sales/schema/schema.yaml"]  # Fabric merges subfolders too


def test_switching_from_one_file_to_schema_metrics_lists_the_old_root_file_as_removed(svc):  # noqa: F811
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie())["confirm"]
    svc.meta_model_semantic_view_save("Sales", text=_ossie(), confirmed=True, token=plan["token"])
    plan = svc.meta_model_semantic_view_save("Sales", text=_ossie(), split="schema_metrics")["confirm"]
    assert plan["removed"] == ["semantic-views/Sales/Sales.yaml"]  # Fabric would merge it into the view
    svc.meta_model_semantic_view_save("Sales", text=_ossie(), split="schema_metrics", confirmed=True,
                                      token=plan["token"])
    folder = _views(svc) / "Sales"
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.yaml")) == [
        "metrics.yaml", "schema.yaml"]


# ---- the publish-steps card's read (meta_model_semantic_view_status, OSSIE-005b)

def _saved(svc, split="schema_metrics", text=None):  # noqa: F811
    text = text or _ossie()
    plan = svc.meta_model_semantic_view_save("Sales", text=text, split=split)["confirm"]
    svc.meta_model_semantic_view_save("Sales", text=text, split=split, confirmed=True, token=plan["token"])
    return _views(svc) / "Sales"


def test_status_lists_views_and_reads_nothing_else(svc):  # noqa: F811
    assert svc.meta_model_semantic_view_status() == {"views": [], "folder": "semantic-views"}
    out = svc.meta_model_semantic_view_status("Sales")
    assert out["exists"] is False and out["files_summary"] == [] and out["valid"] is False
    assert not _views(svc).exists()  # nothing is made


def test_status_gives_the_summary_and_the_check(svc):  # noqa: F811
    _saved(svc)
    out = svc.meta_model_semantic_view_status("Sales")
    assert out["views"] == ["Sales"] and out["folder"] == "semantic-views/Sales" and out["published"] is False
    assert [f["path"] for f in out["files_summary"]] == ["metrics.yaml", "schema.yaml"]
    schema = out["files_summary"][1]
    assert (schema["datasets"], schema["relationships"], schema["metrics"]) == (2, 1, 0)
    assert out["valid"] and out["validation"]["counts"]["yaml_files"] == 2


def test_status_reads_a_crlf_copy_as_the_same_files(svc):  # noqa: F811
    folder = _saved(svc)
    before = svc.meta_model_semantic_view_status("Sales")
    for f in folder.iterdir():  # as Git on Windows checks them out
        f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    after = svc.meta_model_semantic_view_status("Sales")
    assert after["valid"] and after["files_summary"] == before["files_summary"]


def test_status_shows_a_failed_check_with_its_fix(svc):  # noqa: F811
    folder = _saved(svc)
    (folder / "extra.yaml").write_text("version: 0.1.0\nname: Sales\n", encoding="utf-8")
    (folder / "notes.yml").write_text("x: 1\n", encoding="utf-8")
    (folder / "bad.yaml").write_bytes(b"\xff\xfe")
    out = svc.meta_model_semantic_view_status("Sales")
    assert out["valid"] is False
    assert all(d["fix"] for r in out["validation"]["files"].values() for d in r["diagnostics"])
    assert out["validation"]["files"]["extra.yaml"]["valid"] is False
    assert {f["path"] for f in out["files_summary"] if f.get("unreadable")} == {"bad.yaml"}
    assert "notes.yml" in out["validation"]["counts"]["ignored"]


def test_status_compares_with_the_model_and_lists_what_is_left_out(svc):  # noqa: F811
    model = Path(svc._project().root) / "semantic" / "Sales.SemanticModel"
    model.mkdir(parents=True)
    (model / "model.bim").write_text(json.dumps(_rich_bim()), encoding="utf-8")
    plan = svc.meta_model_semantic_view_save("Sales", path="semantic/Sales.SemanticModel", split="schema_metrics")["confirm"]
    svc.meta_model_semantic_view_save("Sales", path="semantic/Sales.SemanticModel", split="schema_metrics", confirmed=True,
                                      token=plan["token"])
    src = svc.meta_model_semantic_view_status("Sales", path="semantic/Sales.SemanticModel")["source"]
    assert src["same"] and src["split"] == "schema_metrics"
    assert "role 'EU only'" in [r["object"] for r in src["left_out"]]
    (_views(svc) / "Sales" / "metrics.yaml").write_text("version: 0.2.0.dev0\nname: Sales\n", encoding="utf-8")
    assert svc.meta_model_semantic_view_status("Sales", path="semantic/Sales.SemanticModel")["source"]["same"] is False


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symbolic links")
def test_status_refuses_a_link_in_the_view_folder(svc, tmp_path):  # noqa: F811
    folder = _saved(svc)
    outside = tmp_path / "outside.yaml"
    outside.write_text("version: 0.2.0.dev0\nname: X\n", encoding="utf-8")
    (folder / "linked.yaml").symlink_to(outside)
    with pytest.raises(ValueError, match="link"):
        svc.meta_model_semantic_view_status("Sales")


def test_status_caps_count_and_size_before_reading(svc, monkeypatch):  # noqa: F811
    from fabric_desktop import model_ossie_service as mos
    folder = _saved(svc)
    monkeypatch.setattr(mos, "VIEW_MAX_ENTRIES", 3)
    for i in range(3):
        (folder / f"{i}.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="more than 3"):
        svc.meta_model_semantic_view_status("Sales")
    monkeypatch.setattr(mos, "VIEW_MAX_ENTRIES", 200)
    monkeypatch.setattr(op, "FABRIC_MAX_YAML_BYTES", 10)
    with pytest.raises(ValueError, match="16 MB"):
        svc.meta_model_semantic_view_status("Sales")


@pytest.mark.parametrize("name", ["../x", "CON", "a/b"])
def test_status_refuses_a_hostile_view_name(svc, name):  # noqa: F811
    with pytest.raises(ValueError):
        svc.meta_model_semantic_view_status(name)


def test_status_opens_only_the_saved_view_folder(svc, monkeypatch):  # noqa: F811
    from fabric_desktop import model_ossie_service as mos
    shown = []
    monkeypatch.setattr(mos, "_reveal", lambda path, select: shown.append((path, select)))
    with pytest.raises(ValueError, match="Save the view's files first"):
        svc.meta_model_semantic_view_status("Sales", reveal=True)
    assert shown == [] and not _views(svc).exists()
    folder = _saved(svc)
    out = svc.meta_model_semantic_view_status("Sales", reveal=True)
    assert shown == [(folder, False)] and out["shown"] == str(folder)


def test_status_is_read_only_in_the_audit_and_has_a_ui_caller():
    from fabric_desktop import audit, server
    assert "meta_model_semantic_view_status" in server.METHODS
    assert not audit.audited("meta_model_semantic_view_status")
    assert audit.audited("meta_model_semantic_view_save")
