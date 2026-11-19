"""Platform setup (central/setup.py): what the mutation run (batch 125, docs/quality/mutation-testing.md) showed
no test pinned. The landing step's own detail, a workspace Fabric returns without a name, and the variable
library's files as indented JSON."""

from __future__ import annotations

from test_platform_setup_steps import Fabric, platform  # noqa: F401 - platform: the autouse UDF stand-in

from fabric_desktop.central import setup


def _token():
    return "execute-token"


def _details(out):
    return {s["step"]: s["detail"] for s in out["steps"]}


def test_the_landing_step_says_whether_it_made_or_kept_lh_landing():
    fabric = Fabric()
    first = setup.setup(fabric, _token, "dev")
    assert _details(first)["LH_Landing"] == "Created LH_Landing"
    again = setup.setup(fabric, _token, "dev", existing=first["config"])
    assert _details(again)["LH_Landing"] == "Kept LH_Landing"


class Nameless(Fabric):
    """Fabric answering a create with the new workspace's id only."""

    def create_workspace(self, name, capacity_id, description):
        ws = super().create_workspace(name, capacity_id, description)
        return {"id": ws["id"]}


def test_a_new_workspace_without_a_name_in_the_answer_takes_the_name_asked_for():
    out = setup.setup(Nameless(), _token, "test")
    assert out["config"]["workspace_name"] == "Fabric Desktop Platform - Test"
    assert out["steps"][0] == {"step": "Platform workspace", "status": "done",
                               "detail": "Created Fabric Desktop Platform - Test"}
    landing = setup.landing_setup(Nameless(), name="Landing EU")
    assert landing["config"]["workspace_name"] == "Landing EU"
    assert landing["steps"][0]["detail"] == "Created Landing EU"


def test_the_variable_library_files_are_json_indented_by_two():
    parts = setup._varlib({"workspace_id": "ws"})
    assert parts["variables.json"].startswith('{\n  "$schema": ')
    assert parts["settings.json"].startswith('{\n  "$schema": ')
