"""Workspace rankings (FEA-502): assured score only, within a workload category, among workspaces assessed
with the same profile and rules; insufficient evidence listed, never ranked; the same answer in any order."""

from __future__ import annotations

import copy
import hashlib
import json
import random

import pytest

from fabric_desktop.excellence import assess, catalog, evidence as ev, profiles as pf, rankings as rk
from fabric_desktop.excellence.evidence import canonical

AT = "2026-11-17T23:00:00+00:00"
LATER = "2026-11-18T09:00:00Z"
RULES = {"FEA-ARC-001": "1.0.0", "FEA-SEC-001": "1.0.0"}
DE = ["Lakehouse", "Notebook", "Notebook", "DataPipeline"]
BI = ["SemanticModel", "Report", "Report"]


def ws(id_: str, assured: float | None, coverage: float | None = 90.0, *, name: str | None = None,
       status: str | None = None, items: list[str] = DE) -> dict:
    if status is None:
        status = ("insufficient_evidence" if coverage is None or coverage < 50
                  else "provisional" if coverage < 80 else "rated")
    return {"id": id_, "name": name or id_.upper(), "assured": assured, "coverage": coverage, "status": status,
            "items": items}


def snap(*wss: dict, at: str = AT, profile: pf.Profile | None = None, rules: dict | None = None,
         fixture: bool = False, fp: str = "f" * 16) -> dict:
    prof = (profile or pf.get()).as_dict()
    rules = dict(RULES if rules is None else rules)
    cov = [{"item": {"type": "item", "id": f"{w['id']}-{n}", "name": t, "workspace_id": w["id"], "item_type": t},
            "state": "inspected", "reason": None} for w in wss for n, t in enumerate(w["items"])]
    return {"schema": assess.SCHEMA, "fixture": fixture, "assessed_at": at,
            "scope": {"workspaces": [{"type": "workspace", "id": w["id"], "name": w["name"]} for w in wss],
                      "capacities": []},
            "profile": prof, "rule_versions": rules,
            "policy_version": hashlib.sha256(canonical([prof, rules]).encode()).hexdigest()[:16],
            "fingerprint": fp,
            "workspace_scores": {w["id"]: {"score": w["assured"], "assured": w["assured"], "coverage": w["coverage"],
                                           "status": w["status"],
                                           "rating": None if w["status"] == "insufficient_evidence"
                                           else {"id": "at_risk", "label": "At risk"}} for w in wss},
            "item_coverage": cov}


def ids(cat: dict, key: str = "ranked") -> list[str]:
    return [r["workspace"]["id"] for r in cat[key]]


def only_group(result: dict) -> dict:
    assert result["comparable"] and len(result["groups"]) == 1
    return result["groups"][0]


def cats(result: dict) -> dict:
    return {c["category"]: c for c in only_group(result)["categories"]}


# ---- categories


@pytest.mark.parametrize("types,expected", [
    (["Lakehouse", "Notebook", "Notebook"], "data_engineering"),
    (["Warehouse", "Warehouse", "Report"], "warehousing"),
    (["SemanticModel", "Report", "Report", "Notebook"], "business_intelligence"),
    (["Eventhouse", "KQLDatabase", "Eventstream"], "real_time"),
    (["Lakehouse", "Notebook", "Report", "Report"], "mixed"),  # 2 of 4: no majority
    (["Lakehouse", "Warehouse", "Report", "Eventhouse"], "mixed"),
    (["Lakehouse", "SQLEndpoint", "SQLEndpoint", "SQLEndpoint"], "data_engineering"),  # endpoints not counted
    ([], "uncategorized"),
    (["SomethingNew", "SQLEndpoint"], "uncategorized"),
])
def test_category_from_item_mix(types, expected):
    assert rk.category_from_items(types)["category"] == expected


