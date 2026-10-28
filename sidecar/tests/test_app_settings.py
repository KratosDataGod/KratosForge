"""App settings: policy over environment over the person's choice over the built-in app ID."""

import json

import pytest

from fabric_desktop import app_settings
from fabric_desktop.auth import MICROSOFT_CLIENT_ID
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
    return policy


def test_defaults(dirs):
    s = app_settings.load()
    assert s["client_id"] == {"value": MICROSOFT_CLIENT_ID, "source": "default"}  # no app registration
    assert s["channel"] == {"value": "stable", "source": "default"}
    assert s["show_preview"]["value"] is True


def test_client_id_order(dirs, monkeypatch):
    monkeypatch.setenv("FABRIC_DESKTOP_BUILTIN_CLIENT_ID", APP)
    assert app_settings.load()["client_id"] == {"value": APP, "source": "built_in"}
    app_settings.save({"client_id": OTHER})
    assert app_settings.load()["client_id"] == {"value": OTHER, "source": "user"}
    monkeypatch.setenv("FABRIC_DESKTOP_CLIENT_ID", APP)
    assert app_settings.load()["client_id"]["source"] == "environment"
    dirs.write_text(json.dumps({"client_id": OTHER}))
    assert app_settings.load()["client_id"] == {"value": OTHER, "source": "policy"}


def test_policy_locks_a_setting(dirs):
    dirs.write_text(json.dumps({"channel": "preview", "support_url": "mailto:help@contoso.com"}))
    s = app_settings.load()
    assert s["channel"] == {"value": "preview", "source": "policy"}
    assert s["support_url"]["value"] == "mailto:help@contoso.com"
    with pytest.raises(ValueError, match="organization"):
        app_settings.save({"channel": "stable"})


def test_save_checks_values(dirs):
    with pytest.raises(ValueError, match="isn't an app"):
        app_settings.save({"client_id": "not-a-guid"})
    with pytest.raises(ValueError, match="channel"):
        app_settings.save({"channel": "nightly"})
    with pytest.raises(ValueError, match="can change"):
        app_settings.save({"support_url": "https://x"})
    assert app_settings.save({"channel": "preview", "show_preview": False})["show_preview"]["value"] is False
    assert app_settings.save({"client_id": ""})["client_id"]["source"] == "default"


def test_forge_animations_default_on_saved_and_lockable(dirs):
    assert app_settings.load()["forge_animations"] == {"value": True, "source": "default"}
    assert app_settings.save({"forge_animations": False})["forge_animations"] == {"value": False, "source": "user"}
    assert app_settings.load()["forge_animations"]["value"] is False  # kept for the next start
    dirs.write_text(json.dumps({"forge_animations": False}))
    assert app_settings.load()["forge_animations"]["source"] == "policy"
    with pytest.raises(ValueError, match="organization"):
        app_settings.save({"forge_animations": True})


def test_motion_level_defaults_to_refined_saved_checked_and_lockable(dirs):
    assert app_settings.load()["motion_level"] == {"value": 2, "source": "default"}
    assert app_settings.save({"motion_level": 3})["motion_level"] == {"value": 3, "source": "user"}
    for bad in (4, -1, True, "2", 2.5):
        with pytest.raises(ValueError, match="motion_level"):
            app_settings.save({"motion_level": bad})
    assert app_settings.load()["motion_level"]["value"] == 3  # kept for the next start
    assert app_settings.save({"motion_wolf": False})["motion_wolf"] == {"value": False, "source": "user"}
    assert app_settings.load()["motion_follow_system"] == {"value": True, "source": "default"}
    assert app_settings.load()["motion_achievements"] == {"value": False, "source": "default"}  # turned on, not off
    dirs.write_text(json.dumps({"motion_level": 0, "motion_sparks": False}))
    loaded = app_settings.load()
    assert loaded["motion_level"] == {"value": 0, "source": "policy"}
    assert loaded["motion_sparks"] == {"value": False, "source": "policy"}
    with pytest.raises(ValueError, match="organization"):
        app_settings.save({"motion_level": 2})
    dirs.write_text(json.dumps({"motion_level": 9}))
    assert app_settings.load()["motion_level"] == {"value": 3, "source": "user"}  # out of range: ignored
    assert "motion_level must be between 0 and 3" in app_settings.policy_problems()


