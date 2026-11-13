"""Platform skins: how KratosForge looks and how its pages are arranged, as declarative data (docs/skins.md).

A skin is a JSON manifest, ``"schema": "fabric-desktop.skin/1"``. It holds colors for the design tokens
on ``app/ui/styles.css``'s ``:root`` and an arrangement of the left rail, and nothing else. It never holds
code, CSS text, links, credentials, engine or connection settings, or anything about projects: a skin
can't change where code runs or what it can reach. The same rules check the built-in skins and a file
someone imports, so a shared skin is only ever data.

The four dimensions stay apart (Chris's brief, 2026-11-03): Appearance and Workflow live in a skin;
Platform is the connection profile (connprofiles.py) and Engine the engine registry (engines/registry.py).
Nothing here reads or writes either.

Rules ``validate`` enforces, each with a reason a person can act on:

- Only known keys, at every level. Unknown keys are refused, not ignored, so a newer file can't
  smuggle a setting an older app doesn't check.
- Token names from ``TOKENS`` only; values ``#rgb`` or ``#rrggbb`` only (no ``url()``, ``var()``,
  expressions or text that could end a CSS declaration).
- Text tokens must read on the surfaces they sit on: 4.5:1 for ``--text``, ``--text-2`` and
  ``--muted`` on ``--surface``, and ``--on-accent`` on ``--accent`` (WCAG 2.2 AA).
- Rail entries name pages by id (letters and digits); labels are short plain text. Which ids exist
  is the app's to check, since pages are registered in the UI (features.js).
- Commands (``workflow.commands.notebook``): a primary and at most ``MAX_BAR`` more, by id from
  ``NOTEBOOK_COMMANDS`` only. Whatever a skin doesn't place stays under More, so none can be hidden,
  and a skin may rename only a low or moderate command that stays on this PC (never Publish or Run in
  Fabric), never to another command's name or to a name it gives another command.
- At most ``MAX_BYTES`` of JSON.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from typing import Any

SCHEMA = "fabric-desktop.skin/1"
MAX_BYTES = 64 * 1024
PLATFORMS = ("kratosforge", "fabric", "databricks", "snowflake")
COLOR_MODES = ("light", "dark")
DENSITIES = ("comfortable", "compact")
# appearance.style: how the app is drawn, as fixed choices only (mockup "skin look and feel"). The app
# maps each choice to its own CSS and bundled fonts, so a skin never carries CSS, fonts or images. The
# first choice of each is the KratosForge look today, used when a skin doesn't say. The choices are plain
# platform looks: no art or game styling on controls, the rail, the app bar or type.
STYLE: dict[str, tuple[str, ...]] = {
    "font": ("segoe-variable", "segoe", "dm-sans", "inter"),
    "header": ("light", "brand", "none"),
    "rail": ("stacked", "inline"),
    "corners": ("soft", "square", "round"),
    "tabs": ("underline", "boxed", "pill"),
    "cells": ("bordered", "card", "flat", "rounded"),
    "icons": ("regular", "light", "bold"),
}

# The tokens on styles.css's :root a skin may set (test_skins.py keeps this list inside that file's).
TOKENS = (
    "--accent", "--accent-dark", "--accent-soft", "--accent-soft-2", "--brand-orange", "--brand-orange-soft",
    "--rail-active", "--on-accent",
    "--text", "--text-2", "--muted", "--muted-2",
    "--surface", "--surface-2", "--surface-3", "--surface-3b", "--canvas", "--hover", "--neutral-mark",
    "--border", "--border-soft", "--border-input",
    "--ok", "--ok-2", "--ok-3", "--ok-soft",
    "--warn", "--warn-strong", "--warn-2", "--warn-ink", "--warn-soft", "--warn-soft-2", "--warn-soft-3",
    "--info", "--info-soft",
    "--danger", "--danger-soft", "--danger-soft-2",
)
# (text token, the token it sits on): each pair needs CONTRAST_MIN when the mode sets either one.
CONTRAST_PAIRS = (("--text", "--surface"), ("--text-2", "--surface"), ("--muted", "--surface"),
                  ("--on-accent", "--accent"), ("--danger", "--surface"), ("--accent", "--surface"))
CONTRAST_MIN = 4.5

# Used with fullmatch: "$" would also let a trailing newline through.
_ID = re.compile(r"[a-z][a-z0-9-]{1,39}")
_PAGE = re.compile(r"[a-z][A-Za-z0-9]{0,39}")
_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
_VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}", re.ASCII)
_TEXT_BAD = re.compile(r"[\x00-\x1f\x7f<>{}\\]")

KEYS = {"schema", "id", "name", "version", "description", "targets", "appearance", "workflow"}
TARGET_KEYS = {"platform", "reviewed", "basis"}
APPEARANCE_KEYS = {"color_mode", "density", "tokens", "style"}
WORKFLOW_KEYS = {"rail", "labels", "commands"}
MAX_RAIL = 12  # the app still shows at most 8 main pages (features.js RAIL_LIMIT); the rest go below
# The notebook's commands a skin may place (workflow.commands.notebook), as actions.js defines them
# (test_skins.py keeps this equal to the app's defineAction calls). The value says why a skin can't
# rename one, or is None: a skin may rename only a low or moderate command that stays on this PC.
NOTEBOOK_COMMANDS: dict[str, str | None] = {
    "notebook.pin": None, "notebook.run_all": None, "notebook.run_cell": None,
    "notebook.restart_run_all": None, "notebook.validate": None, "notebook.save": None,
    "notebook.publish": "it writes to Fabric", "notebook.run_in_fabric": "it uses capacity",
    "notebook.go_failed": None,
}
# Each command's own label (test_skins.py keeps these equal to the app's). A skin's label can't be one
# of them, so a renamed command never reads as another one.
NOTEBOOK_LABELS: dict[str, str] = {
    "notebook.pin": "Pin to Home or unpin", "notebook.run_all": "Run all",
    "notebook.run_cell": "Run the selected cell", "notebook.restart_run_all": "Restart the kernel and run all",
    "notebook.validate": "Validate", "notebook.save": "Save the notebook", "notebook.publish": "Publish to OneLake",
    "notebook.run_in_fabric": "Run in Fabric", "notebook.go_failed": "Go to the failed cell",
}
COMMAND_VIEWS = {"notebook": NOTEBOOK_COMMANDS}
COMMAND_LABELS = {"notebook": NOTEBOOK_LABELS}
COMMAND_KEYS = {"primary", "bar", "labels"}
MAX_BAR = 3


class SkinError(ValueError):
    """A manifest that can't be used, with every reason found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("This skin can't be used: " + "; ".join(problems))


