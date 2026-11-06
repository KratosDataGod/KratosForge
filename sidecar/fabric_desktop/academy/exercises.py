"""Academy exercises (ACD-002) and verification classes (ACD-003).

An exercise is a folder ``packs/<pack>/exercises/<id>/`` holding ``exercise.json`` (schema
``kratosforge/academy-exercise@1``), an optional ``lesson.md`` and the files the learner starts from.
Its grading material (the reference solution and the planted wrong answers it must fail) lives apart
in ``packs/<pack>/grading/<id>/`` and is never copied to the learner's workspace or sent to the
screen before the learner asks for the explanation. An exercise built on a Learn lab names it with
``uses`` (``"LAB-04/rebuild"``): the lab's own data, starter and reference solution are used.

Every exercise carries one verification class and says, in plain sentences, what a pass proves
(``verifies``) and what it doesn't (``does_not_verify``), with the engine that ran it
(``engine_label``). ``completes()`` decides whether a pass completes an objective: an objective that
needs Fabric is complete only with FABRIC VERIFIED evidence; any other needs evidence at least as
strong as the best this PC can give for it.

Content is data. ``problems()`` checks the bundled exercises without running anything. ``self_test()``
runs one exercise's reference solution and planted wrong answers on an in-memory DuckDB with no file
or network access, the way CI proves an exercise is real (tests/test_academy_exercises.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import duckdb

from ..learn import checks
from ..learn import content as learn_content
from . import configlab, gitlab, pipelab, pylab, quizlab, registry, sqlprojlab, validators

SCHEMA = "kratosforge/academy-exercise@1"
MODES = ("guided", "challenge", "troubleshoot")
STATUSES = ("draft", "live", "retired")
ADAPTERS = ("duckdb_lab", "dbsql_lab", "python_lab", "pyspark_lab", "delta_lab", "pipeline_lab", "config_model", "git_lab",
            "sqlproj_lab", "quiz", *(p["adapter"] for p in registry.PLATFORMS.values()))
# Adapters that run the learner's artifact, so a pass can be LOCAL VERIFIED.
EXECUTING = ("duckdb_lab", "dbsql_lab", "python_lab", "pyspark_lab", "delta_lab", "pipeline_lab", "git_lab", "sqlproj_lab")
# What each adapter can check today. Adapters with nothing here aren't built yet (ACD-004, 011, 012, 014).
ADAPTER_CHECKS: dict[str, tuple[str, ...]] = {"duckdb_lab": ("result_matches", "table_shape", "integrity", "unchanged",
                                                             "no_findings"),
                                              "dbsql_lab": ("result_matches", "table_shape", "integrity", "idempotent"),
                                              "pyspark_lab": ("result_matches", "table_shape", "idempotent"),
                                              **{a: pylab.CHECKS for a in pylab.ADAPTERS},
                                              pipelab.ADAPTER: pipelab.CHECKS, configlab.ADAPTER: configlab.CHECKS,
                                              quizlab.ADAPTER: quizlab.CHECKS, gitlab.ADAPTER: gitlab.CHECKS,
                                              sqlprojlab.ADAPTER: sqlprojlab.CHECKS}
# Adapters that check a model of the service, so a pass is SIMULATED and never more.
MODEL_ADAPTERS = {configlab.ADAPTER: "simulated", quizlab.ADAPTER: "knowledge_only"}
# Adapters whose answers are SQL files checked in DuckDB (dbsql_lab after translation).
SQL_ADAPTERS = ("duckdb_lab", "dbsql_lab")
# The reference solution's file and the planted wrong answers' pattern, by adapter (SQL unless named).
SOLUTION_FILE = {"pyspark_lab": "solution.py"}
WRONG_GLOB = {"pyspark_lab": "wrong-*.py"}
LABELS = {"local_verified": "LOCAL VERIFIED", "simulated": "SIMULATED", "knowledge_only": "KNOWLEDGE ONLY",
          **{p["verified"]: p["label"] for p in registry.PLATFORMS.values()}}
STRENGTH = {"knowledge_only": 0, "simulated": 1, "local_verified": 2, **{p["verified"]: 3 for p in registry.PLATFORMS.values()}}
# A local result may not name a platform's engine as what it proved (docs/academy/README.md, ground rules).
FABRIC_ENGINE = registry.PLATFORMS["fabric"]["engines"]

_ID = re.compile(r"^(E\d{2}|C\d)$")
_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")
_USES = re.compile(r"^((?:LM|LAB)-\d{2})/([a-z0-9][a-z0-9-]{0,60})$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REQUIRED = ("id", "version", "status", "title", "objectives", "objective_text", "scenario", "minutes", "mode", "class",
             "adapter", "engine_label", "verifies", "does_not_verify", "validation", "explanation", "reset")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _folder(pack_id: str, root: Path) -> Path:
    registry.pack(pack_id, root)
    return root / pack_id / "exercises"


def ids(pack_id: str, root: Path = registry.ROOT) -> list[str]:
    folder = _folder(pack_id, root)
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and _ID.match(p.name)) if folder.is_dir() else []


def get(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    if not _ID.match(str(exercise_id)) or not (_folder(pack_id, root) / exercise_id / "exercise.json").is_file():
        raise ValueError(f"{pack_id} has no exercise {exercise_id}")
    return dict(_read_json(_folder(pack_id, root) / exercise_id / "exercise.json"))


def exercises(pack_id: str, root: Path = registry.ROOT, include_drafts: bool = True) -> list[dict[str, Any]]:
    out = []
    for eid in ids(pack_id, root):
        ex = get(pack_id, eid, root)
        if ex.get("status") == "retired" or (ex.get("status") == "draft" and not include_drafts):
            continue
        out.append(ex)
    return out


def file_text(pack_id: str, exercise_id: str, name: str, root: Path = registry.ROOT) -> str:
    """One of the exercise's own learner files (never grading material)."""
    if not _ID.match(str(exercise_id)) or not _FILE.match(str(name)) or ".." in name:
        raise ValueError(f"Not an exercise file: {exercise_id}/{name}")
    return (_folder(pack_id, root) / exercise_id / name).read_text(encoding="utf-8")


