"""Policy packs (FEA-504): an organization's policy is validated strictly, applied without changing what it
was given, fingerprinted the same way every time, diffed for audit, and never raises a score unseen."""

from __future__ import annotations

import copy
import json
import math
import time

import pytest

from fabric_desktop.excellence import assess, catalog, controls as ct, policy_packs as pp, profiles as pf
from fabric_desktop.service import Service

AT = "2026-11-21T09:00:00+00:00"
REG = catalog.registry()


def pack(**rules) -> dict:
    return {"id": "contoso-fabric", "version": "1.0.0", "name": "Contoso Fabric policy", "owner": "Data platform team",
            "created": "2026-11-21", "rules": rules}


def full() -> dict:
    return pack(naming={"workspace": {"pattern": "^(dev|test|prod)-[a-z0-9]+$", "max_length": 40},
                        "item": {"pattern": "^[A-Za-z][A-Za-z0-9_]*$", "max_length": 30, "item_types": ["Notebook"]}},
                severity_overrides={"FEA-SEC-001": "critical",
                                    "FEA-SEC-002": {"severity": "medium", "reason": "Guests are vetted partners"}},
                disabled=[{"control_id": "FEA-GOV-001", "reason": "Descriptions live in the wiki"}],
                weights={"architecture": 10, "performance": 10, "security": 30, "governance": 10,
                         "data_engineering": 10, "semantic_models": 10, "capacity_finops": 5, "reliability": 5,
                         "devops": 5, "local_development": 5},
                applicability={"FEA-DE-001": {"item_types": ["Notebook"]},
                               "FEA-SEC-003": {"workspaces": ["^prod-.*$"]}})


def problems(p) -> list[str]:
    return pp.validate(p, REG)


# ---- validation


def test_a_full_pack_is_valid():
    assert problems(full()) == []
    assert problems(pack()) == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda p: p.pop("owner"), "missing owner"),
    (lambda p: p.update(extra=1), "unknown fields extra"),
    (lambda p: p.update(id="Contoso Fabric!"), "id:"),
    (lambda p: p.update(version="1.0"), "version: like 1.0.0"),
    (lambda p: p.update(created="yesterday"), "created:"),
    (lambda p: p.update(name="  "), "name:"),
    (lambda p: p["rules"].update(scoring={}), "unknown rules scoring"),
    (lambda p: p["rules"].update(severity_overrides={"FEA-XXX-999": "high"}), "unknown control 'FEA-XXX-999'"),
    (lambda p: p["rules"].update(severity_overrides={"FEA-SEC-001": "informational"}), "severity is one of"),
    (lambda p: p["rules"].update(disabled=[{"control_id": "FEA-SEC-001"}]), "a reason is required"),
    (lambda p: p["rules"].update(disabled=[{"control_id": "FEA-SEC-001", "reason": " "}]), "a reason is required"),
    (lambda p: p["rules"].update(disabled=["FEA-SEC-001"]), "a reason is required"),
    (lambda p: p["rules"].update(disabled=[{"control_id": "NOPE", "reason": "x"}]), "unknown control 'NOPE'"),
    (lambda p: p["rules"].update(applicability={"NOPE": {"item_types": ["Notebook"]}}), "unknown control 'NOPE'"),
    (lambda p: p["rules"].update(applicability={"FEA-DE-001": {"item_types": ["Report"]}}), "narrow that, not widen"),
    (lambda p: p["rules"].update(applicability={"FEA-SEC-001": {"item_types": ["Notebook"]}}), "only item controls"),
    (lambda p: p["rules"].update(applicability={"FEA-CAP-001": {"workspaces": ["^a$"]}}), "capacity control"),
    (lambda p: p["rules"].update(applicability={"FEA-DE-001": {}}), "item_types and/or workspaces"),
    (lambda p: p["rules"].update(disabled=[{"control_id": "FEA-SEC-001", "reason": "x"}],
                                 severity_overrides={"FEA-SEC-001": "high"}), "ambiguous"),
    (lambda p: p["rules"].update(naming={"workspace": {"pattern": "a" * 201}}), "over 200"),
    (lambda p: p["rules"].update(naming={"workspace": {"max_length": 0}}), "max_length"),
    (lambda p: p["rules"].update(naming={"workspace": {"pattern": "(?=prod)"}}), "lookarounds"),
    (lambda p: p["rules"].update(naming={"workspace": {"pattern": "(a)\\1"}}), "isn't valid"),
    (lambda p: p["rules"].update(naming={"team": {"pattern": "x"}}), "rules.naming"),
])
def test_validation_errors(mutate, needle):
    p = pack()
    mutate(p)
    got = problems(p)
    assert any(needle in x for x in got), got


