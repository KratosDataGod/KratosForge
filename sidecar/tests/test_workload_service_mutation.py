"""Workloads (workload_service.py) edges the main tests don't pin down (mutation batch 196,
docs/quality/mutation-testing.md): how long a read counts as fresh, cached or stale, to the second;
default page sizes and limits as numbers; when an item scan starts and what it says; the timeline's
texts; which runs can be cancelled; and audit lines sent, counted and tried again on time."""

import datetime as dt
import os
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from fabric_desktop import schedule_service, workload_service
from fabric_desktop.auth import FABRIC_SCOPES, STORAGE_SCOPES
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service
from fabric_desktop.workloads import inventory, model

from test_workloads import (  # noqa: F401 - svc and project are fixtures
    USER,
    FakeFabric,
    _inject,
    _test_env,
    project,
    scan,
    signed_in,
    svc,
)

T = dt.datetime(2026, 11, 19, 12, 0, 0, tzinfo=dt.timezone.utc)


def frozen(monkeypatch):
    monkeypatch.setattr(model, "now", lambda: T)


def ago(seconds):
    return model.iso(T - dt.timedelta(seconds=seconds))


class Threads:
    """Stands in for the threading module in workload_service: records each thread asked for."""

    def __init__(self):
        self.started = []
        outer = self

        class Thread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self):
                outer.started.append(self.kwargs)

        self.Thread = Thread
        self.Lock = threading.Lock


def fake_threads(monkeypatch):
    threads = Threads()
    monkeypatch.setattr(workload_service, "threading", threads)
    return threads


# ---- how old a read is, and when it is stale


def test_the_age_of_a_read_is_seconds_since_it_or_none_when_its_time_cant_be_read(
    monkeypatch,
):
    frozen(monkeypatch)
    assert workload_service._age_s(ago(90)) == 90.0
    assert (
        workload_service._age_s("not a time") is None
        and workload_service._age_s(None) is None
    )


def test_a_cached_run_log_is_cached_for_exactly_an_hour_then_stale(svc, monkeypatch):  # noqa: F811
    frozen(monkeypatch)
    payload = {"runs": [{"run_id": "r1"}], "deployments": []}
    for age, state in ((3600, "cached"), (3601, "stale")):
        out = svc._wl_cached_or(
            {"collected_at": ago(age), "payload": payload},
            "src",
            "offline",
            "No network.",
        )
        assert out["configured"] is True and out["runs"] == [{"run_id": "r1"}]
        assert out["observation"]["state"] == state
        assert (
            out["observation"]["detail"] == "Showing what was read earlier. No network."
        )


# ---- one environment's run log


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dicts(self):
        return self.rows


def _env_service(svc, monkeypatch, envs):
    asked = []

    def runs(env, limit=100):
        asked.append((env, limit))
        return [{"run_id": f"{env}-1"}]

    monkeypatch.setattr(svc, "meta_runs", runs)
    monkeypatch.setattr(
        svc,
        "_store",
        lambda project, env, create=False: types.SimpleNamespace(
            meta=lambda table: Frame(
                [{"env": env, "at": dt.datetime(2026, 11, 1, 9, 0)}]
            )
        ),
    )
    return types.SimpleNamespace(environments=envs), asked


def test_dev_without_a_target_is_read_on_this_pc_and_other_environments_on_onelake(
    svc, monkeypatch
):  # noqa: F811
    project, asked = _env_service(
        svc,
        monkeypatch,
        {
            "dev": {},
            "test": {"workspace_id": "ws-test", "workspace_name": "Sales Test"},
        },
    )
    dev = svc._wl_meta_env(project, "proj", "dev", False)
    assert (
        dev["configured"] is True
        and dev["runs"] == [{"run_id": "dev-1"}]
        and dev["deployments"] == []
    )
    assert (
        dev["observation"]["state"] == "live"
        and dev["observation"]["source"] == "meta.run_log in Dev"
    )
    assert asked == [("dev", 2000)]  # up to 2000 runs
    assert (
        svc.workload_store.cache_get("meta:proj:dev") is None
    )  # a local read isn't cached
    test = svc._wl_meta_env(
        project, "proj", "test", False
    )  # signed out: OneLake can't be read
    assert test["observation"]["state"] == "signed_out" and test["runs"] == []
    assert test["observation"]["source"] == "meta.run_log in Test (Sales Test)"
    assert asked == [("dev", 2000)]


def test_onelake_needs_both_a_storage_and_a_fabric_token(svc, monkeypatch):  # noqa: F811
    project, asked = _env_service(
        svc, monkeypatch, {"test": {"target": "onelake", "workspace_id": "ws-test"}}
    )
    for only in (STORAGE_SCOPES, FABRIC_SCOPES):
        svc._auth = types.SimpleNamespace(
            signed_in_user=lambda: USER,
            silent_token=lambda scopes, only=only: "tok" if scopes == only else None,
        )
        out = svc._wl_meta_env(project, "proj", "test", True)
        assert out["observation"]["state"] == "signed_out"
        assert (
            out["observation"]["detail"] == "Sign in to read this environment's runs."
        )
    assert asked == []
    signed_in(svc)
    out = svc._wl_meta_env(project, "proj", "test", True)
    assert out["observation"]["state"] == "live" and asked == [("test", 2000)]
    assert out["deployments"] == [{"env": "test", "at": "2026-11-01T09:00:00"}]
    assert svc.workload_store.cache_get("meta:proj:test")["payload"]["runs"] == [
        {"run_id": "test-1"}
    ]


