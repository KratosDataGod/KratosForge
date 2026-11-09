"""A metadata project's Fabric side on this PC (cloud_service.py), the paths the main tests don't reach:
asks it refuses before calling Fabric, Monitor's run list when an environment can't be read or the
user isn't signed in (it never pops a sign-in), and the setting for maintenance after a run.
Fabric is never called: sign-in is stood in."""

from __future__ import annotations

import pytest

from fabric_desktop import cloud_service
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


class SignedOut:
    def silent_token(self, scopes):
        raise RuntimeError("no account is signed in")


def test_seconds_between_times_without_a_zone_or_that_cant_be_read():
    assert cloud_service._seconds("2026-11-09T10:00:00", "2026-11-09T10:00:05Z") == 5.0
    assert cloud_service._seconds(None, "later") == 0.0


@pytest.mark.parametrize("call, error, says", [
    (lambda s: s.meta_set_compute("dev", "cloud"), ValueError, "compute is local"),
    (lambda s: s.meta_set_compute("dev", "fabric"), ValueError, "Dev keeps its tables on this PC"),
    (lambda s: s.meta_add_schedule("dev", {"kind": "daily"}), ValueError, "Set Dev to run in Fabric first"),
    (lambda s: s.meta_delete_schedule("test", "s1"), LookupError, "no runner notebook in the Test workspace"),
    (lambda s: s.meta_fabric_result("nope"), LookupError, "no record of the Fabric run nope"),
    (lambda s: s.meta_model_plan("dev", "nope"), ValueError, "nope is not a layer"),
])
def test_asks_refused_before_fabric_is_called(svc, call, error, says):
    with pytest.raises(error, match=says):
        call(svc)


def test_a_local_environment_has_no_fabric_schedules(svc):
    assert svc.meta_schedules("dev") == {"env": "dev", "compute": "local", "published": False, "schedules": []}


def test_maintenance_after_a_run_is_kept_but_never_runs_on_this_pc(svc):
    assert svc.meta_set_maintenance("dev", True)["optimize_after_run"] is False  # Dev's tables are on this PC
    assert svc._project().environments["dev"]["optimize_after_run"] is True  # kept for when it moves to OneLake
    svc.meta_set_maintenance("dev", False)
    assert svc._project().environments["dev"]["optimize_after_run"] is False


def test_monitor_never_signs_in_for_an_environment_in_onelake(svc, monkeypatch):
    svc.meta_set_environment(env="test", workspace_id="ws-test", workspace_name="Sales Test")
    monkeypatch.setattr(Service, "auth", property(lambda self: SignedOut()))
    assert svc._quiet_fabric() is None
    out = svc.meta_runs_all()
    assert out["unavailable"] == {"test": "Sign in to see its runs"}


def test_an_environment_whose_runs_cant_be_read_is_listed_as_unavailable(svc, monkeypatch):
    def offline(env, limit):
        raise OSError(f"{env}'s run log is on a drive that's gone")
    monkeypatch.setattr(svc, "meta_runs", offline)
    assert svc.meta_runs_all()["unavailable"] == {"dev": "dev's run log is on a drive that's gone"}
