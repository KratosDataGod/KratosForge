"""Academy > Assessments (ACD-013, Preview): practice exams, domain diagnostics and objective drills built from a
pack's original questions (academy/exam.py), for the Assessment Center screen.

Sessions are files under ``<data>/academy/exams``. The clock is honest: it runs only while the screen is open
and sending heartbeats, each gap counts at most a minute, and an exam that was open when the app closed comes
back paused at its last heartbeat. Which exams are running lives only in this engine's memory, so a restart
always starts paused. A result is scored once, at Finish, and kept with the question versions it used; it is
never recalculated. Nothing here reads or writes OneLake, starts a Fabric job or sends anything anywhere.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .academy import exam, registry

_log = logging.getLogger(__name__)

METHODS = {"academy_exam_home", "academy_exam_start", "academy_exam_open", "academy_exam_answer", "academy_exam_flag",
           "academy_exam_check", "academy_exam_heartbeat", "academy_exam_pause", "academy_exam_finish",
           "academy_exam_review"}

PRACTICE_COUNT = 50          # when the certification doesn't publish its exam's length (certification.json "exam")
PRACTICE_MINUTES = 100
DIAGNOSTIC_PER_OBJECTIVE = 2


def _practice(cert: dict[str, Any], available: int) -> tuple[int, int]:
    """A practice exam's questions and minutes: the certification's published ``exam`` (scored questions,
    minutes) when it has one, else PRACTICE_COUNT and PRACTICE_MINUTES; never more questions than the bank has."""
    ex = cert.get("exam") or {}
    scored, mins = ex.get("scored_questions"), ex.get("minutes")
    count: int = scored if isinstance(scored, int) else PRACTICE_COUNT
    minutes: int = mins if isinstance(mins, int) else PRACTICE_MINUTES
    return min(count, available), minutes


def _code(cert: dict[str, Any]) -> str:
    """How the screen names the certification: its exam code (DP-700) for a Fabric pack, else its title."""
    if cert.get("platform", registry.DEFAULT_PLATFORM) == "fabric":
        return str(cert["id"]).upper()
    return str(cert.get("title") or cert["id"])


class AcademyExamMixin:

    def _exam_store(self) -> exam.ExamStore:
        return exam.ExamStore(Path(self.root) / "academy" / "exams")  # type: ignore[attr-defined]

    def _exam_live(self) -> set[str]:
        live = getattr(self, "_academy_exams_running", None)
        if live is None:
            live = set()
            self._academy_exams_running = live
        return live

    def _exam_load(self, session_id: str) -> dict[str, Any]:
        session = self._exam_store().load(str(session_id))
        if session["id"] in self._exam_live() and session.get("status") == "in_progress":
            session["running"] = True
        return session

    def _exam_save(self, session: dict[str, Any]) -> None:
        self._exam_store().save(session)
        live = self._exam_live()
        if session.get("running") and session.get("status") == "in_progress":
            live.add(session["id"])
        else:
            live.discard(session["id"])

    def _exam_view(self, session: dict[str, Any], bank: dict[str, Any] | None = None) -> dict[str, Any]:
        bank = bank or exam.load_bank(session["pack"])
        return {**exam.view(session, bank), "title": _title(session, registry.certification(session["pack"]))}

    def academy_exam_home(self, pack: str = "dp-700") -> dict[str, Any]:
        """The Assessments tab: the notice, the certification's domains and objectives with how many questions
        each has, the three ways to start, and this PC's unfinished and finished exams for the pack."""
        pid = str(pack)
        cert = registry.certification(pid)
        bank = exam.load_bank(pid)
        counts: dict[str, int] = {}
        for q in bank["questions"].values():
            counts[q["objective"]] = counts.get(q["objective"], 0) + 1
        objs = registry.objectives(cert)   # skill areas or, for a guide without them, the domain's own list
        domains = [{"id": d["id"], "title": d["title"], "weight": d.get("weight"),
                    "objectives": [{"id": o["id"], "text": o["text"], "questions": counts.get(o["id"], 0)}
                                   for o in objs if o["domain"] == d["id"]]}
                   for d in cert.get("domains") or []]
        sessions = [s for s in self._exam_store().list() if s.get("pack") == pid]
        count, minutes = _practice(cert, len(bank["questions"]))
        return {"pack": pid, "certification": {"id": cert["id"], "title": cert["title"], "vendor": cert.get("vendor"),
                                               "version": cert.get("version"), "code": _code(cert)},
                "notice": bank["notice"], "label": exam.RESULT_LABEL, "questions": len(bank["questions"]),
                "domains": domains,
                "practice": {"count": count, "minutes": minutes},
                "diagnostic_per_objective": DIAGNOSTIC_PER_OBJECTIVE,
                "unfinished": [s for s in sessions if s.get("status") == "in_progress"],
                "finished": [s for s in sessions if s.get("status") == "finished"]}

    def academy_exam_start(self, pack: str = "dp-700", kind: str = "practice", domain: str | None = None,
                           objectives: list[str] | None = None, timed: bool = True) -> dict[str, Any]:
        """Start an exam and its clock. practice: the certification's published length and time (``_practice``)
        over every objective, or untimed. diagnostic: DIAGNOSTIC_PER_OBJECTIVE per objective in one domain, untimed. drill: every
        question for the objectives picked, untimed, with the answer after each one."""
        pid, kind = str(pack), str(kind)
        cert = registry.certification(pid)
        bank = exam.load_bank(pid)
        known = {o["id"]: o for o in registry.objectives(cert)}
        seen = exam.history(self._exam_store().finished(pid))   # retakes draw unseen, then missed, questions first
        if kind == "practice":
            count, minutes = _practice(cert, len(bank["questions"]))
            session = exam.build(bank, cert_version=cert["version"], count=count,
                                 minutes=minutes if timed else None, kind="practice", history=seen)
        elif kind == "diagnostic":
            ids = [o["id"] for o in known.values() if o["domain"] == str(domain)]
            if not ids:
                raise exam.ExamError("Pick one of the certification's domains")
            session = exam.build(bank, cert_version=cert["version"], objectives=ids,
                                 count=DIAGNOSTIC_PER_OBJECTIVE * len(ids), kind="diagnostic", history=seen)
            session["domain"] = str(domain)
        elif kind == "drill":
            ids = [str(o) for o in objectives or []]
            if not ids or any(o not in known for o in ids):
                raise exam.ExamError("Pick one or more of the certification's objectives")
            session = exam.build(bank, cert_version=cert["version"], objectives=ids, kind="drill")
            session["objectives"] = ids
        else:
            raise exam.ExamError(f"An assessment is one of {', '.join(exam.KINDS)}")
        exam.resume(session)
        self._exam_save(session)
        return self._exam_view(session, bank)

    def academy_exam_open(self, session_id: str) -> dict[str, Any]:
        """An unfinished exam comes back with its clock running again from where it stopped; a finished one
        returns its stored result."""
        session = self._exam_load(session_id)
        if session["status"] == "finished":
            return {"id": session["id"], "status": "finished", "result": _result(session)}
        exam.resume(session)
        self._exam_save(session)
        return self._exam_view(session)

    def academy_exam_answer(self, session_id: str, q: str, choice: list[str]) -> dict[str, Any]:
        """Record an answer (option ids, or every step in order); an empty list clears it."""
        session = self._exam_load(session_id)
        exam.answer(session, str(q), [str(c) for c in choice or []])
        self._exam_save(session)
        return self._exam_view(session)

    def academy_exam_flag(self, session_id: str, q: str, on: bool = True) -> dict[str, Any]:
        session = self._exam_load(session_id)
        exam.flag(session, str(q), bool(on))
        self._exam_save(session)
        return self._exam_view(session)

    def academy_exam_check(self, session_id: str, q: str) -> dict[str, Any]:
        """A drill's answer and why for one question. The answer is locked once checked."""
        session = self._exam_load(session_id)
        bank = exam.load_bank(session["pack"])
        out = exam.check(session, bank, str(q))
        self._exam_save(session)
        return {**out, "view": self._exam_view(session, bank)}

    def academy_exam_heartbeat(self, session_id: str) -> dict[str, Any]:
        """The exam is on screen: count the time since the last heartbeat (at most a minute)."""
        session = self._exam_load(session_id)
        if session["status"] != "in_progress":
            return {"status": session["status"], "running": False}
        exam.heartbeat(session)
        self._exam_save(session)
        return {"status": session["status"], "running": bool(session["running"]), "elapsed": session["elapsed"],
                "time_left": exam.time_left(session)}

    def academy_exam_pause(self, session_id: str) -> dict[str, Any]:
        """Stop the clock: the learner paused or left the screen."""
        session = self._exam_load(session_id)
        if session["status"] == "in_progress":
            exam.pause(session)
            self._exam_save(session)
        return {"status": session["status"], "running": False, "elapsed": session["elapsed"],
                "time_left": exam.time_left(session)}

    def academy_exam_finish(self, session_id: str) -> dict[str, Any]:
        """Score the exam once and keep the result. Unanswered questions count as wrong."""
        session = self._exam_load(session_id)
        exam.finish(session, exam.load_bank(session["pack"]))
        self._exam_save(session)
        return {"id": session["id"], "status": "finished", "result": _result(session)}

    def academy_exam_review(self, session_id: str) -> dict[str, Any]:
        """After finishing: every question with the learner's answer, the right one, why and its source page."""
        session = self._exam_load(session_id)
        items = exam.review(session, exam.load_bank(session["pack"]))
        return {"id": session["id"], "result": _result(session), "items": items}


