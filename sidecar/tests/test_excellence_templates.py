"""Change templates for failed controls (FEA-402): text proposals only, filled from names in the
outcome, every name quoted for its language or left out, no destructive statement, deterministic."""

from __future__ import annotations

import json
import re

import pytest

from fabric_desktop.excellence import assess, catalog, catalog_artifacts, catalog_models, controls as ct
from fabric_desktop.excellence import evidence as ev
from fabric_desktop.excellence import findings as fx
from fabric_desktop.excellence import inspect_items, inspect_models, templates as tp
from fabric_desktop.service import Service

AT = "2026-11-21T02:00:00+00:00"
WS = "11111111-2222-3333-4444-555555555555"
ITEM = "22222222-2222-3333-4444-555555555555"
# Names an attacker (or an unlucky modeller) could give a table, column or activity.
HOSTILE = ["O'Brien", "a'b''c", "x]y", "x`y", "x\"y", "x;y", "Sales Δ ü", "名前", "a = b", "a: b",
           "{x}", "\\back", "#hash", "x // y", "@pipeline()[x]"]
UNSAFE = ["", " lead", "trail ", "a\nb", "a\rb", "a\tb", "a\u2028b", "a\u2029b", "a\u202eb", "a\u200bb",
          "a\x00b", "a\x1bb", "a\u00a0b", "x" * 300, "cut\u2026"]
DESTRUCTIVE = re.compile(r"\b(DROP|DELETE|TRUNCATE|OVERWRITE|ALTER\s+TABLE|RETAIN\s+\d)\b|rm\s+-|shutil\.rmtree|"
                         r"os\.remove|\.vacuum\(\s*\d|retentionDurationCheck\.enabled\W{0,6}false", re.IGNORECASE)