def _text(value: Any, where: str, limit: int, problems: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where} must be text")
    elif len(value) > limit:
        problems.append(f"{where} is longer than {limit} characters")
    elif _TEXT_BAD.search(value):
        problems.append(f"{where} can't hold control characters, < > {{ }} or \\")
    elif any(unicodedata.category(c) == "Cf" for c in value):
        problems.append(f"{where} can't hold invisible formatting characters such as text-direction marks")


def _unknown(obj: dict[str, Any], allowed: set[str], where: str, problems: list[str]) -> None:
    extra = sorted(set(obj) - allowed)
    if extra:
        problems.append(f"{where} has keys a skin can't hold: {', '.join(extra)}")


def _luminance(color: str) -> float:
    hexes = color[1:]
    if len(hexes) == 3:
        hexes = "".join(c * 2 for c in hexes)
    channels = [int(hexes[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #rgb / #rrggbb colors, 1 to 21."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _tokens(tokens: Any, where: str, problems: list[str], base: dict[str, str]) -> None:
    if not isinstance(tokens, dict):
        problems.append(f"{where} must be an object of token: color")
        return
    for name, value in tokens.items():
        if name not in TOKENS:
            problems.append(f"{where} names {name!r}, which isn't a token a skin can set")
        elif not isinstance(value, str) or not _COLOR.fullmatch(value):
            problems.append(f"{where} {name} must be a color like #1b3664")
    good = {k: v for k, v in tokens.items() if k in TOKENS and isinstance(v, str) and _COLOR.fullmatch(v)}
    full = {**base, **good}
    for fg, bg in CONTRAST_PAIRS:
        if (fg in good or bg in good) and not (fg in full and bg in full):
            # Nothing to check it against (no built-in colors for this mode yet): the skin sets both.
            problems.append(f"{where} sets {fg if fg in good else bg}, so it must also set {bg if fg in good else fg}")
        elif fg in good or bg in good:
            ratio = contrast(full[fg], full[bg])
            if ratio < CONTRAST_MIN:
                problems.append(f"{where} {fg} on {bg} has contrast {ratio:.2f}:1; it needs {CONTRAST_MIN}:1 to be readable")


def _command_id(cmd: Any, at: str, view: str, known: dict[str, str | None], problems: list[str]) -> bool:
    if isinstance(cmd, str) and cmd in known:
        return True
    problems.append(f"{at} names {cmd}, which isn't a {view} command" if isinstance(cmd, str)
                    else f"{at} must name {view} commands by id")
    return False


def _commands(commands: Any, problems: list[str]) -> None:
    """workflow.commands: per view, a primary command, up to MAX_BAR more and labels for them. Ids come
    from that view's list; whatever a skin doesn't place stays under More, so nothing can be hidden."""
    if not isinstance(commands, dict):
        problems.append("commands must be an object of view: placement")
        return
    _unknown(commands, set(COMMAND_VIEWS), "commands", problems)
    for view, known in COMMAND_VIEWS.items():
        if view not in commands:
            continue
        where = f"commands.{view}"
        placed = commands[view]
        if not isinstance(placed, dict):
            problems.append(f"{where} must be an object with primary, bar and labels")
            continue
        _unknown(placed, COMMAND_KEYS, where, problems)

        primary = placed.get("primary")
        if primary is None:
            problems.append(f"{where} needs a primary command")
        else:
            _command_id(primary, f"{where}.primary", view, known, problems)
        bar = placed.get("bar", [])
        if not isinstance(bar, list):
            problems.append(f"{where}.bar must be a list of {view} commands")
            bar = []
        elif len(bar) > MAX_BAR:
            problems.append(f"{where}.bar has {len(bar)} commands; at most {MAX_BAR}")
        for cmd in bar:
            _command_id(cmd, f"{where}.bar", view, known, problems)
        ids = [primary, *bar]
        if len({str(i) for i in ids}) != len(ids):
            problems.append(f"{where} places a command twice")
        labels = placed.get("labels", {})
        if not isinstance(labels, dict):
            problems.append(f"{where}.labels must be an object of command id: label")
            continue
        own = {text.casefold(): cmd for cmd, text in COMMAND_LABELS[view].items()}
        taken: dict[str, str] = {}
        for cmd, label in labels.items():
            if not _command_id(cmd, f"{where}.labels", view, known, problems):
                continue
            if known[cmd]:
                problems.append(f"{where}.labels can't rename {cmd}: {known[cmd]}")
                continue
            _text(label, f"{where}.labels {cmd}", 40, problems)
            if not isinstance(label, str):
                continue
            key = label.strip().casefold()
            if own.get(key, cmd) != cmd:
                problems.append(f"{where}.labels {cmd} can't take {own[key]}'s name")
            elif key in taken:
                problems.append(f"{where}.labels gives {taken[key]} and {cmd} the same name")
            taken.setdefault(key, cmd)


def _style(style: Any, problems: list[str]) -> None:
    """appearance.style: each key from STYLE, each value one of its fixed choices."""
    if not isinstance(style, dict):
        problems.append("appearance style must be an object of part: choice")
        return
    _unknown(style, set(STYLE), "appearance style", problems)
    for part, choices in STYLE.items():
        if part not in style:
            continue
        value = style[part]
        if value not in choices:
            problems.append(f"appearance style {part} must be one of {', '.join(choices)}")


def validate(manifest: Any, base: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """The manifest, checked; raises SkinError listing every problem. ``base`` holds each color mode's
    token values the skin builds on (the KratosForge skin's), so contrast is checked on what will show."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        raise SkinError(["a skin must be a JSON object"])
    _unknown(manifest, KEYS, "The skin", problems)
    if manifest.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}")
    if not isinstance(manifest.get("id"), str) or not _ID.fullmatch(manifest["id"]):
        problems.append("id must be 2 to 40 lower-case letters, digits or hyphens, starting with a letter")
    _text(manifest.get("name"), "name", 40, problems)
    if not isinstance(manifest.get("version"), str) or not _VERSION.fullmatch(manifest["version"]):
        problems.append("version must look like 1.0.0")
    if "description" in manifest:
        _text(manifest["description"], "description", 200, problems)

    targets = manifest.get("targets", [])
    if not isinstance(targets, list) or len(targets) > 4:
        problems.append("targets must be a list of at most 4")
        targets = []
    for i, t in enumerate(targets):
        where = f"targets[{i}]"
        if not isinstance(t, dict):
            problems.append(f"{where} must be an object")
            continue
        _unknown(t, TARGET_KEYS, where, problems)
        if t.get("platform") not in PLATFORMS:
            problems.append(f"{where} platform must be one of {', '.join(PLATFORMS)}")
        try:
            date.fromisoformat(str(t.get("reviewed")))
        except ValueError:
            problems.append(f"{where} reviewed must be a date like 2026-11-03")
        if "basis" in t:
            _text(t["basis"], f"{where} basis", 200, problems)

    appearance = manifest.get("appearance", {})
    if not isinstance(appearance, dict):
        problems.append("appearance must be an object")
        appearance = {}
    _unknown(appearance, APPEARANCE_KEYS, "appearance", problems)
    if appearance.get("color_mode", "light") not in COLOR_MODES:
        problems.append(f"appearance color_mode must be one of {', '.join(COLOR_MODES)}")
    if appearance.get("density", "comfortable") not in DENSITIES:
        problems.append(f"appearance density must be one of {', '.join(DENSITIES)}")
    if "style" in appearance:
        _style(appearance["style"], problems)
    tokens = appearance.get("tokens", {})
    if not isinstance(tokens, dict):
        problems.append("appearance tokens must be an object with light and dark")
        tokens = {}
    _unknown(tokens, set(COLOR_MODES), "appearance tokens", problems)
    for mode in COLOR_MODES:
        if mode in tokens:
            _tokens(tokens[mode], f"appearance tokens {mode}", problems, (base or {}).get(mode, {}))
    if appearance.get("color_mode") == "dark" and "dark" not in tokens and not (base or {}).get("dark"):
        problems.append("a skin that starts dark needs dark tokens")

    workflow = manifest.get("workflow", {})
    if not isinstance(workflow, dict):
        problems.append("workflow must be an object")
        workflow = {}
    _unknown(workflow, WORKFLOW_KEYS, "workflow", problems)
    rail = workflow.get("rail", [])
    if not isinstance(rail, list) or len(rail) > MAX_RAIL:
        problems.append(f"workflow rail must be a list of at most {MAX_RAIL} page ids")
        rail = []
    bad = [p for p in rail if not isinstance(p, str) or not _PAGE.fullmatch(p)]
    if bad:
        problems.append("workflow rail entries must be page ids like home or workspaces")
    if len(set(map(str, rail))) != len(rail):
        problems.append("workflow rail names a page twice")
    labels = workflow.get("labels", {})
    if not isinstance(labels, dict) or len(labels) > MAX_RAIL:
        problems.append(f"workflow labels must be an object of at most {MAX_RAIL} page id: label")
        labels = {}
    for page, label in labels.items():
        if not _PAGE.fullmatch(str(page)):
            problems.append(f"workflow labels key {page!r} isn't a page id")
        _text(label, f"workflow labels {page}", 24, problems)
    if "commands" in workflow:
        _commands(workflow["commands"], problems)

    if problems:
        raise SkinError(problems)
    return manifest


def parse(text: str | bytes, base: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """A skin file's text, checked. Refuses anything over MAX_BYTES before parsing it."""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    if len(raw) > MAX_BYTES:
        raise SkinError([f"the file is larger than {MAX_BYTES // 1024} KB; a skin is a few KB"])
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SkinError([f"the file isn't JSON ({exc})"]) from None
    except RecursionError:
        raise SkinError(["the file isn't a skin (it nests too deeply)"]) from None
    return validate(data, base)


def dump(manifest: dict[str, Any]) -> str:
    """A checked manifest as the text an export writes."""
    return json.dumps(validate(manifest, BASE), indent=2, sort_keys=False) + "\n"


# The KratosForge skin: today's look, the base every other skin builds on. Its light tokens are the
# values on styles.css's :root, and its dark tokens the values under html[data-color-mode="dark"]
# (test_skins.py keeps both equal).
KRATOSFORGE: dict[str, Any] = {
    "schema": SCHEMA, "id": "kratosforge", "name": "KratosForge", "version": "1.0.0",
    "description": "The KratosForge look: every engine and platform side by side, local runs up front.",
    "targets": [{"platform": "kratosforge", "reviewed": "2026-11-03"}],
    "appearance": {"color_mode": "light", "density": "comfortable", "tokens": {"light": {
        "--accent": "#1b3664", "--accent-dark": "#12264a", "--accent-soft": "#e9eef7", "--accent-soft-2": "#e8edf5",
        "--brand-orange": "#f8991c", "--brand-orange-soft": "#ffb347", "--rail-active": "#f8991c", "--on-accent": "#fff",
        "--text": "#242424", "--text-2": "#424242", "--muted": "#616161", "--muted-2": "#8a8a8a",
        "--surface": "#ffffff", "--surface-2": "#fafafa", "--surface-3": "#f5f5f5", "--surface-3b": "#f7f7f7",
        "--canvas": "#f3f2f1", "--hover": "#f0f0f0", "--neutral-mark": "#8a8886",
        "--border": "#e0e0e0", "--border-soft": "#f0f0f0", "--border-input": "#d1d1d1",
        "--ok": "#0e5c2a", "--ok-2": "#107c10", "--ok-3": "#1f7a4a", "--ok-soft": "#e7f4e8",
        "--warn": "#8a5300", "--warn-strong": "#8a4b00", "--warn-2": "#a35400", "--warn-ink": "#5c3300",
        "--warn-soft": "#fff4e5", "--warn-soft-2": "#fff4d6", "--warn-soft-3": "#fff8e6",
        "--info": "#0f548c", "--info-soft": "#e8f1fb",
        "--danger": "#b10e1c", "--danger-soft": "#fdf3f4", "--danger-soft-2": "#fde7e9",
    }, "dark": {
        # A neutral charcoal, never navy. The accent turns a light blue so links on --surface and the
        # primary button's dark text both pass; the orange stays.
        "--accent": "#8fb4f0", "--accent-dark": "#85acec", "--accent-soft": "#2d3a52", "--accent-soft-2": "#283244",
        "--brand-orange": "#f8991c", "--brand-orange-soft": "#ffb347", "--rail-active": "#f8991c", "--on-accent": "#0b1420",
        "--text": "#e6e6e6", "--text-2": "#c8c8c8", "--muted": "#a6a6a6", "--muted-2": "#8f8f8f",
        "--surface": "#252526", "--surface-2": "#2a2a2b", "--surface-3": "#2f2f30", "--surface-3b": "#2c2c2d",
        "--canvas": "#1c1c1c", "--hover": "#323233", "--neutral-mark": "#8f8f8f",
        "--border": "#3c3c3c", "--border-soft": "#313131", "--border-input": "#4a4a4a",
        "--ok": "#7fd1a0", "--ok-2": "#7fd1a0", "--ok-3": "#7fd1a0", "--ok-soft": "#1f3327",
        "--warn": "#f3b25b", "--warn-strong": "#f3b25b", "--warn-2": "#f5b968", "--warn-ink": "#f8c98a",
        "--warn-soft": "#3a2e1c", "--warn-soft-2": "#3a301a", "--warn-soft-3": "#362d1d",
        "--info": "#8cc4ff", "--info-soft": "#1e2d3f",
        "--danger": "#ff9a9a", "--danger-soft": "#3a2224", "--danger-soft-2": "#44262a",
    }}},
    "workflow": {"rail": ["home", "create", "workspaces", "onelake", "metadata", "platform", "optimize", "monitor"]},
}
BASE: dict[str, dict[str, str]] = {mode: dict(t) for mode, t in KRATOSFORGE["appearance"]["tokens"].items()}
# Fabric-familiar (Preview): light, a teal accent of our own and Fabric's page order (workspaces and
# OneLake before governance). Patterns only: no Microsoft names, logos or colors beyond a familiar feel.
FABRIC: dict[str, Any] = {
    "schema": SCHEMA, "id": "fabric", "name": "Fabric-familiar", "version": "0.1.0",
    "description": "Light, workspace-first: Create, Workspaces and OneLake at the top, as Fabric developers expect.",
    "targets": [{"platform": "fabric", "reviewed": "2026-11-03",
                 "basis": "Microsoft Fabric documentation (workspaces, lakehouse explorer, task flows), November 2026"}],
    "appearance": {"color_mode": "light",
                   "style": {"font": "segoe", "header": "brand", "rail": "stacked", "corners": "soft", "tabs": "underline", "cells": "card", "icons": "light"},
                   "tokens": {"light": {
        "--accent": "#0f6c5c", "--accent-dark": "#0a4d42", "--accent-soft": "#e6f2ef", "--accent-soft-2": "#edf5f3",
        "--canvas": "#f5f5f5", "--border": "#e1e1e1",
    }}},
    "workflow": {"rail": ["home", "create", "workspaces", "onelake", "monitor", "platform", "optimize", "metadata"],
                 # Run all first, then running one cell and saving, as Fabric notebooks put them.
                 "commands": {"notebook": {"primary": "notebook.run_all", "bar": ["notebook.run_cell", "notebook.save"],
                                           "labels": {"notebook.run_cell": "Run cell", "notebook.save": "Save"}}}},
}
# Databricks-familiar (Preview): Workspace first, then Recents, New and Catalog, with Jobs and pipelines,
# as Databricks developers find their way; a blue accent of our own. Its dark set follows the mockup's
# dark board and shows once dark mode lands (skins.js applies light tokens today). Patterns only: no
# Databricks names beyond the page labels developers know, no logos or brand colors.
DATABRICKS: dict[str, Any] = {
    "schema": SCHEMA, "id": "databricks", "name": "Databricks-familiar", "version": "0.1.0",
    "description": "Workspace first, then Recents, New, Catalog and Jobs and pipelines, as Databricks developers expect.",
    "targets": [{"platform": "databricks", "reviewed": "2026-11-05",
                 "basis": "Azure Databricks documentation (workspace browser, Catalog Explorer, Jobs and pipelines), November 2026"}],
    "appearance": {"color_mode": "light",
                   "style": {"font": "dm-sans", "header": "light", "rail": "inline", "corners": "square", "tabs": "boxed", "cells": "flat", "icons": "bold"},
                   "tokens": {
        "light": {"--accent": "#3b63b0", "--accent-dark": "#2f569c", "--accent-soft": "#e8eef8",
                  "--accent-soft-2": "#eef2f9", "--rail-active": "#3b63b0", "--canvas": "#f4f5f7",
                  "--border": "#dde1e6"},
        "dark": {"--accent": "#8ab0f2", "--accent-dark": "#7a9fe0", "--accent-soft": "#2b3038",
                 "--accent-soft-2": "#262b32", "--rail-active": "#8ab0f2", "--on-accent": "#0b1420",
                 "--text": "#e6e8eb", "--text-2": "#c3c8ce", "--muted": "#a9b0b8", "--muted-2": "#8b939c",
                 "--surface": "#23272e", "--surface-2": "#1f2328", "--surface-3": "#2b3038", "--surface-3b": "#262b32",
                 "--canvas": "#1b1e23", "--hover": "#2b3038", "--border": "#3d434b", "--border-soft": "#2e333a",
                 "--border-input": "#4a515a", "--danger": "#ff8f8f"}}},
    "workflow": {"rail": ["workspaces", "home", "create", "onelake", "monitor", "platform", "optimize", "metadata"],
                 "labels": {"workspaces": "Workspace", "home": "Recents", "create": "New", "onelake": "Catalog",
                            "monitor": "Jobs and pipelines", "optimize": "Optimize and cost"},
                 # Run all, run one cell, and clear state then run all, in Databricks words.
                 "commands": {"notebook": {"primary": "notebook.run_all",
                                           "bar": ["notebook.run_cell", "notebook.restart_run_all", "notebook.publish"],
                                           "labels": {"notebook.run_cell": "Run cell",
                                                      "notebook.restart_run_all": "Clear state and run all"}}}},
}
# Snowflake-familiar (Preview): light, Worksheets and Data first, as in Snowflake's web interface; a
# blue accent of our own. Patterns only: no Snowflake logos or brand colors.
SNOWFLAKE: dict[str, Any] = {
    "schema": SCHEMA, "id": "snowflake", "name": "Snowflake-familiar", "version": "0.1.0",
    "description": "Light, worksheet-first: Worksheets, Data and Monitoring at the top, as Snowflake developers expect.",
    "targets": [{"platform": "snowflake", "reviewed": "2026-11-05",
                 "basis": "Snowflake documentation (Snowsight worksheets, data, monitoring), November 2026"}],
    "appearance": {"color_mode": "light",
                   "style": {"font": "inter", "header": "none", "rail": "inline", "corners": "round", "tabs": "pill", "cells": "rounded", "icons": "regular"},
                   "tokens": {"light": {
        "--accent": "#1660a8", "--accent-dark": "#0e4479", "--accent-soft": "#e6f0fa", "--accent-soft-2": "#eef4fb",
        "--rail-active": "#1660a8", "--canvas": "#f4f7fb", "--border": "#dbe4ee", "--border-input": "#c6d3e1",
    }}},
    "workflow": {"rail": ["home", "onelake", "create", "monitor", "workspaces", "platform", "optimize", "metadata"],
                 "labels": {"home": "Worksheets", "onelake": "Data", "create": "Load and create",
                            "monitor": "Monitoring", "optimize": "Optimize and cost"},
                 # Run all, and a plain Run for the selected cell, as a worksheet has.
                 "commands": {"notebook": {"primary": "notebook.run_all", "bar": ["notebook.run_cell", "notebook.save"],
                                           "labels": {"notebook.run_cell": "Run", "notebook.save": "Save"}}}},
}
BUILT_IN: dict[str, dict[str, Any]] = {m["id"]: validate(m, BASE) for m in (KRATOSFORGE, FABRIC, DATABRICKS, SNOWFLAKE)}


def built_in() -> list[dict[str, Any]]:
    """The skins that ship with the app, checked."""
    return list(BUILT_IN.values())


def listing() -> dict[str, Any]:
    """What Settings shows (the skins_list call): every built-in skin, KratosForge first."""
    return {"schema": SCHEMA, "skins": built_in()}