def _title(session: dict[str, Any], cert: dict[str, Any]) -> str:
    code = _code(cert)
    if session["kind"] == "diagnostic":
        dom = next((d for d in cert.get("domains") or [] if d["id"] == session.get("domain")), None)
        return f"Domain diagnostic: {dom['title']}" if dom else "Domain diagnostic"
    if session["kind"] == "drill":
        ids = session.get("objectives") or []
        return f"Objective drill: {', '.join(ids)}" if len(ids) <= 3 else f"Objective drill: {len(ids)} objectives"
    return f"{code} practice exam"


def _result(session: dict[str, Any]) -> dict[str, Any]:
    """The stored result with the words the screen needs: domain titles and weights, objective text (both from
    the certification version the exam used, when it's still the current one) and the exam's title."""
    cert = registry.certification(session["pack"])
    res = dict(session["result"])
    same = cert.get("version") == session.get("cert_version")
    domains = {d["id"]: d for d in cert.get("domains") or []}
    texts = {o["id"]: o["text"] for o in registry.objectives(cert)} if same else {}
    res["by_domain"] = [{**t, "title": domains.get(t["domain"], {}).get("title") if same else None,
                         "weight": domains.get(t["domain"], {}).get("weight") if same else None}
                        for t in res.get("by_domain") or []]
    res["by_objective"] = [{**t, "text": texts.get(t["objective"])} for t in res.get("by_objective") or []]
    res.pop("items", None)
    return {**res, "title": _title(session, cert), "kind": session["kind"], "pack": session["pack"],
            "code": _code(cert), "vendor": registry.platform(cert)["vendor"],
            "cert_version": session["cert_version"], "minutes": session["minutes"],
            "finished_at": session["finished_at"], "total_items": len(session["items"])}
