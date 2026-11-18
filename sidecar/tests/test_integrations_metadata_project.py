"""The metadata project's read-only tools for an external AI tool (#2363): column lineage, the run
log and one run's data quality results, through the same registry the MCP server and local API use.

These run the real engine on a sample project kept on this PC (Dev), so the tools are checked
against what the Data mesh page shows, not against a fake."""

import pytest

from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError, validate
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.service import Service

IDS = ("fabric_desktop_get_lineage", "fabric_desktop_list_project_runs", "fabric_desktop_get_project_run")


@pytest.fixture
def reg():
    r = Registry()
    register_core(r)
    return r


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    return s


def call(reg, svc, cap_id, **args):
    return reg.invoke(svc, cap_id, args, CallContext(Caller("t", "test"), timeout=30))


def test_the_tools_are_read_only_metadata_and_never_ask_for_approval(reg):
    for cap_id in IDS:
        cap = reg.get(cap_id)
        assert cap.read_only and not cap.destructive and cap.approval == "never", cap_id
        assert cap.permission == "READ_METADATA", cap_id


def test_lineage_of_every_column_and_of_one(reg, svc):
    out = call(reg, svc, "fabric_desktop_get_lineage", entity="silver.orders")
    assert out["entity"] == "silver.orders"
    assert out["columns"]["net_amount"]["how"] == "derived"
    one = call(reg, svc, "fabric_desktop_get_lineage", entity="bronze.orders", column="discount")
    assert [(s["entity"], s["column"]) for s in one["downstream"]][-1] == ("gold.fact_sales", "net_amount")
    assert validate(reg.get("fabric_desktop_get_lineage").output_schema, out) is not None


def test_lineage_of_an_unknown_entity_is_not_found(reg, svc):
    with pytest.raises(IntegrationError) as err:
        call(reg, svc, "fabric_desktop_get_lineage", entity="silver.nope")
    assert err.value.code == "not_found"


def test_runs_are_listed_newest_first_and_paged_without_steps(reg, svc):
    out = call(reg, svc, "fabric_desktop_list_project_runs", limit=4)
    assert out["env"] == "dev" and out["total"] == 6 and len(out["items"]) == 4 and out["next_offset"] == 4
    assert all("steps" not in r and r["status"] == "ok" and r["ran_on"] for r in out["items"])
    rest = call(reg, svc, "fabric_desktop_list_project_runs", offset=4, limit=4)
    assert len(rest["items"]) == 2 and rest["next_offset"] is None
    started = [r["started_at"] for r in out["items"] + rest["items"]]
    assert started == sorted(started, reverse=True)


def test_one_run_has_its_steps_and_quality_results(reg, svc):
    runs = call(reg, svc, "fabric_desktop_list_project_runs")["items"]
    checked = None
    for r in runs:
        got = call(reg, svc, "fabric_desktop_get_project_run", run_id=r["run_id"])
        assert got["run_id"] == r["run_id"] and got["steps"][0]["name"] == "read" and "dq" not in got
        assert got["quality_failed"] == sum(1 for q in got["quality"] if q["failed"] > 0)
        checked = checked or (got if got["quality"] else None)
    assert checked is not None, "the sample project has checks: some run should have quality results"
    assert {"check", "failed", "action"} <= set(checked["quality"][0])


def test_an_unknown_run_is_not_found(reg, svc):
    with pytest.raises(IntegrationError) as err:
        call(reg, svc, "fabric_desktop_get_project_run", run_id="nope")
    assert err.value.code == "not_found"


def test_with_no_project_open_each_tool_says_how_to_open_one(reg, tmp_path):
    svc = Service(root=tmp_path / "empty")
    for cap_id, args in (("fabric_desktop_get_lineage", {"entity": "x"}), ("fabric_desktop_list_project_runs", {}),
                         ("fabric_desktop_get_project_run", {"run_id": "r"})):
        with pytest.raises(IntegrationError) as err:
            call(reg, svc, cap_id, **args)
        assert err.value.code == "not_found" and "Data mesh" in err.value.hint, cap_id


def test_bad_arguments_are_refused_before_anything_runs(reg, svc):
    for cap_id, args in (("fabric_desktop_list_project_runs", {"env": "staging"}),
                         ("fabric_desktop_get_project_run", {"run_id": "../../etc"}),
                         ("fabric_desktop_get_lineage", {})):
        with pytest.raises(IntegrationError) as err:
            call(reg, svc, cap_id, **args)
        assert err.value.code == "invalid_input", cap_id


def test_lineage_is_a_resource_too(reg, svc):
    cap, args = reg.resolve("fabric-desktop://projects/metadata/lineage/silver.orders")
    assert cap.id == "fabric_desktop_get_lineage" and args == {"entity": "silver.orders"}
