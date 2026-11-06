"""Question exercises: the ``quiz`` adapter.

Some objectives are choices and knowledge, not something this PC can run: which store fits, which
transformation tool, what mirroring replicates. A quiz exercise asks a few original questions about a
scenario and is KNOWLEDGE ONLY; ``exercises.problems`` holds every quiz exercise to that class.

The questions live with the grading material, in ``grading/<id>/quiz.json``, in the exam engine's
question format (academy/exam.py) with short ids::

    {"questions": [{"id": "q1", "type": "single" | "multiple" | "order", "stem", "options": [{"id", "text"}],
                    "answer": [option ids], "also_accept"?, "explanation", "source"}]}

``view`` is what the screen gets before the learner checks: stems and options only, never the answers,
explanations or sources. The learner's answer is ``answers.json`` in the workspace, ``{"q1": ["b"]}``.
``grade`` scores it with the exam engine's own all-or-nothing rule and says how many are right and which
aren't answered yet; never which answered ones are wrong, or what the right answer is. ``review`` (the explanation, shown after a pass or on request, which
then counts against an independent pass) gives each answer, why and its source page.

Checks (``validation``):

    quiz_score    at least ``min_correct`` questions right (every question when it isn't given)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import exam, registry

ADAPTER = "quiz"
CHECKS = ("quiz_score",)
ANSWER_FILE = "answers.json"
QUIZ_FILE = "quiz.json"
MIN_QUESTIONS = 3
MAX_QUESTIONS = 20
MAX_BYTES = 64 * 1024   # a quiz answer is a few hundred bytes
_QID = re.compile(r"^q(\d{1,2})$")


def _no(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": False, "detail": detail}


def load(grading: Path) -> list[dict[str, Any]]:
    """The exercise's questions, answers included (engine only)."""
    doc = json.loads((grading / QUIZ_FILE).read_text(encoding="utf-8"))
    return list(doc["questions"]) if isinstance(doc, dict) and isinstance(doc.get("questions"), list) else []


