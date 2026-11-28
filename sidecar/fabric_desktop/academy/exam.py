"""Academy exam engine (ACD-013): original practice questions, timed practice exams, review flags and
results by objective.

Questions are data. Each pack keeps one file per objective under ``packs/<pack>/questions/``
(schema ``kratosforge/academy-question@1``) and shared case studies in ``packs/<pack>/cases.json``.
Every question is original, written for KratosForge from the vendor documentation page it cites (Microsoft
Learn for a Fabric pack, docs.databricks.com for a Databricks pack: the pack's platform ``doc_hosts`` in
registry.py). None is a vendor exam question, and nothing here imitates a vendor's exam interface.

questions/<objective>.json::

    schema, objective, questions [{id ("<objective>-qN"; objectives are 2.2.9 or, for a guide without
    skill areas, 2.3), type ("single" | "multiple" | "order" | "matching" | "hotarea" | "yesno" | "code"),
    stem, options [{id ("a".."h"), text}], answer [option ids; for "order" all of them, in order],
    also_accept? (for "order": other step orders that are equally right, such as two filters that can
    swap), explanation, source (a page on the platform's documentation host), case? (a cases.json id), version (int),
    status ("draft" | "live" | "retired"), reviewed ("YYYY-MM-DD", required when live)}]

The multi-part types have one answer per part, in the parts' order (``PART_TYPES``):

    matching   options are the items (2 to 8); parts [{text}] are the requirements (2 to 6); answer is an
               item id for each requirement, and an item may be the answer to more than one
    hotarea    snippet (text with a numbered blank written {{1}}, {{2}}..., each once and in order),
               snippet_label (what the snippet is: "Copy activity JSON"); parts [{label (the setting the
               blank sets), options [{id, text}] (2 to 6)}], one per blank; no question-level options
    yesno      parts [{text}] are the statements (2 to 5); answer is "yes" or "no" for each; no options
    code       language ("pyspark" | "tsql"), snippet with numbered gaps written {{1}}...; options are the
               token bank, more tokens than gaps (up to 8); answer is a token for each gap, each token once

A multi-part question earns partial credit: k of its n parts right earns k/n of its one point. A learner's
answer to one is a list with an entry for every part ("" while a part is empty); it counts as answered only
when every part has one, and a question not answered earns nothing.

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
* ``finish`` scores the session and stores the result inside it. Totals are points: a question is worth
  one, and a multi-part question earns the share of its parts that are right, so totals and objective
  scores can be fractional (rounded to 2 places when stored). A finished session is never scored
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
from fractions import Fraction
from pathlib import Path
from typing import Any

from .. import jsonfile
from . import registry

QUESTION_SCHEMA = "kratosforge/academy-question@1"
SESSION_SCHEMA = "kratosforge/academy-exam-session@1"
PACKS = Path(__file__).resolve().parent / "packs"

CHOICE_TYPES = ("single", "multiple", "order")
PART_TYPES = ("matching", "hotarea", "yesno", "code")      # one answer per part, partial credit
TYPES = CHOICE_TYPES + PART_TYPES
CODE_LANGUAGES = {"pyspark": "PySpark", "tsql": "T-SQL"}
YES_NO = ({"id": "yes", "text": "Yes"}, {"id": "no", "text": "No"})
STATUSES = ("draft", "live", "retired")
KINDS = ("practice", "diagnostic", "drill")   # a drill shows the answer and why after each question
MIN_PER_OBJECTIVE = 4        # live questions every objective needs before the pack ships
MAX_GAP = 60.0               # most seconds one heartbeat or action may add to the clock
MAX_MINUTES = 300
SOURCE_HOSTS = registry.PLATFORMS[registry.DEFAULT_PLATFORM]["doc_hosts"]   # a Fabric pack's; see source_hosts()
DOCS_NAMES = {"fabric": "Microsoft Learn", "databricks": "Databricks documentation"}
RESULT_LABEL = "KratosForge practice result"


def notice_for(vendor: str, vendors: str | None = None) -> str:
    """The words every exam screen and result carries: original questions, not the vendor's exam or score.
    ``vendors`` is the possessive (registry PLATFORMS ``vendors``: "Databricks'"); "<vendor>'s" when None."""
    poss = vendors or f"{vendor}'s"
    return (f"Original practice questions written for KratosForge. They are not {vendor} exam questions, this is "
            f"not {poss} exam interface, and the result is not a {vendor} score or a prediction of passing.")


NOTICE = notice_for("Microsoft", "Microsoft's")

_OBJECTIVE = re.compile(r"^\d{1,2}\.\d{1,2}(?:\.\d{1,2})?$")
_QID = re.compile(r"^(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)-q(\d{1,2})$")
_OPTION = re.compile(r"^[a-h]$")
_CASE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_SESSION = re.compile(r"^[0-9a-f]{32}$")
_PACK = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HTML = re.compile(r"<[A-Za-z!/]")
_BLANK = re.compile(r"\{\{(\d)\}\}")


class ExamError(ValueError):
    """A request the exam can't carry out, in words the screen can show."""


