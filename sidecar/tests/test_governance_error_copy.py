"""Every refusal that comes from the organization's settings names the setting by its key and says what
IT can do about it, and where (Group Policy or the policy file) when that is known (WR-414). Each case
drives the real refusal path with a policy that turns the setting off and checks the words a person sees."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from fabric_desktop import app_settings, fabric_api, registry_policy, risk
from fabric_desktop.auth import Authenticator
from fabric_desktop.central.service import PlatformMixin
from fabric_desktop.controlplane_service import ControlPlaneMixin
from fabric_desktop.editor_service import EditorMixin
from fabric_desktop.engines.runtimes import LockedRuntimes
from fabric_desktop.environment_service import EnvironmentMixin
from fabric_desktop.environments import Environments
from fabric_desktop.incident_desk import IncidentDeskMixin
from fabric_desktop.integrations import app_service as integrations_app
from fabric_desktop.integrations.extensions import ExtensionHost
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.optimize_service import OptimizeMixin
from fabric_desktop.prove_service import ProveMixin
from fabric_desktop.warehouse_monitoring_service import WarehouseMonitoringMixin

IT_ACTION = re.compile(r"\b[Aa]sk IT to \w+")
AWAY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
HOME = "11111111-2222-3333-4444-555555555555"

OFF = {"allow_fabric_runs": False, "allow_notebook_packages": False, "allow_signed_out_schedules": False,
       "allow_optimize_lakehouse": False, "allow_central_environment": False, "allow_replay_samples": False,
       "allow_dbconnect_install": False, "wh_monitoring_views": False, "allowed_tenants": [HOME],
       "show_preview": False, "integrations": False, "integrations_extensions": [],
       "platform": {"dev": {"workspace_id": HOME}}}


@pytest.fixture
def policy(monkeypatch, tmp_path):
    """Writes the policy file; Group Policy sets nothing unless a test says so."""
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(registry_policy, "_reader", lambda: None)

    def write(data):
        path.write_text(json.dumps(data))
    write(OFF)
    return write


def says(message: str, key: str, where: str | None = "the policy file") -> None:
    assert key in message, message
    assert IT_ACTION.search(message), message
    if where:
        assert f"in {where}" in message, message
    assert "\u2014" not in message and "->" not in message, message  # no em-dashes or arrows


def refusal(call, *args, **kwargs) -> str:
    with pytest.raises(Exception) as caught:
        call(*args, **kwargs)
    return str(caught.value)


def test_a_setting_the_organization_fixed_says_which_key_and_where(policy):
    says(refusal(app_settings.save, {"show_preview": True}), "show_preview")


def test_group_policy_is_named_when_it_sets_the_key(policy, monkeypatch):
    policy({})
    monkeypatch.setattr(registry_policy, "_reader",
                        lambda: lambda: ({"allow_fabric_runs": (0, registry_policy.REG_DWORD),
                                          "show_preview": (0, registry_policy.REG_DWORD)}, {}))
    says(refusal(app_settings.save, {"show_preview": True}), "show_preview", "Group Policy")
    says(refusal(fabric_api._check_fabric_runs_allowed, "runs in Fabric"), "allow_fabric_runs", "Group Policy")


def test_unreadable_group_policy_says_what_it_may_set_and_what_it_can_do(policy, monkeypatch):
    def broken():
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(registry_policy, "_reader", lambda: broken)
    says(refusal(app_settings.save, {"channel": "beta"}), "channel", None)
    _, problems = registry_policy.read()
    assert problems and all(IT_ACTION.search(p) for p in problems)


def test_bad_group_policy_values_say_what_it_can_do(policy, monkeypatch):
    def broken_list():
        return ({"telemetry": ("yes", registry_policy.REG_SZ), "not_a_setting": (1, registry_policy.REG_DWORD)},
                {"fabric_view_sign_in_hosts": PermissionError(5, "Access is denied"), "nope": ["a"]})
    monkeypatch.setattr(registry_policy, "_reader", lambda: broken_list)
    _, problems = registry_policy.read()
    assert len(problems) == 4
    for problem in problems:
        assert IT_ACTION.search(problem) and "Group Policy" in problem, problem
    assert any("telemetry" in p for p in problems) and any("fabric_view_sign_in_hosts" in p for p in problems)


def test_policy_file_problems_each_name_the_key_and_what_it_can_do(policy):
    policy({"allow_fabric_runs": "no", "motion_level": 9, "channel": "nightly", "integrations": "off",
            "notebook_package_folder": "https://example.com/wheels", "engine_folder": "C:\\engine"})
    problems = app_settings.policy_problems()
    for key in ("allow_fabric_runs", "motion_level", "channel", "integrations", "notebook_package_folder", "engine_folder"):
        assert any(key in p and IT_ACTION.search(p) for p in problems), (key, problems)
    assert all(IT_ACTION.search(p) for p in problems), problems


def test_an_unreadable_policy_file_says_what_it_can_do(policy):
    app_settings.policy_path().write_text("{ not json")
    problems = app_settings.policy_problems()
    assert problems and IT_ACTION.search(problems[0]) and "policy file" in problems[0]


def test_fabric_runs(policy):
    says(refusal(fabric_api._check_fabric_runs_allowed, "runs in Fabric"), "allow_fabric_runs")
    found = risk.check_policy({"where": "fabric", "policy": {"allow_fabric_runs": False}})
    says(found["remedy"], "allow_fabric_runs", "Group Policy or the policy file")


def test_notebook_packages(policy):
    says(refusal(EnvironmentMixin()._packages_allowed), "allow_notebook_packages")
    policy({"notebook_package_folder": "https://example.com/wheels"})
    says(refusal(EnvironmentMixin()._packages_allowed), "notebook_package_folder")
    policy({"allow_pypi_packages": False})
    says(refusal(EnvironmentMixin()._packages_allowed), "allow_pypi_packages")


def test_package_install_job_log(tmp_path):
    job = {"folder": "", "pypi": False}
    assert Environments._install(SimpleNamespace(), job, tmp_path, tmp_path, ["x"]) is False
    says(job["log"], "allow_pypi_packages", None)


def test_signed_out_schedules(policy):
    off = SimpleNamespace(allow_signed_out=False)
    says(refusal(LocalSchedules._register, off, {"signed_out": True}), "allow_signed_out_schedules")
    off.scheduler, off._load = None, lambda: {"schedules": [{"id": "s1", "signed_out": True}]}
    says(refusal(LocalSchedules.set_enabled, off, "s1", True), "allow_signed_out_schedules")


def test_optimize_lakehouse_and_central_lakehouse(policy):
    says(refusal(OptimizeMixin._optimize_allowed, object()), "allow_optimize_lakehouse")
    says(IncidentDeskMixin._history_unavailable(object())["message"], "allow_optimize_lakehouse")


def test_kratosforge_environments(policy):
    says(refusal(ControlPlaneMixin._cp_allowed, object()), "allow_central_environment")


def test_replay_samples(policy):
    says(refusal(ProveMixin.replay_bundle_save, object(), "n", [], 0, include_sample=True), "allow_replay_samples")


def test_databricks_connect_installs(policy):
    runtimes = SimpleNamespace(allow_key="allow_dbconnect_install", label="Databricks Connect")
    says(refusal(LockedRuntimes.install, runtimes, "15.4"), "allow_dbconnect_install")


def test_warehouse_monitoring_views(policy):
    says(refusal(WarehouseMonitoringMixin.wh_monitoring_build, object(), HOME, HOME, HOME, []), "wh_monitoring_views")


def test_allowed_tenants(policy):
    auth = SimpleNamespace(sign_out=lambda: None, signed_in_tenant=lambda: AWAY)
    says(refusal(Authenticator._check_tenant, auth, {"id_token_claims": {"tid": AWAY}}), "allowed_tenants")


def test_platform(policy):
    fixed = SimpleNamespace(_platform_source=lambda: "policy")
    says(refusal(PlatformMixin._save_platform, fixed, "dev", {}), "platform")
    says(refusal(PlatformMixin._check_can_share, fixed), "platform")


def test_external_ai_tools(policy):
    says(PermissionAuthorizer._enabled(SimpleNamespace())[1], "integrations")
    says(refusal(integrations_app.IntegrationsMixin.integrations_set_enabled, object(), True), "integrations")
    says(refusal(integrations_app.IntegrationsMixin.integrations_set_http, SimpleNamespace(
        _http_state=lambda: {"allowed": False}), True), "integrations")
    says(EditorMixin._editor_allowed(object()), "integrations")
    policy({"integrations_http": False, "integrations_editors": False, "integrations_max_permissions": ["READ_METADATA"],
            "integrations_extensions": []})
    says(refusal(integrations_app.IntegrationsMixin.integrations_set_http, SimpleNamespace(
        _http_state=lambda: {"allowed": False}), True), "integrations_http")
    says(EditorMixin._editor_allowed(object()), "integrations_editors")
    says(ExtensionHost.allowed(SimpleNamespace(), "notebook-outline")[1], "integrations_extensions")
    says(refusal(integrations_app._check_permissions, ["READ_DATA"], "grants"), "integrations_max_permissions")
