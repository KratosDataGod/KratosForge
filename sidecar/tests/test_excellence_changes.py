"""Change-based reassessment (FEA-506): what changed between two reads, and which controls to assess
again. Polling comparison only; a read that failed gives unknown, never unchanged."""

from __future__ import annotations

import json

from fabric_desktop.excellence import adapters, assess, catalog, changes as cx, evidence as ev
from fabric_desktop.excellence import catalog_architecture as arch

AT1 = "2026-11-18T02:00:00+00:00"
AT2 = "2026-11-19T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
CAP = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CAP2 = "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee"
REG = catalog.registry()


def ws(capacity: str = CAP, description: str = "Sales data") -> dict:
    return {"id": WSID, "displayName": "Sales Prod", "description": description, "capacityId": capacity}


def item(i: str, kind: str = "Notebook", name: str | None = None) -> dict:
    return {"id": i, "type": kind, "displayName": name or i}


def role(pid: str, r: str = "Admin", kind: str = "User", name: str | None = None) -> dict:
    return {"principal": {"id": pid, "type": kind, "displayName": name or pid,
                          "userDetails": {"userPrincipalName": f"{pid}@contoso.com"}}, "role": r}


def opt(matched: dict[str, list[str]] | None = None, unread: tuple[str, ...] = ()) -> dict:
    matched = matched or {}
    workloads = [{"item_id": i, "type": "Notebook", "name": i, "workspace_id": WSID,
                  "definition": "not_read" if i in unread else "read", "runs": {"read": False}}
                 for i in ("nb1", "nb2")]
    opps = [{"id": f"o-{r}-{i}", "rule": r, "title": r, "confidence": "high", "artifacts": [{"item_id": i, "role": "x"}]}
            for i, rules in matched.items() for r in rules]
    return {"workloads": workloads, "opportunities": opps}


def reads(*, items: list[dict] | None = None, roles: list[dict] | None = None, w: dict | None = None,
          o: dict | None = None, at: str = AT1, sku: str = "F64", capacities: bool = True) -> list[dict]:
    w = w or ws()
    items = [item("nb1"), item("nb2"), item("lh1", "Lakehouse")] if items is None else items
    roles = [role("u1"), role("u2", "Member")] if roles is None else roles
    out = adapters.workspace(w, items, collected_at=at)
    out += adapters.roles(w, roles, collected_at=at)
    if capacities:
        out += adapters.capacity(CAP, [{"id": CAP, "displayName": "Cap", "state": "Active", "sku": sku,
                                        "region": "West Europe"}], collected_at=at)
    out += adapters.optimization(o or opt(), collected_at=at)
    out += arch.notebook_bindings(o or opt(), collected_at=at)
    return out


def result(records: list[dict], at: str = AT1) -> dict:
    return assess.assess(records, REG, assessed_at=at,
                         scope={"workspaces": [{"id": WSID, "name": "Sales Prod"}], "capacities": [{"id": CAP}]})


def run(prev: list[dict], new: list[dict] | dict, *, as_result: bool = True) -> dict:
    return cx.changes(result(prev), result(new, AT2) if as_result and isinstance(new, list) else new, REG)


def kinds(res: dict, status: str = "changed") -> list[tuple[str, str]]:
    return [(c["kind"], c["subject"]["id"]) for c in res["changes"] if c["status"] == status]


def declaring(*evidence_kinds: str) -> set[str]:
    return {c.id for c in REG.controls() if set(evidence_kinds) & (set(c.evidence_kinds) | set(c.optional_kinds))}


# ---- unchanged, determinism, order

def test_same_reads_have_no_changes():
    res = run(reads(), reads(at=AT2))
    assert res["changes"] == [] and res["count"] == 0 and res["unknown"] == 0
    assert res["method"] == "polling" and "not by event-driven monitoring" in res["note"]
    assert res["from"] == AT1 and res["to"] == AT2


def test_reordering_is_not_a_change():
    prev = reads(items=[item("nb1"), item("nb2"), item("lh1", "Lakehouse")], roles=[role("u1"), role("u2", "Member")])
    new = reads(items=[item("lh1", "Lakehouse"), item("nb2"), item("nb1")], roles=[role("u2", "Member"), role("u1")],
                at=AT2)
    # also a payload list reordered by hand (adapters sort, a stored snapshot from elsewhere may not)
    for r in new:
        if r["kind"] == "workspace.inventory":
            items = list(reversed(r["payload"]["items"]))
            r.update(ev.record(r["kind"], r["subject"], source=r["source"], confidence=r["confidence"],
                               collected_at=r["collected_at"], payload={"items": items}))
    assert run(prev, new)["count"] == 0


