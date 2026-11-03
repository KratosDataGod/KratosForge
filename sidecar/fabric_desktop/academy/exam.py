"""Academy exam engine (ACD-013): original practice questions, timed practice exams, review flags and
results by objective.

Questions are data. Each pack keeps one file per objective under ``packs/<pack>/questions/``
(schema ``kratosforge/academy-question@1``) and shared case studies in ``packs/<pack>/cases.json``.
Every question is original, written for KratosForge from the vendor documentation page it cites (Microsoft
Learn for a Fabric pack, docs.databricks.com for a Databricks pack: the pack's platform ``doc_hosts`` in
registry.py). None is a vendor exam question, and nothing here imitates a vendor's exam interface.

questions/<objective>.json::

    schema, objective, questions [{id ("<objective>-qN"; objectives are 2.2.9 or, for a guide without
    skill areas, 2.3), type ("single" | "multiple" | "order"),
    stem, options [{id ("a".."h"), text}], answer [option ids; for "order" all of them, in order],
    also_accept? (for "order": other step orders that are equally right, such as two filters that can
    swap), explanation, source (a page on the platform's documentation host), case? (a cases.json id), version (int),
    status ("draft" | "live" | "retired"), reviewed ("YYYY-MM-DD", required when live)}]

cases.json::

    {"<case id>": {"title", "text"}}

A session is a plain dict, saved by ``ExamStore`` and changed only through the functions here:

* ``build`` picks questions, spread evenly over the objectives asked for, in an order and with option
  orders fixed by a seed, so the same seed gives the same exam.
* ``view`` is everything the screen may show while the exam runs. It never carries answers,
  explanations or sources: those stay in the engine until the learner finishes.
* Time is counted honestly as elapsed time. The screen sends a ``heartbeat`` while the exam is on
  screen; each heartbeat or action adds the time since the last one, but never more than
  ``MAX_GAP`` seconds, so time while the app is closed or the PC sleeps isn't counted. ``pause``
  stops the clock. A timed exam takes no answers once its time is used.
* ``finish`` scores the session and stores the result inside it. A finished session is never scored
  again, so a later question edit or a new certification version can't change a learner's past result.

Scores are by objective and domain. They are a KratosForge practice result: never the vendor's score,
never a scaled score, and never a promise of passing (``notice_for``; ``NOTICE`` is the Microsoft wording).
"""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import secrets
from pathlib import Path
from typing import Any

from .. import jsonfile
from . import registry

QUESTION_SCHEMA = "kratosforge/academy-question@1"
SESSION_SCHEMA = "kratosforge/academy-exam-session@1"
PACKS = Path(__file__).resolve().parent / "packs"

TYPES = ("single", "multiple", "order")
STATUSES = ("draft", "live", "retired")
KINDS = ("practice", "diagnostic", "drill")   # a drill shows the answer and why after each question
MIN_PER_OBJECTIVE = 4        # live questions every objective needs before the pack ships
MAX_GAP = 60.0               # most seconds one heartbeat or action may add to the clock
MAX_MINUTES = 300
SOURCE_HOSTS = registry.PLATFORMS[registry.DEFAULT_PLATFORM]["doc_hosts"]   # a Fabric pack's; see source_hosts()
DOCS_NAMES = {"fabric": "Microsoft Learn", "databricks": "Databricks documentation"}
RESULT_LABEL = "KratosForge practice result"


def notice_for(vendor: str) -> str:
    """The words every exam screen and result carries: original questions, not the vendor's exam or score."""
    return (f"Original practice questions written for KratosForge. They are not {vendor} exam questions, this is "
            f"not {vendor}'s exam interface, and the result is not a {vendor} score or a prediction of passing.")


NOTICE = notice_for("Microsoft")

_OBJECTIVE = re.compile(r"^\d{1,2}\.\d{1,2}(?:\.\d{1,2})?$")
_QID = re.compile(r"^(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)-q(\d{1,2})$")
_OPTION = re.compile(r"^[a-h]$")
_CASE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_SESSION = re.compile(r"^[0-9a-f]{32}$")
_PACK = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HTML = re.compile(r"<[A-Za-z!/]")


class ExamError(ValueError):
    """A request the exam can't carry out, in words the screen can show."""


# ---------------------------------------------------------------- question bank

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pack_root(pack: str, packs: Path = PACKS) -> Path:
    if not _PACK.match(str(pack)) or not (packs / pack).is_dir():
        raise ExamError(f"Not an Academy pack: {pack}")
    return packs / pack


