"""Fuzz the files the app reads its settings from: the person's app-settings.json, the organization's
policy.json, any shared JSON file read through jsonfile.read_json, and the headless agent's agent.toml.

Each case is a seeded mutation of a valid file (tests/settings_fuzz.py): torn, not UTF-8, a BOM, NUL
bytes, the wrong top-level type, a field of the wrong type, missing and extra keys, NaN, huge numbers,
deep nesting, duplicate keys, very long strings, or a folder where the file should be. Every case must
be read correctly or refused by name, never raise anything else, and never lose the file silently:
when the file can't be used, its bytes are still on disk (in place or in a ``.corrupt-`` copy)."""

from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path

import pytest

from fabric_desktop import agent_config, app_settings, jsonfile

from tests.settings_fuzz import ODD_VALUES, mutations

CASES = 160


@pytest.fixture
def home(monkeypatch, tmp_path):
    for var in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    for var in app_settings.ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("FABRIC_DESKTOP_BUILTIN_CLIENT_ID", raising=False)
    return tmp_path


def _kept(path: Path, raw: bytes) -> bool:
    """``raw`` is still on disk: the file itself, or a set-aside copy of it."""
    if path.is_file() and path.read_bytes() == raw:
        return True
    return any(p.read_bytes() == raw for p in path.parent.glob(f"{path.name}.corrupt-*"))


def _valid_settings() -> dict:
    return {"channel": "beta", "show_preview": False, "motion_level": 2, "ui_density": "compact",
            "editor_font_size": 14, "editor_key_overrides": {"format": "Mod-Shift-f"}, "sql_page_rows": 500,
            "wh_monitoring_auto": ["queries"], "incident_sla": {"p1": 30}, "client_id": ""}


def _check_loaded(out: dict) -> None:
    assert set(out) == set(app_settings.DEFAULTS)
    for key, entry in out.items():
        value = entry["value"]
        assert entry["source"] in ("policy", "environment", "user", "built_in", "default"), key
        if value == app_settings.DEFAULTS[key] or entry["source"] in ("policy", "default", "built_in"):
            continue
        assert app_settings._typed(key, value) and app_settings._in_range(key, value), (key, value)


@pytest.mark.parametrize("seed", [1, 2])
def test_any_settings_file_reads_as_its_values_or_defaults_and_is_never_lost(home, seed):
    path = app_settings.user_path()
    path.parent.mkdir(parents=True)
    for label, raw in mutations(_valid_settings(), seed, CASES):
        for old in path.parent.glob("app-settings.json*"):
            old.unlink()
        path.write_bytes(raw)
        out = app_settings.load()
        _check_loaded(out)
        assert _kept(path, raw), f"{label}: the settings file was lost on read"
        app_settings.save({"show_preview": True})
        assert app_settings.load()["show_preview"] == {"value": True, "source": "user"}, label
        # A file that wasn't one JSON object was set aside before the save replaced it.
        parsed = _object(raw)
        if parsed is None:
            assert any(p.read_bytes() == raw for p in path.parent.glob("app-settings.json.corrupt-*")), \
                f"{label}: a settings file that couldn't be used was overwritten without a copy"


def _object(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw.decode("utf-8").lstrip("﻿"))
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def test_a_settings_folder_where_the_file_should_be_reads_as_defaults(home):
    app_settings.user_path().mkdir(parents=True)
    _check_loaded(app_settings.load())
    with pytest.raises(OSError):  # the save can't replace a folder, and says so
        app_settings.save({"show_preview": False})
    assert app_settings.user_path().is_dir()


