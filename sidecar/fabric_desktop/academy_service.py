"""Academy (ACD-006/008 engine): certification packs, exercises, the learner's DuckDB workspaces and
readiness, for the Academy screens (Preview; the screens wait on their mockup).

Everything reads bundled content (academy/registry.py, academy/exercises.py) or works in a workspace
under ``<data>/academy`` (academy/lab.py). Nothing reads or writes OneLake, starts a Fabric job or sends
anything anywhere. A learner view never carries the reference solution or the planted wrong answers. An
exercise that passes is recorded in Profile as evidence for its exam objectives (progress.from_academy);
recording never changes the result.
"""

from __future__ import annotations

import logging
from typing import Any

from . import progress
from .academy import appcontainer, exercises, lab, quizlab, readiness, registry, sandbox
from .learn import content as learn_content
from .redact import describe

_log = logging.getLogger(__name__)

METHODS = {"academy_catalog", "academy_exercise", "academy_start", "academy_save", "academy_run", "academy_validate",
           "academy_hint", "academy_explanation", "academy_reset", "academy_readiness", "academy_sandbox_status",
           "academy_sandbox_remove_grants"}

_SUMMARY = ("id", "title", "objectives", "minutes", "mode", "class", "adapter", "status", "version")


def _summary(ex: dict[str, Any]) -> dict[str, Any]:
    return {k: ex.get(k) for k in _SUMMARY}


class AcademyMixin:

    def academy_catalog(self) -> dict[str, Any]:
        """Every certification pack: its published objectives (word for word, with weights and gap
        records), coverage counts and its exercises (summaries; drafts say so)."""
        out = []
        for pid in registry.pack_ids():
            cert = registry.certification(pid)
            out.append({"pack": registry.pack(pid), "certification": cert, "coverage": registry.coverage(cert),
                        "exercises": [_summary(e) for e in exercises.exercises(pid)]})
        return {"packs": out}

    def academy_exercise(self, pack_id: str, exercise_id: str) -> dict[str, Any]:
        """One exercise as the learner sees it: scenario, requirements, lesson, resources, how it's checked,
        the hint count and its workspace on this PC. Never the reference solution or the explanation
        (academy_explanation gives that, and notes it was opened)."""
        pid, eid = str(pack_id), str(exercise_id)
        ex = exercises.get(pid, eid)
        lesson = ""
        if ex.get("lesson"):
            lesson = exercises.file_text(pid, eid, ex["lesson"])
        elif ex.get("uses"):
            module = learn_content.get(ex["uses"].split("/", 1)[0])
            lesson = learn_content.file_text(module["id"], module["lesson"])
        return {**_summary(ex), "objective_text": ex["objective_text"], "scenario": ex["scenario"],
                "prerequisites": list(ex.get("prerequisites") or []), "expected": ex.get("expected") or {},
                "lesson": lesson, "remediation": list(ex.get("remediation") or []), "reset": ex["reset"],
                "verification": exercises.label(ex), "hints": len(ex.get("hints") or []),
                "checks": [c["kind"] for c in ex["validation"]],
                "quiz": quizlab.view(quizlab.load(lab._grading_folder(pid, eid))) if ex["adapter"] == quizlab.ADAPTER else None,
                "workspace": lab.state(self.root, pid, eid) if ex["adapter"] in lab.LAB_ADAPTERS else None}  # type: ignore[attr-defined]

    def academy_start(self, pack_id: str, exercise_id: str) -> dict[str, Any]:
        """Make the exercise's workspace on this PC if it isn't there yet (kept across restarts)."""
        return lab.start(self.root, str(pack_id), str(exercise_id))  # type: ignore[attr-defined]

    def academy_save(self, pack_id: str, exercise_id: str, text: str) -> dict[str, Any]:
        lab.save(self.root, str(pack_id), str(exercise_id), str(text))  # type: ignore[attr-defined]
        return {"saved": True}

    def academy_run(self, pack_id: str, exercise_id: str, sql: str) -> dict[str, Any]:
        """The first rows of the learner's SQL, run in the workspace and rolled back; for a Python exercise,
        the script's status and output from the learner process (no sign-in, time and memory limits)."""
        return lab.run(self.root, str(pack_id), str(exercise_id), str(sql))  # type: ignore[attr-defined]

    def academy_validate(self, pack_id: str, exercise_id: str, answer: str | None = None) -> dict[str, Any]:
        """Run the exercise's validation. A pass of a published exercise is recorded in Profile against
        its objectives (``recorded``); recording never changes the result."""
        pid, eid = str(pack_id), str(exercise_id)
        result = lab.validate(self.root, pid, eid, None if answer is None else str(answer))  # type: ignore[attr-defined]
        result["recorded"] = False
        if result["passed"]:
            try:
                row = progress.from_academy(result, exercises.get(pid, eid), registry.certification(pid),
                                            hints_used=result["hints_used"], explanation_seen=result["explanation_seen"],
                                            answers_changed=result.get("answers_changed", False))
                self._progress_record(row)  # type: ignore[attr-defined]
                result["recorded"] = row is not None
            except Exception as exc:  # noqa: BLE001 - the profile must never change the validation's result
                _log.warning("Could not record the Academy exercise in Profile: %s", describe(exc))
        return result

    def academy_hint(self, pack_id: str, exercise_id: str, level: int = 1) -> dict[str, Any]:
        return lab.hint(self.root, str(pack_id), str(exercise_id), int(level))  # type: ignore[attr-defined]

    def academy_explanation(self, pack_id: str, exercise_id: str) -> dict[str, Any]:
        return lab.explanation(self.root, str(pack_id), str(exercise_id))  # type: ignore[attr-defined]

    def academy_reset(self, pack_id: str, exercise_id: str) -> dict[str, Any]:
        """Throw the workspace away, answer included, and make it again."""
        return lab.reset(self.root, str(pack_id), str(exercise_id))  # type: ignore[attr-defined]

    def academy_readiness(self, pack_id: str = "dp-700") -> dict[str, Any]:
        """Readiness by objective from this account's evidence: a labelled KratosForge estimate, never the
        vendor's score."""
        return readiness.readiness(progress.load(self._profile_path()), str(pack_id))  # type: ignore[attr-defined]

    def academy_sandbox_status(self) -> dict[str, Any]:
        """For Settings: whether exercise scripts can and do run in an AppContainer on this PC (if not,
        why), and how many paths hold the runtime grant. Reads only."""
        return {**appcontainer.status(), "enabled": sandbox.USE_APPCONTAINER,
                "granted": appcontainer.granted_count(self.root)}  # type: ignore[attr-defined]

    def academy_sandbox_remove_grants(self) -> dict[str, Any]:
        """Settings > Advanced > Exercise sandbox: take the AppContainer's read permission off
        KratosForge's Python. ``{removed, failed}``; a folder Windows wouldn't change is listed, and
        the next exercise run grants it again. Audited (it changes permissions on this PC)."""
        return appcontainer.revoke_runtime(self.root)  # type: ignore[attr-defined]
