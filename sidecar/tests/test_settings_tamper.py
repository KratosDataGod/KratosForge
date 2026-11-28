"""A setting the organization's policy sets can't be changed from this PC: not through the Settings
RPC, not by editing app-settings.json by hand, and not by bringing an old copy back from a backup. Each
attempt through the RPC is refused by the setting's name and recorded in the audit log. A settings file
that's corrupt or hand-edited loads defaults, says why, and never loosens what the policy sets. Every
setting is checked, so a new one is covered without a line here."""

import json
import logging

import pytest

from fabric_desktop import app_settings, server
from fabric_desktop.service import Service

APP = "11111111-2222-3333-4444-555555555555"
OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.delenv("FABRIC_DESKTOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("FABRIC_DESKTOP_BUILTIN_CLIENT_ID", raising=False)
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    app_settings.user_path().parent.mkdir(parents=True, exist_ok=True)
    return policy


def _candidates(key):
    pool = [True, False, 0, 1, 2, 3, 5, 10, 12, 18, 30, 60, 100, 500, APP, OTHER, "stable", "preview",
            "kratosforge", "another-skin", "same", "expert", "guided", ["ws-one"], ["ws-two"],
            {"format": "Mod-Shift-f"}, {"format": "F3"}, "C:/temp/one", "C:/temp/two"]
    return [*app_settings.CHOICES.get(key, ()), *pool]


def _two_values(dirs, key):
    """Two different values the policy file can set ``key`` to, found by asking load() itself."""
    found = []
    for value in _candidates(key):
        dirs.write_text(json.dumps({key: value}), encoding="utf-8")
        got = app_settings.load()[key]
        if got["source"] == "policy" and got["value"] == value and value not in found:
            found.append(value)
        if len(found) == 2:
            return found
    pytest.fail(f"no two values found for {key}: add one to _candidates")


@pytest.mark.parametrize("key", app_settings.USER_KEYS)
def test_a_setting_the_policy_sets_cant_be_changed_or_edited_around(dirs, key):
    locked, other = _two_values(dirs, key)
    dirs.write_text(json.dumps({key: locked}), encoding="utf-8")
    user = app_settings.user_path()
    user.write_text(json.dumps({"ui_density": "compact"}), encoding="utf-8")
    before = user.read_bytes()
    with pytest.raises(ValueError, match=f"Your organization sets {key}"):
        app_settings.save({key: other})
    assert user.read_bytes() == before  # refused before anything was written
    user.write_text(json.dumps({key: other}), encoding="utf-8")  # edited by hand, or brought back from a backup
    assert app_settings.load()[key] == {"value": locked, "source": "policy"}


@pytest.mark.parametrize("key", sorted(set(app_settings.DEFAULTS) - set(app_settings.USER_KEYS)))
def test_a_policy_only_setting_cant_be_saved_or_set_by_hand(dirs, key):
    with pytest.raises(ValueError, match=f"{key} is not a setting you can change"):
        app_settings.save({key: app_settings.DEFAULTS[key]})
    loose = {**app_settings.DEFAULTS, **{k: not v for k, v in app_settings.RESTRICTED.items() if isinstance(v, bool)}}
    dirs.write_text(json.dumps(app_settings.RESTRICTED), encoding="utf-8")
    strict = app_settings.load()[key]
    app_settings.user_path().write_text(json.dumps({key: loose[key]}), encoding="utf-8")
    assert app_settings.load()[key] == strict


@pytest.mark.parametrize("key", sorted(app_settings.RESTRICTED))
def test_a_restriction_holds_whatever_the_persons_file_says(dirs, key):
    dirs.write_text(json.dumps({key: app_settings.RESTRICTED[key]}), encoding="utf-8")
    loose = (not app_settings.RESTRICTED[key]) if isinstance(app_settings.RESTRICTED[key], bool) else app_settings.DEFAULTS[key]
    app_settings.user_path().write_text(json.dumps({key: loose}), encoding="utf-8")
    assert app_settings.load()[key] == {"value": app_settings.RESTRICTED[key], "source": "policy"}


@pytest.fixture
def svc(dirs, tmp_path):
    return Service(root=tmp_path / "app")


def test_a_refused_change_through_the_settings_rpc_names_the_setting_and_is_audited(dirs, svc):
    dirs.write_text(json.dumps({"telemetry": False, "channel": "stable"}), encoding="utf-8")
    reply = server.handle(svc, json.dumps({"id": 1, "method": "save_app_settings",
                                           "params": {"changes": {"channel": "preview"}}}))
    assert "Your organization sets channel" in reply["error"]
    [line] = [e for e in svc.audit.recent(10) if e["action"] == "save_app_settings"]
    assert line["outcome"] == "error" and "channel" in json.dumps(line)
    assert app_settings.load()["channel"] == {"value": "stable", "source": "policy"}
    reply = server.handle(svc, json.dumps({"id": 2, "method": "save_app_settings",
                                           "params": {"changes": {"allow_fabric_runs": True}}}))
    assert "allow_fabric_runs is not a setting you can change" in reply["error"]


@pytest.mark.parametrize("text", ['{"channel": "preview", "telemetry": tr', "[1, 2]", "\ufeff\x00\x01garbage",
                                  '"just a string"', ""])
def test_a_corrupt_settings_file_loads_defaults_says_why_and_keeps_the_policy(dirs, text, caplog):
    dirs.write_text(json.dumps({"telemetry": False, "allow_fabric_runs": False}), encoding="utf-8")
    user = app_settings.user_path()
    user.write_text(text, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        loaded = app_settings.load()
    assert loaded["telemetry"] == {"value": False, "source": "policy"}
    assert loaded["allow_fabric_runs"] == {"value": False, "source": "policy"}
    assert all(v["source"] in ("policy", "default") for v in loaded.values())
    if text.strip() and not text.startswith(("[", '"')):  # unreadable as JSON: set aside, by name
        assert not user.exists() and list(user.parent.glob("app-settings.json.corrupt-*"))
        assert "app-settings.json could not be read" in caplog.text


def test_a_hand_edited_value_out_of_range_or_of_the_wrong_kind_is_ignored(dirs):
    app_settings.user_path().write_text(json.dumps({
        "cell_time_limit_minutes": -5, "kernel_memory_limit_gb": "lots", "channel": "nightly", "telemetry": "yes",
        "client_id": "not-a-guid", "motion_level": 99, "editor_key_overrides": {"format": "F3; rm"}}), encoding="utf-8")
    loaded = app_settings.load()
    for key in ("cell_time_limit_minutes", "kernel_memory_limit_gb", "channel", "telemetry", "motion_level",
                "editor_key_overrides"):
        assert loaded[key] == {"value": app_settings.DEFAULTS[key], "source": "default"}, key
    assert loaded["client_id"]["source"] != "user"


def test_a_hand_edited_copy_of_the_last_good_policy_changes_nothing_while_the_policy_reads(dirs):
    dirs.write_text(json.dumps({"allow_fabric_runs": False, "telemetry": False}), encoding="utf-8")
    app_settings.load()  # keeps the copy
    copy = app_settings.saved_policy_path()
    copy.write_text(json.dumps({"allow_fabric_runs": True, "telemetry": True}), encoding="utf-8")
    loaded = app_settings.load()
    assert loaded["allow_fabric_runs"] == {"value": False, "source": "policy"}
    assert json.loads(copy.read_text(encoding="utf-8")) == {"allow_fabric_runs": False, "telemetry": False}


def test_a_hand_edited_client_id_is_ignored_and_the_next_source_applies(dirs):
    app_settings.user_path().write_text(json.dumps({"client_id": "not-a-guid"}), encoding="utf-8")
    assert app_settings.load()["client_id"]["value"] != "not-a-guid"
    app_settings.user_path().write_text(json.dumps({"client_id": APP}), encoding="utf-8")
    assert app_settings.load()["client_id"] == {"value": APP, "source": "user"}


def test_a_mistyped_policy_client_id_still_locks_it_and_is_named(dirs, monkeypatch):
    monkeypatch.setenv("FABRIC_DESKTOP_CLIENT_ID", OTHER)
    dirs.write_text(json.dumps({"client_id": "not-a-guid"}), encoding="utf-8")
    app_settings.user_path().write_text(json.dumps({"client_id": APP}), encoding="utf-8")
    assert app_settings.load()["client_id"] == {"value": "not-a-guid", "source": "policy"}
    assert any("client_id isn't an app (client) ID" in p for p in app_settings.policy_problems())
    with pytest.raises(ValueError, match="Your organization sets client_id"):
        app_settings.save({"client_id": APP})
