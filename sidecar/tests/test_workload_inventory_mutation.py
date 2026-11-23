"""Workload inventory edges the main tests don't pin down (mutation batch 140, docs/quality/mutation-testing.md):
the defaults each run, schedule and workload gets for fields its source leaves out, and the fields it
keeps when they are there."""

from fabric_desktop.workloads import inventory as inv


def test_an_agent_run_names_the_agent_and_its_host_when_it_has_one():
    assert inv.meta_run({"worker": "etl-1", "host": "srv"}, "prod", "s")["ran_on"] == "Agent etl-1 on srv"
    assert inv.meta_run({"worker": "etl-1"}, "prod", "s")["ran_on"] == "Agent etl-1"
    assert inv.meta_run({"worker": "etl-1"}, "prod", "s")["where"] == "agent"


def test_a_run_ends_its_seconds_after_it_started_and_without_seconds_has_no_end():
    r = {"started_at": "2026-11-18T10:00:00Z"}
    assert inv.meta_run(r | {"seconds": 90}, "prod", "s")["ended_at"] == "2026-11-18T10:01:30+00:00"
    assert inv.meta_run(r | {"seconds": 0}, "prod", "s")["ended_at"] == "2026-11-18T10:00:00+00:00"
    assert inv.meta_run(r | {"seconds": ""}, "prod", "s")["ended_at"] is None
    assert inv.meta_run(r, "prod", "s")["ended_at"] is None
    assert inv.meta_run({"seconds": 5}, "prod", "s")["ended_at"] is None


def test_a_fabric_run_from_this_app_gets_defaults_and_keeps_what_it_has():
    bare = inv.fabric_run({})
    assert (bare["env"], bare["ran_on"], bare["message"], bare["job_id"], bare["open"]) == \
        ("", "Fabric · workspace", "", "", None)
    full = inv.fabric_run({"env": "prod", "workspace_name": "Sales", "message": "m", "job_id": "j", "url": "https://u"})
    assert (full["env"], full["ran_on"], full["message"], full["job_id"], full["open"]) == \
        ("prod", "Fabric · Sales", "m", "j", {"kind": "url", "url": "https://u"})


def test_a_fabric_job_takes_either_spelling_of_its_times_and_defaults_the_rest():
    j = inv.fabric_job({"started": "a", "ended": "b"})
    assert (j["started_at"], j["ended_at"], j["job_id"], j["invoke"]) == ("a", "b", "", "")
    j = inv.fabric_job({"id": "i", "startTimeUtc": "s", "endTimeUtc": "e", "started": "a", "ended": "b",
                        "invokeType": "Scheduled"})
    assert (j["started_at"], j["ended_at"], j["job_id"], j["invoke"]) == ("s", "e", "i", "Scheduled")


def test_a_schedule_run_is_named_by_its_schedule_and_start_and_defaults_its_message():
    r = inv.pc_schedule_run({"schedule_id": "s1", "started": "t", "status": "ok"})
    assert r["id"] == "s1:t" and r["message"] == "" and r["state"] == "ok"
    assert inv.pc_schedule_run({"message": "m"})["message"] == "m"


def test_runs_are_newest_first_and_one_with_no_start_comes_last():
    runs = [{"started_at": "2026-01-01"}, {"started_at": None}, {"started_at": "2026-02-01"}]
    assert [r["started_at"] for r in inv._newest(runs)] == ["2026-02-01", "2026-01-01", None]


def meta(project, env_data, pc_schedules=(), fabric_schedules=None, **kw):
    return inv.meta_workloads(project, version="v2", impacts={}, env_data=env_data, freshness=[],
                              pc_schedules=list(pc_schedules), fabric_schedules=fabric_schedules or {},
                              assignments={}, **kw)


ENTITY = {"name": "orders", "owner": "dana", "layer": "silver", "description": "Orders", "schema": "s", "table": "t"}


def test_a_project_with_no_name_team_or_contacts_gets_the_defaults():
    [w] = meta({"entities": [ENTITY]}, {})
    assert w["id"] == "meta:project:orders" and w["group"]["label"] == "project"
    assert "team" not in w["owners"] and "business_contact" not in w["owners"]
    assert w["search"] == "orders project  dana silver Orders"


def test_a_project_names_its_team_and_first_contact_as_owners():
    [w] = meta({"project": {"name": "sales", "team": "data", "contacts": ["ana@contoso.com", "bo@contoso.com"]},
                "entities": [ENTITY]}, {})
    assert w["owners"]["team"]["value"] == "data" and w["owners"]["business_contact"]["value"] == "ana@contoso.com"
    assert w["search"] == "orders sales data dana silver Orders"