def test_not_an_object():
    assert pp.validate([], REG) == ["a policy pack is a JSON object"]
    assert pp.validate({"id": "x", "rules": []}, REG)[-1] == "rules: an object"


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1, True, "10"])
def test_nan_infinite_negative_and_non_number_weights_are_rejected(bad):
    w = dict(full()["rules"]["weights"])
    w["security"] = bad
    got = problems(pack(weights=w))
    assert any("finite number of 0 or more" in x for x in got), got


def test_weights_name_every_dimension_and_add_up_to_100():
    w = dict(full()["rules"]["weights"])
    w.pop("devops")
    assert any("every dimension" in x for x in problems(pack(weights=w)))
    w = dict(full()["rules"]["weights"], devops=6)
    assert any("add up to 101" in x for x in problems(pack(weights=w)))


def test_loads_refuses_nan_and_infinity_in_the_json_text():
    text = json.dumps(full()).replace('"security": 30', '"security": NaN')
    with pytest.raises(pp.PolicyPackError, match="NaN"):
        pp.loads(text, REG)
    with pytest.raises(pp.PolicyPackError, match="Infinity"):
        pp.loads(json.dumps(full()).replace('"security": 30', '"security": Infinity'), REG)
    with pytest.raises(pp.PolicyPackError, match="over"):
        pp.loads(" " * (pp.MAX_BYTES + 1), REG)
    assert pp.loads(json.dumps(full()), REG) == full()


def test_lowering_a_critical_control_needs_a_reason():
    crit = ct.Control(**{**{f: getattr(REG.get("FEA-SEC-002"), f) for f in ct.Control.__dataclass_fields__},
                         "severity": "critical"})
    reg = ct.Registry()
    reg.register(crit)
    got = pp.validate(pack(severity_overrides={"FEA-SEC-002": "high"}), reg)
    assert any("lowering a critical control needs a reason" in x for x in got)
    assert pp.validate(pack(severity_overrides={"FEA-SEC-002": {"severity": "high", "reason": "Vetted guests"}}), reg) == []
    assert pp.validate(pack(severity_overrides={"FEA-SEC-002": {"severity": "high", "reason": ""}}), reg)
    # raising one, or naming its own severity again, needs none
    assert pp.validate(pack(severity_overrides={"FEA-SEC-002": "critical"}), reg) == []


def test_lowering_a_control_the_profile_makes_critical_needs_a_reason_when_applied():
    prof = pf.checked(pf.Profile(**{**pf.get().__dict__, "severity_overrides": {"FEA-SEC-002": "critical"}}))
    with pytest.raises(pp.PolicyPackError, match="lowering it needs a reason"):
        pp.apply(prof, pack(severity_overrides={"FEA-SEC-002": "low"}), REG)
    got = pp.apply(prof, pack(severity_overrides={"FEA-SEC-002": {"severity": "low", "reason": "Accepted"}}), REG)
    assert got.policy_pack["severity_overrides"] == [{"control_id": "FEA-SEC-002", "from": "critical", "to": "low",
                                                      "reason": "Accepted"}]


# ---- patterns: no catastrophic backtracking


@pytest.mark.parametrize("pattern", ["(a+)+$", "^(a*)*b$", "((ab)*c)*", "(x+x+)+y", "(?:a{2,})+", "(a?)*",
                                     "^([a-z]+)*$", "(\\d+\\.)+", "(a|aa)(b+){2,}(c+)+"])
def test_redos_patterns_are_rejected_quickly(pattern):
    p = pack(naming={"workspace": {"pattern": pattern}},
             applicability={"FEA-SEC-003": {"workspaces": [pattern]}})
    start = time.perf_counter()
    got = problems(p)
    assert time.perf_counter() - start < 1.0
    assert sum("nested quantifiers" in x for x in got) == 2, got


def test_a_long_pattern_is_rejected_before_it_is_scanned():
    evil = "(a+)+" * 1000
    start = time.perf_counter()
    got = pp.pattern_problems(evil, "x")
    assert time.perf_counter() - start < 0.5
    assert "over 200" in got[0]


@pytest.mark.parametrize("pattern", ["^(dev|test|prod)-[a-z0-9]+$", "(a+)?b", "x{3}(ab)+", "(a{2})+",
                                     "\\((a+)\\)+", "[(a+)]+", "(?i)^prod"])
def test_ordinary_patterns_pass(pattern):
    assert pp.pattern_problems(pattern, "x") == []