def _platform_name(root: Path) -> str:
    """The pack's platform from its pack.json (``fabric`` when it doesn't say or isn't known)."""
    try:
        name = _read_json(root / "pack.json").get("platform", registry.DEFAULT_PLATFORM)
    except (OSError, ValueError):
        name = registry.DEFAULT_PLATFORM
    return name if name in registry.PLATFORMS else registry.DEFAULT_PLATFORM


def source_hosts(pack: str, packs: Path = PACKS) -> tuple[str, ...]:
    """The documentation hosts a pack's questions may cite: its platform's ``doc_hosts``."""
    return tuple(registry.PLATFORMS[_platform_name(_pack_root(pack, packs))]["doc_hosts"])


def notice(pack: str, packs: Path = PACKS) -> str:
    """``notice_for`` the pack's vendor."""
    return notice_for(registry.PLATFORMS[_platform_name(_pack_root(pack, packs))]["vendor"])


def load_bank(pack: str, packs: Path = PACKS, include_drafts: bool = False) -> dict[str, Any]:
    """{"questions": {id: question with "objective"}, "cases": {id: case}} for a pack. Retired questions are
    left out; drafts only when asked for."""
    root = _pack_root(pack, packs)
    cases = _read_json(root / "cases.json") if (root / "cases.json").is_file() else {}
    questions: dict[str, dict[str, Any]] = {}
    for f in sorted((root / "questions").glob("*.json")):
        doc = _read_json(f)
        for q in doc.get("questions") or []:
            status = q.get("status")
            if status == "retired" or (status == "draft" and not include_drafts):
                continue
            questions[q["id"]] = {**q, "objective": doc.get("objective")}
    vendor = registry.PLATFORMS[_platform_name(root)]["vendor"]
    return {"pack": pack, "questions": questions, "cases": dict(cases), "notice": notice_for(vendor)}


def _text_problems(where: str, text: Any) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return [f"{where} is empty"]
    if _HTML.search(re.sub(r"`[^`]*`", "", text)):
        return [f"{where} has raw HTML; the app shows questions as text"]
    return []


def _question_problems(objective: str, q: dict[str, Any], cases: dict[str, Any],
                       hosts: tuple[str, ...] = SOURCE_HOSTS, docs: str = DOCS_NAMES["fabric"]) -> list[str]:
    qid = str(q.get("id") or "?")
    where = f"question {qid}"
    m = _QID.match(qid)
    out = [] if m and m.group(1) == objective else [f"{where}: id is <objective>-qN, like {objective}-q1"]
    if q.get("type") not in TYPES:
        return out + [f"{where}: type is one of {', '.join(TYPES)}"]
    out += _text_problems(f"{where} stem", q.get("stem"))
    out += _text_problems(f"{where} explanation", q.get("explanation"))
    options = q.get("options") or []
    ids: list[str] = [str(o.get("id")) for o in options if isinstance(o, dict)]
    if len(ids) != len(options) or len(set(ids)) != len(ids) or not all(_OPTION.match(str(i)) for i in ids):
        out.append(f"{where}: options each need their own id, a to h")
    for o in options:
        if isinstance(o, dict):
            out += _text_problems(f"{where} option {o.get('id')}", o.get("text"))
    answer = q.get("answer") or []
    if not isinstance(answer, list) or any(a not in ids for a in answer) or len(set(answer)) != len(answer):
        out.append(f"{where}: answer names options that aren't there, or names one twice")
    elif q["type"] == "single" and (len(answer) != 1 or not 3 <= len(ids) <= 6):
        out.append(f"{where}: a single-answer question has 3 to 6 options and one answer")
    elif q["type"] == "multiple" and not (4 <= len(ids) <= 8 and 2 <= len(answer) < len(ids)):
        out.append(f"{where}: a multiple-answer question has 4 to 8 options and at least 2 answers, not all")
    elif q["type"] == "order" and (sorted(answer) != sorted(ids) or not 3 <= len(ids) <= 6):
        out.append(f"{where}: an ordering question has 3 to 6 steps and its answer lists every step once")
    elif q["type"] == "order" and answer == sorted(answer):
        out.append(f"{where}: the steps' ids give the order away; store the steps out of order")
    also = q.get("also_accept")
    if also is not None and (q["type"] != "order" or not isinstance(also, list) or not also or any(
            not isinstance(a, list) or sorted(a) != sorted(ids) or a == answer for a in also)):
        out.append(f"{where}: also_accept is for ordering questions: other full step orders, each different from the answer")
    source = str(q.get("source") or "")
    host = re.match(r"^https://([^/]+)/", source)
    if not host or host.group(1) not in hosts:
        out.append(f"{where}: source is the {docs} page the question is based on")
    if q.get("case") is not None and q.get("case") not in cases:
        out.append(f"{where}: unknown case study {q.get('case')}")
    if not isinstance(q.get("version"), int) or q["version"] < 1:
        out.append(f"{where}: version is a whole number from 1")
    if q.get("status") not in STATUSES:
        out.append(f"{where}: status is one of {', '.join(STATUSES)}")
    elif q["status"] == "live" and not _DATE.match(str(q.get("reviewed") or "")):
        out.append(f"{where}: a live question needs the date it was last reviewed (reviewed: YYYY-MM-DD)")
    return out


