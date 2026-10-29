"""Tests for queues.py check(): errors fail the Docs workflow, shortfalls only go in the weekly report.

    python -m unittest discover -s docs/product/operations/tools -p "test_*.py"
"""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import queues  # noqa: E402

BACKLOG = {
    "PB-001": {"id": "PB-001", "title": "One", "state": "Proposed"},
    "PB-002": {"id": "PB-002", "title": "Two", "state": "In progress"},
    "PB-003": {"id": "PB-003", "title": "Three", "state": "Proposed"},
    "PB-009": {"id": "PB-009", "title": "Done", "state": "Accepted"},
}


def _workstream(key: str, active: str, ready: list[dict[str, Any]]) -> dict[str, Any]:
    return {"key": key, "name": key.title(), "scope": "s", "lead": "l", "next_action": "n",
            "active": {"story": active}, "ready": ready, "backlog": [], "blockers": []}


def _queues() -> dict[str, Any]:
    first = _workstream("a", "PB-001", [{"story": "PB-002"}, {"issue": 900, "title": "Issue-only slot"}])
    rest = [_workstream(k, "", []) for k in "bcdef"]
    for ws in rest:
        ws["active"] = {}
    return {"rules": {"ready_minimum": 2}, "workstreams": [first, *rest], "new_stories": {}}


class CheckTest(unittest.TestCase):
    def run_check(self, data: dict[str, Any]) -> tuple[list[str], list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text("no generated table\n", encoding="utf-8")
            with mock.patch.object(queues, "load", return_value=(data, BACKLOG)), \
                    mock.patch.object(queues, "README", readme):
                return queues.check()

    def test_short_queues_are_shortfalls_not_errors(self) -> None:
        errors, short = self.run_check(_queues())
        self.assertEqual(errors, [])
        self.assertIn("b: no active story", short)
        self.assertIn("b: 0 ready stories, needs 2", short)
        self.assertFalse(any(s.startswith("a:") for s in short))

    def test_issue_only_slot_is_accepted(self) -> None:
        errors, _ = self.run_check(_queues())
        self.assertEqual(errors, [])

    def test_slot_without_story_or_title_is_an_error(self) -> None:
        data = _queues()
        data["workstreams"][0]["ready"][1] = {"issue": 900}
        errors, _ = self.run_check(data)
        self.assertTrue(any("900" in e and "no issue and title" in e for e in errors), errors)

    def test_unknown_and_accepted_stories_are_errors(self) -> None:
        data = _queues()
        data["workstreams"][0]["ready"][0] = {"story": "PB-404"}
        data["workstreams"][1]["active"] = {"story": "PB-009"}
        errors, _ = self.run_check(data)
        self.assertTrue(any("PB-404" in e for e in errors), errors)
        self.assertTrue(any("PB-009 is Accepted" in e for e in errors), errors)

    def test_story_in_two_queues_is_an_error(self) -> None:
        data = _queues()
        data["workstreams"][1]["active"] = {"story": "PB-001"}
        errors, _ = self.run_check(data)
        self.assertIn("PB-001 is queued in both a and b", errors)

    def test_wrong_workstream_count_and_missing_field_are_errors(self) -> None:
        data = _queues()
        data["workstreams"].append(copy.deepcopy(data["workstreams"][1]) | {"key": "g"})
        data["workstreams"][0]["lead"] = ""
        errors, _ = self.run_check(data)
        self.assertIn("expected 6 workstreams, found 7", errors)
        self.assertIn("a: no lead", errors)

    def test_unknown_backlog_story_is_an_error(self) -> None:
        data = _queues()
        data["workstreams"][2]["backlog"] = ["PB-003", "PB-777"]
        errors, _ = self.run_check(data)
        self.assertEqual(errors, ["c: backlog story PB-777 doesn't exist"])

    def test_stale_readme_is_an_error_and_a_bad_slot_does_not_crash_it(self) -> None:
        data = _queues()
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(f"x\n{queues.BEGIN}\nold\n{queues.END}\n", encoding="utf-8")
            with mock.patch.object(queues, "load", return_value=(data, BACKLOG)), \
                    mock.patch.object(queues, "README", readme):
                errors, _ = queues.check()
                self.assertIn("README.md's queue table is out of date: run queues.py render", errors)
                data["workstreams"][0]["ready"][1] = {"issue": 900}
                errors, _ = queues.check()
                self.assertFalse(any("out of date" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