def test_valid_settings_round_trip(home):
    import random
    rnd = random.Random(7)
    choices = {"channel": list(app_settings.CHANNELS), "show_preview": [True, False], "motion_level": [0, 1, 2, 3],
               "editor_font_size": [0, 10, 14, 24], "sql_page_rows": [100, 1000, 5000],
               **{k: list(v) for k, v in app_settings.CHOICES.items() if k in app_settings.USER_KEYS}}
    for _ in range(60):
        changes = {k: rnd.choice(v) for k, v in choices.items() if rnd.random() < 0.5}
        before = app_settings.load()
        changes = {k: v for k, v in changes.items() if before[k]["source"] not in ("policy", "environment")}
        out = app_settings.save(changes)
        for key, value in changes.items():
            want = None if key == "editor_font_size" and value == 0 else value
            got = out[key]["value"]
            assert got == (app_settings.DEFAULTS[key] if want is None else want), (key, value, got)


def _unlink(path: Path) -> None:
    """Remove ``path``, waiting out a brief hold: on Windows a virus scanner or the indexer may still have a
    file the engine just wrote open (WinError 32), as jsonfile.replace waits out for a swap."""
    for attempt in range(20):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


@pytest.mark.parametrize("seed", [3])
def test_any_policy_file_applies_or_is_named_as_broken(home, seed):
    path = app_settings.policy_path()
    path.parent.mkdir(parents=True)
    valid = {"channel": "stable", "show_preview": False, "allowed_tenants": ["contoso.onmicrosoft.com"],
             "telemetry": False, "sql_page_rows": 200}
    for label, raw in mutations(valid, seed, CASES):
        _unlink(app_settings.saved_policy_path())
        path.write_bytes(raw)
        policy, reason = app_settings.read_policy()
        assert isinstance(policy, dict), label
        assert reason is None or (isinstance(reason, str) and str(path) in reason), (label, reason)
        _check_loaded(app_settings.load())
        assert path.read_bytes() == raw, f"{label}: the policy file was changed by reading it"


@pytest.mark.parametrize("seed", [4])
def test_read_json_gives_the_file_or_the_default_and_sets_aside_what_it_cant_read(tmp_path, seed):
    path = tmp_path / "shared.json"
    default = object()
    for label, raw in mutations({"jobs": [{"id": "a", "every": 5}], "version": 2}, seed, CASES):
        for old in tmp_path.glob("shared.json*"):
            old.unlink()
        path.write_bytes(raw)
        got = jsonfile.read_json(path, default)
        if got is default:
            assert any(p.read_bytes() == raw for p in tmp_path.glob("shared.json.corrupt-*")), \
                f"{label}: read_json gave the default but kept no copy"
        else:
            assert path.read_bytes() == raw, label


def test_read_json_of_a_folder_is_refused_by_name(tmp_path):
    (tmp_path / "shared.json").mkdir()
    with pytest.raises(OSError):
        jsonfile.read_json(tmp_path / "shared.json", {})


AGENT = """\
[agent]
name = "agent-1"
project = "{project}"
environments = ["dev", "prod"]
concurrency = 2
home = "{project}/home"
owner = "ops"
workspaces = ["ws1"]
log_level = "info"
poll_seconds = 5
max_queued = 100

[auth]
type = "service_principal"
tenant_id = "t"
client_id = "c"
certificate_path = "{project}/layers.toml"

[retry]
max_attempts = 3
backoff_seconds = 60

[alerts]
teams_webhook_env = "HOOK"

[pipeline]
enabled = true
environments = ["dev"]
entities = ["sales.*"]
allow_backfill = true
claim = "always"
poll_seconds = 30

[incidents]
enabled = true
quiet_hours = "22:00-06:00"
quiet_except = ["critical"]
escalate_to = "oncall"
escalate_after_minutes = 30

[[incidents.route]]
name = "oncall"
webhook_env = "ONCALL"
teams = ["data"]

[shortcut_health]
enabled = true
minutes = 60
environments = ["dev"]

[[schedule]]
id = "nightly"
env = "dev"
daily = ["02:00"]
entities = ["sales.orders"]

[[schedule]]
id = "weekly"
env = "prod"
weekly = {{ days = ["Monday"], times = ["06:00"] }}

[[schedule]]
id = "often"
env = "dev"
every_minutes = 30
shared = true
entities = ["sales.x"]
"""


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return "nan" if value != value else ("inf" if value == float("inf") else repr(value))
    if isinstance(value, (int, str)):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in value.items()) + "}"
    return '""'  # TOML has no null


