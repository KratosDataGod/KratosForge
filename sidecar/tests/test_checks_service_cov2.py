"""Check now, the promote gate and a product's checklist (central/checks_service.py): the paths where a
source can't be read, a rule is missing or runs in Fabric, and the checklist's pass texts. The mixin runs on
a stand-in service whose catalog, notebooks, schedules and queries are fakes: nothing reaches a platform."""

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

import fabric_desktop.service  # noqa: F401 - registers the built-in checkers
from fabric_desktop import governance_checks as gc
from fabric_desktop.central import checks_service as cs


class Catalog:
    """A catalog whose operations answer from a dict: a callable is called, an exception is raised."""

    kind = "local"
    path = None

    def __init__(self, **ops):
        self.ops = ops
        self.calls = []

    def call(self, op, **kw):
        self.calls.append(op)
        got = self.ops.get(op)
        if isinstance(got, Exception):
            raise got
        if callable(got):
            return got(**kw)
        if got is None:
            raise RuntimeError(f"{op} isn't faked")
        return got


def project(tmp_path, products=None, name="crm"):
    return SimpleNamespace(
        info={"name": name}, root=tmp_path, entities={}, products=products or {}
    )


def product(
    status="published", entity="", owner="ann", steward="bob", description="Customers"
):
    ports = [{"entity": entity}] if entity else []
    return SimpleNamespace(
        ports=ports,
        status=status,
        spec={"owner": owner, "steward": steward, "description": description},
    )


class Svc(cs.ChecksMixin):
    def __init__(self, tmp_path, catalog=None, proj=None, resolve=None):
        self.root = tmp_path / "data"
        self.root.mkdir(exist_ok=True)
        self.cat = catalog or Catalog()
        self.proj = proj
        self.resolve = resolve
        self.runs = {"runs": [], "unavailable": {}}

    def _catalog(self):
        return self.cat

    def _project(self):
        if self.proj is None:
            raise LookupError("No project is open.")
        return self.proj

    def governance_resolve(self, **kw):
        if isinstance(self.resolve, Exception):
            raise self.resolve
        return self.resolve(**kw) if callable(self.resolve) else self.resolve

    def meta_runs_all(self, limit=0):
        return self.runs


@pytest.fixture
def checkers():
    """A local test checker (kind "*" rules: many findings, an unknown) and one that runs in Fabric."""

    def rules():
        return [
            {
                "id": "zz.many",
                "kind": "*",
                "severity": "Error",
                "category": "test",
                "name": "Many",
                "description": "d",
            },
            {
                "id": "zz.unknown",
                "kind": "*",
                "severity": "Warning",
                "category": "test",
                "name": "Unk",
                "description": "d",
            },
        ]

    def check(item, settings):
        out = []
        if "zz.many" in settings:
            r = rules()[0]
            out += [
                gc.finding(r, settings["zz.many"], "product", f"obj{i}", f"problem {i}")
                for i in range(12)
            ]
        if "zz.unknown" in settings:
            out.append(
                gc.finding(
                    rules()[1],
                    settings["zz.unknown"],
                    "product",
                    "obj",
                    "can't tell",
                    outcome="unknown",
                )
            )
        return out

    def fabric_rules():
        return [
            {
                "id": "zz.fabric",
                "kind": "product",
                "severity": "Info",
                "category": "test",
                "name": "In Fabric",
                "description": "d",
            }
        ]

    gc.register("zzcov", rules, check)
    gc.register(
        "zzcov_fabric", fabric_rules, lambda item, settings: [], runs_in="fabric"
    )
    yield
    gc.unregister("zzcov")
    gc.unregister("zzcov_fabric")


# --------------------------------------------------------------------------- small helpers


def test_last_load_of_a_product_with_no_port_says_nothing_loads_it():
    assert cs.last_load([{"status": "ok", "entity": "x"}], []) == {
        "last_load": None,
        "last_rows": None,
        "load_reason": "The product has no port, so nothing loads it.",
    }


def test_last_check_with_no_project_open_is_none(tmp_path):
    assert Svc(tmp_path).governance_last_check() == {"project": None, "result": None}


def test_practice_settings_when_the_policies_cant_be_read_keeps_every_rule_as_built(
    tmp_path,
):
    out = Svc(
        tmp_path, resolve=RuntimeError("platform down")
    ).governance_practice_settings("notebook")
    assert out["settings"] == {} and out["policies"] == [] and out["guidance"] is None
    assert out["note"].startswith(
        "The policies couldn't be read, so every rule is on as built:"
    )
    assert "platform down" in out["note"]


