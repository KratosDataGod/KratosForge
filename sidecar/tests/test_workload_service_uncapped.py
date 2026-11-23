"""Workloads (workload_service.py) edges left after running every mutant (mutation batch 258,
docs/quality/mutation-testing.md): audit lines go 50 at a time, unlinking says which notebook, a
OneLake read exactly two minutes old is read again, a run exactly the period ago is counted, the
preflight of a run in Fabric, the "Not authorized" note only for an item refused, and settling
only open actions, from the newest manual run."""

import datetime as dt
import types

import pytest

from fabric_desktop import workload_service
from fabric_desktop.workloads import model
from fabric_desktop.workloads import store as store_mod

from test_workloads import signed_in, svc  # noqa: F401 - svc is a fixture
from test_workload_service_mutation import _env_service, ago, frozen

T = dt.datetime(2026, 11, 19, 12, 0, 0, tzinfo=dt.timezone.utc)


class Platform:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append(kw["entry"]["name"])


def test_audit_lines_go_to_the_platform_50_at_a_time_oldest_first(svc, monkeypatch):  # noqa: F811
    assert workload_service.AUDIT_BATCH == 50
    store = svc.workload_store
    for i in range(52):
        store.audit_queue(f"k{i:02}", "nb:x", "run", "submitted", {"name": f"line{i}"})
    platform = Platform()
    monkeypatch.setattr(svc, "_wl_audit_platform", lambda: platform)
    assert svc._wl_audit_send() == 50
    assert platform.calls == [f"line{i}" for i in range(50)]
    assert [x["entry"]["name"] for x in store.audit_waiting(10)] == ["line50", "line51"]


def test_unlinking_a_notebook_says_which_one(svc):  # noqa: F811
    assert svc.workload_unlink("Daily load") == {"unlinked": "Daily load"}


def test_a_onelake_read_exactly_two_minutes_old_isnt_reused(svc, monkeypatch):  # noqa: F811
    frozen(monkeypatch)
    project, asked = _env_service(
        svc, monkeypatch, {"test": {"target": "onelake", "workspace_id": "ws-test"}}
    )
    svc.workload_store.cache_put(
        "meta:proj:test",
        {"runs": [{"run_id": "kept"}], "deployments": []},
        collected_at=ago(120),
    )
    out = svc._wl_meta_env(
        project, "proj", "test", False
    )  # signed out: it tries to read, and can't
    assert (
        out["runs"] == [{"run_id": "kept"}] and out["observation"]["state"] == "cached"
    )
    assert (
        out["observation"]["detail"]
        == "Showing what was read earlier. Sign in to read this environment's runs."
    )


