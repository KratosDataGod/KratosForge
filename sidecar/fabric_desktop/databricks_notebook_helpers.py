"""Which Databricks notebook helpers run in a notebook on this PC: ``dbutils`` and the ``%`` magics.

``databricks_notebook_helpers.json`` lists Databricks' documented ``dbutils`` calls and the magic
commands a Databricks notebook takes, with a note where this PC's answer differs. The status comes
from the code, not the file:

- a ``dbutils`` call is looked up on the ``dbutils`` this PC's kernel builds. One it lacks is
  **missing**; one that raises ``NotImplementedError`` when called is **refused**, with its message.
- a cell magic is read with ``magics.cell``; Scala and R cells are **refused** (they need Fabric's Spark).
- the rest **run**, or **differ** when the file notes how.

``python -m fabric_desktop.databricks_notebook_helpers`` in sidecar/ writes
docs/databricks-notebook-helpers.md, and CI fails when the page is stale.
"""

from __future__ import annotations

import contextlib
import io
import json
import types
from pathlib import Path
from typing import Any, cast

from . import dbutils_local, magics
from .notebookutils_local import Host

DATA = Path(__file__).with_name("databricks_notebook_helpers.json")
DOC = Path(__file__).resolve().parents[2] / "docs" / "databricks-notebook-helpers.md"
GROUPS = ("dbutils", "cells", "line_magics")
_TITLES = {"dbutils": "dbutils", "cells": "Cell magics", "line_magics": "Line magics"}
# What the kernel runs; any other cell language stops with "needs Fabric's Spark" (notebook.Kernel._exec).
RUNS_HERE = {"python", "sql", "markdown", "html", "shell"}
SCALA_R = "Scala and R cells need Fabric's Spark. Switch Runs on to Fabric to run them."


class _NotCalled(Exception):
    """Raised by the stand-in notebookutils: the probe reached a call that would do real work."""


def _stub(*_a: Any, **_kw: Any) -> Any:
    raise _NotCalled


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
    return data


def built() -> types.SimpleNamespace:
    """The ``dbutils`` a notebook gets, over a notebookutils that does nothing."""
    ns = types.SimpleNamespace
    nbu = ns(fs=ns(**{n: _stub for n in ("ls", "head", "put", "cp", "mv", "rm", "mkdirs")}),
             notebook=ns(run=_stub, exit=_stub), credentials=ns(getSecret=_stub, getToken=_stub))
    return dbutils_local.build(Host(), cast(types.ModuleType, nbu))  # the stand-in has what build reads


def public_calls(dbutils: types.SimpleNamespace | None = None) -> set[str]:
    """Every dotted call on ``dbutils`` (``fs.ls``, ``jobs.taskValues.get``), help() left out."""
    found: set[str] = set()

    def walk(obj: Any, prefix: str) -> None:
        for name in dir(obj):
            if name.startswith("_") or name == "help":
                continue
            value = getattr(obj, name)
            if isinstance(value, (types.SimpleNamespace, dbutils_local.Widgets)):
                walk(value, f"{prefix}{name}.")
            elif callable(value) and not (isinstance(obj, dbutils_local.Widgets) and name in _WIDGET_APP):
                found.add(prefix + name)
    walk(dbutils or built(), "")
    return found


# Widgets methods the app calls, not notebooks (they aren't in Databricks' dbutils.widgets).
_WIDGET_APP = {"bind", "describe"}


def _probe(dbutils: types.SimpleNamespace, dotted: str) -> tuple[str, str]:
    obj: Any = dbutils
    for part in dotted.split("."):
        if not hasattr(obj, part):
            return "missing", ""
        obj = getattr(obj, part)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            obj()
    except NotImplementedError as exc:
        return "refused", str(exc)
    except Exception:  # noqa: BLE001, S110  # silent-ok: wrong arguments or the stand-in mean the call exists and runs
        pass
    return "runs", ""


def rows() -> list[dict[str, str]]:
    """Every helper: ``{group, name, status, note}``, status ``runs``, ``differs``, ``refused`` or ``missing``."""
    data = load()
    dbutils = built()
    out = []
    for name, note in data["dbutils"].items():
        status, why = _probe(dbutils, name)
        if status == "runs" and note:
            status = "differs"
        out.append({"group": "dbutils", "name": name, "status": status, "note": why or note})
    for header, note in data["cells"].items():
        language = magics.cell(header + "\n").language
        if language not in RUNS_HERE:
            out.append({"group": "cells", "name": header, "status": "refused", "note": SCALA_R})
        else:
            out.append({"group": "cells", "name": header, "status": "differs" if note else "runs", "note": note})
    for magic, note in data["line_magics"].items():
        out.append({"group": "line_magics", "name": magic, "status": "differs" if note else "runs", "note": note})
    return out


def markdown(found: list[dict[str, str]]) -> str:
    data = load()
    statuses = ("runs", "differs", "refused", "missing")
    lines = [
        "# Databricks notebook helpers on this PC",
        "",
        "<!-- Generated by sidecar/fabric_desktop/databricks_notebook_helpers.py. Don't edit by hand: run",
        "     `python -m fabric_desktop.databricks_notebook_helpers` in sidecar/ and commit the result. -->",
        "",
        "What a Databricks notebook's `dbutils` calls and `%` magic commands do when the notebook runs on",
        "this PC:",
        "",
        "- **Runs**: as in Databricks.",
        "- **Differs**: runs, with the difference noted.",
        "- **Refused**: stops with an error that says why, so nothing is skipped quietly. Run the",
        "  notebook in Fabric, or change the call.",
        "- **Missing**: not on this PC's `dbutils` yet.",
        "",
        "Each `dbutils` status comes from the `dbutils` the kernel builds, and CI regenerates this page,",
        "so it changes when a call is added or refused.",
        "",
        "| Helpers | " + " | ".join(s.capitalize() for s in statuses) + " |",
        "| --- |" + " ---: |" * len(statuses),
    ]
    for group in GROUPS:
        mine = [r for r in found if r["group"] == group]
        lines.append(f"| {_TITLES[group]} | " + " | ".join(str(sum(r["status"] == s for r in mine))
                                                          for s in statuses) + " |")
    lines.append("")
    for group in GROUPS:
        lines += [f"## {_TITLES[group]}", "", "| Helper | Status | Note |", "| --- | --- | --- |"]
        prefix = "dbutils." if group == "dbutils" else ""
        for r in (r for r in found if r["group"] == group):
            lines.append(f"| `{prefix}{r['name']}` | {r['status'].capitalize()} | {r['note'].replace('|', '/')} |")
        lines.append("")
    lines += ["Source: " + data["source"], ""]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - writes the docs page
    from .jsonfile import write_text
    write_text(DOC, markdown(rows()), newline="\n")  # LF, like the committed page
    print(f"Wrote {DOC}")
