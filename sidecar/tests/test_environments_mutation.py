"""Mutation batch 271: notebook environments (environments.py) where the earlier tests didn't look.
pip never runs: ``subprocess.run`` is replaced by a stand-in that answers as pip would, and installed
packages are small .dist-info folders written by the test."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import environments as envs
from fabric_desktop.environments import Environments


@pytest.fixture
def env(tmp_path):
    e = Environments(tmp_path / "app")
    e.create("Data")
    return e


def _dist(target, name, files=(), version="1.0", metadata_name=True):
    info = target / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    head = f"Name: {name}\n" if metadata_name else ""
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\n{head}Version: {version}\n", encoding="utf-8")
    record = []
    for f in files:
        p = target / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
        record.append(f"{f},,")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    return info


def _job(name="Data", remove=False, specs=("demo-pkg==1.0",), folder="", pypi=True):
    return {"id": "j", "environment": name, "packages": list(specs), "remove": remove, "state": "running",
            "log": "", "finished": None, "folder": folder, "pypi": pypi}


def _pip(monkeypatch, *answers):
    """Each pip run answers with the next (returncode, output); the commands run are kept."""
    runs, it = [], iter(answers)

    def run(cmd, **kw):
        runs.append(cmd)
        code, out = next(it)
        return SimpleNamespace(returncode=code, stdout=out, stderr="")
    monkeypatch.setattr(envs.subprocess, "run", run)
    return runs


def test_pip_tries_twice_per_address_and_stops_after_fifteen_minutes():
    assert envs.PIP_RETRIES == 2 and envs.INSTALL_SECONDS == 900
    cmd = envs.install_commands("py", envs.Path("t"), envs.Path("p"), ["a"])[0]
    assert cmd[cmd.index("--retries") + 1] == "2"


def test_an_environment_is_made_in_a_new_app_folder(tmp_path):
    e = Environments(tmp_path / "new" / "app")
    e.create("Fresh")
    assert e.site_packages("Fresh").is_dir()
    e.site_packages("Left").mkdir(parents=True)  # a folder an earlier environment of that name left
    e.create("Left")
    assert [x["name"] for x in e.list()] == ["Fresh", "Left"]


def test_deleting_one_environment_keeps_the_other_notebooks_attached(env):
    env.create("Other")
    env.attach("a.ipynb", "Data")
    env.attach("b.ipynb", "Other")
    assert [e["notebooks"] for e in env.list()] == [["a.ipynb"], ["b.ipynb"]]
    (env.folder / ".deleting-stray").write_text("a file an earlier delete left", encoding="utf-8")
    assert env.delete("Data") == {"deleted": "Data", "detached": ["a.ipynb"]}
    assert env.of("b.ipynb") == "Other" and env.of("a.ipynb") is None


def test_an_install_job_has_a_twelve_character_id(env, monkeypatch):
    monkeypatch.setattr(env, "_run", lambda job: None)
    assert len(env.start("Data", ["demo-pkg==1.0"])["id"]) == 12


def test_installed_lists_named_packages_the_engine_doesnt_have(env):
    target = env.site_packages("Data")
    _dist(target, "demo-pkg")
    _dist(target, "polars", version="0.0.1")  # one of the engine's own packages
    _dist(target, "nameless", metadata_name=False)
    assert env.installed("Data") == [{"name": "demo-pkg", "version": "1.0"}]


def test_an_install_that_cant_reach_anything_returns_false(env, tmp_path):
    target = env.site_packages("Data")
    assert env._install(_job(pypi=False), target, tmp_path / "pins", ["a"]) is False
    assert env._install(_job(folder=str(tmp_path / "missing")), target, tmp_path / "pins", ["a"]) is False


def test_pypi_alone_writes_no_folder_constraints_and_blames_no_folder(env, tmp_path, monkeypatch):
    _pip(monkeypatch, (1, "ERROR: something else went wrong"))
    target = env.site_packages("Data")
    job = _job()
    assert env._install(job, target, tmp_path / "pins", ["a"]) is False
    assert not (target.parent / "folder-constraints.txt").exists()
    assert "organization's package folder" not in job["log"]


def test_the_offline_note_reads_the_last_try(env, tmp_path, monkeypatch):
    folder = tmp_path / "pkgs"
    folder.mkdir()
    _pip(monkeypatch, (1, "ERROR: No matching distribution found for a"),
         (1, "WARNING: NewConnectionError: Failed to establish a new connection"))
    job = _job(folder=str(folder))
    assert env._install(job, env.site_packages("Data"), tmp_path / "pins", ["a"]) is False
    assert "couldn't reach the package index" in job["log"]


def test_a_failed_install_log_keeps_its_last_twenty_thousand_characters(env, tmp_path, monkeypatch):
    _pip(monkeypatch, (1, "x" * 30000))
    job = _job()
    env._install(job, env.site_packages("Data"), tmp_path / "pins", ["a"])
    assert len(job["log"]) == 20000


def test_reinstalling_a_package_replaces_its_line_and_keeps_the_others(env, monkeypatch):
    monkeypatch.setattr(env, "_install", lambda *a: True)
    env._run(_job(specs=["a==1"]))
    env._run(_job(specs=["b==1"]))
    env._run(_job(specs=["a==2"]))
    assert env.list()[0]["packages"] == ["b==1", "a==2"]


def test_removing_an_installed_package_removes_its_nested_folders(env):
    target = env.site_packages("Data")
    _dist(target, "demo-pkg", files=["demo_pkg/__init__.py", "demo_pkg/sub/mod.py"])
    assert env._remove(target, "other") is False
    job = _job(remove=True)
    env._run(job)
    assert job["log"] == "Removed demo-pkg==1.0"
    assert not (target / "demo_pkg").exists()
    assert envs.Environments._remove(target, "demo-pkg") is False
