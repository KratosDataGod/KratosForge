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


# ---------- command placement (workflow.commands; mockup "skin command placement") ----------

UI = STYLES.parent


def _app_notebook_actions() -> dict[str, str]:
    """Every notebook action the app defines (actions.js defineAction), with its definition's text."""
    found = {}
    for js in sorted(UI.glob("*.js")):
        text = js.read_text(encoding="utf-8")
        for m in re.finditer(r'defineAction\(\{ id: "([^"]+)"(.*?)\n(?=\S|\Z)', text, re.S):
            if '"notebook"' in m.group(2) and "contexts:" in m.group(2):
                found[m.group(1)] = m.group(2)
    return found


def test_the_notebook_commands_a_skin_can_place_are_the_apps_own():
    app = _app_notebook_actions()
    assert set(skins.NOTEBOOK_COMMANDS) == set(app) and len(app) == 9
    for cmd, why in skins.NOTEBOOK_COMMANDS.items():
        # A skin may rename only a low or moderate command that stays on this PC.
        fixed = "spendsCapacity: true" in app[cmd] or 'risk: "high"' in app[cmd] or 'where: "fabric"' in app[cmd]
        assert bool(why) == fixed, cmd
    assert skins.NOTEBOOK_COMMANDS["notebook.run_in_fabric"] == "it uses capacity"
    assert skins.NOTEBOOK_COMMANDS["notebook.publish"] == "it writes to Fabric"


def test_the_engine_knows_each_notebook_commands_own_label():
    app = _app_notebook_actions()
    own = {cmd: re.search(r'label: "([^"]+)"', text).group(1) for cmd, text in app.items()}
    assert skins.NOTEBOOK_LABELS == own


def _commands(**notebook):
    return _skin(workflow={"rail": ["home"], "commands": {"notebook": notebook}})


def test_a_skin_places_a_primary_up_to_three_more_and_renames_them():
    m = _commands(primary="notebook.run_all", bar=["notebook.run_cell", "notebook.restart_run_all", "notebook.publish"],
                  labels={"notebook.run_cell": "Run cell", "notebook.restart_run_all": "Clear state and run all"})
    assert skins.parse(skins.dump(m), skins.BASE) == m


def test_a_skin_that_breaks_the_command_rules_is_refused_with_every_reason():
    problems = _problems(_commands(primary="notebook.run_all",
                                   bar=["notebook.export", "notebook.run_cell", "notebook.save", "notebook.pin", "notebook.validate"],
                                   labels={"notebook.run_in_fabric": "Go"}))
    assert "commands.notebook.bar has 5 commands; at most 3" in problems
    assert "commands.notebook.bar names notebook.export, which isn't a notebook command" in problems
    assert "commands.notebook.labels can't rename notebook.run_in_fabric: it uses capacity" in problems


def test_publish_keeps_its_own_name():
    assert "commands.notebook.labels can't rename notebook.publish: it writes to Fabric" in _problems(
        _commands(primary="notebook.run_all", labels={"notebook.publish": "Save"}))


@pytest.mark.parametrize("labels, problem", [
    # Another command's own name, in any case: Save would read as Publish.
    ({"notebook.save": "publish to onelake"}, "commands.notebook.labels notebook.save can't take notebook.publish's name"),
    ({"notebook.run_cell": "Run all"}, "commands.notebook.labels notebook.run_cell can't take notebook.run_all's name"),
    ({"notebook.run_cell": "Go", "notebook.save": "GO"}, "commands.notebook.labels gives notebook.run_cell and notebook.save the same name"),
])
def test_a_label_cant_make_one_command_read_as_another(labels, problem):
    assert problem in _problems(_commands(primary="notebook.run_all", labels=labels))


def test_a_command_may_keep_its_own_name():
    m = _commands(primary="notebook.run_all", labels={"notebook.save": "Save the notebook"})
    assert skins.parse(skins.dump(m), skins.BASE) == m


@pytest.mark.parametrize("notebook, problem", [
    ({"bar": ["notebook.run_cell"]}, "commands.notebook needs a primary command"),
    ({"primary": "notebook.run_all", "bar": ["notebook.run_all"]}, "commands.notebook places a command twice"),
    ({"primary": "notebook.run_all", "labels": {"notebook.save": "x" * 41}}, "commands.notebook.labels notebook.save is longer than 40 characters"),
    ({"primary": "notebook.run_all", "labels": {"notebook.save": "<b>Save</b>"}}, "commands.notebook.labels notebook.save can't hold control characters, < > { } or \\"),
    ({"primary": "notebook.run_all", "hide": ["notebook.publish"]}, "commands.notebook has keys a skin can't hold: hide"),
])
def test_command_placement_rules(notebook, problem):
    assert problem in _problems(_commands(**notebook))