# ---------------------------------------------------------------- question bank

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pack_root(pack: str, packs: Path = PACKS) -> Path:
    if not _PACK.fullmatch(str(pack)) or not (packs / pack).is_dir():
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
    plat = registry.PLATFORMS[_platform_name(_pack_root(pack, packs))]
    return notice_for(plat["vendor"], plat.get("vendors"))


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
    plat = registry.PLATFORMS[_platform_name(root)]
    return {"pack": pack, "questions": questions, "cases": dict(cases),
            "notice": notice_for(plat["vendor"], plat.get("vendors"))}


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
    m = _QID.fullmatch(qid)
    out = [] if m and m.group(1) == objective else [f"{where}: id is <objective>-qN, like {objective}-q1"]
    if q.get("type") not in TYPES:
        return out + [f"{where}: type is one of {', '.join(TYPES)}"]
    out += _text_problems(f"{where} stem", q.get("stem"))
    out += _text_problems(f"{where} explanation", q.get("explanation"))
    out += _parts_problems(where, q) if q["type"] in PART_TYPES else _choice_problems(where, q)
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
    elif q["status"] == "live" and not _DATE.fullmatch(str(q.get("reviewed") or "")):
        out.append(f"{where}: a live question needs the date it was last reviewed (reviewed: YYYY-MM-DD)")
    return out


def _options_problems(where: str, options: Any, what: str = "options") -> tuple[list[str], list[str]]:
    """Problems with a list of {id, text} options, and their ids."""
    options = options if isinstance(options, list) else []
    ids: list[str] = [str(o.get("id")) for o in options if isinstance(o, dict)]
    out = []
    if len(ids) != len(options) or len(set(ids)) != len(ids) or not all(_OPTION.fullmatch(str(i)) for i in ids):
        out.append(f"{where}: {what} each need their own id, a to h")
    for o in options:
        if isinstance(o, dict):
            out += _text_problems(f"{where} option {o.get('id')}", o.get("text"))
    return out, ids


def _blank_count(where: str, snippet: Any, word: str) -> tuple[list[str], int]:
    """A snippet's numbered blanks, which must be {{1}}, {{2}}... each once and in order."""
    if not isinstance(snippet, str) or not snippet.strip():
        return [f"{where} snippet is empty"], 0
    found = [int(n) for n in _BLANK.findall(snippet)]
    if not found or found != list(range(1, len(found) + 1)):
        return [f"{where}: the snippet's {word}s are written {{{{1}}}}, {{{{2}}}} and so on, each once and in order"], len(found)
    return [], len(found)


