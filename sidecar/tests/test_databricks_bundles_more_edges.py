"""Databricks Asset Bundles with a fake CLI: unreadable files, a bundle that isn't a definition, a commands
check that skips odd shapes, and a CLI that is missing, slow, can't start or says no. No real CLI runs."""

from __future__ import annotations

import subprocess

import pytest

from fabric_desktop.engines import databricks_bundles as db

from test_databricks_bundles import YML, Done, FakeCli


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    (tmp_path / "databricks.yml").write_text(YML)
    monkeypatch.setattr(db, "find_cli", lambda: "/usr/bin/databricks")
    return tmp_path


def test_a_relative_or_missing_folder_is_refused(tmp_path):
    with pytest.raises(db.BundleError, match="Pick the folder that holds databricks.yml"):
        db.bundle_file("relative/folder")
    with pytest.raises(db.BundleError, match="Pick the folder"):
        db.bundle_file(str(tmp_path / "missing"))


def test_databricks_yml_that_wont_parse_or_isnt_a_mapping_is_refused(tmp_path):
    (tmp_path / "databricks.yml").write_text("bundle: [unclosed\n")
    with pytest.raises(db.BundleError, match="databricks.yml can't be read: .*Error"):
        db.targets(str(tmp_path))
    (tmp_path / "databricks.yml").write_text("- just\n- a list\n")
    with pytest.raises(db.BundleError, match="isn't a bundle definition"):
        db.targets(str(tmp_path))


def test_an_included_file_that_wont_parse_is_refused_by_name(bundle):
    (bundle / "databricks.yml").write_text(YML + "include:\n  - resources/*.yml\n")
    (bundle / "resources").mkdir()
    (bundle / "resources" / "jobs.yml").write_text("jobs: {unclosed\n")
    with pytest.raises(db.BundleError, match="jobs.yml can't be read"):
        db.local_commands(str(bundle))


def test_the_commands_check_skips_shapes_that_cant_run_anything():
    assert db._commands_in("x.yml", ["not", "a", "mapping"]) == []
    doc = {"experimental": {"scripts": "not a mapping"}, "artifacts": {"lib": "not a mapping"}}
    [(shown, _)] = db._commands_in("x.yml", doc)
    assert "artifacts.lib.build: the CLI's default build for a whl artifact" in shown


def test_the_cli_must_be_installed_and_signed_in_by_profile(bundle, monkeypatch):
    with pytest.raises(db.BundleError, match="run as a Databricks CLI profile"):
        db.validate(str(bundle), "test", "bad profile!", FakeCli())
    monkeypatch.setattr(db, "find_cli", lambda: "")
    with pytest.raises(db.BundleError, match="isn't installed or isn't on PATH"):
        db.validate(str(bundle), "test", "dev", FakeCli())


@pytest.mark.parametrize("raised, says", [
    (subprocess.TimeoutExpired(["databricks"], 60), "didn't finish within"),
    (FileNotFoundError("gone"), r"couldn't be started \(FileNotFoundError\)"),
])
def test_a_cli_that_is_slow_or_cant_start_says_so(bundle, raised, says):
    def run(*a, **k):
        raise raised

    with pytest.raises(db.BundleError, match=says):
        db.validate(str(bundle), "test", "dev", run)


def test_a_failed_validate_or_plan_returns_the_clis_own_words(bundle):
    def refuse(args, **kw):
        return Done(1, "", "Error: the workspace refused\n")

    assert db.validate(str(bundle), "test", "dev", refuse) == {"ok": False, "error": "Error: the workspace refused"}
    assert db.plan(str(bundle), "test", "dev", refuse) == {"ok": False, "error": "Error: the workspace refused"}


def test_a_long_command_is_shortened_and_points_at_its_file():
    shown = db._shown("x" * 400, "databricks.yml")
    assert shown.endswith("(a long command, 400 characters: open databricks.yml to read it all)")