def test_commands_only_for_known_views():
    assert "commands has keys a skin can't hold: sql" in _problems(
        _skin(workflow={"commands": {"sql": {"primary": "notebook.run_all"}}}))


@pytest.mark.parametrize("skin_id, primary, bar", [
    ("fabric", "notebook.run_all", ["notebook.run_cell", "notebook.save"]),
    ("databricks", "notebook.run_all", ["notebook.run_cell", "notebook.restart_run_all", "notebook.publish"]),
    ("snowflake", "notebook.run_all", ["notebook.run_cell", "notebook.save"]),
])
def test_the_built_in_skins_place_the_notebook_commands_as_drawn(skin_id, primary, bar):
    placed = skins.BUILT_IN[skin_id]["workflow"]["commands"]["notebook"]
    assert (placed["primary"], placed["bar"]) == (primary, bar)


def test_the_kratosforge_skin_places_no_commands():
    assert "commands" not in skins.KRATOSFORGE["workflow"]


@pytest.mark.parametrize("skin_id", ["databricks", "snowflake"])
def test_the_databricks_and_snowflake_skins_mark_the_active_page_in_their_accent(skin_id):
    for mode, tokens in skins.BUILT_IN[skin_id]["appearance"]["tokens"].items():
        assert tokens["--rail-active"] == tokens["--accent"], mode


# ---------- look and feel (appearance.style; mockup "skin look and feel") ----------

def _style(skin_id="custom", **style):
    m = _skin(appearance={"style": style})
    m["id"] = skin_id
    return m


def test_a_skin_chooses_its_look_from_fixed_choices():
    m = _style(font="inter", header="none", rail="inline", corners="round", tabs="pill", cells="rounded",
               buttons="accent", tiles="plain", icons="regular")
    assert skins.parse(skins.dump(m), skins.BASE) == m


@pytest.mark.parametrize("style, problem", [
    ({"font": "Comic Sans"}, "appearance style font must be one of segoe-variable, segoe, dm-sans, inter"),
    ({"corners": "12px"}, "appearance style corners must be one of soft, square, round"),
    ({"tabs": {"css": "x"}}, "appearance style tabs must be one of underline, boxed, pill"),
    ({"css": "body{}"}, "appearance style has keys a skin can't hold: css"),
    ({"header": "hero"}, "appearance style header hero is for the KratosForge skin only"),
])
def test_a_look_outside_the_choices_is_refused(style, problem):
    assert problem in _problems(_style(**style))


def test_style_must_be_an_object():
    assert "appearance style must be an object of part: choice" in _problems(_skin(appearance={"style": "dark"}))


def test_the_kratosforge_skin_may_use_the_hero_header():
    m = _style("kratosforge", header="hero")
    assert skins.validate(m, skins.BASE)["appearance"]["style"] == {"header": "hero"}


@pytest.mark.parametrize("skin_id, font, header, rail, tabs", [
    ("fabric", "segoe", "brand", "stacked", "underline"),
    ("databricks", "dm-sans", "light", "inline", "boxed"),
    ("snowflake", "inter", "none", "inline", "pill"),
])
def test_the_platform_skins_look_as_drawn(skin_id, font, header, rail, tabs):
    style = skins.BUILT_IN[skin_id]["appearance"]["style"]
    assert (style["font"], style["header"], style["rail"], style["tabs"]) == (font, header, rail, tabs)


def test_the_kratosforge_skin_keeps_todays_look_until_its_redraw():
    assert "style" not in skins.KRATOSFORGE["appearance"]


def test_the_apps_default_look_is_the_first_choice_of_each_part():
    js = (UI / "skins.js").read_text(encoding="utf-8")
    block = re.search(r"const SKIN_STYLE_DEFAULTS = \{(.*?)\};", js, re.S).group(1)
    app = dict(re.findall(r'(\w+): "([^"]+)"', block))
    assert app == {part: choices[0] for part, choices in skins.STYLE.items()}