class Frozen(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return T


def test_capacity_use_counts_a_run_that_started_exactly_the_period_ago(
    svc, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(
        workload_service,
        "dt",
        types.SimpleNamespace(
            datetime=Frozen, timedelta=dt.timedelta, timezone=dt.timezone, date=dt.date
        ),
    )
    run = {
        "job_id": "j1",
        "where": "fabric",
        "started_at": model.iso(T - dt.timedelta(days=30)),
    }
    monkeypatch.setattr(
        svc, "_wl_record", lambda id, refresh=False: {"id": id, "runs": [run]}
    )
    monkeypatch.setattr(svc, "_risk_cu", lambda runs: ([], [], ""), raising=False)
    assert svc.workload_capacity_use("fabric:ws1:pl1")["fabric_runs"] == 1


def _notebook(workspace_name="Sales Dev"):
    return {
        "name": "nb",
        "kind": "notebook",
        "definition": {},
        "runs": [],
        "deployments": [
            {
                "env": None,
                "actions": ["run_in_fabric"],
                "identity": {"label": "x"},
                "location": {
                    "label": "Fabric · Sales Dev",
                    "kind": "fabric",
                    "workspace_name": workspace_name,
                },
            }
        ],
    }


def test_a_run_in_fabric_needs_a_sign_in_and_names_the_workspace(svc, monkeypatch):  # noqa: F811
    w = _notebook()
    monkeypatch.setattr(svc, "_wl_record", lambda id, refresh=False: w)
    monkeypatch.setattr(
        svc, "_risk_for_preflight", lambda w, action, env: "risk", raising=False
    )
    assert svc.workload_preflight("nb:nb", "run_in_fabric") == {
        "allowed": False,
        "reason": "Sign in first: this action uses Fabric.",
    }
    signed_in(svc)
    pre = svc.workload_preflight(
        "nb:nb", "run_in_fabric"
    )  # the risk is checked unless told not to
    assert (
        pre["allowed"] is True
        and pre["target"] == "the notebook item in Sales Dev"
        and pre["risk"] == "risk"
    )
    w.update(_notebook(workspace_name=""))
    assert (
        svc.workload_preflight("nb:nb", "run_in_fabric", assess=False)["target"]
        == "the notebook item in its workspace"
    )


@pytest.mark.parametrize(
    "action, error, said",
    [
        ("run_item", ConnectionError("no network"), False),
        ("run_item", PermissionError("refused"), True),
        ("cancel", PermissionError("That isn't a running job of n."), False),
    ],
)
def test_only_an_item_fabric_refused_to_start_says_not_authorized(
    svc, monkeypatch, action, error, said
):  # noqa: F811
    w = {
        "name": "n",
        "kind": "fabric_item",
        "definition": {"workspace_id": "ws", "item_id": "it", "job_type": "Run"},
        "deployments": [],
        "runs": [],
    }
    monkeypatch.setattr(
        svc,
        "workload_preflight",
        lambda *a, **k: {
            "allowed": True,
            "target": "t",
            "where": "Fabric",
            "identity": "you",
        },
    )
    monkeypatch.setattr(svc, "_wl_record", lambda id: w)

    def refuse(*a, **k):
        raise error

    monkeypatch.setattr(svc, "run_fabric_item_now", refuse, raising=False)
    with pytest.raises(type(error)):
        svc.workload_act("fabric:ws:it", action)
    [a] = svc.workload_store.actions("fabric:ws:it")
    assert a["status"] == "failed" and a["detail"].startswith("Not authorized") is said


def test_an_open_action_is_settled_behind_200_newer_settled_ones(svc, monkeypatch):  # noqa: F811
    store = svc.workload_store
    clock = [T]
    monkeypatch.setattr(store_mod, "now", lambda: clock[0])
    a = store.start_action("fabric:ws1:pl1", "run_item")
    store.update_action(a["id"], ref={"job_id": "j1"})
    for i in range(
        200
    ):  # only open actions are read, so newer ended ones don't crowd it out
        clock[0] = T + dt.timedelta(minutes=i + 1)
        done = store.start_action("fabric:ws1:pl2", "run_item")
        store.update_action(done["id"], "succeeded")
    svc._wl_reconcile([{"job_id": "j1", "status": "Failed", "message": "boom"}], [])
    [a] = store.actions("fabric:ws1:pl1")
    assert (a["status"], a["detail"]) == ("failed", "boom")


def test_a_schedule_action_is_settled_by_the_newest_manual_run(svc):  # noqa: F811
    store = svc.workload_store
    rec = store.start_action(
        "nb:x", "run_schedule", "", {"target": "t", "where": "This PC"}, "you", ""
    )
    store.update_action(rec["id"], ref={"schedule_id": "s1"})
    run = {"schedule_id": "s1", "trigger": "manual", "started": "9999-01-01T00:00:00"}
    svc._wl_reconcile(
        [],
        [
            run | {"status": "ok", "message": "first"},
            run | {"status": "failed", "message": "second"},
        ],
    )
    [a] = store.actions("nb:x")
    assert (a["status"], a["detail"]) == ("failed", "second")
