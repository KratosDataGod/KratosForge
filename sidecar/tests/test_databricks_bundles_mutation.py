"""Databricks Asset Bundles (engines/databricks_bundles.py): what mutation testing (batch 87,
docs/quality/mutation-testing.md) showed no test pinned. How long each CLI call may take, what the
CLI is started with, the cut-off for showing a command whole, which plan shapes are read, the kind a
plan row names, the digests' length and key order, the lines kept from the CLI's output, and a
target that is production by its mode alone. A fake CLI and tmp folders only: nothing runs."""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from fabric_desktop.engines import databricks_bundles as db

YML = """bundle:
  name: sales-etl
targets:
  dev:
    mode: development
  live:
    mode: production
"""


class Done:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


class Cli:
    """Records every call's keyword arguments; answers each verb from ``replies``."""

    def __init__(self, **replies):
        self.calls: list[tuple[list[str], dict]] = []
        self.replies = replies

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        return self.replies.get(args[2], Done(0, json.dumps({"plan": {}})))


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "databricks.yml").write_text(YML, encoding="utf-8")
    monkeypatch.setattr(db, "find_cli", lambda: "/usr/bin/databricks")
    return folder


# ---- how the CLI is started


def test_validate_and_plan_get_a_tenth_of_the_deploy_time_and_deploy_gets_all_of_it(bundle):
    cli = Cli(plan=Done(0, json.dumps({"plan": {"resources.jobs.a": {"action": "update"}}})))
    db.validate(str(bundle), "dev", "p1", cli)
    seen = db.plan(str(bundle), "dev", "p1", cli)
    db.deploy(str(bundle), "dev", "p1", seen["digest"], run=cli)
    timeouts = [(args[2], kw["timeout"]) for args, kw in cli.calls]
    assert timeouts == [("validate", 60), ("plan", 60), ("plan", 60), ("deploy", 600)]
    assert db.TIMEOUT == 600


def test_the_cli_runs_with_its_output_captured_as_text(bundle):
    cli = Cli()
    db.validate(str(bundle), "dev", "p1", cli)
    [(args, kw)] = cli.calls
    assert args == ["/usr/bin/databricks", "bundle", "validate", "--target", "dev", "--output", "json", "--profile", "p1"]
    assert kw["capture_output"] is True and kw["text"] is True
    assert kw["encoding"] == "utf-8" and kw["errors"] == "replace" and kw["stdin"] is subprocess.DEVNULL
    assert kw["cwd"] == str(bundle)


@pytest.mark.parametrize(("verb", "minutes"), [("validate", "1 minutes"), ("deploy", "10 minutes")])
def test_a_slow_cli_says_how_many_minutes_it_had(bundle, verb, minutes):
    def slow(args, **kw):
        if args[2] == verb:
            raise subprocess.TimeoutExpired(args, kw["timeout"])
        return Done(0, json.dumps({"plan": {"resources.jobs.a": {"action": "update"}}}))

    with pytest.raises(db.BundleError, match=f"within {minutes}"):
        if verb == "validate":
            db.validate(str(bundle), "dev", "p1", slow)
        else:
            seen = db.plan(str(bundle), "dev", "p1", slow)
            db.deploy(str(bundle), "dev", "p1", seen["digest"], run=slow)


# ---- the targets


def test_a_target_is_production_by_its_mode_alone(bundle):
    assert db.production(str(bundle), "live") is True
    assert db.production(str(bundle), "dev") is False
    modes = {t["name"]: t["mode"] for t in db.targets(str(bundle))["targets"]}
    assert modes == {"dev": "development", "live": "production"}


def test_a_target_that_isnt_a_mapping_has_no_mode(tmp_path):
    (tmp_path / "databricks.yml").write_text("targets:\n  odd: just-text\n  dev:\n    default: true\n", encoding="utf-8")
    out = db.targets(str(tmp_path))["targets"]
    assert out == [{"name": "odd", "mode": "", "default": False}, {"name": "dev", "mode": "", "default": True}]


# ---- the commands a bundle could run


def _commands(folder, text):
    (folder / "databricks.yml").write_text(text, encoding="utf-8")
    return db.local_commands(str(folder))


def test_a_command_of_exactly_300_characters_is_shown_whole(bundle):
    cmd = "x" * 300
    out = _commands(bundle, f"experimental:\n  scripts:\n    prebuild: {cmd}\n")
    assert out["commands"] == [f"databricks.yml: experimental.scripts.prebuild: {cmd}"]