def _grading(pack_id: str, exercise_id: str, root: Path) -> Path:
    return root / pack_id / "grading" / exercise_id


def label(ex: dict[str, Any]) -> dict[str, Any]:
    """What a result screen shows about how the work was checked."""
    return {"class": ex["class"], "label": LABELS[ex["class"]], "engine": ex["engine_label"],
            "verifies": list(ex["verifies"]), "does_not_verify": list(ex["does_not_verify"])}


def completes(objective: dict[str, Any], cls: str, platform: str = registry.DEFAULT_PLATFORM) -> bool:
    """Whether evidence of class ``cls`` completes ``objective`` (a registry objective of a ``platform``
    pack). Another platform's verified class never counts."""
    plat = registry.PLATFORMS[platform]
    if cls not in STRENGTH or (cls in registry.CLASSES[len(registry.LOCAL_CLASSES):] and cls != plat["verified"]):
        return False
    if registry.required(objective, plat):
        return cls == plat["verified"]
    return STRENGTH[cls] >= STRENGTH.get(objective.get("best_local", "local_verified"), 2)


def reference(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    """Where an exercise's reference answer comes from: its own grading folder, or the Learn lab it uses.
    ``{"setup": sql or None, "starter": sql, "solution": sql, "wrong": {name: sql}}``."""
    ex = get(pack_id, exercise_id, root)
    setup: str | None
    uses = _USES.match(str(ex.get("uses") or ""))
    if uses:
        module_id, lab_ex = uses.groups()
        m = learn_content.get(module_id)
        lex = next(e for e in m["exercises"] if e["id"] == lab_ex)
        setup = "\n".join(learn_content.file_text(module_id, e["setup"]) for e in m["exercises"] if e.get("setup"))
        starter = learn_content.file_text(module_id, lex["starter"]) if lex.get("starter") else ""
        solution = learn_content.file_text(module_id, lex["solution"])
    else:
        setup = file_text(pack_id, exercise_id, ex["setup"], root) if ex.get("setup") else None
        starter = file_text(pack_id, exercise_id, ex["starter"], root) if ex.get("starter") else ""
        solution = (_grading(pack_id, exercise_id, root) / "solution.sql").read_text(encoding="utf-8")
    grading = _grading(pack_id, exercise_id, root)
    wrong = {p.name: p.read_text(encoding="utf-8") for p in sorted(grading.glob("wrong-*.sql"))} if grading.is_dir() else {}
    return {"setup": setup or None, "starter": starter, "solution": solution, "wrong": wrong}


def run_check(con: duckdb.DuckDBPyConnection, c: dict[str, Any], answer: str, solution: str) -> dict[str, Any]:
    kind = c["kind"]
    if kind == "result_matches":
        return checks.result_matches(con, answer, reference=solution, ordered=bool(c.get("ordered")))
    if kind == "no_findings":   # reads the answer's text only; nothing runs
        return checks.no_findings(answer, [str(r) for r in c["rules"]])
    try:
        checks.guard(con, answer)
    except ValueError as exc:
        return {"kind": kind, "passed": False, "detail": f"Your SQL wasn't run: {exc}."}
    con.begin()
    try:
        # unchanged: the engine fingerprints the protected tables inside the same rolled-back transaction,
        # just before the answer runs; nothing of it is visible to or computed by the learner's SQL.
        before = validators.snapshot(con, [str(t) for t in c["tables"]]) if kind == "unchanged" else None
        con.execute(answer)
        if before is not None:
            return validators.unchanged(con, [str(t) for t in c["tables"]], before)
        if kind == "table_shape":
            return checks.table_shape(con, c["table"], c.get("columns"), c.get("unique"), c.get("not_null"))
        return checks.integrity(con, c["child"], c["column"], c["parent"], c.get("parent_column"))
    except duckdb.Error as exc:
        return {"kind": kind, "passed": False, "detail": f"Your SQL didn't run: {exc}"}
    finally:
        con.rollback()


def grade(setup: str | None, validation: list[dict[str, Any]], answer: str, solution: str) -> dict[str, Any]:
    """Run ``validation`` on ``answer`` against ``solution`` in a fresh in-memory DuckDB holding ``setup``,
    with no file or network access. Only for duckdb_lab exercises."""
    con = duckdb.connect(":memory:")
    try:
        if setup:
            con.execute(setup)
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        results = [run_check(con, c, answer, solution) for c in validation]
    finally:
        con.close()
    return {"passed": all(r["passed"] for r in results), "results": results}


def self_test(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    """The reference solution must pass and every planted wrong answer must fail. ``{"solution": result,
    "wrong": {name: result}}``."""
    ex = get(pack_id, exercise_id, root)
    if ex["adapter"] in pylab.ADAPTERS:   # each run in a learner process of its own (ACD-011)
        return pylab.self_test(_folder(pack_id, root) / exercise_id, _grading(pack_id, exercise_id, root), ex)
    if ex["adapter"] == pipelab.ADAPTER:   # parsed and run as data on the local runner (ACD-012)
        return pipelab.self_test(_folder(pack_id, root) / exercise_id, _grading(pack_id, exercise_id, root), ex)
    if ex["adapter"] == configlab.ADAPTER:   # a settings document checked as data; nothing runs
        return configlab.self_test(_grading(pack_id, exercise_id, root), ex)
    if ex["adapter"] == gitlab.ADAPTER:   # the session replayed with real Git in a fresh repository
        return gitlab.self_test(_folder(pack_id, root) / exercise_id, _grading(pack_id, exercise_id, root), ex)
    if ex["adapter"] == sqlprojlab.ADAPTER:   # the change set applied as text and built in memory
        return sqlprojlab.self_test(_folder(pack_id, root) / exercise_id, _grading(pack_id, exercise_id, root), ex)
    if ex["adapter"] == quizlab.ADAPTER:   # questions scored against their key; nothing runs
        return quizlab.self_test(_grading(pack_id, exercise_id, root), ex)
    if ex["adapter"] == "dbsql_lab":
        from . import dbsql_lab   # it imports this module
        return dbsql_lab.self_test(pack_id, exercise_id, root)
    if ex["adapter"] == "pyspark_lab":
        from . import pyspark_lab   # it imports this module
        return pyspark_lab.self_test(pack_id, exercise_id, root)
    if ex["adapter"] != "duckdb_lab":
        raise ValueError(f"{exercise_id}: only duckdb_lab, dbsql_lab, pyspark_lab, python_lab, delta_lab, "
                         "pipeline_lab, config_model, git_lab, sqlproj_lab and quiz exercises can be self-tested so far")
    ref = reference(pack_id, exercise_id, root)
    return {"solution": grade(ref["setup"], ex["validation"], ref["solution"], ref["solution"]),
            "wrong": {name: grade(ref["setup"], ex["validation"], sql, ref["solution"]) for name, sql in ref["wrong"].items()}}


def _exercise_problems(pack_id: str, folder: Path, ex: dict[str, Any], cert: dict[str, Any], root: Path) -> list[str]:
    eid = folder.name
    out = [f"{eid}: exercise.json needs {f}" for f in _REQUIRED if ex.get(f) in (None, "", [])]
    if out:
        return out
    if ex.get("schema") != SCHEMA:
        out.append(f"{eid}: schema is {SCHEMA}")
    if ex["id"] != eid:
        out.append(f"{eid}: id {ex['id']} doesn't match its folder")
    for key, allowed in (("status", STATUSES), ("mode", MODES), ("class", tuple(LABELS)), ("adapter", ADAPTERS)):
        if ex[key] not in allowed:
            out.append(f"{eid}: {key} is one of {', '.join(allowed)}")
    if not isinstance(ex["version"], int) or ex["version"] < 1:
        out.append(f"{eid}: version is a whole number from 1")
    if not isinstance(ex["minutes"], int) or not 1 <= ex["minutes"] <= 240:
        out.append(f"{eid}: minutes is a whole number from 1 to 240")
    if ex["status"] == "live" and not _DATE.match(str(ex.get("reviewed") or "")):
        out.append(f"{eid}: a live exercise needs the date it was last reviewed (reviewed: YYYY-MM-DD)")
    # ACD-003: the class has to be one this adapter can honestly give, on this pack's platform.
    plat = registry.platform(cert)
    for other in registry.PLATFORMS.values():
        if ex["class"] == other["verified"] and ex["adapter"] != other["adapter"]:
            out.append(f"{eid}: only the {other['adapter']} adapter can give {other['label']}")
        if ex["adapter"] == other["adapter"] and ex["class"] != other["verified"]:
            out.append(f"{eid}: a {other['adapter']} exercise is {other['label']}")
        if other is not plat and (ex["class"] == other["verified"] or ex["adapter"] == other["adapter"]):
            out.append(f"{eid}: a {plat['name']} pack can't hold {other['name']} exercises")
    if ex["adapter"] in MODEL_ADAPTERS and ex["class"] != MODEL_ADAPTERS[ex["adapter"]]:
        out.append(f"{eid}: a {ex['adapter']} exercise is {LABELS[MODEL_ADAPTERS[ex['adapter']]]}: nothing runs")
    if ex["class"] == "local_verified" and ex["adapter"] not in EXECUTING:
        out.append(f"{eid}: LOCAL VERIFIED needs an adapter that runs the learner's work ({', '.join(EXECUTING)})")
    for other in registry.PLATFORMS.values():
        if ex["class"] != other["verified"]:
            claims = [s for s in [ex["engine_label"], *ex["verifies"]] if other["engines"].search(str(s))]
            out += [f"{eid}: a local result can't claim a {other['name']} engine: {s!r}" for s in claims]
    for key in ("difficulty",):
        if key in ex and ex[key] not in (1, 2, 3):
            out.append(f"{eid}: difficulty is 1, 2 or 3")
    for key in ("verifies", "does_not_verify", "hints"):
        if not all(isinstance(s, str) and s.strip() for s in ex.get(key) or []):
            out.append(f"{eid}: {key} is a list of sentences")
    objs = {o["id"]: o for o in registry.objectives(cert)}
    for oid in ex["objectives"]:
        if oid not in objs:
            out.append(f"{eid}: unknown objective {oid}")
        elif eid not in objs[oid]["exercises"] + objs[oid].get("capstones", []):
            out.append(f"{eid}: objective {oid} doesn't list this exercise in the registry")
    for p in ex.get("prerequisites") or []:
        if not (_ID.match(str(p)) and (folder.parent / p).is_dir()) and not (learn_content.is_id(p)
                                                                              and (learn_content.ROOT / p).is_dir()):
            out.append(f"{eid}: unknown prerequisite {p}")
    out += [f"{eid}: unknown remediation lesson {r}" for r in ex.get("remediation") or []
            if not str(r).startswith("https://") and not (learn_content.is_id(r) and (learn_content.ROOT / r).is_dir())]
    if ex["mode"] == "troubleshoot":
        fault: dict[str, Any] = ex["fault"] if isinstance(ex.get("fault"), dict) else {}
        if not (fault.get("summary") and fault.get("symptom") and fault.get("planted")):
            out.append(f"{eid}: a troubleshoot exercise needs a fault with summary, symptom and what was planted")
    for c in ex["validation"]:
        out += [f"{eid}: check {c.get('kind')}'s {k} message is a sentence" for k in ("pass", "fail")
                if k in c and not (isinstance(c[k], str) and c[k].strip())]
    checkable = ADAPTER_CHECKS.get(ex["adapter"])
    if checkable is not None:
        out += [f"{eid}: {ex['adapter']} can't check {c.get('kind')}" for c in ex["validation"] if c.get("kind") not in checkable]
        if ex["adapter"] in pylab.ADAPTERS:
            out += [f"{eid}: {p}" for c in ex["validation"] for p in pylab.check_problems(c)]
        if ex["adapter"] == pipelab.ADAPTER:
            out += [f"{eid}: {p}" for c in ex["validation"] for p in pipelab.check_problems(c)]
        if ex["adapter"] == configlab.ADAPTER:
            out += [f"{eid}: {p}" for c in ex["validation"] for p in configlab.check_problems(c)]
        if ex["adapter"] == quizlab.ADAPTER:
            out += [f"{eid}: {p}" for c in ex["validation"] for p in quizlab.check_problems(c)]
        if ex["adapter"] == gitlab.ADAPTER:
            out += [f"{eid}: {p}" for c in ex["validation"] for p in gitlab.check_problems(c)]
        if ex["adapter"] == sqlprojlab.ADAPTER:
            out += [f"{eid}: {p}" for c in ex["validation"] for p in sqlprojlab.check_problems(c)]
        for c in ex["validation"]:
            need = {"table_shape": ("table",), "integrity": ("child", "column", "parent"),
                    "idempotent": ("table",), "unchanged": ("tables",), "no_findings": ("rules",)}.get(c.get("kind"), ())
            if ex["adapter"] == "pyspark_lab" and c.get("kind") == "result_matches":
                need = ("table",)
            if [k for k in need if not c.get(k)]:
                out.append(f"{eid}: check {c.get('kind')} needs {', '.join(need)}")
            if c.get("data") not in (None, "practice", "hidden"):
                out.append(f"{eid}: check {c.get('kind')}'s data is practice or hidden")
            if ex["adapter"] == "duckdb_lab" and (c.get("data") == "hidden" or (c.get("kind") == "result_matches"
                                                                                and c.get("table"))):
                out.append(f"{eid}: a duckdb_lab check reads the answer's last result on the setup's data, "
                           "so it takes no table or hidden data")
    out += _file_problems(pack_id, folder, ex, root)
    if ex["adapter"] == "dbsql_lab" and (_grading(pack_id, eid, root) / "solution.sql").is_file():
        from . import dbsql_lab   # it imports this module
        untested = dbsql_lab.unproven((_grading(pack_id, eid, root) / "solution.sql").read_text(encoding="utf-8"))
        if untested:
            out.append(f"{eid}: the reference solution uses {', '.join(untested)}, which the Databricks SQL parity "
                       "table (engines/databricks_sql_parity.py) hasn't shown to answer the same on this PC")
    return out


def _file_problems(pack_id: str, folder: Path, ex: dict[str, Any], root: Path) -> list[str]:
    eid, out = folder.name, []
    grading = _grading(pack_id, eid, root)
    names = [ex[k] for k in ("lesson", "setup", "starter") if ex.get(k)] + list(ex.get("files") or [])
    names += list((ex.get("children") or {}).values()) if isinstance(ex.get("children"), dict) else []
    for name in names:
        if not isinstance(name, str) or not _FILE.match(name) or ".." in name:
            out.append(f"{eid}: {name!r} isn't a plain file name in the exercise's folder")
        elif not (folder / name).is_file():
            out.append(f"{eid}: {name} is missing")
    if ex.get("lesson") and (folder / ex["lesson"]).is_file():
        out += learn_content._markdown_problems(f"{eid}/{ex['lesson']}", (folder / ex["lesson"]).read_text(encoding="utf-8"))
    leaked = [p.name for p in folder.iterdir() if p.name.startswith(("solution", "wrong-"))]
    out += [f"{eid}: {n} is grading material; it goes in grading/{eid}/, never beside the starter" for n in leaked]
    if ex.get("uses"):
        m = _USES.match(str(ex["uses"]))
        if not m:
            out.append(f"{eid}: uses names a Learn lab exercise like LAB-04/rebuild")
        else:
            try:
                lab = learn_content.get(m.group(1))
            except ValueError:
                lab = {"exercises": []}
            if m.group(2) not in [e["id"] for e in lab["exercises"]]:
                out.append(f"{eid}: uses {ex['uses']}, which isn't a Learn exercise")
        out += [f"{eid}: with uses, {k} comes from the Learn lab" for k in ("setup", "starter") if ex.get(k)]
        if ex.get("adapter") in (*pylab.ADAPTERS, pipelab.ADAPTER, configlab.ADAPTER, quizlab.ADAPTER, gitlab.ADAPTER,
                                  sqlprojlab.ADAPTER):
            out.append(f"{eid}: uses reuses a Learn SQL lab, so it's only for duckdb_lab exercises")
    elif ex.get("adapter") == pipelab.ADAPTER:
        if not str(ex.get("starter") or "").endswith(".json"):
            out.append(f"{eid}: a pipeline_lab exercise starts from a pipeline or DAG file (starter: pipeline.json)")
        if not isinstance(ex.get("mocks") or {}, dict) or not isinstance(ex.get("children") or {}, dict):
            out.append(f"{eid}: mocks and children map activity names and pipeline keys to what stands in for them")
        if not (grading / "solution.json").is_file():
            out.append(f"{eid}: needs an independently runnable reference solution at grading/{eid}/solution.json")
        if len(list(grading.glob("wrong-*.json")) if grading.is_dir() else []) < 2:
            out.append(f"{eid}: needs at least two planted wrong answers (grading/{eid}/wrong-*.json) that validation must fail")
    elif ex.get("adapter") == quizlab.ADAPTER:
        if ex.get("starter"):
            out.append(f"{eid}: a quiz starts with no answers, so it takes no starter")
        out += quizlab.problems(eid, grading, ex, root)
        leaked = [p.name for p in folder.iterdir() if p.name == quizlab.QUIZ_FILE]
        out += [f"{eid}: {n} holds the answers; it goes in grading/{eid}/, never beside the lesson" for n in leaked]
    elif ex.get("adapter") == configlab.ADAPTER:
        if not str(ex.get("starter") or "").endswith(".json"):
            out.append(f"{eid}: a config_model exercise starts from a JSON settings document (starter: settings.json)")
        if not (grading / "solution.json").is_file():
            out.append(f"{eid}: needs a reference settings document at grading/{eid}/solution.json")
        if len(list(grading.glob("wrong-*.json")) if grading.is_dir() else []) < 2:
            out.append(f"{eid}: needs at least two planted wrong answers (grading/{eid}/wrong-*.json) that validation must fail")
    elif ex.get("adapter") == gitlab.ADAPTER:
        if not str(ex.get("starter") or "").endswith(".git"):
            out.append(f"{eid}: a git_lab exercise starts from a Git session (starter: session.git)")
        out += gitlab.spec_problems(eid, ex)
        if not (grading / "solution.git").is_file():
            out.append(f"{eid}: needs a reference session at grading/{eid}/solution.git")
        if len(list(grading.glob("wrong-*.git")) if grading.is_dir() else []) < 2:
            out.append(f"{eid}: needs at least two planted wrong answers (grading/{eid}/wrong-*.git) that validation must fail")
    elif ex.get("adapter") == sqlprojlab.ADAPTER:
        if not str(ex.get("starter") or "").endswith(".sql"):
            out.append(f"{eid}: a sqlproj_lab exercise starts from a change set (starter: changes.sql)")
        out += sqlprojlab.spec_problems(eid, ex)
        if not (grading / "solution.sql").is_file():
            out.append(f"{eid}: needs a reference change set at grading/{eid}/solution.sql")
        if len(list(grading.glob("wrong-*.sql")) if grading.is_dir() else []) < 2:
            out.append(f"{eid}: needs at least two planted wrong answers (grading/{eid}/wrong-*.sql) that validation must fail")
    elif ex.get("adapter") in pylab.ADAPTERS:
        if not str(ex.get("starter") or "").endswith(".py"):
            out.append(f"{eid}: a {ex['adapter']} exercise starts from a Python script (starter: answer.py)")
        if not (grading / "solution.py").is_file():
            out.append(f"{eid}: needs an independently runnable reference solution at grading/{eid}/solution.py")
        if len(list(grading.glob("wrong-*.py")) if grading.is_dir() else []) < 2:
            out.append(f"{eid}: needs at least two planted wrong answers (grading/{eid}/wrong-*.py) that validation must fail")
    elif ex.get("adapter") in EXECUTING:
        sol = SOLUTION_FILE.get(ex["adapter"], "solution.sql")
        if not (grading / sol).is_file():
            out.append(f"{eid}: needs an independently runnable reference solution at grading/{eid}/{sol}")
    wrong = WRONG_GLOB.get(ex.get("adapter", ""), "wrong-*.sql")
    if ex.get("adapter") in (*SQL_ADAPTERS, "pyspark_lab") and len(list(grading.glob(wrong)) if grading.is_dir() else []) < 2:
        out.append(f"{eid}: needs at least two planted wrong answers (grading/{eid}/{wrong}) that validation must fail")
    if any(c.get("data") == "hidden" for c in ex.get("validation") or []) and not (grading / "hidden.sql").is_file():
        out.append(f"{eid}: a check on hidden data needs grading/{eid}/hidden.sql")
    return out


def problems(root: Path = registry.ROOT) -> list[str]:
    """Everything wrong with the bundled exercises, in words; empty when they can ship."""
    out: list[str] = []
    for pack_id in registry.pack_ids(root):
        cert = registry.certification(pack_id, root)
        folder = root / pack_id / "exercises"
        if not folder.is_dir():
            continue
        for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
            if not _ID.match(sub.name):
                out.append(f"{pack_id}/{sub.name}: an exercise folder is named by its id, like E01 or C1")
                continue
            try:
                ex = _read_json(sub / "exercise.json")
            except (OSError, ValueError) as exc:
                out.append(f"{sub.name}: exercise.json can't be read ({exc})")
                continue
            out += [f"{pack_id} {p}" for p in _exercise_problems(pack_id, sub, ex, cert, root)]
        grading = root / pack_id / "grading"
        if grading.is_dir():
            out += [f"{pack_id} grading/{g.name}: no exercise has this id" for g in grading.iterdir()
                    if g.is_dir() and not (folder / g.name).is_dir()]
    return out