def problems(pack: str, objectives: list[str] | None = None, packs: Path = PACKS) -> list[str]:
    """Everything wrong with a pack's questions, in words; empty when they can ship. With ``objectives``
    (the certification's objective ids), each one needs ``MIN_PER_OBJECTIVE`` live questions and no
    question may name an objective outside the list."""
    try:
        root = _pack_root(pack, packs)
    except ExamError as exc:
        return [str(exc)]
    out: list[str] = []
    plat = _platform_name(root)
    hosts, docs = tuple(registry.PLATFORMS[plat]["doc_hosts"]), DOCS_NAMES.get(plat, registry.PLATFORMS[plat]["name"])
    cases: dict[str, Any] = {}
    if (root / "cases.json").is_file():
        try:
            cases = _read_json(root / "cases.json")
        except ValueError as exc:
            return [f"cases.json can't be read ({exc})"]
        for cid, case in cases.items():
            if not _CASE.match(str(cid)) or not isinstance(case, dict):
                out.append(f"case {cid}: ids are lowercase words joined by hyphens")
                continue
            out += _text_problems(f"case {cid} title", case.get("title"))
            out += _text_problems(f"case {cid} text", case.get("text"))
    live: dict[str, int] = {}
    seen: set[str] = set()
    used_cases: set[str] = set()
    for f in sorted((root / "questions").glob("*")):
        objective = f.stem
        if f.suffix != ".json" or not _OBJECTIVE.match(objective):
            out.append(f"questions/{f.name}: a question file is named by its objective, like 2.2.9.json or 2.3.json")
            continue
        try:
            doc = _read_json(f)
        except ValueError as exc:
            out.append(f"questions/{f.name} can't be read ({exc})")
            continue
        if doc.get("schema") != QUESTION_SCHEMA:
            out.append(f"questions/{f.name}: schema is {QUESTION_SCHEMA}")
        if doc.get("objective") != objective:
            out.append(f"questions/{f.name}: objective doesn't match the file name")
        if objectives is not None and objective not in objectives:
            out.append(f"questions/{f.name}: {objective} isn't one of the certification's objectives")
        for q in doc.get("questions") or []:
            if not isinstance(q, dict):
                out.append(f"questions/{f.name}: each question is an object")
                continue
            if q.get("id") in seen:
                out.append(f"question {q.get('id')}: id used twice")
            seen.add(str(q.get("id")))
            out += _question_problems(objective, q, cases, hosts, docs)
            if q.get("status") == "live":
                live[objective] = live.get(objective, 0) + 1
            if q.get("case"):
                used_cases.add(q["case"])
    out += [f"case {cid}: no question uses it" for cid in cases if cid not in used_cases]
    for objective in objectives or []:
        if live.get(objective, 0) < MIN_PER_OBJECTIVE:
            out.append(f"objective {objective}: has {live.get(objective, 0)} live questions; needs {MIN_PER_OBJECTIVE}")
    return out


# ---------------------------------------------------------------- sessions

def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(stamp: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(stamp)) if stamp else None
    except ValueError:
        return None