def test_where_tables_go_in_each_environment():
    project = {"project": {"name": "sales"}, "entities": [ENTITY],
               "environments": {"test": {"workspace_name": "Sales Test"}, "prod": {"target": "local"}}}
    on = {"configured": True}
    [w] = meta(project, {"dev": on, "test": on, "prod": on})
    tables = {d["env"]: d["tables_in"] for d in w["deployments"]}
    assert tables == {"dev": "This PC only", "test": "Sales Test", "prod": "This PC only"}
    project["environments"] = {"test": {}}
    [w] = meta(project, {"test": on})
    assert w["deployments"][0]["tables_in"] == "Workspace"


def test_the_newest_deployment_says_which_version_an_environment_runs():
    deps = [{"version": "v1", "at": "2026-11-01", "source_env": "test"},
            {"version": "v2", "at": "2026-11-10", "source_env": "dev"},
            {"version": "v0", "at": None}]
    [w] = meta({"project": {"name": "sales"}, "entities": [ENTITY]}, {"prod": {"configured": True, "deployments": deps}})
    v = w["deployments"][0]["version"]
    assert v == {"deployed": "v2", "current": "v2", "behind": False, "at": "2026-11-10", "from": "dev"}


def test_schedules_for_an_environment_are_the_ones_for_this_entity_or_all_of_them():
    pc = [{"id": "all", "target": {"kind": "meta", "env": "prod"}},
          {"id": "mine", "target": {"kind": "meta", "env": "prod", "entities": ["orders"]}, "enabled": False},
          {"id": "other", "target": {"kind": "meta", "env": "prod", "entities": ["customers"]}},
          {"id": "dev", "target": {"kind": "meta", "env": "dev"}},
          {"id": "nb", "target": {"kind": "notebook", "env": "prod"}},
          {"id": "none"}]
    [w] = meta({"project": {"name": "sales"}, "entities": [ENTITY]}, {"prod": {"configured": True}}, pc,
               {"prod": [{"id": "f1", "text": "daily"}]})
    scheds = w["deployments"][0]["schedules"]
    assert [(s["id"], s["enabled"], s["where"]) for s in scheds] == \
        [("all", True, "This PC"), ("mine", False, "This PC"), ("f1", False, "Fabric")]


def test_the_products_an_entity_feeds_use_the_name_when_they_have_no_title():
    project = {"project": {"name": "sales"}, "entities": [ENTITY],
               "products": [{"name": "p1", "ports": [{"name": "o", "entity": "orders"}, {"name": "c", "entity": "x"}]},
                            {"name": "p2", "title": "Two", "owner": "bo", "status": "published",
                             "ports": [{"name": "o2", "entity": "orders"}]}]}
    [w] = meta(project, {})
    assert w["dependencies"]["products"] == [
        {"product": "p1", "title": "p1", "port": "o", "status": None, "owner": ""},
        {"product": "p2", "title": "Two", "port": "o2", "status": "published", "owner": "bo"}]


def test_an_entitys_errors_are_its_own_without_its_name():
    project = {"project": {"name": "sales"}, "entities": [ENTITY],
               "errors": ["orders: no key", "orders: a: b", "customers: bad", "orders:nospace"]}
    [w] = meta(project, {})
    assert w["errors"] == ["no key", "a: b"]


def test_a_kept_link_without_a_workspace_name_or_how_gets_the_defaults():
    link = inv.notebook_link("Load", {}, {"Load": {"workspace_id": "w", "item_id": "i"}}, [], None)
    assert link == {"workspace_id": "w", "workspace_name": "", "item_id": "i", "how": "recorded", "at": None, "kept": True}
    link = inv.notebook_link("Load", {}, {"Load": {"workspace_id": "w", "item_id": "i", "workspace_name": "W",
                                                   "how": "by hand", "at": "t"}}, [], None)
    assert (link["workspace_name"], link["how"], link["at"]) == ("W", "by hand", "t")


def test_a_link_from_a_run_without_a_workspace_name_gets_an_empty_one():
    runs = [{"kind": "Notebook run", "name": "Load", "item_id": "i", "workspace_id": "w", "started_at": "t"}]
    link = inv.notebook_link("Load", {}, {}, runs, None)
    assert link["workspace_name"] == "" and link["how"] == "from a run" and link["kept"] is False
    link = inv.notebook_link("Load", {}, {}, [runs[0] | {"workspace_name": "W"}], None)
    assert link["workspace_name"] == "W"


