"""Skin manifests (skins.py) whose parts are the wrong shape: each is named in the problems, and a skin with
no description is still a skin."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import skins


def _skin(**changes):
    m = json.loads(json.dumps(skins.KRATOSFORGE))
    m.update(id="my-skin", name="My skin")
    m.update(changes)
    return m


def _problems(manifest) -> list[str]:
    with pytest.raises(skins.SkinError) as err:
        skins.validate(manifest, skins.BASE)
    return err.value.problems


@pytest.mark.parametrize("changes, problem", [
    ({"targets": ["fabric"]}, "targets[0] must be an object"),
    ({"appearance": "dark"}, "appearance must be an object"),
    ({"appearance": {"color_mode": "sepia"}}, "appearance color_mode must be one of light, dark"),
    ({"appearance": {"density": "roomy"}}, "appearance density must be one of comfortable, compact"),
    ({"appearance": {"tokens": ["#000"]}}, "appearance tokens must be an object with light and dark"),
    ({"appearance": {"tokens": {"light": "#000000"}}}, "appearance tokens light must be an object of token: color"),
    ({"workflow": ["home"]}, "workflow must be an object"),
    ({"workflow": {"commands": ["notebook.run_all"]}}, "commands must be an object of view: placement"),
    ({"workflow": {"commands": {"notebook": "notebook.run_all"}}},
     "commands.notebook must be an object with primary, bar and labels"),
    ({"workflow": {"commands": {"notebook": {"primary": "notebook.run_all", "bar": "notebook.save"}}}},
     "commands.notebook.bar must be a list of notebook commands"),
    ({"workflow": {"commands": {"notebook": {"primary": "notebook.run_all", "labels": ["Go"]}}}},
     "commands.notebook.labels must be an object of command id: label"),
])
def test_a_part_of_the_wrong_shape_is_named(changes, problem):
    assert _problems(_skin(**changes)) == [problem]


def test_a_label_that_is_not_text_is_named_once():
    problems = _problems(_skin(workflow={"commands": {"notebook": {"primary": "notebook.run_all",
                                                                   "labels": {"notebook.save": 7}}}}))
    assert len(problems) == 1 and problems[0].startswith("commands.notebook.labels notebook.save")


def test_a_skin_with_no_description_is_valid():
    m = _skin()
    m.pop("description", None)
    assert skins.validate(m, skins.BASE) is m
