"""A state file that can't be parsed (torn by a crash, or edited by hand) is moved aside to
``<name>.corrupt-<timestamp>`` with its bytes kept, and the load starts empty, so the next save
never silently overwrites what was in it (#1427)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop.git_service import GitMixin
from fabric_desktop.localdata.projects import ProjectStore
from fabric_desktop.optimize.installation import Installation
from fabric_desktop.optimize.registry import Registry
from fabric_desktop.telemetry import Telemetry
from fabric_desktop.workloads.incident_history import Sent

TORN = b'{"half": [1, 2'


class _Git(GitMixin):
    def __init__(self, root: Path):
        self.root = root


LOADERS = {
    "optimize installation": (lambda d: Installation(d), lambda o: o.path, lambda o: o._load(), {}),
    "optimize registry": (lambda d: Registry(d), lambda o: o.path, lambda o: o._load()["connections"], []),
    "telemetry": (lambda d: Telemetry(d), lambda o: o.path, lambda o: o._load(), {}),
    "incident history": (lambda d: Sent(d / "sent.json"), lambda o: o.path, lambda o: o._load(), {}),
    "local projects index": (lambda d: ProjectStore(d), lambda o: o.index_file, lambda o: o._index(), {}),
    "git repositories": (lambda d: _Git(d), lambda o: o.git_repos_file, lambda o: o._git_saved()["repos"], []),
}


@pytest.mark.parametrize("name", sorted(LOADERS))
def test_corrupt_file_is_set_aside_and_load_starts_empty(name, tmp_path):
    make, path_of, load, empty = LOADERS[name]
    owner = make(tmp_path)
    path = path_of(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TORN)

    assert load(owner) == empty
    assert not path.exists()
    aside = list(path.parent.glob(f"{path.name}.corrupt-*"))
    assert len(aside) == 1, aside
    assert aside[0].read_bytes() == TORN


@pytest.mark.parametrize("name", sorted(LOADERS))
def test_missing_file_is_empty_and_nothing_is_set_aside(name, tmp_path):
    make, path_of, load, empty = LOADERS[name]
    owner = make(tmp_path)
    path = path_of(owner)

    assert load(owner) == empty
    assert not list(path.parent.glob("*.corrupt-*")) if path.parent.exists() else True


def test_valid_json_of_the_wrong_shape_is_left_in_place(tmp_path):
    owner = Installation(tmp_path)
    owner.path.parent.mkdir(parents=True, exist_ok=True)
    owner.path.write_text("[1, 2]", encoding="utf-8")

    assert owner._load() == {}
    assert owner.path.read_text(encoding="utf-8") == "[1, 2]"