def notebooks(nb, **kw):
    args = {"links": {}, "fabric_runs": [], "fabric_items": None, "fabric_items_obs": {"state": "live"},
            "fabric_schedules": [], "pc_schedules": [], "pc_runs": [], "assignments": {}} | kw
    return inv.notebook_workloads([nb], **args)


LINKED = {"Load": {"workspace_id": "w", "item_id": "i"}}


def test_a_linked_notebooks_fabric_runs_are_cached_until_signed_in():
    [w] = notebooks({"name": "Load"}, links=LINKED)
    assert w["deployments"][1]["observation"]["state"] == "cached"
    [w] = notebooks({"name": "Load"}, links=LINKED, signed_in=True)
    assert w["deployments"][1]["observation"]["state"] == "live"


def test_a_notebook_has_only_its_own_schedules_on_this_pc_and_in_fabric():
    pc = [{"id": "a", "target": {"kind": "notebook", "name": "Load"}},
          {"id": "b", "target": {"kind": "notebook", "name": "Other"}},
          {"id": "c", "target": {"kind": "pipeline", "name": "Load"}}]
    fab = [{"id": "f1", "item_id": "i"}, {"id": "f2", "item_id": "other"}]
    [w] = notebooks({"name": "Load"}, links=LINKED, pc_schedules=pc, fabric_schedules=fab)
    assert [s["id"] for s in w["deployments"][0]["schedules"]] == ["a"]
    assert [s["id"] for s in w["deployments"][1]["schedules"]] == ["f1"]


def test_a_linked_notebook_names_its_lakehouses_workspace_and_can_be_cancelled():
    [w] = notebooks({"name": "Load"}, links=LINKED)
    assert w["deployments"][1]["tables_in"] == "" and "cancelable" in w["capabilities"]
    [w] = notebooks({"name": "Load", "lakehouse": {"workspace_name": "Sales"}}, links=LINKED)
    assert w["deployments"][1]["tables_in"] == "Sales"
    [w] = notebooks({"name": "Load"})
    assert "cancelable" not in w["capabilities"] and len(w["deployments"]) == 1


def test_a_pipeline_run_stopped_by_the_app_says_so_and_counts_its_mocks():
    r = inv.pipeline_run({"run_id": "r", "state": "interrupted"})
    assert r["state"] == "cancelled" and r["message"] == "KratosForge stopped while this run was going. It wasn't resumed."
    r = inv.pipeline_run({"state": "interrupted"}, "the schedule's own text")
    assert r["message"] == "the schedule's own text" and r["id"] == ""
    r = inv.pipeline_run({"state": "failed", "stop_reason": "boom", "options": {"mocked": ["a", "b"]}})
    assert r["message"] == "boom" and r["mocked"] == 2 and r["state"] == "failed"
    r = inv.pipeline_run({"state": "succeeded"})
    assert r["message"] == "" and r["mocked"] == 0


def pipelines(pipes, **kw):
    args = {"runs": [], "pc_schedules": [], "pc_runs": [], "assignments": {}} | kw
    return inv.pipeline_workloads(pipes, **args)


def test_a_pipeline_only_a_schedule_runs_is_listed_by_its_folder_name():
    pc = [{"id": "s1", "target": {"kind": "pipeline", "path": "C:/p/Load.DataPipeline"}},
          {"id": "s2", "target": {"kind": "pipeline", "path": "C:/p/Copy.DataPipeline", "name": "Copy it"}},
          {"id": "s3", "target": {"kind": "notebook", "path": "C:/p/Nb.DataPipeline"}},
          {"id": "s4", "target": {"kind": "pipeline"}}]
    out = {w["name"]: w for w in pipelines([], pc_schedules=pc)}
    assert set(out) == {"Load", "Copy it"}
    assert out["Load"]["description"] == "" and out["Load"]["definition"]["repo"] == ""
    assert [s["id"] for s in out["Load"]["deployments"][0]["schedules"]] == ["s1"]


def test_the_first_pipeline_at_a_path_is_kept_and_its_place_is_described():
    a = {"path": "C:/r/A.DataPipeline", "name": "A", "repo": "repo", "relative": "pipes/A"}
    [w] = pipelines([a, a | {"name": "Other"}])
    assert w["name"] == "A" and w["description"] == "repo · pipes/A" and w["errors"] == []
    assert (w["definition"]["repo"], w["definition"]["relative"]) == ("repo", "pipes/A")
    [w] = pipelines([a | {"repo": "", "problem": "bad json"}])
    assert w["description"] == "pipes/A" and w["errors"] == ["bad json"]