def test_matching_is_linear_even_for_an_ambiguous_alternation():
    # (a|a)* has no nested quantifier, so it is accepted; RE2 matches it without backtracking
    assert pp.pattern_problems("^(a|a)*$", "x") == []
    pp.matches("^a$", "a")  # the matcher's first use opens its connection; time only the match
    start = time.perf_counter()
    assert pp.matches("^(a|a)*$", "a" * 200 + "!") is False
    assert time.perf_counter() - start < 1.0


# ---- fingerprint and diff


def test_the_fingerprint_is_deterministic_and_follows_the_content():
    p = full()
    fp = pp.fingerprint(p)
    assert len(fp) == 64 and int(fp, 16) >= 0
    reordered = json.loads(json.dumps(p, sort_keys=True))
    reordered = dict(reversed(list(reordered.items())))
    assert pp.fingerprint(reordered) == fp
    assert pp.fingerprint(copy.deepcopy(p)) == fp
    assert pp.fingerprint({**p, "version": "1.0.1"}) != fp


def test_diff_is_an_audit_trail():
    old = full()
    new = copy.deepcopy(old)
    new["version"] = "1.1.0"
    new["rules"]["disabled"].append({"control_id": "FEA-SEC-004", "reason": "Workspace identities not used"})
    new["rules"]["severity_overrides"]["FEA-SEC-001"] = "low"
    del new["rules"]["applicability"]["FEA-SEC-003"]
    d = pp.diff(old, new)
    assert d["from"]["fingerprint"] == pp.fingerprint(old) and d["to"]["version"] == "1.1.0" and not d["same"]
    by = {c["path"]: c for c in d["changes"]}
    assert by["rules.disabled.FEA-SEC-004"]["change"] == "added"
    assert by["rules.severity_overrides.FEA-SEC-001"] == {"path": "rules.severity_overrides.FEA-SEC-001",
                                                          "change": "changed", "old": "critical", "new": "low"}
    assert by["rules.applicability.FEA-SEC-003"]["change"] == "removed"
    assert set(d["relaxes"]) == {"rules.disabled.FEA-SEC-004", "rules.severity_overrides.FEA-SEC-001"}
    assert d["problems"] == []
    same_version = dict(new, version="1.0.0")
    assert any("version didn't go up" in x for x in pp.diff(old, same_version)["problems"])
    assert pp.diff(old, copy.deepcopy(old)) == {"from": pp.ref(old), "to": pp.ref(old), "same": True,
                                                "changes": [], "relaxes": [], "problems": []}
    assert "different packs" in pp.diff(old, dict(new, id="other"))["problems"][0]


def test_diff_ignores_the_order_of_disabled_controls():
    a = pack(disabled=[{"control_id": "FEA-SEC-001", "reason": "x"}, {"control_id": "FEA-SEC-004", "reason": "y"}])
    b = copy.deepcopy(a)
    b["rules"]["disabled"].reverse()
    assert pp.diff(a, b)["changes"] == []


# ---- apply


def test_apply_is_pure():
    base = pf.get()
    before_profile = copy.deepcopy(base.as_dict())
    p = full()
    before_pack = copy.deepcopy(p)
    got = pp.apply(base, p, REG)
    assert p == before_pack and base.as_dict() == before_profile
    assert got is not base and got.as_dict() != base.as_dict()
    # the result shares nothing mutable with the pack
    got.policy_pack["disabled"][0]["reason"] = "changed"
    got.applicability["FEA-DE-001"]["item_types"].append("X")
    assert p == before_pack
    assert pp.apply(base, full(), REG).as_dict() == pp.apply(base, full(), REG).as_dict()


def test_apply_records_the_pack():
    got = pp.apply(pf.get(), full(), REG)
    assert got.id == "baseline+contoso-fabric" and got.version == "1.1.0+1.0.0"
    rec = got.policy_pack
    assert (rec["id"], rec["version"], rec["fingerprint"]) == ("contoso-fabric", "1.0.0", pp.fingerprint(full()))
    assert rec["base_profile"] == {"id": "baseline", "version": "1.1.0", "fingerprint": pf.get().fingerprint()}
    assert rec["disabled"] == [{"control_id": "FEA-GOV-001", "reason": "Descriptions live in the wiki"}]
    assert "policy pack contoso-fabric@1.0.0" in got.disabled["FEA-GOV-001"]
    assert got.severity_overrides == {"FEA-SEC-001": "critical", "FEA-SEC-002": "medium"}
    assert got.weights["security"] == 30
    assert pf.from_dict(json.loads(json.dumps(got.as_dict()))) == got
    assert got.fingerprint() != pf.get().fingerprint()


