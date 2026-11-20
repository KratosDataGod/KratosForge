"""Workload list edges the main tests don't pin down (mutation batch 145, docs/quality/mutation-testing.md):
which group a row lands in, the default page size, nobody signed in, and missing tags, actions and
environment names."""

from fabric_desktop.workloads import query
from fabric_desktop.workloads.model import HEALTH, HEALTH_LABELS


def _w(name, envs=(), where="this_pc", **extra):
    deps = [{"env": e, "location": {"kind": where, "label": "This PC"}} for e in envs] or \
        [{"location": {"kind": where, "label": "Fabric" if where == "fabric" else "This PC"}}]
    return {"id": name, "name": name, "kind": "notebook", "kind_label": "Notebook", "group": {"id": "p", "label": "p"},
            "health": "healthy", "health_label": HEALTH_LABELS["healthy"], "health_rank": HEALTH.index("healthy"),
            "readiness": {"state": "ready"}, "unowned": True, "partial": False, "capabilities": [],
            "findings": [], "owners": {}, "deployments": deps, **extra}


def test_each_row_lands_in_its_own_environment_group():
    page = query.select([_w("fab", where="fabric"), _w("pc")], group="environment")
    assert {r["name"]: r["group_key"] for r in page["rows"]} == {"fab": "env:fabric", "pc": "env:pc"}


def test_prod_comes_before_test_whatever_order_the_deployments_are_in():
    page = query.select([_w("x", envs=("test", "prod")), _w("y", envs=("dev", "test"))], group="environment")
    assert {r["name"]: r["group_key"] for r in page["rows"]} == {"x": "env:prod", "y": "env:test"}


def test_the_default_page_is_100_rows():
    page = query.select([_w(f"w{n:03d}") for n in range(150)])
    assert page["limit"] == 100 and len(page["rows"]) == 100 and page["total"] == 150


def test_nobody_signed_in_owns_nothing():
    assert query.is_mine({"owners": {"team": {"value": ""}}}, "") is False


def test_a_row_without_tags_or_actions_has_empty_lists():
    r = query.row(_w("x", envs=("dev",)))
    assert r["tags"] == [] and r["deployments"][0]["actions"] == []
    r = query.row(_w("x", envs=("dev",), tags=["a"]) | {"deployments": [
        {"env": "dev", "location": {"kind": "this_pc", "label": "This PC"}, "actions": ["run"]}]})
    assert r["tags"] == ["a"] and r["deployments"][0]["actions"] == ["run"]


def test_search_finds_a_deployments_environment_and_its_label():
    w = _w("x") | {"deployments": [{"env": "qa", "env_label": "Quality", "location": {"kind": "this_pc", "label": "This PC"}}]}
    assert [r["name"] for r in query.select([w], query="qa")["rows"]] == ["x"]
    assert [r["name"] for r in query.select([w], query="quality")["rows"]] == ["x"]