def test_runs_count_for_a_pipeline_by_its_schedule_or_its_unique_name():
    a = {"path": "C:/r/A.DataPipeline", "name": "A"}
    b = {"path": "C:/s/A.DataPipeline", "name": "A"}  # the same name in another folder
    c = {"path": "C:/r/C.DataPipeline", "name": "C"}
    runs = [{"run_id": "r1", "pipeline": "A", "state": "succeeded", "started": "1"},
            {"run_id": "r2", "pipeline": "C", "state": "failed", "started": "2"},
            {"run_id": "r3", "pipeline": "C", "state": "succeeded", "started": "3"}]
    pc = [{"id": "s1", "target": {"kind": "pipeline", "path": "C:/r/A.DataPipeline"}},
          {"id": "s9", "target": {"kind": "pipeline", "path": "C:/r/C.DataPipeline"}}]
    pc_runs = [{"schedule_id": "s1", "run_id": "r1", "message": "from the schedule"},
               {"schedule_id": "s1", "run_id": "gone", "started": "0", "status": "failed"},
               {"schedule_id": "s9", "run_id": "r3"}]
    out = {w["definition"]["path"]: w for w in pipelines([a, b, c], runs=runs, pc_schedules=pc, pc_runs=pc_runs)}
    assert [(r["id"], r["message"]) for r in out["C:/r/A.DataPipeline"]["runs"]] == \
        [("r1", "from the schedule"), ("s1:0", "")]
    assert out["C:/s/A.DataPipeline"]["runs"] == []  # a shared name never guesses
    assert [r["id"] for r in out["C:/r/C.DataPipeline"]["runs"]] == ["r3", "r2"]
    assert out["C:/r/C.DataPipeline"]["deployments"][0]["latest_run"]["id"] == "r3"
    assert out["C:/s/A.DataPipeline"]["deployments"][0]["actions"] == []
    assert out["C:/r/A.DataPipeline"]["deployments"][0]["actions"] == ["run_schedule"]


def test_a_fabric_item_without_a_name_workspace_name_or_description_gets_the_defaults():
    items = [{"id": "i1", "workspace_id": "w", "type": "Notebook"}]
    [w] = inv.fabric_workloads(items, items_obs={"state": "live"}, linked=set(), fabric_schedules=[],
                               fabric_runs=[], assignments={})
    assert (w["name"], w["description"], w["group"]["label"]) == ("i1", "", "w")
    assert w["definition"]["workspace_name"] == "" and w["deployments"][0]["location"]["workspace_name"] == ""
    assert w["deployments"][0]["location"]["label"] == "Fabric · workspace" and w["search"] == "  Notebook "
    items = [{"id": "i1", "workspace_id": "w", "type": "Notebook", "displayName": "Load", "workspace_name": "Sales",
              "description": "d"}]
    [w] = inv.fabric_workloads(items, items_obs={"state": "live"}, linked=set(), fabric_schedules=[],
                               fabric_runs=[], assignments={})
    assert (w["name"], w["description"], w["group"]["label"], w["search"]) == ("Load", "d", "Sales", "Load Sales Notebook d")


def test_an_environment_that_runs_in_fabric_says_which_workspace_and_whose_identity():
    project = {"project": {"name": "sales"}, "entities": [ENTITY | {"reads": ["raw.orders"]}],
               "environments": {"test": {"compute": "fabric", "workspace_id": "w"},
                                "prod": {"compute": "fabric", "workspace_id": "w2", "workspace_name": "Sales"}}}
    on = {"configured": True}
    [w] = meta(project, {"test": on, "prod": on})
    loc = {d["env"]: d["location"]["label"] for d in w["deployments"]}
    assert loc == {"test": "Fabric · workspace", "prod": "Fabric · Sales"}
    assert {d["identity"]["kind"] for d in w["deployments"]} == {"you_or_schedule_owner"}
    assert w["dependencies"]["upstream"] == [{"name": "raw.orders", "declared": True}]
    [w] = meta({"project": {"name": "sales"}, "entities": [ENTITY]}, {"dev": on})
    assert w["dependencies"]["upstream"] == [] and w["deployments"][0]["actions"] == ["run"]


def test_an_environment_not_set_up_offers_no_run():
    [w] = meta({"project": {"name": "sales"}, "entities": [ENTITY]},
               {"prod": {"configured": True, "observation": {"state": "not_set_up"}}})
    assert w["deployments"][0]["actions"] == []
    [w] = meta({"project": {"name": "sales"}, "entities": [ENTITY]},
               {"prod": {"configured": True, "observation": {"state": "live"}}})
    assert w["deployments"][0]["actions"] == ["run"]


