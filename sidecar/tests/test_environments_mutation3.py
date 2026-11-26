"""environments.py: the mutants that test_environments_mutation.py and the module's other tests leave alive.
Removing a package doesn't stop at a folder that won't go, and deleting an environment detaches only its own
notebooks and clears its leftover folders."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import environments as envs
from fabric_desktop.environments import Environments


@pytest.fixture
def env(tmp_path):
    e = Environments(tmp_path, python="py")
    e.create("Data")
    return e


def dist(folder, name, files=()):
    info = folder / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n", encoding="utf-8")
    record = []
    for rel in files:
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
        record.append(f"{rel},,")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    return info


def test_removing_a_package_never_stops_at_a_folder_that_wont_go(env, monkeypatch):
    target = env.site_packages("Data")
    info = dist(target, "demo-pkg", ["demo_pkg/__init__.py"])
    cleared = []
    monkeypatch.setattr(envs, "shutil", SimpleNamespace(rmtree=lambda path, ignore_errors=False: cleared.append((path, ignore_errors))))
    assert envs.Environments._remove(target, "demo-pkg") is True
    assert (info, True) in cleared and all(flag is True for _, flag in cleared)


def test_delete_detaches_only_its_own_notebooks_and_clears_leftovers(env, monkeypatch):
    env.create("Other")
    env.attach("a", "Data")
    env.attach("b", "Other")
    cleared = []
    monkeypatch.setattr(envs, "shutil", SimpleNamespace(rmtree=lambda path, ignore_errors=False: cleared.append((path, ignore_errors))))
    assert env.delete("Data") == {"deleted": "Data", "detached": ["a"]}
    assert env.of("b") == "Other" and env.of("a") is None
    assert [e["notebooks"] for e in env.list()] == [["b"]]
    (path, ignore), = cleared
    assert ignore is True and path.name.startswith(".deleting-") and len(path.name) == len(".deleting-") + 8