def test_practice_settings_two_policies_one_rule_keeps_it_on_when_the_second_turns_it_off(
    tmp_path,
):
    resolved = {
        "policies": [
            {"id": "a", "name": "On", "rule": "NB001", "severity": "medium"},
            {
                "id": "b",
                "name": "Off",
                "rule": "NB001 SQ001",
                "params": {"enabled": False},
            },
            {
                "id": "c",
                "name": "Also off",
                "rule": "NB002",
                "params": {"enabled": False},
            },
            {
                "id": "d",
                "name": "Off again",
                "rule": "NB002",
                "params": {"enabled": False},
                "severity": "high",
            },
        ]
    }
    out = Svc(tmp_path, resolve=resolved).governance_practice_settings("notebook")
    assert out["settings"]["NB001"] == {"enabled": True, "severity": "Warning"}
    # both off: the worse severity is kept
    assert out["settings"]["NB002"] == {"enabled": False, "severity": "Error"}
    assert "SQ001" not in out["settings"]
    assert out["policies"] == ["On", "Off", "Also off", "Off again"]


def test_trust_from_a_platform_without_the_operation_says_to_repair(tmp_path):
    svc = Svc(tmp_path, Catalog(product_trust=LookupError("404")))
    trust, why = svc._gc_trust("crm/p")
    assert trust is None and "older than this app" in why


# --------------------------------------------------------------------------- notebooks, SQL, the model


def test_notebook_items_go_on_when_schedules_or_one_notebook_cant_be_read(tmp_path):
    svc = Svc(tmp_path)

    def no_schedules():
        raise OSError("schedules locked")

    def load(name):
        if name == "bad":
            raise ValueError("not JSON")
        return {
            "cells": [
                {"source": "print(1)"},
                {"type": "markdown", "source": "# hi"},
                {"type": "code"},
            ]
        }

    svc.pc_schedules = SimpleNamespace(notebooks=no_schedules)
    svc.notebooks = SimpleNamespace(
        list=lambda: [{"name": "bad"}, {"name": "good"}], load=load
    )
    problems = []
    items = svc._gc_notebook_items(problems)
    assert items == [
        {
            "kind": "notebook",
            "id": "good",
            "scheduled": False,
            "sources": ["print(1)", ""],
        }
    ]
    assert problems[0].startswith(
        "This PC's schedules couldn't be read, so no notebook was checked as scheduled:"
    )
    assert "schedules locked" in problems[0]
    assert problems[1].startswith(
        "The notebook bad couldn't be read, so it wasn't checked:"
    )


def test_sql_items_report_each_source_that_cant_be_read(tmp_path, monkeypatch):
    from fabric_desktop import warehouse_project_service as wps

    monkeypatch.setattr(wps, "MAX_FILE", 64)
    svc = Svc(tmp_path)
    svc.sql_queries_list = lambda: (_ for _ in ()).throw(OSError("no queries folder"))
    svc.wh_projects_list = lambda: (_ for _ in ()).throw(OSError("no repos"))
    problems = []
    assert svc._gc_sql_items(problems) == []
    assert problems[0].startswith(
        "The saved queries couldn't be listed, so they weren't checked:"
    )
    assert problems[1].startswith(
        "The warehouse projects couldn't be listed, so they weren't checked:"
    )


