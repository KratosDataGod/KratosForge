"""Learn (LX-05): the lessons and labs that ship inside the app, for the Learn page.

Everything here reads the bundled content (learn/content.py) or works in a lab under
``<data>/learn`` (learn/labs.py). Nothing reads or writes OneLake, starts a Fabric job or sends
anything anywhere: a lab's DuckDB connection has no file or network access and every answer is
rolled back.
"""

from __future__ import annotations

from typing import Any

from .learn import content, labs

METHODS = {"learn_catalog", "learn_module", "learn_lab_start", "learn_lab_save", "learn_lab_run",
           "learn_lab_check", "learn_lab_hint", "learn_lab_reset"}

_SUMMARY = ("id", "kind", "title", "path", "level", "minutes", "objectives", "prerequisites", "platforms", "engines",
            "rules", "docs", "version", "status")


def _summary(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m.get(k) for k in _SUMMARY}


class LearnMixin:

    def learn_catalog(self) -> dict[str, Any]:
        """{paths, modules}: every path and module, with whether this PC has started its lab. Drafts are
        listed with status "draft"; the whole page is Preview."""
        root = self.root  # type: ignore[attr-defined]
        modules = []
        for m in content.modules():
            row = _summary(m)
            row["started"] = labs.state(root, m["id"])["started"]
            modules.append(row)
        return {"paths": content.paths(), "modules": modules}

    def learn_module(self, module_id: str) -> dict[str, Any]:
        """One module: its summary, lesson text (Markdown), exercises (prompt and hint count, never the
        solution) and its lab's state on this PC."""
        m = content.get(str(module_id))
        exercises = [{"id": ex["id"], "prompt": ex.get("prompt", ""), "engine": ex["engine"],
                      "hints": len(ex.get("hints") or []), "checks": [c["kind"] for c in ex["checks"]]}
                     for ex in m["exercises"]]
        return {**_summary(m), "lesson": content.file_text(m["id"], m["lesson"]), "exercises": exercises,
                "size": m.get("size") or "S", "datasets": m.get("datasets") or [],
                "lab": labs.state(self.root, m["id"])}  # type: ignore[attr-defined]

    def learn_lab_start(self, module_id: str) -> dict[str, Any]:
        """Make the module's lab on this PC (generated data, no network) if it isn't there yet."""
        return labs.start(self.root, str(module_id))  # type: ignore[attr-defined]

    def learn_lab_save(self, module_id: str, exercise_id: str, text: str) -> dict[str, Any]:
        labs.save_answer(self.root, str(module_id), str(exercise_id), str(text))  # type: ignore[attr-defined]
        return {"saved": True}

    def learn_lab_run(self, module_id: str, sql: str) -> dict[str, Any]:
        """The first rows of the learner's SQL, run in the lab and rolled back."""
        return labs.run(self.root, str(module_id), str(sql))  # type: ignore[attr-defined]

    def learn_lab_check(self, module_id: str, exercise_id: str, answer: str | None = None) -> dict[str, Any]:
        return labs.check(self.root, str(module_id), str(exercise_id),  # type: ignore[attr-defined]
                          None if answer is None else str(answer))

    def learn_lab_hint(self, module_id: str, exercise_id: str, level: int = 1) -> dict[str, Any]:
        return labs.hint(str(module_id), str(exercise_id), int(level))

    def learn_lab_reset(self, module_id: str) -> dict[str, Any]:
        """Throw the lab away, answers included, and make it again."""
        return labs.reset(self.root, str(module_id))  # type: ignore[attr-defined]