def test_ranked_within_each_category_only():
    r = rk.rank([snap(ws("de-1", 70), ws("de-2", 80), ws("bi-1", 95, items=BI), ws("bi-2", 40, items=BI),
                      ws("rt-1", 10, items=["Eventhouse"]))])
    c = cats(r)
    assert list(c) == ["data_engineering", "business_intelligence", "real_time"]
    assert ids(c["data_engineering"]) == ["de-2", "de-1"]
    assert ids(c["business_intelligence"]) == ["bi-1", "bi-2"]
    assert [x["rank"] for x in c["data_engineering"]["ranked"]] == [1, 2]
    assert c["real_time"]["ranked"][0]["rank"] == 1
    assert all(x["category_source"] == "derived" for k in c.values() for x in k["ranked"])
    assert c["business_intelligence"]["ranked"][0]["item_counts"]["business_intelligence"] == 3


def test_caller_category_overrides_derived_mapping_and_function():
    s = snap(ws("a", 70), ws("b", 80), ws("c", 90, items=BI))
    by_map = cats(rk.rank([s], category_of={"a": "Finance", "c": "Finance"}))
    assert ids(by_map["Finance"]) == ["c", "a"]
    assert ids(by_map["data_engineering"]) == ["b"]
    assert by_map["Finance"]["label"] == "Finance"
    assert {x["category_source"] for x in by_map["Finance"]["ranked"]} == {"caller"}
    by_fn = cats(rk.rank([s], category_of=lambda w: "Sales" if w["name"] in ("A", "B") else None))
    assert ids(by_fn["Sales"]) == ["b", "a"] and ids(by_fn["business_intelligence"]) == ["c"]


@pytest.mark.parametrize("bad", [{"a": 3}, {"a": "   "}, {"a": "\u202e"}, lambda w: 5, "not-a-mapping"])
def test_caller_category_must_be_a_name(bad):
    with pytest.raises(ValueError):
        rk.rank([snap(ws("a", 70))], category_of=bad)


# ---- ties


def test_equal_assured_scores_share_a_rank_and_order_by_coverage_name_id():
    r = rk.rank([snap(ws("z", 60, 85, name="Same"), ws("y", 60, 85, name="Same"), ws("x", 60, 95, name="Zed"),
                      ws("w", 60, 85, name="alpha"), ws("v", 75), ws("u", 50))])
    c = cats(r)["data_engineering"]["ranked"]
    assert [x["workspace"]["id"] for x in c] == ["v", "x", "w", "y", "z", "u"]
    assert [x["rank"] for x in c] == [1, 2, 2, 2, 2, 6]
    assert [x["tied"] for x in c] == [False, True, True, True, True, False]


# ---- coverage gates


def test_insufficient_evidence_listed_never_ranked_even_with_higher_assured():
    r = rk.rank([snap(ws("good", 60, 95), ws("thin", 45, 40), ws("none", None, None, status="insufficient_evidence"))])
    c = cats(r)["data_engineering"]
    assert ids(c) == ["good"] and c["ranked"][0]["rank"] == 1
    assert ids(c, "insufficient_evidence") == ["none", "thin"]
    assert all(x["rating"] is None for x in c["insufficient_evidence"])
    assert "rank" not in c["insufficient_evidence"][0]


def test_provisional_ranked_and_flagged():
    c = cats(rk.rank([snap(ws("rated", 60, 90), ws("prov", 65, 70))]))["data_engineering"]["ranked"]
    assert [(x["workspace"]["id"], x["provisional"], x["status"]) for x in c] == [
        ("prov", True, "provisional"), ("rated", False, "rated")]


def test_status_the_coverage_does_not_support_is_taken_down_never_up():
    c = cats(rk.rank([snap(ws("liar", 30, 20, status="rated"), ws("half", 40, 60, status="rated"),
                           ws("modest", 50, 95, status="provisional"))]))["data_engineering"]
    assert ids(c, "insufficient_evidence") == ["liar"]
    assert {x["workspace"]["id"]: x["status"] for x in c["ranked"]} == {"half": "provisional", "modest": "provisional"}


def test_fewer_problems_found_at_lower_coverage_does_not_win():
    """The plan's rule: a perfect score on little evidence ranks on what was assured, not on the score."""
    thin, thorough = ws("thin", 55, 55), ws("thorough", 70, 95)
    thin_block = {"score": 100.0}
    s = snap(thin, thorough)
    s["workspace_scores"]["thin"].update(thin_block)
    c = cats(rk.rank([s]))["data_engineering"]["ranked"]
    assert [(x["workspace"]["id"], x["score"], x["rank"]) for x in c] == [("thorough", 70.0, 1), ("thin", 100.0, 2)]