def test_deterministic_and_input_order_free():
    prev, new = reads(), reads(items=[item("nb1"), item("nb3"), item("lh1", "Lakehouse")], roles=[role("u9")], at=AT2)
    one = cx.changes(prev, new, REG)
    two = cx.changes(list(reversed(prev)), list(reversed(new)), REG)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert json.dumps(one, sort_keys=True) == json.dumps(cx.changes(prev, new, REG), sort_keys=True)


def test_default_registry_is_the_catalog():
    assert cx.changes(reads(), reads(items=[item("nb1")], at=AT2))["controls_to_reassess"] == \
        cx.changes(reads(), reads(items=[item("nb1")], at=AT2), REG)["controls_to_reassess"]


# ---- each kind

def test_item_added_and_removed():
    res = run(reads(), reads(items=[item("nb1"), item("lh1", "Lakehouse"), item("sm1", "SemanticModel", "Sales")],
                             at=AT2))
    got = kinds(res)
    assert ("item_added", "sm1") in got and ("item_removed", "nb2") in got
    added = next(c for c in res["changes"] if c["kind"] == "item_added")
    assert added["subject"] == {"type": "item", "id": "sm1", "name": "Sales", "workspace_id": WSID,
                                "item_type": "SemanticModel"}
    sm_controls = {c.id for c in REG.controls() if "SemanticModel" in c.item_types}
    assert sm_controls and sm_controls <= set(added["controls_to_reassess"])
    assert declaring("workspace.inventory") <= set(added["controls_to_reassess"])
    nb_only = {c.id for c in REG.controls() if c.scope == "item" and c.item_types == ("Notebook",)}
    assert not (nb_only - declaring("workspace.inventory")) & set(added["controls_to_reassess"])
    removed = next(c for c in res["changes"] if c["kind"] == "item_removed")
    assert removed["subject"]["item_type"] == "Notebook"
    # the removed item's own evidence isn't reported again as unknown
    assert not [c for c in res["changes"] if c["subject"]["id"] == "nb2" and c["kind"] != "item_removed"]


def test_definition_changed_uses_evidence_hash_as_proxy():
    res = run(reads(), reads(o=opt({"nb1": ["OE-FULL-REFRESH"]}), at=AT2))
    defs = [c for c in res["changes"] if c["kind"] == "definition_changed"]
    assert [(c["subject"]["id"], c["evidence_kinds"]) for c in defs] == [("nb1", ["item.optimization"])]
    expected = {c.id for c in REG.controls() if c.scope == "item" and "Notebook" in c.item_types
                and "item.optimization" in (*c.evidence_kinds, *c.optional_kinds)}
    assert set(defs[0]["controls_to_reassess"]) == expected
    # no definition text is kept: only the evidence the change was seen in
    assert set(defs[0]) <= {"kind", "status", "subject", "evidence_kinds", "controls_to_reassess", "detail", "reason"}


def test_role_assignment_changed():
    res = run(reads(), reads(roles=[role("u1"), role("u2", "Member"), role("g1", "Contributor", "Group")], at=AT2))
    assert kinds(res) == [("role_assignment_changed", WSID)]
    c = res["changes"][0]
    assert c["detail"] == {"added": 1, "removed": 0}
    assert set(c["controls_to_reassess"]) == declaring("workspace.roles")
    role_swap = run(reads(), reads(roles=[role("u1"), role("u2", "Admin")], at=AT2))
    assert role_swap["changes"][0]["detail"] == {"added": 1, "removed": 1}


def test_display_name_alone_is_evidence_changed_not_role_change():
    res = run(reads(), reads(roles=[role("u1", name="Una"), role("u2", "Member")], at=AT2))
    assert kinds(res) == [("evidence_changed", WSID)]
    assert res["changes"][0]["evidence_kinds"] == ["workspace.roles"]


def test_capacity_reassignment():
    res = run(reads(), reads(w=ws(capacity=CAP2), at=AT2))
    assert kinds(res) == [("capacity_changed", WSID)]
    c = res["changes"][0]
    assert c["detail"] == {"from": [CAP], "to": [CAP2]}
    cap_controls = {x.id for x in REG.controls() if x.scope == "capacity"}
    assert cap_controls and cap_controls <= set(c["controls_to_reassess"])
    assert declaring("workspace.info") <= set(c["controls_to_reassess"])


def test_capacity_state_change():
    res = run(reads(), reads(sku="F128", at=AT2))
    assert kinds(res) == [("capacity_changed", CAP)]
    assert set(res["changes"][0]["controls_to_reassess"]) == declaring("capacity.state")