SECRETISH = re.compile(r"(password\s*=|AccountKey=|SharedAccessSignature|Bearer\s|eyJ[A-Za-z0-9_-]{10,}|"
                       r"Server=|Data Source=|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                       re.IGNORECASE)


def fail(control_id: str, detail: dict) -> dict:
    return {"control_id": control_id, "outcome": "fail", "detail": detail, "text": "x", "evidence_ids": ["e"],
            "subject": {"type": "item", "id": ITEM, "name": "n"}, "basis": "verified_configuration"}


NB_FAIL = {"rule": "NB001", "count": 1, "where": [{"cell": 1, "line": 2}]}
SAMPLES = {
    "FEA-SEC-005": NB_FAIL, "FEA-DEVOPS-001": NB_FAIL, "FEA-REL-001": NB_FAIL, "FEA-REL-002": NB_FAIL,
    "FEA-DE-003": NB_FAIL,
    "FEA-REL-003": {"inactive": ["Copy orders", "Notify"], "count": 2},
    "FEA-DEVOPS-002": {"activities": ["Run notebook"], "count": 1},
    "FEA-SM-001": {"relationships": ["Orders.customer_sk to Customer.customer_sk"], "count": 1},
    "FEA-SM-003": {"behavior": "Automatic", "tables": 3},
    "FEA-SM-006": {"rule": "SM004", "count": 2, "objects": ["Orders.customer_sk", "Orders.product_sk"]},
}


def all_templates() -> list[dict]:
    out = [t for cid, d in SAMPLES.items() for t in tp.for_outcome(fail(cid, d))]
    for name in HOSTILE:
        out += tp.for_outcome(fail("FEA-SM-001", {"relationships": [f"{name}.k to D.k"], "count": 1}))
        out += tp.for_outcome(fail("FEA-SM-006", {"objects": [f"{name}.k"], "count": 1}))
        out += tp.for_outcome(fail("FEA-REL-003", {"inactive": [name], "count": 1}))
        out += tp.for_outcome(fail("FEA-DEVOPS-002", {"activities": [name], "count": 1}))
    return out


# ---- a small TMDL reader for the lines templates write: Name or 'Quoted ''name'''


_TMDL_PART = r"(?:[A-Za-z_][A-Za-z0-9_]*|'(?:[^'\n]|'')*')"


def tmdl_unquote(part: str) -> str:
    return part[1:-1].replace("''", "'") if part.startswith("'") else part


def tmdl_ref(value: str) -> tuple[str, str]:
    m = re.fullmatch(rf"({_TMDL_PART})\.({_TMDL_PART})", value)
    assert m, value
    return tmdl_unquote(m.group(1)), tmdl_unquote(m.group(2))


def tmdl_props(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        m = re.fullmatch(r"\t(fromColumn|toColumn): (.*)", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


# ---- the set of controls


def test_templates_cover_six_to_ten_registered_controls():
    ids = {c.id for c in catalog.registry().controls()}
    assert 6 <= len(tp.CONTROLS) <= 10 and set(tp.CONTROLS) <= ids


@pytest.mark.parametrize("cid", sorted(SAMPLES))
def test_each_control_gives_well_formed_templates(cid):
    got = tp.for_outcome(fail(cid, SAMPLES[cid]))
    assert got
    for t in got:
        assert set(t) == {"control_id", "kind", "title", "text", "placeholders", "caveats"}
        assert t["control_id"] == cid and t["kind"] in tp.KINDS and t["title"] and t["text"].endswith("\n")
        assert t["caveats"][0].startswith("Proposal only")
        for p in t["placeholders"]:
            assert p["token"] in t["text"] and p["meaning"]


@pytest.mark.parametrize("outcome", ["pass", "not_applicable", "not_evaluated", "error"])
def test_no_template_unless_failed(outcome):
    for cid, d in SAMPLES.items():
        assert tp.for_outcome({**fail(cid, d), "outcome": outcome}) == []


def test_no_template_for_other_controls_or_odd_input():
    assert tp.for_outcome(fail("FEA-SEC-001", {"admins": 9})) == []
    assert tp.for_outcome(None) == [] and tp.for_outcome({}) == []
    assert tp.for_outcome({**fail("FEA-SM-001", {}), "detail": "nonsense"}) == []
    assert tp.for_outcome(fail("FEA-SM-001", {"relationships": "Orders.k to D.k"})) == []
    assert tp.for_outcome(fail("FEA-REL-003", {"inactive": [1, None, {"x": 1}]})) == []


def test_advisory_findings_carry_no_templates():
    c = catalog.registry().get("FEA-REL-003")
    o = {**fail("FEA-REL-003", SAMPLES["FEA-REL-003"]), "outcome": "not_evaluated", "basis": "inferred",
         "advisory": {"outcome": "fail", "text": "x", "evidence_ids": ["e"]}}
    assert fx.make(c, o, "low")["templates"] == []
    assert fx.make(c, fail("FEA-REL-003", SAMPLES["FEA-REL-003"]), "low")["templates"]


def test_determinism_and_order_independence():
    for cid, d in SAMPLES.items():
        a = tp.for_outcome(fail(cid, d))
        shuffled = {k: list(reversed(v)) if isinstance(v, list) else v for k, v in d.items()}
        assert a == tp.for_outcome(fail(cid, d)) == tp.for_outcome(fail(cid, shuffled))
        assert json.dumps(a, sort_keys=True) == json.dumps(tp.for_outcome(fail(cid, d)), sort_keys=True)


# ---- each template


def test_bidirectional_relationship_tmdl():
    [t] = tp.for_outcome(fail("FEA-SM-001", SAMPLES["FEA-SM-001"]))
    assert t["kind"] == "tmdl"
    assert t["text"] == ("relationship <relationship id>\n\tfromColumn: Orders.customer_sk\n"
                         "\ttoColumn: Customer.customer_sk\n\tcrossFilteringBehavior: oneDirection\n")


def test_visible_keys_tmdl_groups_columns_by_table():
    [t] = tp.for_outcome(fail("FEA-SM-006", SAMPLES["FEA-SM-006"]))
    assert t["text"] == ("table Orders\n\n\tcolumn customer_sk\n\t\tisHidden\n\n"
                         "\tcolumn product_sk\n\t\tisHidden\n")


def test_direct_lake_only_tmdl_only_for_automatic():
    [t] = tp.for_outcome(fail("FEA-SM-003", {"behavior": "automatic", "tables": 1}))
    assert t["text"] == "model Model\n\tdirectLakeBehavior: directLakeOnly\n"
    assert tp.for_outcome(fail("FEA-SM-003", {"behavior": "DirectQueryOnly", "tables": 1})) == []


def test_deactivated_activities_json():
    [t] = tp.for_outcome(fail("FEA-REL-003", SAMPLES["FEA-REL-003"]))
    assert json.loads(t["text"]) == [{"name": "Copy orders", "state": "Active"}, {"name": "Notify", "state": "Active"}]
    assert any("Skipped" in c for c in t["caveats"])


def test_fixed_workspace_json_uses_a_parameter_and_no_id():
    [t] = tp.for_outcome(fail("FEA-DEVOPS-002", SAMPLES["FEA-DEVOPS-002"]))
    body = json.loads(t["text"])
    assert body["parameters"]["target_workspace_id"]["defaultValue"] == "<workspace id>"
    assert body["activities"] == [{"name": "Run notebook", "typeProperties": {"workspaceId": {
        "value": "@pipeline().parameters.target_workspace_id", "type": "Expression"}}}]


def test_secret_template_reads_key_vault_and_holds_no_secret():
    [t] = tp.for_outcome(fail("FEA-SEC-005", NB_FAIL))
    assert "notebookutils.credentials.getSecret(\"<key vault URL>\", \"<secret name>\")" in t["text"]
    assert any("Rotate" in c for c in t["caveats"])


def test_notebook_cells_are_valid_python_once_placeholders_are_filled():
    for cid in ("FEA-SEC-005", "FEA-DEVOPS-001", "FEA-REL-001", "FEA-DE-003"):
        for t in tp.for_outcome(fail(cid, NB_FAIL)):
            if t["kind"] != "python" and cid != "FEA-DEVOPS-001":
                continue
            code = t["text"]
            for p in t["placeholders"]:
                code = code.replace(p["token"], "ValueError" if "Error" in p["token"] else "pass"
                                    if p["token"].startswith("<the step") or p["token"].startswith("<what")
                                    else "x")
            compile(code, cid, "exec")


def test_unpinned_package_cell():
    [t] = tp.for_outcome(fail("FEA-REL-002", NB_FAIL))
    assert "%pip install <package>==<version>" in t["text"]


def test_vacuum_keeps_the_default_retention_and_says_so():
    got = tp.for_outcome(fail("FEA-DE-003", NB_FAIL))
    assert {t["kind"] for t in got} == {"sql", "python"}
    for t in got:
        assert "7 days (168 hours)" in t["text"] and "RETAIN" not in t["text"].replace("No RETAIN clause", "")
    sql = next(t for t in got if t["kind"] == "sql")["text"]
    assert [ln for ln in sql.splitlines() if not ln.startswith("--")] == ["VACUUM <table>;"]
    py = next(t for t in got if t["kind"] == "python")["text"]
    assert ".vacuum()" in py


def test_vacuum_caveat_doesnt_promise_seven_days_the_table_may_not_keep():
    """Without RETAIN, VACUUM uses the table's delta.deletedFileRetentionDuration, which a notebook
    may have set below 7 days: the proposal must say to check it, never promise 7 days."""
    for t in tp.for_outcome(fail("FEA-DE-003", NB_FAIL)):
        said = " ".join(t["caveats"])
        assert "never shortens" not in said and "unless the table is set to keep more" not in said
        assert "deletedFileRetentionDuration" in said and "SHOW TBLPROPERTIES" in said


@pytest.mark.parametrize("count", [float("inf"), float("-inf"), float("nan"), "1e999", "x", None, [], {}])
def test_an_odd_count_never_raises_and_keeps_the_proposal(count):
    for cid in ("FEA-REL-003", "FEA-DEVOPS-002", "FEA-SM-001", "FEA-SM-006"):
        d = {k: v for k, v in SAMPLES[cid].items() if k != "count"}
        got = tp.for_outcome(fail(cid, {**d, "count": count}))
        assert len(got) == 1 and not any(" of " in c and "change the others" in c for c in got[0]["caveats"])


# ---- names: quoted for their language, or left out


@pytest.mark.parametrize("name", HOSTILE)
def test_tmdl_names_round_trip(name):
    [t] = tp.for_outcome(fail("FEA-SM-001", {"relationships": [f"{name}.k to D.{name}"], "count": 1}))
    props = dict(tmdl_props(t["text"]))
    assert tmdl_ref(props["fromColumn"]) == (name, "k") and tmdl_ref(props["toColumn"]) == ("D", name)
    assert len(t["text"].splitlines()) == 4
    [t] = tp.for_outcome(fail("FEA-SM-006", {"objects": [f"{name}.{name}"], "count": 1}))
    head, _, col, hidden = t["text"].splitlines()
    assert tmdl_unquote(head.removeprefix("table ")) == name and tmdl_unquote(col.removeprefix("\tcolumn ")) == name
    assert hidden == "\t\tisHidden"


@pytest.mark.parametrize("name", HOSTILE)
def test_json_names_round_trip(name):
    [t] = tp.for_outcome(fail("FEA-REL-003", {"inactive": [name], "count": 1}))
    assert json.loads(t["text"]) == [{"name": name, "state": "Active"}]
    [t] = tp.for_outcome(fail("FEA-DEVOPS-002", {"activities": [name], "count": 1}))
    assert [a["name"] for a in json.loads(t["text"])["activities"]] == [name]


@pytest.mark.parametrize("name", UNSAFE)
def test_unsafe_names_give_no_template(name):
    assert tp.tmdl_name(name) is None and tp.json_string(name) is None
    assert tp.for_outcome(fail("FEA-SM-001", {"relationships": [f"{name}.k to D.k"], "count": 1})) == []
    assert tp.for_outcome(fail("FEA-SM-006", {"objects": [f"T.{name}"], "count": 1})) == []
    assert tp.for_outcome(fail("FEA-REL-003", {"inactive": [name], "count": 1})) == []
    assert tp.for_outcome(fail("FEA-DEVOPS-002", {"activities": [name], "count": 1})) == []


@pytest.mark.parametrize("ambiguous", ["A.b.c to D.e", "A.b to C.d to E.f", "Ab to C.d", "A.b toC.d"])
def test_names_that_cant_be_read_back_are_left_out(ambiguous):
    assert tp.for_outcome(fail("FEA-SM-001", {"relationships": [ambiguous], "count": 1})) == []


def test_a_left_out_name_is_counted_and_the_rest_kept():
    [t] = tp.for_outcome(fail("FEA-SM-001", {"relationships": ["a\nb.k to D.k", "F.k to D.k"], "count": 60}))
    assert "\tfromColumn: F.k" in t["text"] and "a\nb" not in t["text"]
    assert any(c.startswith("1 relationships left out") for c in t["caveats"])
    assert any("2 of 60" in c for c in t["caveats"])
    [t] = tp.for_outcome(fail("FEA-REL-003", {"inactive": ["ok", "x\ny"], "count": 2}))
    assert json.loads(t["text"]) == [{"name": "ok", "state": "Active"}]


def test_a_line_break_never_starts_a_new_statement():
    for t in all_templates():
        for line in t["text"].splitlines():
            assert "\u2028" not in line and "\r" not in line


# ---- scans over every template


def test_the_scan_would_catch_a_destructive_statement():
    for bad in ("DROP TABLE t", "delete from t", "TRUNCATE TABLE t", "VACUUM t RETAIN 0 HOURS", ".vacuum(0)",
                "spark.conf.set('spark.databricks.delta.retentionDurationCheck.enabled', 'false')",
                "INSERT OVERWRITE t"):
        assert DESTRUCTIVE.search(bad), bad


def test_no_destructive_statement_anywhere():
    got = all_templates()
    assert len(got) > 40
    for t in got:
        for part in (t["text"], t["title"], *t["caveats"], *(p["meaning"] for p in t["placeholders"])):
            assert not DESTRUCTIVE.search(part), (t["control_id"], part)


def test_no_secret_connection_string_token_or_id_anywhere():
    for t in all_templates():
        assert not SECRETISH.search(json.dumps(t)), t["control_id"]


# ---- through the engine


def _run(control: ct.Control, kind: str, payload: dict, item_type: str) -> dict:
    subj = ev.subject("item", ITEM, "x", workspace_id=WS, item_type=item_type)
    rec = ev.record(kind, subj, source="test", confidence="verified_configuration", collected_at=AT,
                    payload=payload)
    return ct.evaluate(control, subj, lambda k: [rec] if k == kind else [])


def test_hostile_pipeline_activity_names_from_inspection():
    names = ["Copy \"orders\"; drop", "line\nbreak", "ok"]
    p = inspect_items.pipeline({"activities": [{"name": n, "inactive": True, "type_properties": {}} for n in names]})
    c = next(c for c in catalog_artifacts.CONTROLS if c.id == "FEA-REL-003")
    o = _run(c, catalog_artifacts.KIND, p, "DataPipeline")
    assert o["outcome"] == "fail"
    [t] = tp.for_outcome(o)
    assert [a["name"] for a in json.loads(t["text"])] == ["Copy \"orders\"; drop", "ok"]


def test_hostile_model_names_from_inspection():
    bim = {"model": {"tables": [
        {"name": "Fact 'x'", "columns": [{"name": "k]`;", "dataType": "int64"}]},
        {"name": "Dim", "columns": [{"name": "k", "dataType": "int64", "isHidden": True}]}],
        "relationships": [{"fromTable": "Fact 'x'", "fromColumn": "k]`;", "toTable": "Dim", "toColumn": "k",
                           "crossFilteringBehavior": "bothDirections"}]}}
    p = inspect_models.inspect(bim)
    by_id = {c.id: c for c in catalog_models.CONTROLS}
    [t] = tp.for_outcome(_run(by_id["FEA-SM-001"], catalog_models.KIND, p, "SemanticModel"))
    props = dict(tmdl_props(t["text"]))
    assert props["fromColumn"] == "'Fact ''x'''.'k]`;'" and tmdl_ref(props["fromColumn"]) == ("Fact 'x'", "k]`;")
    [t] = tp.for_outcome(_run(by_id["FEA-SM-006"], catalog_models.KIND, p, "SemanticModel"))
    assert t["text"].startswith("table 'Fact ''x'''\n")


def test_the_demo_findings_carry_templates_and_rescore_keeps_them(tmp_path):
    r = Service(root=tmp_path / "app").excellence_assess(demo=True)
    with_tpl = {f["rule"].split("@")[0] for f in r["findings"] if f["templates"]}
    assert {"FEA-SM-001", "FEA-SM-003", "FEA-SM-006"} <= with_tpl
    assert all(f["templates"] == [] for f in r["findings"] if f["rule"].split("@")[0] not in tp.CONTROLS)
    again = assess.rescore(json.loads(json.dumps(r)), catalog.registry())
    assert [f["templates"] for f in again["findings"]] == [f["templates"] for f in r["findings"]]