# ---- one snapshot per workspace


def test_latest_snapshot_per_workspace_wins():
    old = snap(ws("a", 90), ws("b", 50), at=AT, fp="0" * 16)
    new = snap(ws("a", 20), at=LATER, fp="1" * 16)
    r = rk.rank([new, old])
    c = cats(r)["data_engineering"]["ranked"]
    assert [(x["workspace"]["id"], x["assured"]) for x in c] == [("b", 50.0), ("a", 20.0)]
    assert r["superseded"] == 1 and r["workspaces"] == 2


def test_same_instant_pick_does_not_depend_on_order():
    a = snap(ws("a", 90), fp="a" * 16)
    b = snap(ws("a", 10), at="2026-11-18T00:00:00+01:00", fp="b" * 16)  # the same instant as AT
    assert rk.rank([a, b]) == rk.rank([b, a])
    assert cats(rk.rank([a, b]))["data_engineering"]["ranked"][0]["assured"] == 10.0


# ---- profiles and rules


def test_different_profiles_or_rules_are_never_ranked_together():
    base = snap(ws("a", 70), ws("b", 60))
    sec = snap(ws("c", 99), profile=pf.get("enterprise_security"))
    newer_rule = snap(ws("d", 95), rules={**RULES, "FEA-SEC-001": "1.1.0"})
    r = rk.rank([base, sec, newer_rule])
    assert r["comparable"] is False and len(r["groups"]) == 3
    for g in r["groups"]:
        for c in g["categories"]:
            assert [x["rank"] for x in c["ranked"]] == list(range(1, len(c["ranked"]) + 1))
    by_pv = {g["policy_version"]: g for g in r["groups"]}
    assert ids(by_pv[base["policy_version"]]["categories"][0]) == ["a", "b"]
    assert by_pv[sec["policy_version"]]["profile"]["id"] == "enterprise_security"
    assert ids(by_pv[newer_rule["policy_version"]]["categories"][0]) == ["d"]


def test_fixture_snapshots_are_their_own_group():
    r = rk.rank([snap(ws("a", 70)), snap(ws("demo", 99), fixture=True)])
    assert not r["comparable"]
    assert [(g["fixture"], g["workspaces"]) for g in r["groups"]] == [(False, 1), (True, 1)]


def test_no_average_anywhere():
    text = json.dumps(rk.rank([snap(ws("a", 70), ws("b", 60))]))
    assert "average" not in text.replace("No score is averaged", "") and "mean" not in text


# ---- determinism


def test_same_result_under_shuffle():
    snaps = [snap(ws(f"w{i:02d}", float((i * 37) % 101), float(40 + (i * 13) % 61), name=f"Name {i % 7}",
                     items=[DE, BI, ["Eventhouse"], ["Warehouse"], DE + BI][i % 5]),
                  at=[AT, LATER][i % 2], profile=pf.get(["baseline", "capacity"][i % 3 == 0]), fp=f"{i:016x}")
             for i in range(60)]
    snaps.append(snap(ws("w01", 5.0), at="2026-11-01T00:00:00+00:00"))  # older, superseded
    want = rk.rank(snaps)
    rng = random.Random(502)
    for _ in range(20):
        again = copy.deepcopy(snaps)
        rng.shuffle(again)
        assert rk.rank(again) == want
    assert json.dumps(want, sort_keys=True) == json.dumps(rk.rank(list(reversed(snaps))), sort_keys=True)
    assert want["superseded"] == 1


# ---- malformed and hostile input


def _broken(path: str, value):
    s = snap(ws("a", 70))
    node = s
    *parents, last = path.split(".")
    for p in parents:
        node = node[p]
    node[last] = value
    return s