def test_sql_items_read_queries_and_project_files_and_skip_the_unreadable(
    tmp_path, monkeypatch
):
    from fabric_desktop import warehouse_project_service as wps

    monkeypatch.setattr(wps, "MAX_FILE", 64)
    repo = tmp_path / "repo"
    folder = repo / "wh"
    folder.mkdir(parents=True)
    (folder / "ok.sql").write_bytes(b"\xef\xbb\xbfSELECT 1")
    (folder / "big.sql").write_bytes(b"SELECT 1 -- " + b"x" * 100)
    (folder / "bin.sql").write_bytes(b"\xff\xfe\x00bad")
    svc = Svc(tmp_path)
    svc.sql_queries_list = lambda: {
        "notes": ["one folder is missing"],
        "queries": [
            {"place_label": "My queries", "name": "a.sql", "sql": "SELECT a"},
            {
                "place_label": "My queries",
                "name": "b.sql",
                "problem": "It is too large.",
            },
        ],
    }
    svc.wh_projects_list = lambda: {
        "projects": [{"repo": str(repo), "folder": "wh", "name": "WH"}]
    }
    svc._wh_walk = lambda f: ([f / "ok.sql", f / "big.sql", f / "bin.sql"], True)
    problems = []
    items = svc._gc_sql_items(problems)
    assert [(i["id"], i["query"]) for i in items] == [
        ("My queries/a.sql", "SELECT a"),
        ("WH/ok.sql", "SELECT 1"),
    ]
    assert problems[0] == "Saved queries: one folder is missing"
    assert (
        problems[1]
        == "The saved query My queries/b.sql wasn't checked: It is too large."
    )
    assert (
        problems[2]
        == "The warehouse project WH has more .sql files than are read; the first 3 were checked."
    )
    assert (
        problems[3].startswith("The SQL file WH/big.sql couldn't be read")
        and "larger than 0 KB" in problems[3]
    )
    assert problems[4].startswith("The SQL file WH/bin.sql couldn't be read")


def test_model_items_when_the_model_cant_be_planned(tmp_path):
    svc = Svc(tmp_path)

    def no_layer(project, layer):
        raise ValueError("no gold layer")

    svc._model_layer = no_layer
    problems = []
    assert svc._gc_model_items(project(tmp_path), {}, problems) == []
    assert problems == [
        "The semantic model couldn't be planned, so it wasn't checked: ValueError: no gold layer"
    ]


# --------------------------------------------------------------------------- Check now's evaluation


def test_evaluate_says_when_the_package_and_catalog_text_cant_be_read_and_a_rule_is_missing(
    tmp_path, checkers
):
    cat = Catalog(
        policy_snapshot=RuntimeError("snapshot refused"),
        enrichment=RuntimeError("enrichment down"),
    )
    proj = project(tmp_path, {"p": product()})
    resolved = {
        "policies": [
            {"id": "gone", "name": "Gone", "rule": "zz.nothere", "severity": "high"},
            {"id": "fab", "name": "Fabric", "rule": "zz.fabric", "severity": "low"},
            {"id": "half", "name": "Half", "rule": "zz.unknown zz.nothere"},
        ]
    }
    result = Svc(tmp_path, cat)._gc_evaluate(proj, resolved)
    assert (
        result["package"] is None
        and result["package_note"] == "Policies read from the platform."
    )
    assert any(
        p.startswith("The policy package on this PC wasn't refreshed")
        and "snapshot refused" in p
        for p in result["problems"]
    )
    assert any(
        p.startswith("The platform's catalog text couldn't be read")
        and "enrichment down" in p
        for p in result["problems"]
    )
    by = {p["id"]: p for p in result["policies"]}
    assert by["gone"]["status"] == "not_checked" and by["gone"]["missing"] == [
        "zz.nothere"
    ]
    assert by["gone"]["note"] == "No checker on this PC has the rule zz.nothere."
    assert by["fab"]["status"] == "not_run"
    assert by["fab"]["not_run"] == [
        {"rule_id": "zz.fabric", "name": "In Fabric", "last": None}
    ]
    assert (
        by["half"]["status"] == "unknown"
        and by["half"]["note"] == "No checker on this PC has the rule zz.nothere."
    )
    assert result["counts"]["unknown"] == 1


def test_promote_gate_lists_ten_findings_then_counts_the_rest_and_notes_unknown_and_fabric(
    tmp_path, checkers
):
    resolved = {
        "policies": [
            {
                "id": "many",
                "name": "Many",
                "rule": "zz.many zz.unknown zz.fabric",
                "enforcement": "block",
            },
            {"id": "warn", "name": "Warn", "rule": "zz.many", "enforcement": "warn"},
        ]
    }
    cat = Catalog(enrichment={})
    svc = Svc(
        tmp_path,
        cat,
        project(tmp_path, {"p": product()}),
        resolve=lambda **kw: resolved,
    )
    gate = svc.governance_promote_gate("test")
    assert len(gate["errors"]) == 11
    assert gate["errors"][0] == 'Block policy "Many": obj0: problem 0'
    assert gate["errors"][10].startswith('Block policy "Many": 2 more findings.')
    assert gate["notes"] == [
        "Block policy \"Many\": 1 couldn't be checked here, so they don't stop the promote: can't tell",
        "Block policy \"Many\": its rules that run in Fabric aren't run here, so they don't stop the promote.",
    ]
    assert "policy_snapshot" not in cat.calls  # the gate reads only