def view(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the screen may show before the learner checks: stems and options, and how many to pick for a
    multiple-answer question. Never answers, explanations or sources."""
    return [{"id": q["id"], "type": q["type"], "stem": q["stem"],
             "options": [{"id": o["id"], "text": o["text"]} for o in q["options"]],
             "select": len(q["answer"]) if q["type"] == "multiple" else None} for q in questions]


def review(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each question's answer, why, and the documentation page it's based on (the explanation)."""
    return [{"id": q["id"], "answer": list(q["answer"]), "also_accept": [list(a) for a in q.get("also_accept") or []],
             "explanation": q["explanation"], "source": q["source"]} for q in questions]


def parse(text: str) -> tuple[dict[str, list[str]] | None, str | None]:
    """The learner's answers, ``{question id: [option ids]}``, or ``(None, why it can't be read)``."""
    raw = str(text)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        return None, "The answers are over 64 KB; a quiz's answers are much smaller."
    try:
        doc = json.loads(raw.lstrip("﻿") or "{}")
    except (json.JSONDecodeError, RecursionError):
        return None, f"{ANSWER_FILE} isn't valid JSON."
    if not isinstance(doc, dict):
        return None, f"{ANSWER_FILE} is an object of question ids and the options picked, like {{\"q1\": [\"b\"]}}."
    out = {}
    for qid, choice in doc.items():
        if isinstance(choice, str):
            choice = [choice]
        if isinstance(choice, list) and all(isinstance(c, str) for c in choice):
            out[str(qid)] = list(choice)
    return out, None


def changed(questions: list[dict[str, Any]], checked: dict[str, list[str]], answers: dict[str, list[str]]) -> bool:
    """Whether any answer the learner already checked is different now. Trying answers until the count
    moves can find the key, so a pass after that isn't independent (Security read)."""
    kinds = {q["id"]: q["type"] for q in questions}
    for qid, before in checked.items():
        now = answers.get(qid) or []
        same = now == before if kinds.get(qid) == "order" else sorted(now) == sorted(before)
        if qid in kinds and not same:
            return True
    return False


def check_problems(c: dict[str, Any]) -> list[str]:
    """What's wrong with one check's fields, in words (for exercises.problems)."""
    if c.get("kind") != "quiz_score" or "min_correct" not in c:
        return []
    n = c["min_correct"]
    return [] if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else ["check quiz_score's min_correct is a whole number from 1"]


def score(questions: list[dict[str, Any]], answers: dict[str, list[str]], min_correct: int | None = None) -> dict[str, Any]:
    """How many are right. A failed check names only the unanswered questions, never which answered ones
    are wrong (Security read), so Check can't be used to find the key one question at a time."""
    kind = "quiz_score"
    right = sum(1 for q in questions if exam._correct(q, answers.get(q["id"])))
    need = len(questions) if min_correct is None else min(int(min_correct), len(questions))
    said = f"{right} of {len(questions)} right"
    if right >= need:
        return {"kind": kind, "passed": True, "detail": f"{said}."}
    unanswered = [q["id"] for q in questions if not answers.get(q["id"])]
    tail = f" Not answered yet: {', '.join(unanswered)}." if unanswered else ""
    return _no(kind, f"{said}; you need {need}.{tail}")


def grade(text: str, ex: dict[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Every check of ``ex`` on the learner's answers ``text``."""
    answers, problem = parse(text)
    results = []
    for c in ex["validation"]:
        if c["kind"] != "quiz_score":
            results.append(_no(str(c["kind"]), f"A quiz can't check {c['kind']}."))
        elif answers is None:
            results.append(_no("quiz_score", str(problem)))
        else:
            results.append(score(questions, answers, c.get("min_correct")))
    return {"passed": bool(results) and all(r["passed"] for r in results), "results": results}


def _wrong_choice(q: dict[str, Any]) -> list[str]:
    ids = [o["id"] for o in q["options"]]
    if q["type"] == "order":
        return list(reversed(q["answer"]))
    return [next(i for i in ids if i not in q["answer"])]


def self_test(grading: Path, ex: dict[str, Any]) -> dict[str, Any]:
    """The answer key must pass; no answers and every answer wrong must fail."""
    questions = load(grading)
    key = {q["id"]: list(q["answer"]) for q in questions}
    wrong = {q["id"]: _wrong_choice(q) for q in questions}
    return {"solution": grade(json.dumps(key), ex, questions),
            "wrong": {"no answers": grade("{}", ex, questions), "every answer wrong": grade(json.dumps(wrong), ex, questions)}}


def problems(eid: str, grading: Path, ex: dict[str, Any], root: Path) -> list[str]:
    """What's wrong with a quiz exercise's questions, in words: the exam engine's rules for each question
    (options, answer, explanation, a source on the pack's documentation host), short ids, and enough of them."""
    if not (grading / QUIZ_FILE).is_file():
        return [f"{eid}: needs its questions at grading/{eid}/{QUIZ_FILE}"]
    try:
        questions = load(grading)
    except (OSError, ValueError) as exc:
        return [f"{eid}: {QUIZ_FILE} can't be read ({exc})"]
    out = []
    if not MIN_QUESTIONS <= len(questions) <= MAX_QUESTIONS:
        out.append(f"{eid}: a quiz has {MIN_QUESTIONS} to {MAX_QUESTIONS} questions")
    pack = grading.parent.parent.name
    try:
        hosts = exam.source_hosts(pack, root)
        plat = exam._platform_name(root / pack)
    except exam.ExamError:
        hosts, plat = exam.SOURCE_HOSTS, registry.DEFAULT_PLATFORM
    docs = exam.DOCS_NAMES.get(plat, registry.PLATFORMS[plat]["name"])
    seen = set()
    objective = str((ex.get("objectives") or ["1.1"])[0])
    for n, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            out.append(f"{eid}: question {n} isn't an object")
            continue
        m = _QID.match(str(q.get("id") or ""))
        if not m or q["id"] in seen:
            out.append(f"{eid}: question {n}'s id is q1, q2 and so on, each once")
        seen.add(q.get("id"))
        # The exam engine's rules, with the exercise standing in for the bank's id, version and status.
        as_bank = {**q, "id": f"{objective}-q{m.group(1) if m else n}", "version": 1, "status": "draft"}
        out += [f"{eid}: {p.replace(as_bank['id'], str(q.get('id')))}"
                for p in exam._question_problems(objective, as_bank, {}, hosts, docs)]
        if q.get("case") is not None:
            out.append(f"{eid}: question {q.get('id')}: a quiz's scenario is the exercise's own; it takes no case")
    return out