def build(bank: dict[str, Any], *, cert_version: str, objectives: list[str] | None = None,
          count: int | None = None, minutes: int | None = None, seed: int | None = None,
          kind: str = "practice", now: dt.datetime | None = None) -> dict[str, Any]:
    """A new, paused session. Questions are spread evenly over ``objectives`` (all the bank's when None),
    up to ``count`` (all of them when None). Questions sharing a case study sit together. ``minutes`` None
    is untimed. The same bank and seed give the same questions, order and option orders."""
    if kind not in KINDS:
        raise ExamError(f"An exam is one of {', '.join(KINDS)}")
    if minutes is not None and (not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES):
        raise ExamError(f"An exam's time is 1 to {MAX_MINUTES} minutes, or none")
    seed = secrets.randbelow(2**31) if seed is None else int(seed)
    rng = random.Random(seed)
    by_objective: dict[str, list[str]] = {}
    for qid in sorted(bank["questions"]):
        q = bank["questions"][qid]
        if objectives is None or q["objective"] in objectives:
            by_objective.setdefault(q["objective"], []).append(qid)
    if not by_objective:
        raise ExamError("There are no questions for those objectives yet")
    pools = {o: rng.sample(ids, len(ids)) for o, ids in sorted(by_objective.items())}
    order = list(pools)
    rng.shuffle(order)
    total = sum(len(v) for v in pools.values())
    want = total if count is None else max(1, min(int(count), total))
    picked: list[str] = []
    while len(picked) < want:          # one per objective per round: an even spread
        for o in order:
            if pools[o] and len(picked) < want:
                picked.append(pools[o].pop())
    # Case-study questions sit together, where the first of them fell.
    grouped: list[str] = []
    for qid in picked:
        case = bank["questions"][qid].get("case")
        if qid in grouped:
            continue
        grouped += [x for x in picked if x == qid or (case and bank["questions"][x].get("case") == case)]
    items = []
    for qid in grouped:
        q = bank["questions"][qid]
        ids = [o["id"] for o in q["options"]]
        items.append({"q": qid, "version": q["version"], "objective": q["objective"], "options": rng.sample(ids, len(ids))})
    t = _iso(now or _now())
    return {"schema": SESSION_SCHEMA, "id": secrets.token_hex(16), "pack": bank["pack"], "cert_version": str(cert_version),
            "kind": kind, "seed": seed, "minutes": minutes, "items": items, "answers": {}, "flags": [],
            "elapsed": 0.0, "running": False, "last_seen": t, "created_at": t, "status": "in_progress",
            "finished_at": None, "result": None, "checked": []}


def _tick(session: dict[str, Any], now: dt.datetime) -> None:
    """Add the time since the last heartbeat or action, never more than MAX_GAP, while the clock runs."""
    last = _parse(session.get("last_seen")) or now
    if session.get("running"):
        session["elapsed"] = round(float(session["elapsed"]) + min(max((now - last).total_seconds(), 0.0), MAX_GAP), 3)
    session["last_seen"] = _iso(now)


def time_left(session: dict[str, Any]) -> float | None:
    """Seconds left on a timed exam (never below 0); None when untimed."""
    if not session.get("minutes"):
        return None
    return max(0.0, session["minutes"] * 60 - float(session["elapsed"]))


def _open(session: dict[str, Any]) -> None:
    if session.get("status") != "in_progress":
        raise ExamError("This exam is finished")