def test_layout_choices_default_to_help_on_request_and_take_only_their_choices(dirs):
    loaded = app_settings.load()
    assert [loaded[k] for k in ("ui_explanations", "ui_preview_marks", "ui_density")] == [
        {"value": "on_request", "source": "default"}, {"value": "tags", "source": "default"},
        {"value": "comfortable", "source": "default"}]
    saved = app_settings.save({"ui_explanations": "beside", "ui_preview_marks": "dots", "ui_density": "compact"})
    assert [saved[k]["value"] for k in ("ui_explanations", "ui_preview_marks", "ui_density")] == ["beside", "dots", "compact"]
    with pytest.raises(ValueError, match="beside, on_request"):
        app_settings.save({"ui_explanations": "hidden"})
    dirs.write_text(json.dumps({"ui_density": "tiny"}))
    assert "ui_density must be one of comfortable, compact" in app_settings.policy_problems()


def test_forge_animations_off_still_means_motion_off(dirs):
    app_settings.save({"forge_animations": False})
    assert app_settings.load()["motion_level"] == {"value": 0, "source": "user"}
    app_settings.save({"motion_level": 1})  # a level chosen since wins
    assert app_settings.load()["motion_level"] == {"value": 1, "source": "user"}
    dirs.write_text(json.dumps({"forge_animations": False}))
    # An old policy file still locks motion off, whatever level the person chose.
    assert app_settings.load()["motion_level"] == {"value": 0, "source": "policy"}
    with pytest.raises(ValueError, match="organization"):
        app_settings.save({"motion_level": 2})
    dirs.write_text(json.dumps({"forge_animations": False, "motion_level": 1}))
    assert app_settings.load()["motion_level"] == {"value": 1, "source": "policy"}  # a policy level wins over its own old switch


def test_service_uses_the_current_app_id(dirs, tmp_path):
    svc = Service(root=tmp_path / "app")
    assert svc.auth.client_id == MICROSOFT_CLIENT_ID and svc.auth.microsoft
    svc.save_app_settings({"client_id": APP})
    assert svc.auth.client_id == APP
    svc.save_app_settings({"client_id": OTHER})
    assert svc.auth.client_id == OTHER  # a new ID means a new sign-in
    assert not svc.auth.microsoft
    assert svc.sign_out() == {"user": None}


def test_leaves_the_sidecars_own_settings_alone(dirs, tmp_path):
    own = tmp_path / "la" / "FabricDesktop" / "settings.json"
    own.parent.mkdir(parents=True)
    own.write_text(json.dumps({"project": "C:/metadata", "teams_webhook": "https://x"}))
    app_settings.save({"channel": "preview", "show_preview": False})
    assert json.loads(own.read_text()) == {"project": "C:/metadata", "teams_webhook": "https://x"}


def test_save_refuses_a_client_id_the_environment_overrides(dirs, monkeypatch):
    """UX-006: no "Saved" for a value that wouldn't apply; the file stays as it was."""
    app_settings.save({"channel": "preview"})
    before = app_settings.user_path().read_text(encoding="utf-8")
    monkeypatch.setenv("FABRIC_DESKTOP_CLIENT_ID", APP)
    for value in (OTHER, ""):
        with pytest.raises(ValueError, match="FABRIC_DESKTOP_CLIENT_ID environment variable sets client_id"):
            app_settings.save({"client_id": value})
    assert app_settings.user_path().read_text(encoding="utf-8") == before
    assert app_settings.load()["client_id"] == {"value": APP, "source": "environment"}
    assert app_settings.save({"channel": "stable"})["channel"]["value"] == "stable"  # other settings still save


def test_every_policy_key_is_documented_for_it():
    """PB-061: IT can only use a key the docs name, and a key the docs name must be one the app reads.
    Every key the policy file accepts is in backticks in a user doc (product, quality and hardening notes
    don't count), and every key in the policy table of azure-setup.md is one the app accepts."""
    import re
    from pathlib import Path
    docs = Path(__file__).resolve().parents[2] / "docs"
    text = "\n".join(p.read_text(encoding="utf-8") for p in docs.rglob("*.md")
                     if p.relative_to(docs).parts[0] not in ("product", "quality", "hardening"))
    missing = sorted(k for k in app_settings.POLICY_KEYS if f"`{k}`" not in text)
    assert not missing, f"Policy keys no user doc names; add them to docs/azure-setup.md: {missing}"
    section = (docs / "azure-setup.md").read_text(encoding="utf-8").split("## 4. ", 1)[1].split("\n## ", 1)[0]
    rows = {m for line in section.splitlines() if line.startswith("| `")
            for m in re.findall(r"`([a-z_]+)`", line.split("|")[1])}
    assert rows and not rows - set(app_settings.POLICY_KEYS), sorted(rows - set(app_settings.POLICY_KEYS))
