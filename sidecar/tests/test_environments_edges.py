"""Notebook environments: the edges the main tests don't reach. Names and package lists that are
refused, odd files in the approved folder, unknown or busy environments, an install that runs too
long or breaks, and removing a package with no RECORD match. pip never runs."""

from __future__ import annotations

import subprocess

import pytest

from fabric_desktop import environments as envs
from fabric_desktop.environments import Environments


@pytest.fixture
def env(tmp_path):
    e = Environments(tmp_path)
    e.create("Data")
    return e


@pytest.mark.parametrize("name", ["", "bad/name", "trailing.", "x" * 65])
def test_a_name_that_isnt_one_is_refused(name):
    with pytest.raises(ValueError, match="letters, numbers"):
        envs.check_name(name)


def test_blank_package_lines_are_skipped_and_none_at_all_is_refused():
    assert envs.check_packages(["", "  ", "polars-ds==0.1"]) == ["polars-ds==0.1"]
    with pytest.raises(ValueError, match="at least one package"):
        envs.check_packages(["", "   "])
    with pytest.raises(ValueError, match="at least one package"):
        envs.check_packages(None)


def test_odd_files_in_the_approved_folder_are_ignored(tmp_path):
    for name in ("good-1.0-py3-none-any.whl", "notes.txt", "broken.whl", "half.tar.gz", "other-2.0.zip",
                 "good-2.0-py3-none-any.whl", "duckdb-9.9-py3-none-any.whl"):
        (tmp_path / name).write_bytes(b"")
    assert envs.folder_pins(tmp_path, ["duckdb==1.5.6"]) == ["good==2.0", "other==2.0"]


def test_an_environment_without_its_folder_has_nothing_installed(env):
    assert env.installed("Elsewhere") == []


def test_unknown_environments_are_named(env):
    for call in (lambda: env.delete("Gone"), lambda: env.attach("Sales", "Gone"), lambda: env.start("Gone", ["a==1"])):
        with pytest.raises(ValueError, match="no environment called Gone"):
            call()
    with pytest.raises(ValueError, match="isn't known"):
        env.job("nope")


def test_a_busy_environment_cant_be_deleted_or_installed_into_again(env):
    env.jobs["j1"] = {"id": "j1", "environment": "Data", "state": "running"}
    with pytest.raises(RuntimeError, match="installing packages"):
        env.delete("Data")
    with pytest.raises(RuntimeError, match="already installing"):
        env.start("Data", ["a==1"])
    env.jobs["j1"]["state"] = "done"
    assert env.delete("Data") == {"deleted": "Data", "detached": []}


def test_attaching_nothing_detaches_and_renames_follow_the_notebook(env):
    env.attach("Sales", "Data")
    env.rename_notebook("Sales", "Revenue")
    env.rename_notebook("Nobody", "Else")  # a notebook without an environment: nothing changes
    assert env.of("Revenue") == "Data" and env.of("Sales") is None and env.of("Else") is None
    assert env.attach("Revenue", None) == {"notebook": "Revenue", "environment": None}
    assert env.of("Revenue") is None


def _job(name="Data", remove=False):
    return {"id": "j", "environment": name, "packages": ["demo-pkg==1.0"], "remove": remove,
            "state": "running", "log": "", "finished": None}


def test_an_install_that_runs_too_long_is_reported(env, monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("pip", envs.INSTALL_SECONDS)

    monkeypatch.setattr(env, "_install", slow)
    job = _job()
    env._run(job)
    assert job["state"] == "failed" and f"within {envs.INSTALL_SECONDS // 60} minutes" in job["log"]
    assert job["finished"] is not None


def test_an_install_that_breaks_says_why(env, monkeypatch):
    def broken(*a, **k):
        raise PermissionError("site-packages is read-only")

    monkeypatch.setattr(env, "_install", broken)
    job = _job()
    env._run(job)
    assert job["state"] == "failed" and job["log"] == "PermissionError: site-packages is read-only"


def test_removing_a_package_that_isnt_there_says_so(env):
    target = env.site_packages("Data")
    info = target / "other_pkg-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: other-pkg\nVersion: 1.0\n", encoding="utf-8")
    job = _job(remove=True)
    env._run(job)
    assert job["state"] == "done" and job["log"] == "None of those were installed."
    assert info.is_dir()  # another package is left alone