@pytest.mark.parametrize("seed", [5])
def test_any_agent_config_is_read_or_refused_with_its_problems(tmp_path, seed):
    import random
    project = tmp_path / "proj"
    project.mkdir()
    (project / "layers.toml").write_text("", encoding="utf-8")
    base = AGENT.format(project=project.as_posix())
    assert agent_config.load(_write(tmp_path, base.encode())).environments == ["dev", "prod"]
    lines = base.splitlines()
    rnd = random.Random(seed)
    tables = ["agent", "auth", "retry", "alerts", "pipeline", "incidents", "shortcut_health", "schedule"]
    cases: list[bytes] = [b"", b"\xff\xfe", base.encode()[:40], b"agent = 5", b"schedule = 3", b'schedule = ["x"]',
                          b"[[agent]]\nname='a'", b"[agent]\nenvironments = 'dev'", b"[auth]\ntype = 1",
                          b"a = " + b"[" * 5000 + b"]" * 5000, base.encode() + b"\x00"]
    for _ in range(4 * CASES):
        kind = rnd.random()
        if kind < 0.5:  # a value of another type
            i = rnd.choice([n for n, line in enumerate(lines) if " = " in line])
            key = lines[i].split(" = ")[0]
            changed = [*lines[:i], f"{key} = {_toml(rnd.choice(ODD_VALUES[:-3] + [ODD_VALUES[-1]]))}", *lines[i + 1:]]
            cases.append("\n".join(changed).encode())
        elif kind < 0.8:  # a whole table of another type
            cases.append(f"{rnd.choice(tables)} = {_toml(rnd.choice(ODD_VALUES[:-3]))}\n".encode() + base.encode())
        else:  # a line dropped
            i = rnd.randrange(len(lines))
            cases.append("\n".join(lines[:i] + lines[i + 1:]).encode())
    for raw in cases:
        path = _write(tmp_path, raw)
        try:
            cfg = agent_config.load(path)
        except agent_config.ConfigError as exc:
            assert str(path) in str(exc) and exc.errors, raw[:200]
            continue
        except UnicodeDecodeError:  # not UTF-8: Python names the byte; TOML files are UTF-8
            continue
        assert isinstance(cfg, agent_config.AgentConfig), raw[:200]


def _write(tmp_path: Path, raw: bytes) -> Path:
    path = tmp_path / "agent.toml"
    path.write_bytes(raw)
    return path


def test_the_toml_writer_here_writes_toml():
    for value in ODD_VALUES[:-3]:
        tomllib.loads(f"a = {_toml(value)}")


@pytest.mark.parametrize("seed", [6])
def test_any_app_settings_json_reads_or_is_set_aside_before_a_save(tmp_path, seed):
    """The engine's settings.json (meta_service: the open project, Teams addresses and the rest)."""
    from fabric_desktop.meta_service import MetaMixin

    class Svc(MetaMixin):
        root = tmp_path

    svc, path = Svc(), tmp_path / "settings.json"
    valid = {"meta_project": str(tmp_path / "proj"), "teams_webhook": "", "recent": ["a", "b"],
             "platform_notify": {"routes": [{"name": "ops", "url": ""}]}}
    for label, raw in mutations(valid, seed, CASES):
        for old in tmp_path.glob("settings.json*"):
            old.unlink()
        path.write_bytes(raw)
        assert isinstance(svc._settings(), dict), label
        svc._save_settings(recent=["c"])
        assert svc._settings()["recent"] == ["c"], label
        if _object(raw) is None:
            assert any(p.read_bytes() == raw for p in tmp_path.glob("settings.json.corrupt-*")), \
                f"{label}: a settings file that couldn't be used was replaced without a copy"
