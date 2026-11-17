"""Shortcut Center helpers: a workspace name that is only environment words, a workspace label found
from the lakehouse list, and a target whose readers can't be seen."""

from fabric_desktop import shortcut_center as sc

LAKEHOUSES = [{"workspace_id": "w-dev", "workspace_name": "Dev", "lakehouse_id": "l1", "lakehouse_name": "LH"},
              {"workspace_id": "w-prod", "workspace_name": "Prod", "lakehouse_id": "l2", "lakehouse_name": "LH"}]


def test_a_workspace_named_only_by_its_environment_has_no_counterpart():
    assert sc.env_workspace("w-dev", "Dev", "prod", {}, LAKEHOUSES, []) is None


def test_a_workspace_label_comes_from_the_lakehouse_list_and_else_says_that_workspace():
    assert sc._workspace_label({"workspace_id": "w-prod", "workspace_name": ""}, LAKEHOUSES) == "Prod"
    assert sc._workspace_label({"workspace_id": "w-gone", "workspace_name": ""}, LAKEHOUSES) == "that workspace"


def test_readers_of_a_target_that_cant_be_seen_are_unknown():
    out = sc.readers_gap([{"id": "a"}, {"id": "b"}], None, "onelake")
    assert out == {"mode": "onelake", "here": 2, "target": None, "gap": None, "missing": [],
                   "text": "You can't see who the target lets in"}
