"""Workload inventory edges the uncapped run found unpinned (mutation batch 316,
docs/quality/mutation-testing.md): fields a source gives that are kept as given, a notebook linked by
name only when its publish workspace's items were read, who a run on this PC runs as, and a Fabric
schedule's last run that a run this app started already shows."""

from fabric_desktop.workloads import inventory as inv

ENTITY = {
    "name": "orders",
    "owner": "dana",
    "layer": "silver",
    "description": "Orders",
    "schema": "s",
    "table": "t",
}


def meta(project, env_data):
    return inv.meta_workloads(
        project,
        version="v2",
        impacts={},
        env_data=env_data,
        freshness=[],
        pc_schedules=[],
        fabric_schedules={},
        assignments={},
    )


def notebooks(nb, **kw):
    args = {
        "links": {},
        "fabric_runs": [],
        "fabric_items": None,
        "fabric_items_obs": {"state": "live"},
        "fabric_schedules": [],
        "pc_schedules": [],
        "pc_runs": [],
        "assignments": {},
    } | kw
    return inv.notebook_workloads([nb], **args)


def test_a_metadata_run_keeps_its_job_id():
    assert inv.meta_run({"job_id": "j1"}, "prod", "s")["job_id"] == "j1"


def test_a_fabric_job_keeps_its_id_and_names_its_workspace():
    j = inv.fabric_job({"id": "i"}, "Sales")
    assert j["id"] == "i" and j["ran_on"] == "Fabric · Sales"


def test_an_entity_keeps_its_description():
    [w] = meta({"project": {"name": "sales"}, "entities": [ENTITY]}, {})
    assert w["description"] == "Orders"


def test_dev_is_never_behind_the_project():
    [w] = meta(
        {"project": {"name": "sales"}, "entities": [ENTITY]},
        {"dev": {"configured": True}},
    )
    assert w["deployments"][0]["version"]["behind"] is False


def test_only_a_run_of_this_notebook_links_it():
    runs = [
        {"kind": "Notebook run", "name": "other", "item_id": "x", "workspace_id": "w"},
        {"kind": "Spark job", "name": "nb", "item_id": "y", "workspace_id": "w"},
        {"kind": "Notebook run", "name": "nb", "workspace_id": "w"},
    ]
    [w] = notebooks({"name": "nb"}, fabric_runs=runs)
    assert w["link"] is None


def test_a_notebook_is_linked_by_name_only_when_its_workspaces_items_were_read():
    nb = {"name": "nb", "workspace": {"workspace_id": "w"}}
    [w] = notebooks(nb, fabric_items=None)
    assert w["link"] is None
    items = [
        {
            "id": "i",
            "workspace_id": "w",
            "type": "Notebook",
            "displayName": "nb",
            "workspace_name": "Sales",
        }
    ]
    [w] = notebooks(nb, fabric_items=items)
    assert w["link"]["workspace_name"] == "Sales" and w["link"]["how"] == "name match"


def test_a_notebook_names_its_lakehouse_runs_as_you_and_runs_its_schedule():
    sched = [{"id": "s1", "target": {"kind": "notebook", "name": "nb"}}]
    [w] = notebooks(
        {"name": "nb", "lakehouse": {"name": "lh"}},
        pc_schedules=sched,
        user="me@contoso.com",
    )
    d = w["deployments"][0]
    assert d["tables_in"] == "lh"
    assert d["identity"]["label"] == "You (me@contoso.com), signed in on this PC"
    assert d["actions"] == ["run_schedule"]


def test_a_local_pipeline_is_preview_and_runs_as_you():
    [w] = inv.pipeline_workloads(
        [{"path": "C:/p/Load.DataPipeline", "name": "Load"}],
        runs=[],
        pc_schedules=[],
        pc_runs=[],
        assignments={},
        user="me@contoso.com",
    )
    assert w["preview"] is True
    assert (
        w["deployments"][0]["identity"]["label"]
        == "You (me@contoso.com), signed in on this PC"
    )


def test_a_schedules_last_run_already_shown_by_a_run_from_this_app_isnt_added_again():
    item = {"id": "i", "workspace_id": "w", "type": "DataPipeline"}
    runs = [{"item_id": "i", "job_id": "j", "status": "Completed", "started_at": "t1"}]
    sched = [
        {"id": "f1", "item_id": "i", "last": {"status": "Completed", "started": "t1"}}
    ]
    [w] = inv.fabric_workloads(
        [item],
        items_obs={"state": "live"},
        linked=set(),
        fabric_schedules=sched,
        fabric_runs=runs,
        assignments={},
    )
    assert [r["id"] for r in w["runs"]] == ["j"]