def resume(session: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    """Start or restart the clock (on opening the exam or coming back to it)."""
    _open(session)
    now = now or _now()
    _tick(session, now)
    session["running"] = True
    return session


def heartbeat(session: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    """The exam is still on screen: count the time since the last heartbeat."""
    _open(session)
    _tick(session, now or _now())
    return session


def pause(session: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    """Stop the clock (the learner paused, left the page or closed the app)."""
    _open(session)
    _tick(session, now or _now())
    session["running"] = False
    return session


def _item(session: dict[str, Any], qid: str) -> dict[str, Any]:
    for it in session["items"]:
        if it["q"] == qid:
            return it
    raise ExamError(f"{qid} isn't in this exam")


def answer(session: dict[str, Any], qid: str, choice: list[str], now: dt.datetime | None = None) -> dict[str, Any]:
    """Record the learner's answer: the option ids they picked, or for ordering every step in their order.
    An empty list clears the answer. A timed exam whose time is used takes no more answers."""
    _open(session)
    _tick(session, now or _now())
    item = _item(session, qid)
    if time_left(session) == 0:
        raise ExamError("Time is up: finish the exam to see your results")
    if qid in (session.get("checked") or []):
        raise ExamError("That answer was already checked, so it can't change")
    choice = [str(c) for c in choice or []]
    if any(c not in item["options"] for c in choice) or len(set(choice)) != len(choice):
        raise ExamError("That answer names an option the question doesn't have")
    if choice:
        session["answers"][qid] = choice
    else:
        session["answers"].pop(qid, None)
    return session


def flag(session: dict[str, Any], qid: str, on: bool = True, now: dt.datetime | None = None) -> dict[str, Any]:
    """Mark a question for review, or clear the mark. Flags never change the score."""
    _open(session)
    _tick(session, now or _now())
    _item(session, qid)
    flags = [f for f in session["flags"] if f != qid]
    session["flags"] = flags + [qid] if on else flags
    return session


def _correct(q: dict[str, Any], choice: list[str] | None) -> bool:
    """All or nothing: a single or multiple answer must pick exactly the right options; an ordering every
    step in the right place."""
    if not choice:
        return False
    if q["type"] == "order":
        return choice == q["answer"] or choice in (q.get("also_accept") or [])
    return sorted(choice) == sorted(q["answer"])


def check(session: dict[str, Any], bank: dict[str, Any], qid: str, now: dt.datetime | None = None) -> dict[str, Any]:
    """A drill only: lock the learner's answer to ``qid`` and give back whether it's right, the right answer, why
    and the page it's based on. A checked answer can't change, and the final score uses the same answer."""
    _open(session)
    if session.get("kind") != "drill":
        raise ExamError("Only a drill shows answers before the end")
    _tick(session, now or _now())
    _item(session, qid)
    q = bank["questions"].get(qid)
    if q is None:
        raise ExamError(f"{qid} is no longer in the question bank")
    choice = session["answers"].get(qid)
    if not choice:
        raise ExamError("Answer the question first")
    checked = session.setdefault("checked", [])
    if qid not in checked:
        checked.append(qid)
    return {"q": qid, "correct": _correct(q, choice), "answer": list(choice), "key": list(q["answer"]),
            "also_accept": [list(a) for a in q.get("also_accept") or []], "explanation": q["explanation"],
            "source": q["source"]}


def _expected_count(q: dict[str, Any]) -> int | None:
    return len(q["answer"]) if q["type"] == "multiple" else None


def view(session: dict[str, Any], bank: dict[str, Any]) -> dict[str, Any]:
    """What the screen shows while the exam runs: questions and options in the session's order, the learner's
    answers and flags, and the clock. Never answers, explanations or sources."""
    items = []
    for n, it in enumerate(session["items"], 1):
        q = bank["questions"].get(it["q"])
        if q is None:
            items.append({"n": n, "q": it["q"], "missing": True, "objective": it["objective"]})
            continue
        texts = {o["id"]: o["text"] for o in q["options"]}
        case = bank["cases"].get(q["case"]) if q.get("case") else None
        items.append({"n": n, "q": it["q"], "objective": it["objective"], "type": q["type"], "stem": q["stem"],
                      "select": _expected_count(q), "case": {"id": q["case"], **case} if case else None,
                      "options": [{"id": o, "text": texts.get(o, "")} for o in it["options"]],
                      "answer": session["answers"].get(it["q"]), "flagged": it["q"] in session["flags"],
                      "checked": it["q"] in (session.get("checked") or [])})
    return {"id": session["id"], "pack": session["pack"], "cert_version": session["cert_version"],
            "kind": session["kind"], "status": session["status"], "items": items,
            "answered": sum(1 for it in session["items"] if it["q"] in session["answers"]),
            "flagged": len(session["flags"]), "total": len(session["items"]),
            "elapsed": float(session["elapsed"]), "minutes": session["minutes"], "time_left": time_left(session),
            "running": bool(session.get("running")), "notice": bank.get("notice", NOTICE)}


def _domain(objective: str) -> str:
    return objective.split(".")[0]


def _tally(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = out.setdefault(r[key], {key: r[key], "correct": 0, "total": 0})
        t["total"] += 1
        t["correct"] += int(r["correct"])
    return [{**t, "percent": round(100 * t["correct"] / t["total"])} for _, t in sorted(out.items(), key=_order_key)]


def _order_key(kv: tuple[str, Any]) -> tuple[int, ...]:
    return tuple(int(x) for x in kv[0].split("."))


def finish(session: dict[str, Any], bank: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    """Score the exam and keep the result in the session. Unanswered questions count as wrong. Questions
    removed from the bank since the exam started are left out of the score and listed."""
    _open(session)
    now = now or _now()
    _tick(session, now)
    rows, left_out = [], []
    for it in session["items"]:
        q = bank["questions"].get(it["q"])
        if q is None:
            left_out.append(it["q"])
            continue
        choice = session["answers"].get(it["q"])
        rows.append({"q": it["q"], "version": q["version"], "objective": it["objective"],
                     "domain": _domain(it["objective"]), "answered": bool(choice),
                     "correct": _correct(q, choice), "key": list(q["answer"]),
                     "also_accept": [list(a) for a in q.get("also_accept") or []]})
    correct = sum(int(r["correct"]) for r in rows)
    session["result"] = {
        "label": RESULT_LABEL, "notice": bank.get("notice", NOTICE), "correct": correct, "total": len(rows),
        "percent": round(100 * correct / len(rows)) if rows else 0,
        "by_objective": _tally(rows, "objective"), "by_domain": _tally(rows, "domain"),
        "items": rows, "left_out": left_out, "elapsed": float(session["elapsed"]),
        "time_up": time_left(session) == 0}
    session.update(status="finished", running=False, finished_at=_iso(now))
    return session


def review(session: dict[str, Any], bank: dict[str, Any]) -> list[dict[str, Any]]:
    """After finishing: each question with the learner's answer, the right answer, why, and the documentation
    page it's based on. Right and wrong come from the stored result, not from today's bank."""
    if session.get("status") != "finished" or not session.get("result"):
        raise ExamError("Finish the exam to review the answers")
    scored = {r["q"]: r for r in session["result"]["items"]}
    out = []
    for n, it in enumerate(session["items"], 1):
        q, r = bank["questions"].get(it["q"]), scored.get(it["q"])
        if q is None or r is None:
            out.append({"n": n, "q": it["q"], "missing": True})
            continue
        texts = {o["id"]: o["text"] for o in q["options"]}
        out.append({"n": n, "q": it["q"], "objective": it["objective"], "type": q["type"], "stem": q["stem"],
                    "options": [{"id": o, "text": texts.get(o, "")} for o in it["options"]],
                    "answer": session["answers"].get(it["q"]), "key": r["key"],
                    "also_accept": r.get("also_accept") or [], "correct": r["correct"],
                    "flagged": it["q"] in session["flags"], "explanation": q["explanation"], "source": q["source"],
                    "changed_since": q["version"] != r["version"]})
    return out


def evidence_row(session: dict[str, Any]) -> dict[str, Any]:
    """The ``academy.assessment`` evidence row for a finished exam: certification and version, correct and
    total by objective, the question ids and versions, and the time taken. Never the answers."""
    res = session.get("result")
    if session.get("status") != "finished" or not res:
        raise ExamError("Only a finished exam gives evidence")
    return {"type": "academy.assessment", "at": session["finished_at"], "pack": session["pack"],
            "cert_version": session["cert_version"], "kind": session["kind"], "session": session["id"],
            "objectives": {t["objective"]: {"correct": t["correct"], "total": t["total"]} for t in res["by_objective"]},
            "items": [{"q": r["q"], "version": r["version"]} for r in res["items"]],
            "timing": {"elapsed": res["elapsed"], "minutes": session["minutes"], "time_up": res["time_up"]}}


# ---------------------------------------------------------------- storage

class ExamStore:
    """Sessions saved as ``<root>/<id>.json``, one per exam, written atomically. Opening a session that was
    running when the app closed leaves its clock stopped at the last heartbeat."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, session_id: str) -> Path:
        if not _SESSION.match(str(session_id)):
            raise ExamError("Not an exam id")
        return self.root / f"{session_id}.json"

    def save(self, session: dict[str, Any]) -> None:
        jsonfile.write_json(self._path(session["id"]), session)

    def load(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.is_file():
            raise ExamError("That exam isn't on this PC")
        session = _read_json(path)
        if session.get("schema") != SESSION_SCHEMA:
            raise ExamError("That exam file isn't one KratosForge can open")
        session["running"] = False   # the clock restarts only when the screen resumes it
        return session

    def list(self) -> list[dict[str, Any]]:
        """Summaries of saved exams, newest first."""
        out = []
        for f in self.root.glob("*.json") if self.root.is_dir() else []:
            if not _SESSION.match(f.stem):
                continue
            try:
                s = _read_json(f)
            except ValueError:
                continue
            res = s.get("result") or {}
            out.append({"id": s.get("id"), "pack": s.get("pack"), "kind": s.get("kind"), "status": s.get("status"),
                        "created_at": s.get("created_at"), "finished_at": s.get("finished_at"),
                        "total": len(s.get("items") or []), "percent": res.get("percent"), "label": RESULT_LABEL})
        return sorted(out, key=lambda s: str(s.get("created_at")), reverse=True)
