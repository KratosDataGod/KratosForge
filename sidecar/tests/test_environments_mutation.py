"""environments.py: what mutation testing showed no test checked. pip's retries and time limit, the log's
length, which install try's output says pip was offline, a no-folder install writing no folder constraints,
installed() leaving out the engine's own packages, updating an environment's package list, removing a
package and its nested folders, delete's leftover folders and detaching only its own notebooks, and the ids.
pip never runs: subprocess is a module-local fake."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop import environments as envs
from fabric_desktop.environments import Environments


@pytest.fixture
def env(tmp_path):
    e = Environments(tmp_path, python="py")
    e.create("Data")
    return e


def fake_pip(monkeypatch, *answers):
    """subprocess for environments.py: each run answers the next (returncode, output)."""
    calls = []
    left = list(answers)

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        code, out = left.pop(0)
        return SimpleNamespace(returncode=code, stdout=out, stderr="")
    monkeypatch.setattr(envs, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    return calls


def job(name="Data", **kw):
    return {"id": "j", "environment": name, "packages": ["demo-pkg==1.0"], "remove": False, "state": "running",
            "log": "", "finished": None, **kw}


def test_pip_gets_two_retries_and_fifteen_minutes(env, monkeypatch, tmp_path):
    assert envs.PIP_RETRIES == 2 and envs.INSTALL_SECONDS == 900
    cmd = envs.install_commands("py", tmp_path / "t", tmp_path / "c.txt", ["a==1"])[0]
    assert cmd[cmd.index("--retries") + 1] == "2"
    calls = fake_pip(monkeypatch, (0, "ok"))
    target = env.site_packages("Data")
    assert env._install(job(), target, target.parent / "pins.txt", ["a==1"]) is True
    assert calls[0][1]["timeout"] == 900 and calls[0][1]["capture_output"] is True
    assert not (target.parent / "folder-constraints.txt").exists()  # no folder: no folder constraints


def test_the_log_keeps_its_last_20000_characters(env, monkeypatch):
    fake_pip(monkeypatch, (1, "x" * 30000))
    j = job()
    target = env.site_packages("Data")
    assert env._install(j, target, target.parent / "pins.txt", ["a==1"]) is False
    assert len(j["log"]) == 20000


def test_refusals_before_pip_are_false(env, tmp_path):
    target = env.site_packages("Data")
    assert env._install(job(folder="", pypi=False), target, target, ["a==1"]) is False
    assert env._install(job(folder=str(tmp_path / "nowhere"), pypi=True), target, target, ["a==1"]) is False


def test_offline_is_read_from_the_last_tries_output(env, monkeypatch, tmp_path):
    folder = tmp_path / "approved"
    folder.mkdir()
    fake_pip(monkeypatch, (1, "ERROR: No matching distribution found for a"),
             (1, "ProxyError('Cannot connect to proxy.') ERROR: No matching distribution found for a"))
    j = job(folder=str(folder), pypi=True)
    target = env.site_packages("Data")
    assert env._install(j, target, target.parent / "pins.txt", ["a==1"]) is False
    assert j["log"].endswith("The proxy refused pip's connection to the package index, so it reported the packages "
                             f"as not found. {envs.PIP_OFFLINE_FIX}")
    assert "Ask IT to add it to the folder" not in j["log"]


def test_a_package_missing_from_the_folder_without_pypi_says_ask_it(env, monkeypatch, tmp_path):
    folder = tmp_path / "approved"
    folder.mkdir()
    fake_pip(monkeypatch, (1, "ERROR: No matching distribution found for a"))
    j = job(folder=str(folder), pypi=False)
    target = env.site_packages("Data")
    env._install(j, target, target.parent / "pins.txt", ["a==1"])
    assert j["log"].endswith("Ask IT to add it to the folder.")
    fake_pip(monkeypatch, (1, "ERROR: No matching distribution found for a"))
    j = job(folder="", pypi=True)
    env._install(j, target, target.parent / "pins.txt", ["a==1"])
    assert "Ask IT to add it to the folder" not in j["log"]


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


def test_installed_leaves_out_the_engines_own_packages(env):
    target = env.site_packages("Data")
    dist(target, "duckdb")
    dist(target, "demo-pkg")
    assert env.installed("Data") == [{"name": "demo-pkg", "version": "1.0"}]


def test_removing_a_package_takes_its_nested_folders_and_says_so(env):
    target = env.site_packages("Data")
    info = dist(target, "demo-pkg", ["demo_pkg/__init__.py", "demo_pkg/sub/mod.py"])
    dist(target, "other-pkg", ["other_pkg/__init__.py"])
    assert envs.Environments._remove(target, "missing") is False
    j = job(remove=True)
    env._run(j)
    assert j["state"] == "done" and j["log"] == "Removed demo-pkg==1.0"
    assert not (target / "demo_pkg").exists() and not info.exists()
    assert (target / "other_pkg" / "__init__.py").exists()
    dist(target, "third", ["third/__init__.py"])
    assert envs.Environments._remove(target, "third") is True



def test_removing_a_package_never_stops_at_a_folder_that_wont_go(env, monkeypatch):
    target = env.site_packages("Data")
    info = dist(target, "demo-pkg", ["demo_pkg/__init__.py"])
    cleared = []
    monkeypatch.setattr(envs, "shutil", SimpleNamespace(rmtree=lambda path, ignore_errors=False: cleared.append((path, ignore_errors))))
    assert envs.Environments._remove(target, "demo-pkg") is True
    assert (info, True) in cleared and all(flag is True for _, flag in cleared)

def test_an_install_replaces_the_packages_version_and_keeps_the_others(env, monkeypatch):
    monkeypatch.setattr(env, "_install", lambda *a: True)
    data = env._read()
    data["environments"]["Data"]["packages"] = ["demo-pkg==0.9", "keep==1"]
    env._write(data)
    j = job()
    env._run(j)
    assert j["state"] == "done"
    assert env._read()["environments"]["Data"]["packages"] == ["keep==1", "demo-pkg==1.0"]


def test_creating_over_a_leftover_folder_works(tmp_path):
    e = Environments(tmp_path)
    (e.folder / "Old" / "site-packages").mkdir(parents=True)
    assert e.create("Old") == {"name": "Old"}


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


def test_list_names_each_environments_own_notebooks(env):
    env.create("Other")
    env.attach("a", "Data")
    env.attach("b", "Other")
    assert {e["name"]: e["notebooks"] for e in env.list()} == {"Data": ["a"], "Other": ["b"]}


def test_a_job_id_is_twelve_characters(env, monkeypatch):
    monkeypatch.setattr(env, "_run", lambda job: None)
    started = env.start("Data", ["demo-pkg==1.0"])
    assert len(started["id"]) == 12 and started["state"] == "running"