def test_a_profile_without_a_pack_keeps_its_fingerprint():
    assert "policy_pack" not in pf.get().as_dict() and "applicability" not in pf.get().as_dict()


def test_apply_refuses_a_bad_pack_and_a_second_pack():
    with pytest.raises(pp.PolicyPackError) as e:
        pp.apply(pf.get(), pack(disabled=[{"control_id": "FEA-SEC-001"}]), REG)
    assert e.value.problems
    once = pp.apply(pf.get(), full(), REG)
    with pytest.raises(pp.PolicyPackError, match="already has policy pack"):
        pp.apply(once, full(), REG)


# ---- in an assessment: disabled and scoped-out controls are listed, never passed


def test_disabled_controls_are_listed_in_the_assessment(tmp_path, monkeypatch):
    def no_fabric(self):
        raise AssertionError("the demo doesn't call Fabric")
    monkeypatch.setattr(Service, "fabric", property(no_fabric))
    s = Service(root=tmp_path / "app")
    plain = s.excellence_assess(demo=True)
    assert plain["policy_pack"] is None
    p = pack(disabled=[{"control_id": "FEA-SEC-001", "reason": "Admins are reviewed quarterly"}],
             applicability={"FEA-SEC-003": {"workspaces": ["^nothing-matches$"]}})
    r = s.excellence_assess(demo=True, policy_pack=p)
    eff = r["policy_pack"]
    assert (eff["id"], eff["version"], eff["fingerprint"]) == ("contoso-fabric", "1.0.0", pp.fingerprint(p))
    assert eff["disabled_controls"] == [{"control_id": "FEA-SEC-001", "reason": "Admins are reviewed quarterly"}]
    assert eff["out_of_scope"] == [{"control_id": "FEA-SEC-003", "outcomes": 1}]
    for cid, effect in (("FEA-SEC-001", "disabled"), ("FEA-SEC-003", "out_of_scope")):
        outs = [o for o in r["outcomes"] if o["control_id"] == cid]
        assert outs and all(o["outcome"] == "not_applicable" and o["policy"]["effect"] == effect for o in outs)
        assert all("contoso-fabric@1.0.0" in o["text"] for o in outs)
    assert r["profile"]["policy_pack"]["fingerprint"] == pp.fingerprint(p)
    assert r["policy_version"] != plain["policy_version"]
    # a stored snapshot with a pack rescores exactly
    stored = json.loads(json.dumps(r))
    assert assess.rescore(stored, catalog.registry())["scores"] == r["scores"]


def test_the_engine_call_refuses_a_bad_pack_before_reading(tmp_path, monkeypatch):
    def no_fabric(self):
        raise AssertionError("a bad pack is refused before any read")
    monkeypatch.setattr(Service, "fabric", property(no_fabric))
    s = Service(root=tmp_path / "app")
    bad = pack(weights=dict(full()["rules"]["weights"], security=math.nan))
    with pytest.raises(pp.PolicyPackError, match="finite"):
        s.excellence_assess(workspace_id="00000000-0000-4000-8000-000000000001", policy_pack=bad)
    with pytest.raises(ValueError, match="policy pack object"):
        s.excellence_assess(demo=True, policy_pack="not a pack")


def test_applicability_by_item_type_and_unknown_workspace_names():
    prof = pp.apply(pf.get(), pack(applicability={"FEA-DE-001": {"item_types": ["Notebook"],
                                                                "workspaces": ["^prod-.*$"]}}), REG)
    nb = {"type": "item", "id": "i1", "name": "nb", "workspace_id": "w1", "item_type": "Notebook"}
    pl = dict(nb, item_type="DataPipeline")
    prod = {"type": "workspace", "id": "w1", "name": "prod-sales"}
    dev = dict(prod, name="dev-sales")
    unnamed = dict(prod, name="w1")  # evidence.subject falls back to the id: the name isn't known
    assert pp.out_of_scope(prof, "FEA-DE-001", nb, prod) is None
    assert "applies only to Notebook" in pp.out_of_scope(prof, "FEA-DE-001", pl, prod)
    assert "matches none" in pp.out_of_scope(prof, "FEA-DE-001", nb, dev)
    assert pp.out_of_scope(prof, "FEA-DE-001", nb, unnamed) is None
    assert pp.out_of_scope(prof, "FEA-SEC-001", nb, dev) is None


