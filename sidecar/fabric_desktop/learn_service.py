"""Learn (LX-05): the lessons and labs that ship inside the app, for the Learn page.

Everything here reads the bundled content (learn/content.py) or works in a lab under
``<data>/learn`` (learn/labs.py). Nothing reads or writes OneLake, starts a Fabric job or sends
anything anywhere: a lab's DuckDB connection has no file or network access and every answer is
rolled back. An exercise that passes in a published lesson is recorded in Profile (LX-07), like a
clean Validate run.
"""

from __future__ import annotations

import logging
from typing import Any

from . import progress, verify
from .learn import content, dismissals, fixes, guides, labs, patterns
from .redact import describe
from .sqlcompat import practices

_log = logging.getLogger(__name__)

METHODS = {"learn_catalog", "learn_module", "learn_lab_start", "learn_lab_save", "learn_lab_run",
           "learn_lab_check", "learn_lab_hint", "learn_lab_reset", "learn_rule_guides", "learn_dismissals",
           "learn_dismiss", "learn_undismiss", "learn_fixes", "learn_fix_compare", "learn_fix_checked",
           "learn_patterns"}

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
        """Run the exercise's checks. A pass in a published lesson is recorded in Profile (``earned``: the
        achievements it brought); recording never changes the result."""
        result = labs.check(self.root, str(module_id), str(exercise_id),  # type: ignore[attr-defined]
                            None if answer is None else str(answer))
        result["earned"] = []
        if result["passed"]:
            try:
                result["earned"] = self._progress_observe_lab(result, content.get(str(module_id)))  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - the profile must never change the check's result
                _log.warning("Could not record the Learn exercise in Profile: %s", describe(exc))
        return result

    def learn_lab_hint(self, module_id: str, exercise_id: str, level: int = 1) -> dict[str, Any]:
        return labs.hint(str(module_id), str(exercise_id), int(level))

    def learn_lab_reset(self, module_id: str) -> dict[str, Any]:
        """Throw the lab away, answers included, and make it again."""
        return labs.reset(self.root, str(module_id))  # type: ignore[attr-defined]

    # ---- beside a best-practice finding (LX-09): Explain, Show example and Dismiss

    def learn_rule_guides(self) -> dict[str, Any]:
        """Each notebook and SQL rule's Why, When it's fine and Before/After, from the docs that ship
        with the app (learn/guides.py)."""
        return guides.load()

    def learn_patterns(self) -> dict[str, Any]:
        """The decision patterns (GD-05): each one's title, explanation and an example that runs on this
        PC, from the docs that ship with the app (learn/patterns.py)."""
        return patterns.load()

    def learn_dismissals(self) -> list[dict[str, Any]]:
        """The findings this person dismissed on this PC (learn/dismissals.py)."""
        return dismissals.load(self.root)  # type: ignore[attr-defined]

    def learn_dismiss(self, rule_id: str, scope: str, where: str = "", line: str = "") -> list[dict[str, Any]]:
        """Hide a finding in this person's panels. A rule a policy for the open project names can't be
        dismissed: the organization checks it on purpose."""
        rule_id = str(rule_id)
        kind = "notebook" if rule_id.startswith("NB") else "sql"
        named = self.governance_practice_settings(kind).get("settings") or {}  # type: ignore[attr-defined]
        if (named.get(rule_id) or {}).get("enabled"):
            raise ValueError(f"Your organization's policy checks {rule_id}, so it can't be dismissed")
        return dismissals.add(self.root, rule_id, str(scope), str(where or ""), str(line or ""))  # type: ignore[attr-defined]

    def learn_fixes(self, query: str, target: str = "warehouse", findings: list[dict[str, Any]] | None = None,
                    catalog: dict[str, Any] | None = None) -> dict[str, Any]:
        """Try the fix (GD-02): the rewrites that keep ``query``'s result, for the findings that have one
        (learn/fixes.py). Reads the SQL and the catalog the page sent; nothing runs or changes."""
        return fixes.propose(query, str(target), findings or [], catalog)

    def learn_fix_compare(self, before: str, after: str) -> dict[str, Any]:
        """Compare (GD-02): the rows of two runs that only read, the SQL as written and as fixed, with
        verify.py, and each run's own time. Both runs already happened; this reads their results on
        this PC and runs nothing."""
        (a, ta), (b, tb) = self.sql_runs.table(str(before)), self.sql_runs.table(str(after))  # type: ignore[attr-defined]
        if a.get("change") or b.get("change") or a.get("plan") or b.get("plan"):
            raise ValueError("Compare reads the results of two queries that only read.")
        out = verify.compare(ta, tb)
        return {"result": out["result"], "level": out.get("level", "complete"), "rows": {"before": out["pc"]["rows"],
                "after": out["fabric"]["rows"]}, "limitations": out["limitations"],
                "columns": {"only_before": out["columns"]["only_pc"], "only_after": out["columns"]["only_fabric"],
                            "types": out["columns"]["types"]},
                "seconds": {"before": a.get("seconds"), "after": b.get("seconds")}, "target": a.get("target"),
                "warehouse": a.get("warehouse") or "", "runs": {"before": str(before), "after": str(after)}}

    def learn_fix_checked(self, before: str, after: str, rule_id: str) -> dict[str, Any]:
        """Apply after a matching Compare (GD-06): records the fix in Profile only when the first run's SQL
        has the rule's finding, the second's is different SQL without it, and the two runs' rows,
        compared again here, are the same. A checked fix earns no XP or achievement (rules v3).
        Returns {recorded}: whether Profile has it."""
        if rule_id not in fixes.RULES:
            raise ValueError(f"{rule_id} has no fix to record.")
        # The claim is the engine's own: the first run has the finding, the second is a different query
        # without it, and both returned the same rows. The page's word counts for none of it.
        runs = self.sql_runs  # type: ignore[attr-defined]
        query_before, query_after = runs.query_of(str(before)), runs.query_of(str(after))
        found = lambda q: any(f["rule_id"] == rule_id for f in practices.check(q, "warehouse")["findings"])  # noqa: E731
        if str(before) == str(after) or query_before.strip() == query_after.strip() \
                or not found(query_before) or found(query_after):
            return {"recorded": False}
        compared = self.learn_fix_compare(before, after)
        area = next((r.category for r in practices.RULES if r.id == rule_id), "")
        row = progress.from_fix(rule_id, area, compared, compared["runs"], compared["warehouse"])
        if row is None:
            return {"recorded": False}
        self._progress_record(row)  # type: ignore[attr-defined]
        return {"recorded": any(r["id"] == row["id"] for r in progress.load(self._profile_path())["evidence"])}  # type: ignore[attr-defined]

    def learn_undismiss(self, dismissal_id: str | None = None) -> list[dict[str, Any]]:
        """Show a dismissed finding again, or every one when no id is given."""
        return dismissals.remove(self.root, None if dismissal_id is None else str(dismissal_id))  # type: ignore[attr-defined]
