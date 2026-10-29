"""Learn content (LX-02): the modules and labs that ship inside the app, read and checked.

Each module or lab is a folder under ``content/`` named by its id (``LM-01``, ``LAB-02``) holding
``module.json`` (schema ``kratosforge/learn-module@1``), a ``lesson.md`` and the SQL or Python files its
exercises name. ``content/paths.json`` groups modules into learning paths. Content is data: nothing
here runs code, reads the network or writes anything, and only files inside a module's own folder can
be named. ``problems()`` lists everything wrong with the bundled content; CI fails while it has any
(tests/test_learn_content.py), so a broken lesson never ships.

module.json::

    id, kind ("module" | "lab"), title, path (a path id), level, minutes, objectives [str],
    prerequisites [ids], platforms [str], engines [ENGINES], lesson ("lesson.md"),
    datasets [dataset ids], size? (datasets.SIZES), exercises [{id, prompt, engine, setup?, starter?, solution, hints? [str],
    checks [{kind, ...}], expected?}], rules [best-practice rule ids], docs [docs page or https link], version (int),
    status ("draft" | "live" | "retired"), reviewed ("YYYY-MM-DD", required when live)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .datasets import DATASETS as _DATASETS
from .datasets import SIZES as _SIZES

SCHEMA = "kratosforge/learn-module@1"
ROOT = Path(__file__).resolve().parent / "content"

KINDS = ("module", "lab")
LEVELS = ("beginner", "intermediate", "advanced")
STATUSES = ("draft", "live", "retired")
ENGINES = ("duckdb", "polars", "pandas", "delta", "kernel", "pipeline", "model")
# What a check can ask for; the lab runner (LX-03) carries them out.
CHECK_KINDS = ("result_matches", "table_shape", "integrity", "no_findings", "faster_than", "defects_found")
# Datasets the lab runner generates on this PC (datasets.py).
DATASETS = tuple(_DATASETS)
# Outside links a lesson may give: the platforms' own documentation.
DOC_HOSTS = ("learn.microsoft.com", "docs.databricks.com", "docs.snowflake.com", "duckdb.org", "docs.pola.rs",
             "pandas.pydata.org", "delta.io", "spark.apache.org")

_ID = re.compile(r"^(LM|LAB)-\d{2}$")
_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")
_DOC_PAGE = re.compile(r"^[a-z0-9][a-z0-9/_-]*\.md(#[a-z0-9-]+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HTML = re.compile(r"<[A-Za-z!/]")
# What each check needs from the module (labs.py passes it on).
_CHECK_NEEDS = {"no_findings": ("rules",), "faster_than": ("baseline",), "table_shape": ("table",),
                "integrity": ("child", "column", "parent")}
_REQUIRED = ("id", "kind", "title", "path", "level", "minutes", "objectives", "engines", "lesson", "exercises",
             "version", "status")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def paths(root: Path = ROOT) -> list[dict[str, Any]]:
    """The learning paths, in order: [{id, title, modules: [ids]}]."""
    return list(_read_json(root / "paths.json"))


def modules(root: Path = ROOT, include_drafts: bool = True) -> list[dict[str, Any]]:
    """Every module and lab, sorted by id. Retired ones are left out; drafts only when asked for."""
    out = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and _ID.match(p.name)):
        m = _read_json(folder / "module.json")
        if m.get("status") == "retired" or (m.get("status") == "draft" and not include_drafts):
            continue
        out.append(m)
    return out


def is_id(text: str) -> bool:
    return bool(_ID.match(str(text)))


def get(module_id: str, root: Path = ROOT) -> dict[str, Any]:
    """One module or lab by id."""
    if not is_id(module_id) or not (root / module_id / "module.json").is_file():
        raise ValueError(f"Not a learning module: {module_id}")
    return dict(_read_json(root / module_id / "module.json"))


def file_text(module_id: str, name: str, root: Path = ROOT) -> str:
    """One of a module's own files. Only a plain file name inside the module's folder is accepted."""
    if not _ID.match(module_id) or not _FILE.match(name) or ".." in name:
        raise ValueError(f"Not a learning file: {module_id}/{name}")
    return (root / module_id / name).read_text(encoding="utf-8")


def _markdown_problems(where: str, text: str) -> list[str]:
    """Lesson Markdown may not carry raw HTML outside code: the app shows it as text, never as markup."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and _HTML.search(re.sub(r"`[^`]*`", "", line)):
            out.append(f"{where} line {n}: raw HTML isn't allowed in a lesson; put it in a code block")
    return out


def _module_problems(folder: Path, m: dict[str, Any], rule_ids: set[str]) -> list[str]:
    mid = folder.name
    out = [f"{mid}: module.json needs {f}" for f in _REQUIRED if m.get(f) in (None, "", [])]
    if out:
        return out
    if m.get("schema") != SCHEMA:
        out.append(f"{mid}: schema is {SCHEMA}")
    if m["id"] != mid:
        out.append(f"{mid}: id {m['id']} doesn't match its folder")
    for key, allowed in (("kind", KINDS), ("level", LEVELS), ("status", STATUSES)):
        if m[key] not in allowed:
            out.append(f"{mid}: {key} is one of {', '.join(allowed)}")
    if not isinstance(m["minutes"], int) or not 1 <= m["minutes"] <= 120:
        out.append(f"{mid}: minutes is a whole number from 1 to 120")
    if "size" in m and m["size"] not in _SIZES:
        out.append(f"{mid}: size is one of {', '.join(_SIZES)}")
    if not isinstance(m["version"], int) or m["version"] < 1:
        out.append(f"{mid}: version is a whole number from 1")
    if m["status"] == "live" and not _DATE.match(str(m.get("reviewed") or "")):
        out.append(f"{mid}: a live module needs the date it was last reviewed (reviewed: YYYY-MM-DD)")
    out += [f"{mid}: unknown engine {e}" for e in m["engines"] if e not in ENGINES]
    out += [f"{mid}: unknown dataset {d}" for d in m.get("datasets") or [] if d not in DATASETS]
    out += [f"{mid}: unknown best-practice rule {r}" for r in m.get("rules") or [] if r not in rule_ids]
    for d in m.get("docs") or []:
        host = re.match(r"^https://([^/]+)/", str(d))
        if not (_DOC_PAGE.match(str(d)) or (host and host.group(1) in DOC_HOSTS)):
            out.append(f"{mid}: docs link {d} is neither a docs page nor a platform's documentation")
    files = [m["lesson"]]
    ex_ids = set()
    for ex in m["exercises"]:
        where = f"{mid} exercise {ex.get('id') or '?'}"
        if not ex.get("id") or ex["id"] in ex_ids:
            out.append(f"{where}: each exercise needs its own id")
        ex_ids.add(ex.get("id"))
        if not ex.get("prompt") or not ex.get("solution"):
            out.append(f"{where}: needs a prompt and a solution")
        if ex.get("engine") not in m["engines"]:
            out.append(f"{where}: its engine must be one of the module's engines")
        checks = ex.get("checks") or []
        if not checks:
            out.append(f"{where}: needs at least one check")
        out += [f"{where}: unknown check {c.get('kind')}" for c in checks if c.get("kind") not in CHECK_KINDS]
        out += [f"{where}: no_findings names unknown rule {r}" for c in checks if c.get("kind") == "no_findings"
                for r in c.get("rules") or [] if r not in rule_ids]
        if not all(isinstance(x, str) and x for x in ex.get("hints") or []):
            out.append(f"{where}: hints are a list of sentences")
        for c in checks:
            missing = [k for k in _CHECK_NEEDS.get(c.get("kind"), ()) if not c.get(k)]
            if missing:
                out.append(f"{where}: check {c.get('kind')} needs {', '.join(missing)}")
            if c.get("kind") == "faster_than" and c.get("baseline"):
                files.append(c["baseline"])
        files += [ex[k] for k in ("setup", "starter", "solution") if ex.get(k)]
    for name in files:
        if not isinstance(name, str) or not _FILE.match(name) or ".." in name:
            out.append(f"{mid}: {name!r} isn't a plain file name in the module's folder")
        elif not (folder / name).is_file():
            out.append(f"{mid}: {name} is missing")
    if (folder / m["lesson"]).is_file():
        out += _markdown_problems(f"{mid}/{m['lesson']}", (folder / m["lesson"]).read_text(encoding="utf-8"))
    return out


def _cycles(by_id: dict[str, dict[str, Any]]) -> list[str]:
    out, done = [], set()

    def visit(mid: str, trail: tuple[str, ...]) -> None:
        if mid in trail:
            out.append(f"{mid}: its prerequisites go round in a circle ({' > '.join((*trail, mid))})")
            return
        if mid in done or mid not in by_id:
            return
        for p in by_id[mid].get("prerequisites") or []:
            visit(p, (*trail, mid))
        done.add(mid)

    for mid in by_id:
        visit(mid, ())
    return out


def problems(root: Path = ROOT, rule_ids: set[str] | None = None) -> list[str]:
    """Everything wrong with the content under ``root``, in words; empty when it can ship."""
    if rule_ids is None:
        rule_ids = known_rule_ids()
    out: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if not _ID.match(folder.name):
            out.append(f"{folder.name}: a content folder is named by its id, like LM-01 or LAB-02")
            continue
        try:
            m = _read_json(folder / "module.json")
        except (OSError, ValueError) as exc:
            out.append(f"{folder.name}: module.json can't be read ({exc})")
            continue
        by_id[folder.name] = m
        out += _module_problems(folder, m, rule_ids)
    for mid, m in by_id.items():
        out += [f"{mid}: unknown prerequisite {p}" for p in m.get("prerequisites") or [] if p not in by_id]
    out += _cycles(by_id)
    try:
        path_list = paths(root)
    except (OSError, ValueError) as exc:
        return out + [f"paths.json can't be read ({exc})"]
    path_ids = {p.get("id") for p in path_list}
    for p in path_list:
        out += [f"path {p.get('id')}: unknown module {x}" for x in p.get("modules") or [] if x not in by_id]
    out += [f"{mid}: unknown path {m.get('path')}" for mid, m in by_id.items() if m.get("path") not in path_ids]
    return out


def known_rule_ids() -> set[str]:
    """The best-practice rule ids a lesson can name: the notebook, SQL and semantic model rules."""
    from .. import model_practices, notebook_practices
    from ..sqlcompat import practices as sql_practices

    return {r["id"] for mod in (notebook_practices, sql_practices, model_practices) for r in mod.rules()}