def test_git_state_changed_when_evidence_exists_and_not_tracked_otherwise():
    assert run(reads(), reads(at=AT2))["not_tracked"][0]["kind"] == "git_state_changed"
    subj = ev.subject("workspace", WSID, "Sales Prod")

    def git(branch: str, at: str) -> dict:
        return ev.record("workspace.git", subj, source="GET /v1/workspaces/{id}/git/status",
                         confidence="verified_configuration", collected_at=at, payload={"branch": branch})
    res = cx.changes(reads() + [git("main", AT1)], reads(at=AT2) + [git("dev", AT2)], REG)
    assert kinds(res) == [("git_state_changed", WSID)] and res["not_tracked"] == []


def test_other_evidence_is_never_dropped():
    res = run(reads(), reads(w=ws(description="changed"), at=AT2))
    assert kinds(res) == [("evidence_changed", WSID)]
    assert set(res["changes"][0]["controls_to_reassess"]) == declaring("workspace.info")


# ---- failed reads -> unknown

def test_failed_roles_read_is_unknown_not_unchanged():
    res = run(reads(), [r for r in reads(at=AT2) if r["kind"] != "workspace.roles"])
    assert kinds(res, "unknown") == [("role_assignment_changed", WSID)]
    c = res["changes"][0]
    assert "later read" in c["reason"] and set(c["controls_to_reassess"]) == declaring("workspace.roles")
    assert res["unknown"] == 1 and res["changed"] == 0


def test_failed_inventory_read_is_unknown_for_items():
    new = [r for r in reads(at=AT2) if r["kind"] != "workspace.inventory"]
    res = run(reads(), new)
    assert kinds(res, "unknown") == [("item_added", WSID), ("item_removed", WSID)]
    assert {c.id for c in REG.controls() if c.scope == "item"} <= set(res["changes"][0]["controls_to_reassess"])


def test_definition_not_read_is_unknown():
    res = run(reads(), reads(o=opt(unread=("nb2",)), at=AT2))
    unknown = kinds(res, "unknown")
    assert ("definition_changed", "nb2") in unknown
    assert not [c for c in res["changes"] if c["status"] == "changed" and c["subject"]["id"] == "nb2"]


def test_earlier_read_missing_is_unknown_too():
    prev = [r for r in reads() if r["kind"] != "capacity.state"]
    res = run(prev, reads(at=AT2))
    assert kinds(res, "unknown") == [("capacity_changed", CAP)]
    assert "earlier read" in res["changes"][0]["reason"]


def test_failed_workspace_info_is_unknown_for_capacity():
    res = run(reads(), [r for r in reads(at=AT2) if r["kind"] != "workspace.info"])
    assert kinds(res, "unknown") == [("capacity_changed", WSID), ("evidence_changed", WSID)]


def test_tampered_record_counts_as_missing():
    new = reads(at=AT2)
    for r in new:
        if r["kind"] == "workspace.roles":
            r["payload"]["assignments"].append({"id": "x", "type": "User", "role": "Admin", "name": "x", "guest": False})
    res = run(reads(), new, as_result=False)
    assert kinds(res, "unknown") == [("role_assignment_changed", WSID)]


def test_raw_reads_use_the_previous_scope_and_scope_changes_are_listed():
    res = cx.changes(result(reads()), reads(at=AT2), REG)
    assert res["count"] == 0 and res["to"] is None
    other = assess.assess(reads(at=AT2), REG, assessed_at=AT2, scope={"workspaces": [], "capacities": [{"id": CAP}]})
    res = cx.changes(result(reads()), other, REG)
    assert res["not_compared"] == [{"type": "workspace", "id": WSID}]
    assert all(c["subject"]["type"] == "capacity" for c in res["changes"])


# ---- caps and hostile names

def test_cap_lists_fifty_with_the_full_count():
    many = [item(f"nb{i:03}") for i in range(120)]
    res = run(reads(items=[]), reads(items=many, at=AT2))
    assert len(res["changes"]) == cx.MAX_CHANGES and res["count"] == 120 and res["truncated"]
    assert [c["subject"]["id"] for c in res["changes"]] == [f"nb{i:03}" for i in range(50)]
    nb_controls = {c.id for c in REG.controls() if "Notebook" in c.item_types}
    assert nb_controls <= set(res["controls_to_reassess"])


def test_hostile_names_are_cut_and_cleaned():
    evil = "<script>alert(1)</script>\n\x00 " + "x" * 5000
    res = run(reads(), reads(items=[item("nb1"), item("nb2"), item("lh1", "Lakehouse"), item("e1", "Lakehouse", evil)],
                             at=AT2))
    name = next(c for c in res["changes"] if c["kind"] == "item_added")["subject"]["name"]
    assert len(name) == cx.MAX_NAME and name.endswith("…")
    assert "\n" not in name and "\x00" not in name and " " not in name
    json.dumps(res)