# --------------------------------------------------------------------------- a data product's checklist


def test_contract_falls_back_to_the_built_in_template(tmp_path):
    svc = Svc(
        tmp_path,
        resolve={
            "policies": [{"id": "n", "name": "Naming", "rule": "naming.table_name"}]
        },
    )
    params, source, problems = svc._gc_contract("crm")
    assert source == {
        "template": "data-contract",
        "name": cs.BUILTIN_TEMPLATES["data-contract"]["name"],
        "builtin": True,
    }
    assert problems == [] and params

    svc.resolve = RuntimeError("resolve failed")
    _, source, problems = svc._gc_contract("crm")
    assert source["builtin"] is True
    assert problems == [
        "The project's policies couldn't be read, so the built-in template is used: RuntimeError: resolve failed"
    ]


def test_checklist_of_an_unknown_product_raises(tmp_path):
    svc = Svc(tmp_path, Catalog(list_products=[]))
    with pytest.raises(LookupError, match="crm/x isn't a published data product"):
        svc.governance_product_checklist("crm/x")


def test_checklist_with_no_project_open_reads_the_catalog_only(tmp_path):
    cat = Catalog(
        list_products=[
            {
                "id": "crm/p",
                "name": "p",
                "project": "crm",
                "owner": "ann",
                "description": "d",
            }
        ],
        product_trust={"items": [{"item_name": "LH", "endorsement": "Certified"}]},
    )
    out = Svc(tmp_path, cat, resolve={"policies": []}).governance_product_checklist(
        "crm/p"
    )
    assert out["open_project"] is False
    rows = {r["rule_id"]: r for r in out["rows"]}
    assert rows["contract.endorsed"] == {
        "rule_id": "contract.endorsed",
        "label": "Endorsement",
        "mark": "pass",
        "detail": "Certified in Fabric.",
        "fix": None,
    }
    assert rows["ownership.product_owner"]["mark"] == "unknown"
    assert rows["contract.freshness"]["mark"] == "unknown"
    assert rows["contract.description"]["detail"] == "Written."


def test_checklist_of_a_draft_product_in_the_open_project_checks_it_as_published(
    tmp_path, monkeypatch
):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    cat = Catalog(list_products=[{"id": "crm/p", "name": "p", "project": "crm"}])
    svc = Svc(
        tmp_path,
        cat,
        project(tmp_path, {"p": product(status="draft", entity="silver.c")}),
        resolve={"policies": []},
    )
    svc.runs = {
        "runs": [
            {
                "status": "ok",
                "entity": "silver.c",
                "env": "prod",
                "started_at": now,
                "rows_written": 3,
            }
        ],
        "unavailable": {},
    }
    real = gc.find_rule
    monkeypatch.setattr(
        gc, "find_rule", lambda rid: None if rid == "contract.endorsed" else real(rid)
    )
    out = svc.governance_product_checklist("crm/p")
    assert out["open_project"] is True
    rows = {r["rule_id"]: r for r in out["rows"]}
    assert "contract.endorsed" not in rows  # no checker on this PC has it: no line
    assert rows["ownership.product_owner"]["detail"] == "Owner ann, steward bob."
    assert rows["contract.freshness"]["mark"] == "pass"
    assert rows["contract.freshness"]["label"] == "Fresh within 24 h"
    assert (
        rows["contract.freshness"]["detail"]
        == f"Last loaded {now[:16].replace('T', ' ')} UTC in Prod."
    )
    assert "product_trust" not in cat.calls  # a draft isn't in the catalog yet


def test_passed_texts_without_a_steward_or_environment():
    assert (
        cs._passed("ownership.product_owner", {"owner": "ann", "steward": ""}, {})
        == "Owner ann."
    )
    assert (
        cs._passed("contract.freshness", {"last_load": "2026-01-02T03:04:05"}, {})
        == "Last loaded 2026-01-02 03:04 UTC."
    )


def test_package_path_is_under_the_data_folder(tmp_path):
    assert (
        Svc(tmp_path)._gc_package_path("crm")
        == tmp_path / "data" / cs.pp.FOLDER / "crm.json"
    )
    assert isinstance(Svc(tmp_path)._gc_package_path("crm"), Path)
