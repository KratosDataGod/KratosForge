"""Platform skin manifests (skins.py): what a skin may hold, and that a file can't carry anything else."""

import json
import re
from pathlib import Path

import pytest

from fabric_desktop import app_settings, skins

STYLES = Path(__file__).resolve().parents[2] / "app" / "ui" / "styles.css"


def _root_tokens() -> dict[str, str]:
    css = STYLES.read_text(encoding="utf-8")
    root = css[: css.index("}")]
    return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", root))


def _skin(**changes):
    m = json.loads(json.dumps(skins.KRATOSFORGE))
    m.update(id="my-skin", name="My skin")
    m.update(changes)
    return m


def _problems(manifest) -> list[str]:
    with pytest.raises(skins.SkinError) as err:
        skins.validate(manifest, skins.BASE)
    return err.value.problems


def test_every_token_a_skin_sets_is_on_styles_root():
    root = _root_tokens()
    assert set(skins.TOKENS) <= set(root), sorted(set(skins.TOKENS) - set(root))


def test_the_kratosforge_skin_is_todays_look():
    root = _root_tokens()
    light = skins.KRATOSFORGE["appearance"]["tokens"]["light"]
    assert {k: v.lower() for k, v in light.items()} == {k: root[k].strip().lower() for k in skins.TOKENS}
    assert skins.built_in()[0]["id"] == "kratosforge"


def test_a_valid_skin_round_trips_through_export_and_import():
    m = _skin(appearance={"color_mode": "light", "tokens": {"light": {"--accent": "#0f6c5c"}}},
              workflow={"rail": ["workspaces", "home"], "labels": {"metadata": "Catalog"}})
    assert skins.parse(skins.dump(m), skins.BASE) == m


@pytest.mark.parametrize("extra", [{"engine": "fabric-spark"}, {"credentials": {}}, {"css": "body{}"}, {"script": "x"}])
def test_unknown_keys_are_refused_not_ignored(extra):
    assert any("keys a skin can't hold" in p for p in _problems(_skin(**extra)))


def test_unknown_keys_inside_sections_are_refused():
    probs = _problems(_skin(appearance={"tokens": {"light": {}}, "font": "x"}, workflow={"rail": [], "run": "local-duckdb"}))
    assert any(p.startswith("appearance has keys") for p in probs)
    assert any(p.startswith("workflow has keys") for p in probs)


@pytest.mark.parametrize("value", ["#fff\n", "#1b3664\n", "red", "url(https://example.com/x.png)", "#fff; background: url(x)", "var(--text)",
                                   "#12345", "expression(alert(1))", "", 7, None])
def test_token_values_must_be_plain_colors(value):
    assert any("must be a color" in p for p in _problems(_skin(appearance={"tokens": {"light": {"--accent": value}}})))


def test_token_names_come_from_the_list():
    probs = _problems(_skin(appearance={"tokens": {"light": {"--sidebar-image": "#ffffff", "color": "#000"}}}))
    assert sum("isn't a token a skin can set" in p for p in probs) == 2


def test_unreadable_text_colors_are_refused():
    probs = _problems(_skin(appearance={"tokens": {"light": {"--text": "#bbbbbb"}}}))
    assert any("--text on --surface has contrast" in p for p in probs)
    # Built on the base: a new surface is checked against the base's text colors too.
    assert any("--muted on --surface" in p for p in _problems(_skin(appearance={"tokens": {"light": {"--surface": "#888888"}}})))


def test_contrast_matches_wcag():
    assert round(skins.contrast("#000", "#fff"), 1) == 21.0
    assert round(skins.contrast("#767676", "#ffffff"), 2) == 4.54


@pytest.mark.parametrize("name", ["<img src=x onerror=alert(1)>", "a{b}", "line\nbreak", "x" * 41, "", 5,
                                  "My \u202eniks", "a\u200bb"])
def test_names_are_short_plain_text(name):
    assert any(p.startswith("name") for p in _problems(_skin(name=name)))


@pytest.mark.parametrize("rail", [["../settings"], ["home", "home"], ["Home Page"], "home", ["x"] * 13, [{"id": "home"}]])
def test_rail_entries_are_page_ids(rail):
    assert any("rail" in p for p in _problems(_skin(workflow={"rail": rail})))


def test_ids_versions_schema_and_targets():
    probs = _problems(_skin(id="My Skin", version="one", schema="fabric-desktop.skin/2",
                            targets=[{"platform": "oracle", "reviewed": "soon", "url": "https://x"}]))
    for start in ("id must", "version must", "schema must", "targets[0] has keys", "targets[0] platform", "targets[0] reviewed"):
        assert any(p.startswith(start) for p in probs), start


def test_ids_and_versions_refuse_a_trailing_newline():
    probs = _problems(_skin(id="my-skin\n", version="1.0.0\n", workflow={"rail": ["home\n"], "labels": {"home\n": "Home"}}))
    for start in ("id must", "version must", "workflow rail entries", "workflow labels key"):
        assert any(p.startswith(start) for p in probs), start
    assert any(p.startswith("version must") for p in _problems(_skin(version="\u0661.0.0")))  # Arabic-Indic digit


