"""Settings-model exercises: the ``config_model`` adapter.

The learner's answer is one JSON settings document (the exercise's ``starter``, such as
``settings.json``) that models how something in the service is set up: workspace roles, a Spark
pool, OneLake access roles, a deployment pipeline's rules. Nothing runs. The document is parsed as
data with a size cap and checked against the exercise's rules (validators.config_matches), which
live in ``exercise.json`` and are never sent to the screen; the screen gets only the check kinds.

A pass is SIMULATED: the settings were checked against a model of the service, and nothing was set
in Fabric. ``exercises.problems`` holds every config_model exercise to that class.

Checks (``validation``):

    config_matches    ``rules``: each has ``path`` (``a.b.0.c``) and one test: ``equals``, ``one_of``,
                      ``absent``, ``present``, ``min``/``max``, ``contains``, ``not_contains``, or
                      ``count``/``count_min``/``count_max`` with an optional ``where`` (list entries
                      whose fields all equal it, in any order); ``why`` says why the rule matters.

Grading material: ``grading/<id>/solution.json`` must pass and at least two ``wrong-*.json`` must fail
(``self_test``), the same way CI proves the SQL and pipeline exercises are real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import validators

ADAPTER = "config_model"
CHECKS = ("config_matches",)
MAX_BYTES = 1024 * 1024   # an exercise's settings document is a few KB
TESTS = ("equals", "one_of", "absent", "present", "min", "max", "contains", "not_contains", "count", "count_min",
         "count_max")
_COUNTS = ("count", "count_min", "count_max")
_RULE_KEYS = (*TESTS, "path", "why", "where")


def _no(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": False, "detail": detail}


def parse(text: str, name: str = "settings.json") -> tuple[Any, str | None]:
    """The learner's document, or ``(None, why it can't be read)`` in words."""
    raw = str(text)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        return None, f"{name} is over {MAX_BYTES // (1024 * 1024)} MB; a settings document is much smaller."
    try:
        return json.loads(raw.lstrip("﻿")), None
    except json.JSONDecodeError as exc:
        return None, f"{name} isn't valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}."
    except RecursionError:
        return None, f"{name} is nested too deeply to read."


def check_problems(c: dict[str, Any]) -> list[str]:
    """What's wrong with one check's fields, in words (for exercises.problems)."""
    if c.get("kind") != "config_matches":
        return []
    rules = c.get("rules")
    if not isinstance(rules, list) or not rules:
        return ["check config_matches needs rules"]
    out = []
    for n, rule in enumerate(rules, 1):
        if not isinstance(rule, dict) or not isinstance(rule.get("path"), str) or not rule["path"].strip():
            out.append(f"check config_matches rule {n} needs a path")
            continue
        tests = [k for k in TESTS if k in rule]
        counts = [k for k in tests if k in _COUNTS]
        if not tests or (len(tests) > 1 and not (len(counts) == len(tests) or set(tests) <= {"min", "max"})):
            out.append(f"check config_matches rule {n} ({rule['path']}) needs one test: {', '.join(TESTS)}")
        unknown = [k for k in rule if k not in _RULE_KEYS]
        if unknown:
            out.append(f"check config_matches rule {n} ({rule['path']}) has unknown fields: {', '.join(unknown)}")
        if "where" in rule and (not counts or not isinstance(rule["where"], dict) or not rule["where"]):
            out.append(f"check config_matches rule {n} ({rule['path']}): where is an object of fields, with a count")
        if any(not isinstance(rule[k], int) or isinstance(rule[k], bool) or rule[k] < 0 for k in counts):
            out.append(f"check config_matches rule {n} ({rule['path']}): counts are whole numbers from 0")
        if "one_of" in rule and (not isinstance(rule["one_of"], list) or not rule["one_of"]):
            out.append(f"check config_matches rule {n} ({rule['path']}): one_of is a list")
        if "why" in rule and not (isinstance(rule["why"], str) and rule["why"].strip()):
            out.append(f"check config_matches rule {n} ({rule['path']}): why is a sentence")
    return out


def run_check(doc: Any, c: dict[str, Any]) -> dict[str, Any]:
    if c["kind"] == "config_matches":
        return validators.config_matches(doc, list(c["rules"]))
    return _no(str(c["kind"]), f"A settings exercise can't check {c['kind']}.")


def grade(text: str, ex: dict[str, Any]) -> dict[str, Any]:
    """Every check of ``ex`` on the learner's settings document ``text``. A document that can't be read
    fails every check with the same plain reason."""
    doc, problem = parse(text, str(ex.get("starter") or "settings.json"))
    if problem:
        results = [_no(str(c["kind"]), problem) for c in ex["validation"]]
    else:
        results = [run_check(doc, c) for c in ex["validation"]]
    return {"passed": bool(results) and all(r["passed"] for r in results), "results": results}


def outline(text: str, limit: int = 200) -> dict[str, Any]:
    """What Run shows: every setting in the document as ``path, value`` rows (the first ``limit``), so the
    learner sees what the checks will read. Nothing is checked here."""
    doc, problem = parse(text)
    if problem:
        raise ValueError(problem)
    rows: list[list[Any]] = []

    def walk(here: Any, path: str) -> None:
        if len(rows) > limit:
            return
        if isinstance(here, dict) and here:
            for k, v in here.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(here, list) and here:
            for i, v in enumerate(here):
                walk(v, f"{path}.{i}" if path else str(i))
        else:
            rows.append([path or "(document)", json.dumps(here, ensure_ascii=False)[:200]])

    walk(doc, "")
    return {"columns": ["setting", "value"], "rows": rows[:limit], "more": len(rows) > limit}


def self_test(grading: Path, ex: dict[str, Any]) -> dict[str, Any]:
    """The reference (``grading/<id>/solution.json``) must pass and every ``wrong-*.json`` fail."""
    return {"solution": grade((grading / "solution.json").read_text(encoding="utf-8"), ex),
            "wrong": {p.name: grade(p.read_text(encoding="utf-8"), ex) for p in sorted(grading.glob("wrong-*.json"))}}