def test_a_notebooks_last_run_here_is_its_newest_schedule_run():
    pc = [{"id": "s1", "target": {"kind": "notebook", "name": "Load"}}]
    pc_runs = [{"schedule_id": "s1", "started": "2026-11-18T09:00:00Z", "status": "ok"}]
    [w] = notebooks({"name": "Load"}, pc_schedules=pc, pc_runs=pc_runs)
    assert w["deployments"][0]["latest_run"]["id"] == "s1:2026-11-18T09:00:00Z"
    assert w["deployments"][0]["schedules"][0]["enabled"] is True


def test_a_linked_notebook_in_fabric_shows_its_workspace_schedules_and_runs():
    link = {"Load": {"workspace_id": "w", "item_id": "i", "workspace_name": "Sales"}}
    runs = [{"item_id": "i", "job_id": "j1", "status": "InProgress", "started_at": "2026-11-18T10:00:00Z"}]
    fab = [{"id": "f1", "item_id": "i", "last": {"started": "2026-11-18T10:00:00Z", "status": "InProgress"}}]
    [w] = notebooks({"name": "Load"}, links=link, fabric_runs=runs, fabric_schedules=fab)
    d = w["deployments"][1]
    assert d["location"]["label"] == "Fabric · Sales" and d["location"]["workspace_name"] == "Sales"
    assert d["schedules"] == [{"where": "Fabric", "text": "", "enabled": False, "id": "f1"}]
    assert d["actions"] == ["run_in_fabric", "cancel"] and d["latest_run"]["id"] == "j1"
    assert len(w["runs"]) == 1  # the schedule's last run is the same run
    fab[0]["last"] = {"started": "2026-11-18T08:00:00Z", "status": "Failed"}
    runs[0]["status"] = "Completed"
    [w] = notebooks({"name": "Load"}, links=link, fabric_runs=runs, fabric_schedules=fab)
    assert len(w["runs"]) == 2 and w["deployments"][1]["actions"] == ["run_in_fabric"]
    [w] = notebooks({"name": "Load"}, links={"Load": {"workspace_id": "w", "item_id": "i"}})
    assert w["deployments"][1]["location"]["label"] == "Fabric · workspace"
    assert w["deployments"][1]["location"]["workspace_name"] == ""


def test_a_notebook_linked_by_name_shows_how_the_items_were_read():
    items = [{"workspace_id": "w", "type": "Notebook", "displayName": "Load", "id": "i"}]
    obs = {"state": "stale", "source": "items"}
    [w] = notebooks({"name": "Load", "workspace": {"workspace_id": "w"}}, fabric_items=items, fabric_items_obs=obs,
                    signed_in=True)
    assert w["link"]["how"] == "name match" and w["deployments"][1]["observation"] is obs


def test_a_pipelines_schedule_here_is_on_unless_it_says_otherwise():
    a = {"path": "C:/r/A.DataPipeline", "name": "A"}
    pc = [{"id": "s1", "target": {"kind": "pipeline", "path": "C:/r/A.DataPipeline"}},
          {"id": "s2", "target": {"kind": "pipeline", "path": "C:/r/A.DataPipeline"}, "enabled": False}]
    [w] = pipelines([a], pc_schedules=pc)
    assert [s["enabled"] for s in w["deployments"][0]["schedules"]] == [True, False]


def fabric(items, **kw):
    args = {"items_obs": {"state": "live", "source": "items"}, "linked": set(), "fabric_schedules": [],
            "fabric_runs": [], "assignments": {}} | kw
    return inv.fabric_workloads(items, **args)


def test_a_fabric_items_schedules_runs_and_how_it_was_read():
    item = {"id": "i", "workspace_id": "w", "type": "DataPipeline", "workspace_name": "Sales"}
    [w] = fabric([item], fabric_schedules=[{"id": "f1", "item_id": "i"}])
    d = w["deployments"][0]
    assert d["schedules"] == [{"where": "Fabric", "text": "", "enabled": False, "id": "f1"}]
    assert d["observation"]["state"] == "unknown" and d["actions"] == ["run_item"]
    assert d["location"]["label"] == "Fabric · Sales" and d["location"]["workspace_name"] == "Sales"
    assert w["definition"]["workspace_name"] == "Sales" and "schedulable" in w["capabilities"]
    runs = [{"item_id": "i", "job_id": "j", "status": "InProgress", "started_at": "t"}]
    [w] = fabric([item], fabric_runs=runs)
    d = w["deployments"][0]
    assert d["observation"] == {"state": "live", "source": "items"} and d["actions"] == ["run_item", "cancel"]
    [w] = fabric([item | {"type": "CopyJob"}])
    assert w["preview"] is True and "schedulable" not in w["capabilities"]