def test_a_onelake_read_under_two_minutes_old_is_reused_without_a_token(
    svc, monkeypatch
):  # noqa: F811
    frozen(monkeypatch)
    project, asked = _env_service(
        svc, monkeypatch, {"test": {"target": "onelake", "workspace_id": "ws-test"}}
    )
    payload = {"runs": [{"run_id": "kept"}], "deployments": []}
    svc.workload_store.cache_put("meta:proj:test", payload, collected_at=ago(119))
    out = svc._wl_meta_env(project, "proj", "test", False)  # signed out
    assert out["configured"] is True and out["runs"] == [{"run_id": "kept"}]
    assert (
        out["observation"]["state"] == "cached"
        and out["observation"]["detail"] == ""
        and asked == []
    )
    out = svc._wl_meta_env(
        project, "proj", "test", True
    )  # a refresh asks, and keeps what it had
    assert (
        out["runs"] == [{"run_id": "kept"}] and out["observation"]["state"] == "cached"
    )
    assert (
        out["observation"]["detail"]
        == "Showing what was read earlier. Sign in to read this environment's runs."
    )


# ---- the metadata project: freshness and Fabric schedules


def test_freshness_reads_the_newest_run_first_in_every_readable_environment(
    svc, project, monkeypatch
):  # noqa: F811
    name = sorted(project.entities)[0]
    svc.meta_set_freshness(name, within_hours=24, envs=["dev"])
    rows = [
        {
            "run_id": "r1",
            "entity": name,
            "status": "ok",
            "started_at": "2026-11-18T06:00:00+00:00",
            "seconds": 5,
        },
        {
            "run_id": "r2",
            "entity": name,
            "status": "failed",
            "started_at": "2026-11-18T07:00:00+00:00",
            "seconds": 1,
            "message": "boom",
        },
    ]  # oldest first: the service puts the newest first
    monkeypatch.setattr(
        svc,
        "meta_runs",
        lambda env, limit=100: [dict(r) for r in rows] if env == "dev" else [],
    )
    records, _ = svc._wl_meta(False, {}, [], "")
    dev = next(
        d
        for d in next(r for r in records if r["name"] == name)["deployments"]
        if d["env"] == "dev"
    )
    assert dev["freshness"] is not None  # Dev was read, so its promise is checked
    assert (
        dev["freshness"]["last_failed"] is True
        and dev["freshness"]["failed_run"] == "r2"
    )


def test_only_the_runner_notebooks_schedules_in_an_environments_workspace_are_its_schedules(
    svc, project
):  # noqa: F811
    _test_env(svc, project)
    svc._fabric_schedules = {
        "items": [
            {
                "id": "s1",
                "workspace_id": "ws-test",
                "name": inventory.RUNNER_NOTEBOOK,
                "text": "daily",
            },
            {
                "id": "s2",
                "workspace_id": "ws-test",
                "name": "Other notebook",
                "text": "hourly",
            },
            {
                "id": "s3",
                "workspace_id": "ws-elsewhere",
                "name": inventory.RUNNER_NOTEBOOK,
                "text": "weekly",
            },
        ]
    }
    records, _ = svc._wl_meta(False, {}, [], "")
    test = next(d for d in records[0]["deployments"] if d["env"] == "test")
    assert [s["id"] for s in test["schedules"] if s["where"] == "Fabric"] == ["s1"]


# ---- Fabric items: when a scan starts and what the source says