def _parts_problems(where: str, q: dict[str, Any]) -> list[str]:
    """The answer shapes of the multi-part types (see the module docstring)."""
    t = q["type"]
    out: list[str] = []
    if q.get("also_accept") is not None:
        out.append(f"{where}: also_accept is for ordering questions: other full step orders, each different from the answer")
    parts = q.get("parts")
    answer = q.get("answer")
    if not isinstance(answer, list) or not all(isinstance(a, str) for a in answer):
        return [f"{where}: answer is a list with one entry for each part"]
    if t == "code":
        if parts is not None:
            out.append(f"{where}: a code completion question has gaps in its snippet, not parts")
        if q.get("language") not in CODE_LANGUAGES:
            out.append(f"{where}: language is one of {', '.join(CODE_LANGUAGES)}")
        more, gaps = _blank_count(where, q.get("snippet"), "gap")
        bad, ids = _options_problems(where, q.get("options"), "tokens")
        out += more + bad
        if not 2 <= gaps <= 6 or not gaps < len(ids) <= 8:
            out.append(f"{where}: a code completion question has 2 to 6 gaps and more tokens than gaps, up to 8")
        if len(answer) != gaps or any(a not in ids for a in answer) or len(set(answer)) != len(answer):
            out.append(f"{where}: answer is a different token for each gap")
        return out
    if not isinstance(parts, list) or not all(isinstance(p, dict) for p in parts):
        return out + [f"{where}: parts is a list of objects"]
    if len(answer) != len(parts):
        out.append(f"{where}: answer has one entry for each part")
    if t == "matching":
        bad, ids = _options_problems(where, q.get("options"), "items")
        out += bad
        for n, p in enumerate(parts, 1):
            out += _text_problems(f"{where} requirement {n}", p.get("text"))
        if not 2 <= len(ids) <= 8 or not 2 <= len(parts) <= 6:
            out.append(f"{where}: a matching question has 2 to 8 items and 2 to 6 requirements")
        if any(a not in ids for a in answer):
            out.append(f"{where}: answer names items that aren't there")
        elif len(set(answer)) < 2:
            out.append(f"{where}: a matching question's answer uses at least two different items")
    elif t == "hotarea":
        if q.get("options"):
            out.append(f"{where}: a hot area question's choices are in each part, not in options")
        out += _text_problems(f"{where} snippet_label", q.get("snippet_label"))
        more, blanks = _blank_count(where, q.get("snippet"), "blank")
        out += more
        if blanks != len(parts) or not 2 <= len(parts) <= 6:
            out.append(f"{where}: a hot area question has 2 to 6 blanks and a part for each")
        for n, (p, a) in enumerate(zip(parts, answer, strict=False), 1):
            out += _text_problems(f"{where} blank {n} label", p.get("label"))
            bad, ids = _options_problems(f"{where} blank {n}", p.get("options"))
            out += bad
            if not 2 <= len(ids) <= 6:
                out.append(f"{where}: blank {n} has 2 to 6 choices")
            if a not in ids:
                out.append(f"{where}: blank {n}'s answer isn't one of its choices")
    elif t == "yesno":
        if q.get("options"):
            out.append(f"{where}: a yes or no question has no options; its answers are yes and no")
        for n, p in enumerate(parts, 1):
            out += _text_problems(f"{where} statement {n}", p.get("text"))
        if not 2 <= len(parts) <= 5:
            out.append(f"{where}: a yes or no question has 2 to 5 statements")
        if any(a not in ("yes", "no") for a in answer):
            out.append(f"{where}: each statement's answer is yes or no")
    return out