def test_a_command_of_301_characters_is_shortened_and_counted(bundle):
    cmd = "y" * 301
    [shown] = _commands(bundle, f"experimental:\n  scripts:\n    prebuild: {cmd}\n")["commands"]
    assert shown == ("databricks.yml: experimental.scripts.prebuild: " + "y" * 300
                     + "… (a long command, 301 characters: open databricks.yml to read it all)")


def test_the_commands_digest_is_32_characters_of_the_raw_values(bundle):
    out = _commands(bundle, "experimental:\n  scripts:\n    prebuild: echo hi\n")
    raw = [["databricks.yml", "experimental.scripts.prebuild", json.dumps("echo hi")]]
    assert out["digest"] == hashlib.sha256(json.dumps(raw).encode()).hexdigest()[:32]


@pytest.mark.parametrize(("first", "second"), [
    ("experimental:\n  scripts:\n    prebuild: {a: 1, b: 2}\n", "experimental:\n  scripts:\n    prebuild: {b: 2, a: 1}\n"),
    ("experimental:\n  python: {a: 1, b: 2}\n", "experimental:\n  python: {b: 2, a: 1}\n"),
    ("python: {a: 1, b: 2}\n", "python: {b: 2, a: 1}\n"),
    ("artifacts:\n  w:\n    type: whl\n    path: .\n", "artifacts:\n  w:\n    path: .\n    type: whl\n"),
])
def test_the_commands_digest_doesnt_depend_on_key_order(bundle, first, second):
    one = _commands(bundle, first)
    two = _commands(bundle, second)
    assert one["digest"] == two["digest"] != ""  # the shown text of a script may follow the file's order


# ---- reading a plan


def _plan(bundle, body):
    return db.plan(str(bundle), "dev", "p1", Cli(plan=Done(0, json.dumps(body))))


def test_a_plan_without_the_plan_wrapper_is_read_as_the_rows(bundle):
    out = _plan(bundle, {"resources.jobs.nightly": {"action": "create"}})
    assert out["ok"] is True and [r["name"] for r in out["rows"]] == ["nightly"]


def test_a_plan_list_can_name_its_resources_by_key(bundle):
    out = _plan(bundle, [{"key": "resources.jobs.nightly", "action": "update"}])
    assert out["ok"] is True and out["rows"] == [{"resource": "resources.jobs.nightly", "kind": "job",
                                                  "name": "nightly", "action": "update"}]


@pytest.mark.parametrize(("key", "kind"), [
    ("resources.jobs.nightly", "job"),
    ("other.jobs.nightly", ""),      # three parts, but not under resources
    ("resources.nightly", ""),       # under resources, but no kind
])
def test_a_rows_kind_comes_only_from_a_resources_key(bundle, key, kind):
    [row] = _plan(bundle, {"plan": {key: {"action": "update"}}})["rows"]
    assert row["kind"] == kind


def test_the_plan_digest_is_16_characters_of_the_sorted_rows(bundle):
    out = _plan(bundle, {"plan": {"resources.jobs.nightly": {"action": "update"}}})
    rows = [{"resource": "resources.jobs.nightly", "kind": "job", "name": "nightly", "action": "update"}]
    assert out["digest"] == hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]
    assert db.digest([{"b": "1", "a": "2"}]) == db.digest([{"a": "2", "b": "1"}])


# ---- what is kept of the CLI's output


def test_a_failure_keeps_the_last_20_lines(bundle):
    err = "\n".join(f"line {i}" for i in range(1, 26))
    out = db.validate(str(bundle), "dev", "p1", Cli(validate=Done(1, "", err)))
    assert out["error"].splitlines() == [f"line {i}" for i in range(6, 26)]


def test_warnings_come_from_the_last_50_lines_only(bundle):
    err = "Warning: too early\n" + "\n".join(f"info {i}" for i in range(49)) + "\nWarning: kept\n"
    out = db.validate(str(bundle), "dev", "p1", Cli(validate=Done(0, "{}", err)))
    assert out == {"ok": True, "warnings": ["Warning: kept"]}


def test_a_deploy_log_comes_from_stdout_when_stderr_is_empty(bundle):
    cli = Cli(plan=Done(0, json.dumps({"plan": {"resources.jobs.a": {"action": "update"}}})),
              deploy=Done(0, "Deployed to dev\n", ""))
    seen = db.plan(str(bundle), "dev", "p1", cli)
    out = db.deploy(str(bundle), "dev", "p1", seen["digest"], run=cli)
    assert out == {"ok": True, "target": "dev", "changed": 1, "log": "Deployed to dev"}