def test_items_read_without_a_scan_say_so_or_ask_for_a_sign_in(svc, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(svc, "_wl_start_scan", started.append)
    items, obs, scanning = svc._wl_items(False)  # signed out, nothing read yet
    assert (items, obs["state"], obs["detail"], scanning) == (
        None,
        "signed_out",
        "Sign in to list them.",
        False,
    )
    assert started == []
    signed_in(svc)
    items, obs, scanning = svc._wl_items(False)  # nothing read yet: a scan starts
    assert (items, obs["state"], obs["detail"], scanning) == (
        None,
        "unknown",
        "Reading them now.",
        True,
    )
    assert started == ["tok"]


def test_a_refresh_scans_again_even_with_items_read_before(svc, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(svc, "_wl_start_scan", started.append)
    signed_in(svc)
    svc.workload_store.cache_put(
        "fabric_items", {"items": [{"id": "i1"}], "errors": [], "workspaces": 1}
    )
    items, _, scanning = svc._wl_items(False)
    assert items == [{"id": "i1"}] and scanning is False and started == []
    items, _, scanning = svc._wl_items(True)
    assert scanning is True and started == ["tok"]


def test_items_are_cached_for_exactly_a_day_then_stale(svc, monkeypatch):  # noqa: F811
    frozen(monkeypatch)
    for age, state in ((24 * 3600, "cached"), (24 * 3600 + 1, "stale")):
        svc.workload_store.cache_put(
            "fabric_items",
            {"items": [], "errors": [], "workspaces": 1},
            collected_at=ago(age),
        )
        assert svc._wl_items(False)[1]["state"] == state


def test_items_are_live_only_from_this_scan_and_under_two_minutes_old(svc, monkeypatch):  # noqa: F811
    frozen(monkeypatch)
    for age, scanned, state in (
        (119, None, "live"),
        (120, None, "cached"),
        (60, ago(30), "cached"),
    ):
        collected = ago(age)
        svc.workload_store.cache_put(
            "fabric_items",
            {"items": [], "errors": [], "workspaces": 1},
            collected_at=collected,
        )
        svc._wl_scan = {"pending": False, "collected_at": scanned or collected}
        assert svc._wl_items(False)[1]["state"] == state, (age, scanned)


def test_a_scan_runs_on_a_background_thread_and_a_second_ask_waits_for_it(
    svc, monkeypatch
):  # noqa: F811
    threads = fake_threads(monkeypatch)
    svc._wl_start_scan("tok")
    [t] = threads.started
    assert (
        t["daemon"] is True
        and t["args"] == ("tok",)
        and svc._wl_scan == {"pending": True}
    )
    svc._wl_start_scan("tok2")
    assert len(threads.started) == 1 and svc._wl_scan_again == "tok2"


def test_a_scan_says_when_it_ended_and_why_a_workspace_couldnt_be_read(
    svc, monkeypatch
):  # noqa: F811
    frozen(monkeypatch)
    pools = []

    class Pool(ThreadPoolExecutor):
        def __init__(self, max_workers=None):
            pools.append(max_workers)
            super().__init__(max_workers=max_workers)

    monkeypatch.setattr(workload_service, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(schedule_service, "FabricClient", FakeFabric)
    for ws in ("ws1", "ws2", "ws3"):
        svc.set_workspace_mark(ws, "pins", True)
    out = svc._wl_scan_items("tok", client=FakeFabric())
    assert (
        out == {"pending": False, "collected_at": model.iso(T)} and svc._wl_scan is out
    )
    assert pools == [8]  # eight workspaces at a time
    cached = svc.workload_store.cache_get("fabric_items")
    message = model.classify_error(
        FabricError("GET /workspaces/ws2/items failed with 403: Forbidden")
    )[1]
    assert message != "unauthorized"
    assert cached["payload"]["errors"] == [
        {"workspace": "Locked", "workspace_id": "ws2", "message": message}
    ]
    assert cached["payload"]["workspaces"] == 3


# ---- collecting and paging


def test_a_listing_is_not_a_refresh_unless_asked(svc, monkeypatch):  # noqa: F811
    signed_in(svc)
    runs_refresh, items_refresh = [], []
    monkeypatch.setattr(
        svc, "fabric_runs", lambda n, refresh=False: runs_refresh.append(refresh) or []
    )
    monkeypatch.setattr(
        svc,
        "_wl_items",
        lambda refresh: (
            items_refresh.append(refresh)
            or (None, model.observation("Fabric items", "unknown"), False)
        ),
    )
    svc._wl_collect()
    assert runs_refresh == [False] and items_refresh == [False]


def test_a_page_of_workloads_is_100_by_default(svc):  # noqa: F811
    for i in range(101):
        svc.notebooks.save(f"nb{i:03d}", [{"type": "code", "source": "x = 1"}])
    page = svc.workloads_list()
    assert page["total"] == 101 and len(page["rows"]) == 100


def test_actions_are_listed_50_at_a_time_by_default(svc):  # noqa: F811
    for _ in range(51):
        svc.workload_store.start_action("notebook:x", "run")
    assert (
        len(svc.workload_actions()) == 50
        and len(svc.workload_actions("notebook:x")) == 50
    )


def test_a_pipeline_listing_that_fails_says_why_in_300_characters(svc, monkeypatch):  # noqa: F811
    def broken():
        raise RuntimeError("x" * 500)

    monkeypatch.setattr(svc, "pipeline_list", broken)
    records, obs = svc._wl_pipelines([], [], {}, "")
    assert (
        records == []
        and obs["state"] == "error"
        and obs["detail"] == ("RuntimeError: " + "x" * 500)[:300]
    )


def test_a_fabric_items_history_keeps_its_newest_50_runs(svc, monkeypatch):  # noqa: F811
    class Many(FakeFabric):
        def list_job_instances(self, ws, item):
            return [
                {
                    "id": f"j{i}",
                    "status": "Completed",
                    "startTimeUtc": "2026-10-16T05:00:00Z",
                }
                for i in range(51)
            ]

    signed_in(svc)
    monkeypatch.setattr(workload_service, "FabricClient", Many)
    w = {
        "definition": {
            "workspace_id": "ws1",
            "item_id": "pl1",
            "workspace_name": "Sales Dev",
        },
        "runs": [],
    }
    runs, obs = svc._wl_fabric_history(w)
    assert len(runs) == 50 and obs["state"] == "live"


# ---- the timeline


def test_the_timeline_says_who_promoted_and_changed_what(svc, tmp_path):  # noqa: F811
    nb = tmp_path / "nb.ipynb"
    nb.write_text("{}", encoding="utf-8")
    os.utime(nb, (1_700_000_000, 1_700_000_000))
    w = {
        "runs": [],
        "deployments": [
            {
                "env_label": "Test",
                "version": {
                    "at": "2026-10-14T11:05:00+00:00",
                    "from": "dev",
                    "deployed": "0123456789abcdef",
                },
            }
        ],
        "definition": {"path": str(nb), "modified": 1.0},
        "assignment_history": [
            {
                "at": "2026-10-15T09:00:00+00:00",
                "field": "technical_owner",
                "before": "",
                "after": "Dana",
                "by": "",
            },
            {
                "at": "2026-10-16T09:00:00+00:00",
                "field": "team",
                "before": "Data team",
                "after": "",
                "by": USER,
            },
        ],
    }
    events = svc._wl_timeline(w, [])
    assert [(e["kind"], e["text"]) for e in events] == [
        ("ownership", f"Team: Data team → none by {USER}"),
        ("ownership", "Technical owner: none → Dana by you"),
        ("deployment", "Promoted Dev → Test · version 01234567"),
        ("definition", "Definition changed"),
    ]
    # The file's own time, not the recorded one.
    assert events[-1]["at"] == "2023-11-14T22:13:20+00:00"
    # With no file, the recorded time is the definition's change.
    w = {"runs": [], "deployments": [], "definition": {"modified": 1_700_000_000}}
    assert [(e["kind"], e["at"]) for e in svc._wl_timeline(w, [])] == [
        ("definition", "2023-11-14T22:13:20+00:00")
    ]


def test_the_timeline_keeps_the_newest_100_events(svc):  # noqa: F811
    actions = [
        {
            "at": f"2026-11-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
            "action": "run",
            "status": "succeeded",
            "by": USER,
        }
        for i in range(101)
    ]
    events = svc._wl_timeline(
        {"runs": [], "deployments": [], "definition": {}}, actions
    )
    assert len(events) == 100
    assert (
        events[0]["at"] == "2026-11-01T00:01:40+00:00"
        and events[-1]["at"] == "2026-11-01T00:00:01+00:00"
    )


# ---- consumers


def test_a_product_is_matched_by_its_id_or_its_name_and_shown_by_its_name(
    svc, monkeypatch
):  # noqa: F811
    w = {
        "definition": {"project": "sales"},
        "dependencies": {"products": [{"product": "orders"}]},
    }
    monkeypatch.setattr(
        svc,
        "platform_products",
        lambda: {
            "products": [
                {
                    "id": "sales.orders",
                    "name": "Orders renamed",
                    "consumers": ["finance"],
                },
                {"id": "other.id", "name": "orders", "consumers": ["ops"]},
                {"id": "sales.orders", "consumers": ["audit"]},
                {"id": "x", "name": "y", "consumers": ["nobody"]},
            ]
        },
        raising=False,
    )
    assert [(c["product"], c["consumer"]) for c in svc._wl_consumers(w)] == [
        ("Orders renamed", "finance"),
        ("orders", "ops"),
        ("sales.orders", "audit"),
    ]


# ---- what can be done, and the preflight


def test_only_a_running_fabric_job_with_an_id_can_be_cancelled(svc):  # noqa: F811
    w = {
        "deployments": [
            {
                "env": None,
                "actions": ["cancel"],
                "location": {"label": "Fabric · Sales Dev"},
            }
        ],
        "runs": [
            {"state": "running", "job_id": "j1", "where": "fabric"},
            {"state": "ok", "job_id": "j2", "where": "fabric"},
            {"state": "running", "job_id": "j3", "where": "this_pc"},
            {"state": "running", "job_id": "", "where": "fabric"},
        ],
    }
    assert svc._wl_available(w) == [
        {
            "action": "cancel",
            "label": "Cancel the run",
            "env": None,
            "job_id": "j1",
            "where": "Fabric · Sales Dev",
        }
    ]


def test_a_run_on_this_pc_needs_no_sign_in_and_runs_as_its_identity(svc, project):  # noqa: F811
    wid = model.meta_id("proj", sorted(project.entities)[0])
    pre = svc.workload_preflight(wid, "run", "dev", assess=False)  # signed out
    assert pre["allowed"] is True and pre["identity"] == "You, signed in on this PC"


def test_a_run_in_fabric_runs_as_the_person_who_starts_it(svc, monkeypatch):  # noqa: F811
    signed_in(svc)
    scan(svc, monkeypatch)
    pre = svc.workload_preflight(
        model.fabric_id("ws1", "pl1"), "run_item", assess=False
    )
    assert (
        pre["identity"]
        == f"You ({USER}): a run started here runs as the person who starts it"
    )


def test_a_run_in_test_warns_of_the_version_there_and_runs_in_test(
    svc, project, monkeypatch
):  # noqa: F811
    _test_env(svc, project)
    signed_in(svc)
    name = sorted(project.entities)[0]
    _inject(
        svc,
        monkeypatch,
        "test",
        [],
        deployments=[
            {
                "env": "test",
                "version": "0123456789abcdef",
                "source_env": "dev",
                "at": "2026-10-14T11:05:00+00:00",
            }
        ],
    )
    wid = model.meta_id("proj", name)
    pre = svc.workload_preflight(wid, "run", "test", assess=False)
    assert (
        pre["allowed"]
        and (
            "Test has version 01234567; your newer changes are not included until you "
            "promote them."
        )
        in pre["warnings"]
    )
    asked = []
    monkeypatch.setattr(
        svc,
        "meta_run",
        lambda names, env: (
            asked.append((names, env))
            or [{"entity": names[0], "status": "ok", "run_id": "x"}]
        ),
    )
    assert svc.workload_act(wid, "run", "test")["action"]["status"] == "succeeded"
    assert asked == [([name], "test")]


@pytest.mark.parametrize(
    "kind,action", [("meta_entity", "explode"), ("notebook", "run")]
)
def test_only_an_entitys_run_goes_to_the_metadata_run(svc, monkeypatch, kind, action):  # noqa: F811
    w = {"name": "n", "kind": kind, "definition": {}, "deployments": [], "runs": []}
    monkeypatch.setattr(
        svc,
        "workload_preflight",
        lambda *a, **k: {
            "allowed": True,
            "target": "t",
            "where": "This PC",
            "identity": "you",
        },
    )
    monkeypatch.setattr(svc, "_wl_record", lambda id: w)
    asked = []
    monkeypatch.setattr(svc, "meta_run", lambda names, env: asked.append(names) or [])
    with pytest.raises(ValueError, match=f"unknown action {action}"):
        svc.workload_act("x:n", action)
    assert asked == []


# ---- settling a schedule run


def test_a_manual_run_that_started_in_the_same_second_settles_the_action(svc):  # noqa: F811
    store = svc.workload_store
    rec = store.start_action(
        "nb:x", "run_schedule", "", {"target": "t", "where": "This PC"}, "you", ""
    )
    store.update_action(rec["id"], ref={"schedule_id": "s1"})
    svc._wl_reconcile(
        [],
        [
            {
                "schedule_id": "s1",
                "trigger": "manual",
                "started": rec["at"][:19],
                "status": "ok",
            }
        ],
    )
    done = store.actions("nb:x")[0]
    assert done["status"] == "succeeded" and done["detail"] == "ok"


# ---- the platform's audit lines


class Platform:
    def __init__(self, refuse=()):
        self.refuse, self.calls = set(refuse), []

    def call(self, name, **kw):
        if kw["entry"].get("name") in self.refuse:
            raise ConnectionError("no network")
        self.calls.append((name, kw["workload"], kw["status"]))


def test_audit_lines_sent_are_counted_and_none_go_without_a_platform(svc, monkeypatch):  # noqa: F811
    store = svc.workload_store
    for i in range(3):
        store.audit_queue(f"k{i}", "nb:x", "run", "submitted", {"name": f"line{i}"})
    monkeypatch.setattr(svc, "_wl_audit_platform", lambda: None)
    sent = svc._wl_audit_send()
    assert sent == 0 and sent is not None and len(store.audit_waiting(10)) == 3
    platform = Platform(refuse={"line1"})
    monkeypatch.setattr(svc, "_wl_audit_platform", lambda: platform)
    assert (
        svc._wl_audit_send() == 1 and len(store.audit_waiting(10)) == 2
    )  # stopped at the one refused
    platform.refuse = set()
    assert svc._wl_audit_send() == 2 and store.audit_waiting(10) == []
    assert platform.calls == [("log_workload_action", "nb:x", "submitted")] * 3


def test_waiting_audit_lines_are_tried_again_after_ten_minutes(svc, monkeypatch):  # noqa: F811
    frozen(monkeypatch)
    threads = fake_threads(monkeypatch)
    state = {"waiting": 1, "platform": "this_pc", "tried_at": ago(599)}
    monkeypatch.setattr(svc, "workload_audit_state", lambda: dict(state))
    svc._wl_audit_retry()
    assert threads.started == []
    state["tried_at"] = ago(600)
    svc._wl_audit_retry()
    [t] = threads.started
    assert t["name"] == "workload-audit" and t["daemon"] is True
    # Never tried: tried now, unless nothing waits or there is no platform.
    state["tried_at"] = None
    svc._wl_audit_retry()
    assert len(threads.started) == 2
    for change in ({"waiting": 0}, {"platform": "none"}):
        state.update({"waiting": 1, "platform": "this_pc"} | change)
        svc._wl_audit_retry()
    assert len(threads.started) == 2


# ---- a second sample (seed 11): pipelines, the scan's workspaces, the page, the preflight's texts


def test_without_pipelines_there_are_no_pipeline_workloads_and_no_source():
    assert workload_service.WorkloadMixin._wl_pipelines(
        types.SimpleNamespace(), [], [], {}, ""
    ) == ([], None)


def test_a_scan_reads_only_the_workspaces_this_app_works_in(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(schedule_service, "FabricClient", FakeFabric)
    svc.set_workspace_mark(
        "ws1", "pins", True
    )  # ws2 (which refuses) and ws3 aren't pinned
    svc._wl_scan_items("tok", client=FakeFabric())
    payload = svc.workload_store.cache_get("fabric_items")["payload"]
    assert payload["workspaces"] == 1 and payload["errors"] == []
    assert {i["workspace_id"] for i in payload["items"]} == {"ws1"}


def test_a_workloads_page_shows_its_newest_50_actions(svc):  # noqa: F811
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    wid = model.notebook_id("Daily load")
    for _ in range(51):
        svc.workload_store.start_action(wid, "run")
    assert len(svc.workload_detail(wid)["actions"]) == 50


def test_a_workloads_page_shows_its_newest_50_runs(svc, project, monkeypatch):  # noqa: F811
    name = sorted(project.entities)[0]
    rows = [
        {
            "run_id": f"r{i:02d}",
            "entity": name,
            "status": "ok",
            "started_at": f"2026-11-01T00:{i:02d}:00+00:00",
        }
        for i in range(51)
    ]
    monkeypatch.setattr(
        svc,
        "meta_runs",
        lambda env, limit=100: [dict(r) for r in rows] if env == "dev" else [],
    )
    runs = svc.workload_detail(model.meta_id("proj", name))["runs"]
    assert len(runs) == 50 and runs[0]["started_at"] == "2026-11-01T00:50:00+00:00"


def test_a_run_in_the_timeline_names_its_state_environment_and_where_it_ran(svc):  # noqa: F811
    w = {
        "runs": [
            {
                "started_at": "2026-11-02T00:00:00+00:00",
                "state": "ok",
                "env": "dev",
                "ran_on": "This PC",
            },
            {
                "started_at": "2026-11-01T00:00:00+00:00",
                "state": "needs_decision",
                "ran_on": "Fabric",
            },
        ],
        "deployments": [],
        "definition": {},
    }
    assert [e["text"] for e in svc._wl_timeline(w, [])] == [
        "Run ok in Dev · This PC",
        "Run needs decision · Fabric",
    ]


def test_an_entity_whose_definition_has_problems_is_refused_with_the_first_two(
    svc, project, monkeypatch
):  # noqa: F811
    wid = model.meta_id("proj", sorted(project.entities)[0])
    w = svc._wl_record(wid)
    monkeypatch.setattr(
        svc,
        "_wl_record",
        lambda id, refresh=False: dict(w, errors=["no source", "no key", "third"]),
    )
    assert svc.workload_preflight(wid, "run", "dev", assess=False) == {
        "allowed": False,
        "reason": "The definition has problems: no source; no key",
    }


def _schedule_workload(kind):
    return {
        "name": "n",
        "kind": kind,
        "definition": {},
        "runs": [],
        "deployments": [
            {
                "env": None,
                "actions": ["run_schedule"],
                "location": {"label": "This PC", "kind": "this_pc"},
                "identity": {"label": "You, signed in on this PC"},
                "schedules": [{"where": "This PC", "id": "s1", "text": "daily"}],
            }
        ],
    }


STARTS = "Starts the schedule's run now, in the background, as Task Scheduler would"


@pytest.mark.parametrize(
    "kind,target,starts",
    [
        (
            "pipeline",
            "the pipeline n on this PC",
            STARTS + ", with the schedule's saved parameters",
        ),
        ("notebook", "the notebook n on this PC", STARTS + "."),
    ],
)
def test_a_schedule_run_now_says_what_it_runs(svc, monkeypatch, kind, target, starts):  # noqa: F811
    monkeypatch.setattr(
        svc, "_wl_record", lambda id, refresh=False: _schedule_workload(kind)
    )
    pre = svc.workload_preflight(
        "x:n", "run_schedule", assess=False
    )  # signed out: this PC needs no sign-in
    assert pre["allowed"] and pre["target"] == target and pre["where"] == "This PC"
    assert pre["consequence"].startswith(starts)
    assert ("OneLake reads and writes use capacity" in pre["consequence"]) is (
        kind == "pipeline"
    )


def test_the_preflight_says_the_platform_hears_of_it_only_when_there_is_one(
    svc, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(
        svc, "_wl_record", lambda id, refresh=False: _schedule_workload("notebook")
    )
    done = "Recorded on this workload as submitted, then as succeeded or failed when the run finishes"
    for platform, recorded in (
        ("none", done + "."),
        ("this_pc", done + ", and on Platform > Govern > Audit."),
    ):
        monkeypatch.setattr(
            svc,
            "workload_audit_state",
            lambda p=platform: {"platform": p, "waiting": 0},
        )
        assert (
            svc.workload_preflight("x:n", "run_schedule", assess=False)["recorded"]
            == recorded
        )


# ---- a larger second sample (seed 11, cap 300)


def test_linking_a_notebook_returns_its_page_with_the_link(svc):  # noqa: F811
    svc.notebooks.save("Load", [{"type": "code", "source": "x = 1"}])
    out = svc.workload_link("Load", "ws1", "nb1", "Sales Dev")
    assert out["id"] == model.notebook_id("Load")
    assert (out["link"]["item_id"], out["link"]["how"]) == ("nb1", "manual")


def test_a_cut_pipeline_listing_is_partial_and_says_how_many_are_listed(
    svc, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(svc, "pipeline_runs", lambda limit=0: {"runs": []})
    for truncated, state, detail in (
        (
            True,
            "partial",
            f"Only the first {workload_service.PIPELINE_LIST_LIMIT} pipelines are listed.",
        ),
        (False, "live", ""),
    ):
        monkeypatch.setattr(
            svc, "pipeline_list", lambda t=truncated: {"pipelines": [], "truncated": t}
        )
        _, obs = svc._wl_pipelines([], [], {}, "")
        assert (obs["state"], obs["detail"]) == (state, detail)


def test_capacity_use_counts_the_last_30_days_by_default(svc, monkeypatch):  # noqa: F811
    started = model.iso(
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30, hours=12)
    )
    run = {"job_id": "j1", "where": "fabric", "started_at": started}
    monkeypatch.setattr(
        svc, "_wl_record", lambda id, refresh=False: {"id": id, "runs": [run]}
    )
    monkeypatch.setattr(svc, "_risk_cu", lambda runs: ([], [], ""))
    out = svc.workload_capacity_use("fabric:ws1:pl1")
    assert out["days"] == 30 and out["fabric_runs"] == 0 and out["total"] is None


def test_a_scan_is_kept_as_live_or_as_partial_when_a_workspace_couldnt_be_read(
    svc, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(schedule_service, "FabricClient", FakeFabric)
    svc.set_workspace_mark("ws1", "pins", True)
    svc._wl_scan_items("tok", client=FakeFabric())
    assert svc.workload_store.cache_get("fabric_items")["state"] == "live"
    svc.set_workspace_mark("ws2", "pins", True)  # refuses
    svc._wl_scan_items("tok", client=FakeFabric())
    assert svc.workload_store.cache_get("fabric_items")["state"] == "partial"


def test_a_listing_reads_500_fabric_runs_and_1000_runs_on_this_pc(svc, monkeypatch):  # noqa: F811
    asked = []

    class PC:
        def listing(self):
            return []

        def runs(self, n):
            asked.append(("pc", n))
            return []

    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: PC()))
    monkeypatch.setattr(
        svc, "fabric_runs", lambda n, refresh=False: asked.append(("fabric", n)) or []
    )
    svc._wl_collect()
    assert asked[:2] == [("fabric", 500), ("pc", 1000)]


def test_a_workloads_page_shows_its_newest_50_ownership_changes(svc):  # noqa: F811
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    wid = model.notebook_id("Daily load")
    for i in range(51):
        svc.workload_store.assign(wid, {"team": f"team {i}"})
    assert len(svc.workload_detail(wid)["assignment_history"]) == 50


def test_only_a_last_run_that_failed_warns_of_a_retry(svc, monkeypatch):  # noqa: F811
    warning = "The last run failed: No files. A retry may fail the same way until that's fixed."
    for last, warnings in (
        ({"state": "failed", "message": "No files"}, [warning]),
        ({"state": "ok", "message": "x"}, []),
    ):
        w = _schedule_workload("notebook")
        w["deployments"][0]["latest_run"] = last
        monkeypatch.setattr(svc, "_wl_record", lambda id, refresh=False, w=w: w)
        assert (
            svc.workload_preflight("x:n", "run_schedule", assess=False)["warnings"]
            == warnings
        )


def _entity(tables_in):
    return {
        "name": "orders",
        "kind": "meta_entity",
        "definition": {"table": "silver.orders"},
        "runs": [],
        "errors": [],
        "deployments": [
            {
                "env": "dev",
                "env_label": "Dev",
                "actions": ["run"],
                "tables_in": tables_in,
                "location": {"label": "This PC", "kind": "this_pc"},
                "version": {},
                "identity": {"label": "You, signed in on this PC"},
            }
        ],
    }


def test_a_run_on_this_pc_that_writes_to_a_workspace_needs_a_sign_in(svc, monkeypatch):  # noqa: F811
    for tables_in, allowed in (("Sales Dev", False), ("This PC only", True)):
        monkeypatch.setattr(
            svc, "_wl_record", lambda id, refresh=False, t=tables_in: _entity(t)
        )
        pre = svc.workload_preflight(
            "meta:proj:orders", "run", "dev", assess=False
        )  # signed out
        assert pre["allowed"] is allowed
        if not allowed:
            assert pre == {
                "allowed": False,
                "reason": "Sign in first: this action uses Fabric.",
            }


def test_a_cancel_needs_a_sign_in(svc, monkeypatch):  # noqa: F811
    w = {
        "name": "pl",
        "kind": "fabric_item",
        "definition": {},
        "runs": [{"state": "running", "job_id": "j1", "where": "fabric"}],
        "deployments": [
            {
                "env": None,
                "actions": ["cancel"],
                "identity": {"label": "x"},
                "location": {"label": "Fabric · Sales Dev", "kind": "fabric"},
            }
        ],
    }
    monkeypatch.setattr(svc, "_wl_record", lambda id, refresh=False: w)
    assert svc.workload_preflight("fabric:ws1:pl", "cancel", assess=False) == {
        "allowed": False,
        "reason": "Sign in first: this action uses Fabric.",
    }


def test_freshness_isnt_checked_in_an_environment_that_couldnt_be_read(svc, project):  # noqa: F811
    _test_env(svc, project)  # signed out: Test is configured but can't be read
    name = sorted(project.entities)[0]
    svc.meta_set_freshness(name, within_hours=24, envs=["test"])
    records, _ = svc._wl_meta(False, {}, [], "")
    test = next(
        d
        for d in next(r for r in records if r["name"] == name)["deployments"]
        if d["env"] == "test"
    )
    assert test["observation"]["state"] == "signed_out" and test["freshness"] is None


def test_the_timeline_has_the_first_30_runs(svc):  # noqa: F811
    runs = [
        {
            "started_at": f"2026-11-01T00:{i:02d}:00+00:00",
            "state": "ok",
            "ran_on": "This PC",
        }
        for i in range(31)
    ]
    events = svc._wl_timeline({"runs": runs, "deployments": [], "definition": {}}, [])
    assert len(events) == 30 and events[0]["at"] == "2026-11-01T00:29:00+00:00"


def test_up_to_200_open_actions_are_settled_at_a_time(svc):  # noqa: F811
    store = svc.workload_store
    for i in range(201):
        a = store.start_action("fabric:ws1:pl1", "run_item")
        store.update_action(a["id"], ref={"job_id": f"j{i}"})
    svc._wl_reconcile(
        [{"job_id": f"j{i}", "status": "Completed"} for i in range(201)], []
    )
    assert len(store.actions(limit=500, open_only=True)) == 1


def test_a_settled_fabric_run_says_fabrics_message_or_its_status(svc):  # noqa: F811
    store = svc.workload_store
    for job in ("j1", "j2"):
        a = store.start_action(f"fabric:ws1:{job}", "run_item")
        store.update_action(a["id"], ref={"job_id": job})
    svc._wl_reconcile(
        [
            {"job_id": "j1", "status": "Completed"},
            {"job_id": "j2", "status": "Failed", "message": "Activity Copy failed"},
        ],
        [],
    )
    assert [(a["status"], a["detail"]) for a in store.actions("fabric:ws1:j1")] == [
        ("succeeded", "Fabric says Completed")
    ]
    assert [(a["status"], a["detail"]) for a in store.actions("fabric:ws1:j2")] == [
        ("failed", "Activity Copy failed")
    ]


def test_an_actions_audit_line_is_dated_when_it_ended_or_else_when_it_started(svc):  # noqa: F811
    base = {"wid": "nb:x", "action": "run", "at": "2026-11-01T09:00:00+00:00"}
    svc._wl_audit_action(
        base
        | {"id": "a1", "status": "succeeded", "ended_at": "2026-11-01T09:05:00+00:00"},
        "x",
    )
    svc._wl_audit_action(
        base | {"id": "a2", "status": "submitted"}, "x"
    )  # no platform: both wait
    assert [line["entry"]["at"] for line in svc.workload_store.audit_waiting(10)] == [
        "2026-11-01T09:05:00+00:00",
        "2026-11-01T09:00:00+00:00",
    ]


def test_nothing_waiting_sends_nothing(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        svc,
        "_wl_audit_platform",
        lambda: pytest.fail("asked for a platform with nothing to send"),
    )
    assert svc._wl_audit_send() == 0


def test_a_scan_asked_for_again_is_pending_while_it_runs(svc, monkeypatch):  # noqa: F811
    seen = []

    def scan_items(token, client=None):
        seen.append((token, dict(svc._wl_scan or {})))
        if token == "tok":
            svc._wl_scan_again = "tok2"
        svc._wl_scan = {"pending": False, "collected_at": "t"}

    monkeypatch.setattr(svc, "_wl_scan_items", scan_items)
    svc._wl_scan = {"pending": True}
    svc._wl_scan_until_settled("tok")
    assert (
        seen == [("tok", {"pending": True}), ("tok2", {"pending": True})]
        and svc._wl_scan_again == ""
    )


def test_items_that_couldnt_all_be_read_name_the_first_three_workspaces(svc):  # noqa: F811
    errors = [{"workspace": f"W{i}", "message": f"m{i}"} for i in range(4)]
    svc.workload_store.cache_put(
        "fabric_items", {"items": [], "errors": errors}
    )  # no count of workspaces
    _, obs, _ = svc._wl_items(False)
    assert (
        obs["state"] == "partial"
        and obs["detail"]
        == "4 of 0 workspaces couldn't be read: W0: m0; W1: m1; W2: m2"
    )


def test_a_consumer_from_the_catalog_is_declared(svc, monkeypatch):  # noqa: F811
    w = {
        "definition": {"project": "sales"},
        "dependencies": {"products": [{"product": "orders"}]},
    }
    monkeypatch.setattr(
        svc,
        "platform_products",
        lambda: {
            "products": [
                {"id": "sales.orders", "name": "orders", "consumers": ["finance"]}
            ]
        },
        raising=False,
    )
    assert svc._wl_consumers(w) == [
        {"product": "orders", "consumer": "finance", "declared": True}
    ]


def test_a_metadata_runs_outcome_is_kept_in_2000_characters(svc, project, monkeypatch):  # noqa: F811
    wid = model.meta_id("proj", sorted(project.entities)[0])
    monkeypatch.setattr(
        svc,
        "meta_run",
        lambda names, env: [
            {
                "entity": names[0],
                "status": "failed",
                "message": "x" * 3000,
                "run_id": "r",
            }
        ],
    )
    action = svc.workload_act(wid, "run", "dev")["action"]
    assert action["status"] == "failed" and len(action["detail"]) == 2000


def test_a_fabric_item_with_no_job_history_keeps_its_deployment(svc, monkeypatch):  # noqa: F811
    class NoJobs(FakeFabric):
        def list_job_instances(self, ws, item):
            return []

    signed_in(svc)
    scan(svc, monkeypatch)
    monkeypatch.setattr(workload_service, "FabricClient", NoJobs)
    detail = svc.workload_detail(model.fabric_id("ws1", "pl1"))
    [dep] = detail["deployments"]
    assert (
        detail["runs"] == []
        and dep["latest_run"] is None
        and dep["observation"]["state"] == "live"
    )