def _choice_problems(where: str, q: dict[str, Any]) -> list[str]:
    """The answer shapes of single, multiple and order."""
    out, ids = _options_problems(where, q.get("options"))
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
            if not _CASE.fullmatch(str(cid)) or not isinstance(case, dict):
                out.append(f"case {cid}: ids are lowercase words joined by hyphens")
                continue
            out += _text_problems(f"case {cid} title", case.get("title"))
            out += _text_problems(f"case {cid} text", case.get("text"))
    live: dict[str, int] = {}
    seen: set[str] = set()
    used_cases: set[str] = set()
    for f in sorted((root / "questions").glob("*")):
        objective = f.stem
        if f.suffix != ".json" or not _OBJECTIVE.fullmatch(objective):
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
          kind: str = "practice", now: dt.datetime | None = None,
          history: dict[str, str] | None = None) -> dict[str, Any]:
    """A new, paused session. Questions are spread evenly over ``objectives`` (all the bank's when None),
    up to ``count`` (all of them when None). Questions sharing a case study sit together. ``minutes`` None
    is untimed. ``history`` (from ``history()``) makes a retake draw, within each objective, questions the
    learner hasn't seen first, then ones they got wrong, then ones they got right. The same bank, seed and
    history give the same questions, order and option orders."""
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
    rank = {"wrong": 1, "right": 2}
    seen = history or {}
    # Each pool is popped from the end, so the questions to ask first go last: a stable sort keeps the
    # random order within each group.
    pools = {o: sorted(rng.sample(ids, len(ids)), key=lambda q: -rank.get(seen.get(q, ""), 0))
             for o, ids in sorted(by_objective.items())}
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
        ids = [o["id"] for o in q.get("options") or []]
        item = {"q": qid, "version": q["version"], "objective": q["objective"], "options": rng.sample(ids, len(ids))}
        if q["type"] in PART_TYPES:      # what each part may take, so answer() can check without the bank
            item["type"] = q["type"]
            item["parts"] = [[o["id"] for o in opts] for opts in _part_options(q, item["options"])]
            if q["type"] == "hotarea":   # each blank's choices in its own order, so position gives nothing away
                item["parts"] = [rng.sample(ids_, len(ids_)) for ids_ in item["parts"]]
        items.append(item)
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
    """Record the learner's answer: the option ids they picked, or for ordering every step in their order, or
    for a multi-part question one entry per part ("" for a part not answered yet; a code completion token in
    one gap at most). An empty list, or every part empty, clears the answer. A timed exam whose time is used
    takes no more answers."""
    _open(session)
    _tick(session, now or _now())
    item = _item(session, qid)
    if time_left(session) == 0:
        raise ExamError("Time is up: finish the exam to see your results")
    if qid in (session.get("checked") or []):
        raise ExamError("That answer was already checked, so it can't change")
    choice = [str(c) for c in choice or []]
    if item.get("parts") is not None:
        parts = item["parts"]
        if choice and (len(choice) != len(parts) or any(c and c not in ok for c, ok in zip(choice, parts, strict=True))):
            raise ExamError("That answer names an option the question doesn't have")
        filled = [c for c in choice if c]
        if item.get("type") == "code" and len(set(filled)) != len(filled):
            raise ExamError("Each token goes in one gap at most")
        if not filled:
            choice = []
    elif any(c not in item["options"] for c in choice) or len(set(choice)) != len(choice):
        raise ExamError("That answer names an option the question doesn't have")
    if choice:
        session["answers"][qid] = choice
    else:
        session["answers"].pop(qid, None)
    return session


def _complete(item: dict[str, Any], choice: list[str] | None) -> bool:
    """Whether a learner's answer counts as answered: any choice, or for a multi-part question every part."""
    if not choice:
        return False
    return all(choice) if item.get("parts") is not None else True


def flag(session: dict[str, Any], qid: str, on: bool = True, now: dt.datetime | None = None) -> dict[str, Any]:
    """Mark a question for review, or clear the mark. Flags never change the score."""
    _open(session)
    _tick(session, now or _now())
    _item(session, qid)
    flags = [f for f in session["flags"] if f != qid]
    session["flags"] = flags + [qid] if on else flags
    return session


def _part_options(q: dict[str, Any], order: list[str] | None = None) -> list[list[dict[str, Any]]]:
    """The choices for each part of a multi-part question: the items or tokens (in ``order``, the session's
    shuffle, when given) for matching and code, each blank's own for a hot area, Yes and No for yes or no."""
    t = q["type"]
    if t == "yesno":
        return [list(YES_NO) for _ in q["parts"]]
    if t == "hotarea":
        return [list(p["options"]) for p in q["parts"]]
    texts = {o["id"]: o for o in q["options"]}
    shared = [texts[i] for i in order if i in texts] if order else list(q["options"])
    return [shared for _ in (q["answer"] if t == "code" else q["parts"])]


def part_count(q: dict[str, Any]) -> int:
    """How many parts a question has: 1, except for the multi-part types."""
    return len(q["answer"]) if q["type"] in PART_TYPES else 1


def _score(q: dict[str, Any], choice: list[str] | None) -> tuple[int, int]:
    """(parts right, parts). A multi-part question's parts are scored one by one, but only once every part
    is answered: one with an empty part isn't answered, and scores nothing."""
    n = part_count(q)
    if q["type"] not in PART_TYPES:
        return (1 if _correct(q, choice) else 0), 1
    if not choice or len(choice) != n or not all(choice):
        return 0, n
    return sum(1 for c, k in zip(choice, q["answer"], strict=True) if c == k), n