def test_labels_refuse_text_direction_marks():
    assert any("invisible formatting" in p for p in _problems(_skin(workflow={"labels": {"home": "\u202eemoH"}})))


def test_a_mode_without_built_in_colors_must_set_both_sides_of_a_pair():
    # No built-in dark colors yet, so a dark --text alone has nothing to be checked against.
    probs = _problems(_skin(appearance={"tokens": {"dark": {"--text": "#ffffff"}}}))
    assert any("dark sets --text, so it must also set --surface" in p for p in probs)
    probs = _problems(_skin(appearance={"tokens": {"dark": {"--text": "#333333", "--surface": "#222222"}}}))
    assert any("--text on --surface has contrast" in p for p in probs)


def test_a_dark_skin_needs_dark_tokens():
    assert any("needs dark tokens" in p for p in _problems(_skin(appearance={"color_mode": "dark"})))


def test_files_are_capped_and_must_be_json():
    with pytest.raises(skins.SkinError, match="larger than 64 KB"):
        skins.parse("{" + " " * skins.MAX_BYTES + "}")
    with pytest.raises(skins.SkinError, match="isn't JSON"):
        skins.parse("not json")
    with pytest.raises(skins.SkinError, match="JSON object"):
        skins.parse("[]")
    with pytest.raises(skins.SkinError, match="nests too deeply"):
        skins.parse("[" * (skins.MAX_BYTES - 1))
    assert skins.parse("﻿" + json.dumps(skins.KRATOSFORGE))["id"] == "kratosforge"


def test_skin_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "no-policy.json")
    loaded = app_settings.load()
    assert (loaded["ui_skin"]["value"], loaded["ui_color_mode"]["value"]) == ("kratosforge", "skin")
    saved = app_settings.save({"ui_skin": "my-skin", "ui_color_mode": "dark"})
    assert (saved["ui_skin"]["value"], saved["ui_color_mode"]["value"]) == ("my-skin", "dark")
    with pytest.raises(ValueError, match="ui_skin must be a skin id"):
        app_settings.save({"ui_skin": "../../evil"})
    with pytest.raises(ValueError, match="ui_skin must be a skin id"):
        app_settings.save({"ui_skin": "kratosforge\n"})
    with pytest.raises(ValueError, match="ui_color_mode must be one of"):
        app_settings.save({"ui_color_mode": "sepia"})


def test_the_fabric_skin_is_light_and_orders_the_rail_without_hiding_a_page():
    fabric = skins.BUILT_IN["fabric"]
    assert fabric["targets"][0]["platform"] == "fabric" and fabric["appearance"]["color_mode"] == "light"
    assert set(fabric["workflow"]["rail"]) == set(skins.KRATOSFORGE["workflow"]["rail"])  # reordered, nothing removed
    # Fabric's own order, which docs/parity.md's workflow cells for Fabric rely on.
    assert fabric["workflow"]["rail"][:5] == ["home", "create", "workspaces", "onelake", "monitor"]
    assert skins.contrast(fabric["appearance"]["tokens"]["light"]["--accent"], "#ffffff") >= skins.CONTRAST_MIN


def test_skins_list_answers_from_the_server_and_is_read_only():
    from fabric_desktop import audit, server
    assert "skins_list" in server.METHODS and "skins_list" in audit.READ_ONLY
    listing = skins.listing()
    assert [s["id"] for s in listing["skins"]] == ["kratosforge", "fabric", "databricks", "snowflake"]
    assert listing["schema"] == skins.SCHEMA


@pytest.mark.parametrize("skin_id, first", [("databricks", ["workspaces", "home", "create", "onelake"]),
                                            ("snowflake", ["home", "onelake", "create", "monitor"])])
def test_the_databricks_and_snowflake_skins_lead_with_their_platforms_pages(skin_id, first):
    skin = skins.BUILT_IN[skin_id]
    assert skins.validate(skin, skins.BASE) is skin and skin in skins.listing()["skins"]
    assert skin["targets"][0]["platform"] == skin_id and skin["appearance"]["color_mode"] == "light"
    rail = skin["workflow"]["rail"]
    assert rail[:len(first)] == first
    assert set(rail) == set(skins.KRATOSFORGE["workflow"]["rail"])  # reordered, nothing removed
    assert rail[-3:] == ["platform", "optimize", "metadata"]  # KratosForge's own pages last (#1847)
    assert "density" not in skin["appearance"]  # not applied yet
    assert set(skin["workflow"].get("labels", {})) <= set(rail)
    assert skins.contrast(skin["appearance"]["tokens"]["light"]["--accent"], "#ffffff") >= skins.CONTRAST_MIN


def test_the_databricks_skins_dark_set_is_complete_and_readable():
    dark = skins.BUILT_IN["databricks"]["appearance"]["tokens"]["dark"]
    for fg, bg in skins.CONTRAST_PAIRS:  # no built-in dark base yet, so the skin sets both sides
        assert fg in dark and bg in dark and skins.contrast(dark[fg], dark[bg]) >= skins.CONTRAST_MIN