def test_naming_violations():
    got = pp.naming_violations(full(), "Sales Prod", [
        {"id": "a", "type": "Notebook", "name": "Load_Orders"},
        {"id": "b", "type": "Notebook", "name": "1 load orders"},
        {"id": "c", "type": "Lakehouse", "name": "anything goes"},
        {"id": "d", "type": "Notebook", "name": "N" * 31},
        {"id": "e", "type": "Notebook", "name": "x" * 300}])
    assert [(v["object_type"], v["id"], v["rule"]) for v in got] == [
        ("workspace", "Sales Prod", "pattern"), ("item", "b", "pattern"), ("item", "d", "max_length"),
        ("item", "e", "max_length")]
    assert "too long to check" in got[-1]["text"]
    assert pp.naming_violations(full(), "prod-sales", [{"id": "a", "type": "Notebook", "name": "ok"}]) == []
    assert pp.naming_violations(pack(), "anything", []) == []


# ---- adversarial review


def test_an_unhashable_disabled_control_id_is_a_problem_not_a_crash():
    got = problems(pack(disabled=[{"control_id": ["FEA-GOV-001"], "reason": "r"}]))
    assert any("unknown control" in x for x in got)


def test_a_deeply_nested_pack_is_refused_as_a_pack_error():
    with pytest.raises(pp.PolicyPackError):
        pp.loads("[" * 100000 + "]" * 100000)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(name="Contoso \ud800"),
    lambda p: p.update(owner="\udfff"),
    lambda p: p["rules"].update(disabled=[{"control_id": "FEA-GOV-001", "reason": "wiki \ud800"}]),
    lambda p: p["rules"].update(severity_overrides={"FEA-SEC-002": {"severity": "medium", "reason": "\ud800"}}),
])
def test_lone_surrogates_are_refused_before_they_can_break_the_fingerprint(mutate):
    p = pack()
    mutate(p)
    assert any("text" in x for x in problems(p))
    with pytest.raises(pp.PolicyPackError):
        pp.apply(pf.get(), p, REG)
    with pytest.raises(pp.PolicyPackError):
        pp.loads(json.dumps(p), REG)


def test_a_workspace_or_item_name_that_isnt_valid_text_doesnt_crash_the_assessment():
    prof = pp.apply(pf.get(), pack(applicability={"FEA-DE-001": {"workspaces": ["^prod-.*$"]}}), REG)
    nb = {"type": "item", "id": "i1", "name": "nb", "workspace_id": "w1", "item_type": "Notebook"}
    bad = {"type": "workspace", "id": "w1", "name": "prod-\ud800"}
    assert pp.out_of_scope(prof, "FEA-DE-001", nb, bad) is None  # not known, so assessed as usual
    got = pp.naming_violations(full(), "prod-\ud800", [{"id": "a", "type": "Notebook", "name": "x\ud800"}])
    assert [(v["object_type"], v["rule"]) for v in got] == [("workspace", "pattern"), ("item", "pattern")]
    assert all("couldn't be checked" in v["text"] for v in got)
    json.dumps(got, ensure_ascii=False).encode("utf-8")  # never a pass, and safe to store


def test_a_newly_added_override_that_lowers_a_control_is_a_relaxation():
    old = pack()
    new = pack(severity_overrides={"FEA-SEC-002": "low"})  # FEA-SEC-002 is high in the catalog
    new["version"] = "1.1.0"
    assert REG.get("FEA-SEC-002").severity == "high"
    assert "rules.severity_overrides.FEA-SEC-002" in pp.diff(old, new)["relaxes"]
    raised = pack(severity_overrides={"FEA-SEC-002": "critical"})
    raised["version"] = "1.1.0"
    assert pp.diff(old, raised)["relaxes"] == []


def test_workspace_patterns_are_matched_once_per_name_not_once_per_item(monkeypatch):
    # Each match compiles the pattern (up to ~25 ms for a long counted one): per item, it multiplies.
    prof = pp.apply(pf.get(), pack(applicability={"FEA-DE-001": {"workspaces": ["^prod-.*$", "^dev-[a-z]+$"]}}), REG)
    calls = []
    real = pp._full_match
    monkeypatch.setattr(pp, "_full_match", lambda p, t: calls.append((p, t)) or real(p, t))
    pp._match_cached.cache_clear()
    ws = {"type": "workspace", "id": "w1", "name": "test-sales"}
    for i in range(500):
        it = {"type": "item", "id": f"i{i}", "name": f"nb{i}", "workspace_id": "w1", "item_type": "Notebook"}
        assert pp.out_of_scope(prof, "FEA-DE-001", it, ws)
    assert len(calls) <= 2