@pytest.mark.parametrize("path,value", [
    ("workspace_scores.a.assured", float("nan")),
    ("workspace_scores.a.assured", float("inf")),
    ("workspace_scores.a.coverage", float("-inf")),
    ("workspace_scores.a.assured", 101),
    ("workspace_scores.a.coverage", -1),
    ("workspace_scores.a.assured", "70"),
    ("workspace_scores.a.assured", True),
    ("workspace_scores.a.status", "great"),
    ("workspace_scores.a", None),
    ("workspace_scores", []),
    ("schema", "something-else"),
    ("assessed_at", "yesterday"),
    ("assessed_at", "2026-11-17T23:00:00"),  # no time zone
    ("assessed_at", 1700000000),
    ("policy_version", "0" * 16),  # doesn't match the profile and rules
    ("rule_versions", {"FEA-SEC-001": 1}),
    ("profile", "baseline"),
    ("scope", {"workspaces": "a"}),
    ("item_coverage", [{"item": "x"}]),
    ("item_coverage", "x"),
])
def test_malformed_snapshot_is_refused(path, value):
    with pytest.raises(ValueError):
        rk.rank([_broken(path, value)])


def test_profile_with_nan_is_refused_even_when_policy_version_matches():
    s = snap(ws("a", 70))
    s["profile"]["weights"]["security"] = float("nan")
    s["policy_version"] = hashlib.sha256(canonical([s["profile"], s["rule_versions"]]).encode()).hexdigest()[:16]
    with pytest.raises(ValueError, match="NaN"):
        rk.rank([s])


@pytest.mark.parametrize("bad", [None, "snap", {"a": 1}, [None], [[]], [42]])
def test_not_a_list_of_snapshots_is_refused(bad):
    with pytest.raises(ValueError):
        rk.rank(bad)


@pytest.mark.parametrize("wid", ["", "x" * 257, "a\nb", "a\u202eb", 5])
def test_bad_workspace_ids_are_refused(wid):
    s = snap(ws("a", 70))
    s["scope"]["workspaces"][0]["id"] = wid
    s["workspace_scores"] = {wid if isinstance(wid, str) else "5": s["workspace_scores"]["a"]}
    with pytest.raises(ValueError):
        rk.rank([s])


def test_caps():
    with pytest.raises(ValueError, match="at most"):
        rk.rank([snap(ws("a", 70))] * (rk.MAX_SNAPSHOTS + 1))
    many = snap(*[ws(f"w{i}", 50) for i in range(rk.MAX_WORKSPACES + 1)])
    with pytest.raises(ValueError, match="at most"):
        rk.rank([many])
    ok = rk.rank([snap(*[ws(f"w{i}", float(i % 100)) for i in range(rk.MAX_WORKSPACES)])])
    assert ok["workspaces"] == rk.MAX_WORKSPACES


def test_hostile_names_are_cleaned_and_cut_to_256():
    evil = "\u202e<script>alert(1)</script>\x00\x1b[31m" + "A" * 1000 + "\u200b"
    r = rk.rank([snap(ws("a", 70, name=evil))], category_of={"a": "\u202eCat\x07" + "C" * 500})
    c = only_group(r)["categories"][0]
    name = c["ranked"][0]["workspace"]["name"]
    assert len(name) == rk.MAX_NAME
    assert not any(ch in name for ch in "\u202e\x00\x1b\u200b")
    assert name.startswith("<script>")  # kept as text; escaping is the view's job
    assert c["category"] == ("Cat" + "C" * 500)[:64] and "\u202e" not in c["category"]


def test_blank_name_falls_back_to_id():
    r = rk.rank([snap(ws("a", 70, name="\u200b\x00"))])
    assert only_group(r)["categories"][0]["ranked"][0]["workspace"]["name"] == "a"


def test_real_snapshot_round_trips_through_json():
    reg = catalog.registry()
    inv = ev.record("workspace.inventory", ev.subject("workspace", "ws-a", "A"), source="GET items",
                    confidence="verified_configuration", collected_at=AT,
                    payload={"items": [{"id": "n1", "type": "Notebook", "name": "N"},
                                       {"id": "l1", "type": "Lakehouse", "name": "L"}]})
    s = assess.assess([inv], reg, scope={"workspaces": [{"id": "ws-a", "name": "A"}]}, assessed_at=AT)
    r = rk.rank([json.loads(json.dumps(s))])
    c = only_group(r)["categories"][0]
    assert c["category"] == "data_engineering"
