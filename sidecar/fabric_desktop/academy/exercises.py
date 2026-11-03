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
from . import registry

SCHEMA = "kratosforge/academy-exercise@1"
MODES = ("guided", "challenge", "troubleshoot")
STATUSES = ("draft", "live", "retired")
ADAPTERS = ("duckdb_lab", "python_lab", "delta_lab", "pipeline_lab", "config_model", "git_lab", "sqlproj_lab", "quiz",
            "fabric")
# Adapters that run the learner's artifact, so a pass can be LOCAL VERIFIED.
EXECUTING = ("duckdb_lab", "python_lab", "delta_lab", "pipeline_lab", "git_lab", "sqlproj_lab")
# What each adapter can check today. Adapters with nothing here aren't built yet (ACD-004, 011, 012, 014).
ADAPTER_CHECKS: dict[str, tuple[str, ...]] = {"duckdb_lab": ("result_matches", "table_shape", "integrity")}
LABELS = {"local_verified": "LOCAL VERIFIED", "simulated": "SIMULATED", "fabric_verified": "FABRIC VERIFIED",
          "knowledge_only": "KNOWLEDGE ONLY"}
STRENGTH = {"knowledge_only": 0, "simulated": 1, "local_verified": 2, "fabric_verified": 3}
# A local result may not name a Fabric engine as what it proved (docs/academy/README.md, ground rules).
FABRIC_ENGINE = re.compile(r"\b(Fabric Spark|Spark pool|Fabric Warehouse|Warehouse engine|Fabric SQL|SQL analytics endpoint)\b",
                           re.I)

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


def completes(objective: dict[str, Any], cls: str) -> bool:
    """Whether evidence of class ``cls`` completes ``objective`` (a registry objective)."""
    if cls not in STRENGTH:
        return False
    if objective.get("fabric_required"):
        return cls == "fabric_verified"
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
    try:
        checks.guard(con, answer)
    except ValueError as exc:
        return {"kind": kind, "passed": False, "detail": f"Your SQL wasn't run: {exc}."}
    con.begin()
    try:
        con.execute(answer)
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
    if ex["adapter"] != "duckdb_lab":
        raise ValueError(f"{exercise_id}: only duckdb_lab exercises can be self-tested so far")
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
    # ACD-003: the class has to be one this adapter can honestly give.
    if ex["class"] == "fabric_verified" and ex["adapter"] != "fabric":
        out.append(f"{eid}: only the fabric adapter can give FABRIC VERIFIED")
    if ex["adapter"] == "fabric" and ex["class"] != "fabric_verified":
        out.append(f"{eid}: a fabric exercise is FABRIC VERIFIED")
    if ex["class"] == "local_verified" and ex["adapter"] not in EXECUTING:
        out.append(f"{eid}: LOCAL VERIFIED needs an adapter that runs the learner's work ({', '.join(EXECUTING)})")
    if ex["class"] != "fabric_verified":
        claims = [s for s in [ex["engine_label"], *ex["verifies"]] if FABRIC_ENGINE.search(str(s))]
        out += [f"{eid}: a local result can't claim a Fabric engine: {s!r}" for s in claims]
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
        for c in ex["validation"]:
            need = {"table_shape": ("table",), "integrity": ("child", "column", "parent")}.get(c.get("kind"), ())
            if [k for k in need if not c.get(k)]:
                out.append(f"{eid}: check {c.get('kind')} needs {', '.join(need)}")
    out += _file_problems(pack_id, folder, ex, root)
    return out


def _file_problems(pack_id: str, folder: Path, ex: dict[str, Any], root: Path) -> list[str]:
    eid, out = folder.name, []
    grading = _grading(pack_id, eid, root)
    names = [ex[k] for k in ("lesson", "setup", "starter") if ex.get(k)]
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
    elif ex.get("adapter") in EXECUTING:
        if not (grading / "solution.sql").is_file():
            out.append(f"{eid}: needs an independently runnable reference solution at grading/{eid}/solution.sql")
    if ex.get("adapter") == "duckdb_lab" and len(list(grading.glob("wrong-*.sql")) if grading.is_dir() else []) < 2:
        out.append(f"{eid}: needs at least two planted wrong answers (grading/{eid}/wrong-*.sql) that validation must fail")
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