def _correct(q: dict[str, Any], choice: list[str] | None) -> bool:
    """Fully right: a single or multiple answer must pick exactly the right options; an ordering every
    step in the right place; a multi-part question every part."""
    if not choice:
        return False
    if q["type"] in PART_TYPES:
        right, n = _score(q, choice)
        return right == n
    if q["type"] == "order":
        return choice == q["answer"] or choice in (q.get("also_accept") or [])
    return sorted(choice) == sorted(q["answer"])


def _num(x: Fraction | float | int) -> int | float:
    """A score as stored: a whole number when it is one, else rounded to 2 places."""
    r = round(float(x), 2)
    return int(r) if r.is_integer() else r


def _points(row: dict[str, Any]) -> Fraction:
    """A scored row's points, exactly. Rows from before partial credit have only ``correct``."""
    if "parts_right" in row and row.get("parts"):
        return Fraction(int(row["parts_right"]), int(row["parts"]))
    return Fraction(1 if row.get("correct") else 0)


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
    if not _complete(_item(session, qid), choice):
        raise ExamError("Answer every part of the question first")
    checked = session.setdefault("checked", [])
    if qid not in checked:
        checked.append(qid)
    right, n = _score(q, choice)
    return {"q": qid, "correct": right == n, "parts_right": right, "part_count": n, "points": _num(Fraction(right, n)),
            "answer": list(choice), "key": list(q["answer"]),
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
        case = bank["cases"].get(q["case"]) if q.get("case") else None
        choice = session["answers"].get(it["q"])
        items.append({"n": n, "q": it["q"], "objective": it["objective"], "type": q["type"], "stem": q["stem"],
                      "select": _expected_count(q), "case": {"id": q["case"], **case} if case else None,
                      **_shown(q, it), "answer": choice, "answered": _complete(it, choice),
                      "flagged": it["q"] in session["flags"],
                      "checked": it["q"] in (session.get("checked") or [])})
    return {"id": session["id"], "pack": session["pack"], "cert_version": session["cert_version"],
            "kind": session["kind"], "status": session["status"], "items": items,
            "answered": sum(1 for it in session["items"] if _complete(it, session["answers"].get(it["q"]))),
            "flagged": len(session["flags"]), "total": len(session["items"]),
            "elapsed": float(session["elapsed"]), "minutes": session["minutes"], "time_left": time_left(session),
            "running": bool(session.get("running")), "notice": bank.get("notice", NOTICE)}


def _shown(q: dict[str, Any], it: dict[str, Any]) -> dict[str, Any]:
    """What the screen draws of a question's choices, in the session's order: its options and, for a
    multi-part question, each part's words and choices, and the snippet. Never which choice is right."""
    texts = {o["id"]: o["text"] for o in q.get("options") or []}
    out: dict[str, Any] = {"options": [{"id": o, "text": texts.get(o, "")} for o in it["options"]]}
    if q["type"] not in PART_TYPES:
        return out
    labels = q.get("parts") or [{} for _ in q["answer"]]
    choices = _part_options(q, it["options"])
    if q["type"] == "hotarea" and it.get("parts"):     # the session's order for each blank
        reordered: list[list[dict[str, Any]]] = []
        for opts, ids in zip(choices, it["parts"], strict=True):
            rank = {i: n for n, i in enumerate(ids)}
            reordered.append(sorted(opts, key=lambda o: rank.get(o["id"], len(rank))))
        choices = reordered
    out["parts"] = [{"n": n, "text": p.get("text"), "label": p.get("label"),
                     "options": [{"id": o["id"], "text": o["text"]} for o in opts]}
                    for n, (p, opts) in enumerate(zip(labels, choices, strict=True), 1)]
    if q.get("snippet") is not None:
        out["snippet"] = q["snippet"]
        out["snippet_label"] = q.get("snippet_label") or f"{CODE_LANGUAGES.get(q.get('language', ''), 'The')} code"
    return out


def _domain(objective: str) -> str:
    return objective.split(".")[0]


def _tally(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = out.setdefault(r[key], {key: r[key], "correct": Fraction(0), "total": 0})
        t["total"] += 1
        t["correct"] += _points(r)
    return [{**t, "correct": _num(t["correct"]), "percent": round(100 * t["correct"] / t["total"])}
            for _, t in sorted(out.items(), key=_order_key)]


def _order_key(kv: tuple[str, Any]) -> tuple[int, ...]:
    return tuple(int(x) for x in kv[0].split("."))


def finish(session: dict[str, Any], bank: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    """Score the exam and keep the result in the session. Each question is worth a point; a multi-part
    question earns the share of its parts that are right. Unanswered questions earn nothing. ``correct``
    totals are points (fractional when partial credit was earned). Questions removed from the bank since
    the exam started are left out of the score and listed."""
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
        right, n = _score(q, choice)
        rows.append({"q": it["q"], "version": q["version"], "objective": it["objective"],
                     "domain": _domain(it["objective"]), "answered": _complete(it, choice),
                     "correct": right == n, "parts_right": right, "parts": n, "points": _num(Fraction(right, n)),
                     "key": list(q["answer"]),
                     "also_accept": [list(a) for a in q.get("also_accept") or []]})
    correct = sum((_points(r) for r in rows), Fraction(0))
    session["result"] = {
        "label": RESULT_LABEL, "notice": bank.get("notice", NOTICE), "correct": _num(correct), "total": len(rows),
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
        points = _points(r)
        out.append({"n": n, "q": it["q"], "objective": it["objective"], "type": q["type"], "stem": q["stem"],
                    **_shown(q, it), "answer": session["answers"].get(it["q"]), "key": r["key"],
                    "also_accept": r.get("also_accept") or [], "correct": r["correct"],
                    "answered": r.get("answered", bool(session["answers"].get(it["q"]))),
                    "points": _num(points), "parts_right": r.get("parts_right", int(points)), "part_count": r.get("parts", 1),
                    "flagged": it["q"] in session["flags"], "explanation": q["explanation"], "source": q["source"],
                    "changed_since": q["version"] != r["version"]})
    return out


def history(sessions: list[dict[str, Any]]) -> dict[str, str]:
    """What a learner has done with each question in their finished exams: ``"wrong"`` when any attempt
    got it wrong (until a later attempt gets it right), else ``"right"``. Questions never asked aren't listed.
    A multi-part question earning only partial credit is ``"wrong"``: it isn't known yet, so a retake asks it
    again before questions that were fully right."""
    out: dict[str, str] = {}
    done = [s for s in sessions if s.get("status") == "finished" and isinstance(s.get("result"), dict)]
    for s in sorted(done, key=lambda s: str(s.get("finished_at"))):
        for r in s["result"].get("items") or []:
            if isinstance(r, dict) and isinstance(r.get("q"), str):
                out[r["q"]] = "right" if r.get("correct") else "wrong"
    return out


def evidence_row(session: dict[str, Any]) -> dict[str, Any]:
    """The ``academy.assessment`` evidence row for a finished exam: certification and version, correct and
    total by objective (``correct`` in points, so fractional with partial credit), the question ids and
    versions, and the time taken. Never the answers."""
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
        if not _SESSION.fullmatch(str(session_id)):
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

    def finished(self, pack: str) -> list[dict[str, Any]]:
        """Every finished exam for ``pack`` on this PC, in full. Unreadable files are skipped."""
        out = []
        for f in self.root.glob("*.json") if self.root.is_dir() else []:
            if not _SESSION.fullmatch(f.stem):
                continue
            try:
                s = _read_json(f)
            except ValueError:
                continue
            if isinstance(s, dict) and s.get("pack") == pack and s.get("status") == "finished":
                out.append(s)
        return out

    def list(self) -> list[dict[str, Any]]:
        """Summaries of saved exams, newest first."""
        out = []
        for f in self.root.glob("*.json") if self.root.is_dir() else []:
            if not _SESSION.fullmatch(f.stem):
                continue
            try:
                s = _read_json(f)
            except ValueError:
                continue
            res = s.get("result") or {}
            out.append({"id": s.get("id"), "pack": s.get("pack"), "kind": s.get("kind"), "domain": s.get("domain"), "status": s.get("status"),
                        "created_at": s.get("created_at"), "finished_at": s.get("finished_at"),
                        "total": len(s.get("items") or []), "percent": res.get("percent"), "label": RESULT_LABEL})
        return sorted(out, key=lambda s: str(s.get("created_at")), reverse=True)
