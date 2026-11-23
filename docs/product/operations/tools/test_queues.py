"""Tests for queues.py check(): errors fail the Docs workflow, shortfalls only go in the weekly report.

    python -m unittest discover -s docs/product/operations/tools -p "test_*.py"
"""
from __future__ import annotations

import copy
import json
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


def _ledger() -> dict[str, Any]:
    return {"lanes": [{"key": "eng", "name": "Engine", "workstream": "a", "blocks": [{"first": "WR-100", "last": "WR-199"}]},
                      {"key": "nb", "name": "Notebook", "workstream": "b", "blocks": [{"first": "WR-130", "last": "WR-134"}]},
                      {"key": "wh", "name": "Warehouse", "workstream": "c", "blocks": [{"first": "WR-200", "last": "WR-269"}]},
                      {"key": "tr", "name": "Translator", "workstream": "c", "blocks": [{"first": "WR-270", "last": "WR-299"}]},
                      {"key": "gov", "name": "Governance", "workstream": "d", "blocks": [{"first": "WR-400", "last": "WR-499"}]}],
            "exceptions": [{"first": "WR-410", "last": "WR-410", "lanes": ["gov"], "note": "capacity and audit"}]}


class _Base(unittest.TestCase):
    def run_check(self, data: dict[str, Any], ledger: dict[str, Any] | None = None,
                  text: str | None = None) -> tuple[list[str], list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text("no generated table\n", encoding="utf-8")
            queue_file = Path(tmp) / "workstreams.json"
            queue_file.write_text(json.dumps(data) if text is None else text, encoding="utf-8")
            with mock.patch.object(queues, "load", return_value=(data, BACKLOG)), \
                    mock.patch.object(queues, "load_ledger", return_value=ledger or _ledger()), \
                    mock.patch.object(queues, "README", readme), mock.patch.object(queues, "QUEUES", queue_file):
                return queues.check()


class CheckTest(_Base):
    def test_ci_summary_counts_cancelled_apart_from_failed(self) -> None:
        ci = ["success", "cancelled", "cancelled", "failure", "timed_out", "in_progress"]
        self.assertEqual(queues.ci_summary(ci), "1 green, 2 failed, 2 cancelled, 1 running or other")
        self.assertEqual(queues.ci_summary(["success"]), "1 green, 0 failed, 0 cancelled")

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
            queue_file = Path(tmp) / "workstreams.json"
            queue_file.write_text(json.dumps(data), encoding="utf-8")
            with mock.patch.object(queues, "load", return_value=(data, BACKLOG)), \
                    mock.patch.object(queues, "load_ledger", return_value=_ledger()), \
                    mock.patch.object(queues, "README", readme), mock.patch.object(queues, "QUEUES", queue_file):
                errors, _ = queues.check()
                self.assertIn("README.md's queue table is out of date: run queues.py render", errors)
                data["workstreams"][0]["ready"][1] = {"issue": 900}
                errors, _ = queues.check()
                self.assertFalse(any("out of date" in e for e in errors), errors)


def _side() -> dict[str, Any]:
    return {"key": "pbe", "name": "Embed", "workstream": "a", "thread": "t", "review_cap": 2,
            "stories": [{"issue": 1127, "title": "Host", "status": "active"},
                        {"issue": 1130, "title": "Screen", "status": "blocked", "note": "mockup"}]}


class SideProgramTest(_Base):
    def test_valid_side_program_has_no_errors(self) -> None:
        data = _queues() | {"side_programs": [_side()]}
        errors, _ = self.run_check(data)
        self.assertEqual(errors, [])

    def test_bad_side_program_fields_are_errors(self) -> None:
        sp = _side() | {"workstream": "zz", "review_cap": 9}
        sp["stories"] = [{"issue": 1127, "title": "Host", "status": "doing"}, {"issue": 1128}]
        errors, _ = self.run_check(_queues() | {"side_programs": [sp]})
        self.assertIn("side program pbe: workstream zz doesn't exist", errors)
        self.assertIn("side program pbe: review_cap must be 1 to 4", errors)
        self.assertTrue(any("#1127 has status doing" in e for e in errors), errors)
        self.assertIn("side program pbe: a story needs an issue number and a title", errors)

    def test_boolean_cap_is_an_error(self) -> None:
        errors, _ = self.run_check(_queues() | {"side_programs": [_side() | {"review_cap": True}]})
        self.assertIn("side program pbe: review_cap must be 1 to 4", errors)

    def test_over_cap_is_reported_not_failed(self) -> None:
        sp = _side() | {"review_cap": 1}
        sp["stories"] = [{"issue": 1127, "title": "Host", "status": "review"}, {"issue": 1128, "title": "Token", "status": "review"}]
        errors, short = self.run_check(_queues() | {"side_programs": [sp]})
        self.assertEqual(errors, [])
        self.assertIn("side program pbe: 2 stories at review, over its cap of 1", short)

    def test_missing_key_is_an_error_not_a_crash(self) -> None:
        sp = _side() | {"key": "", "review_cap": 1}
        sp["stories"] = [{"issue": 1127, "title": "Host", "status": "review"}, {"issue": 1128, "title": "Token", "status": "review"}]
        errors, short = self.run_check(_queues() | {"side_programs": [sp]})
        self.assertIn("side program ?: no key", errors)
        self.assertIn("side program ?: 2 stories at review, over its cap of 1", short)

    def test_issue_in_a_queue_and_a_side_program_is_an_error(self) -> None:
        sp = _side()
        sp["stories"].append({"issue": 900, "title": "Issue-only slot", "status": "ready"})
        errors, _ = self.run_check(_queues() | {"side_programs": [sp]})
        self.assertIn("#900 is queued in both a and pbe", errors)

    def test_side_table_renders_and_stale_one_is_an_error(self) -> None:
        data = _queues() | {"side_programs": [_side()]}
        table = queues.side_table(data)
        self.assertIn("| Embed | A | t | 2 | active: Host ([#1127]", table)
        self.assertIn("blocked: Screen ([#1130]", table)
        self.assertIn("(mockup)", table)
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(f"x\n{queues.SIDE_BEGIN}\nold\n{queues.SIDE_END}\n", encoding="utf-8")
            queue_file = Path(tmp) / "workstreams.json"
            queue_file.write_text(json.dumps(data), encoding="utf-8")
            with mock.patch.object(queues, "load", return_value=(data, BACKLOG)), \
                    mock.patch.object(queues, "load_ledger", return_value=_ledger()), \
                    mock.patch.object(queues, "README", readme), mock.patch.object(queues, "QUEUES", queue_file):
                errors, _ = queues.check()
                self.assertIn("README.md's queue table is out of date: run queues.py render", errors)
                queues.render()
                self.assertEqual(queues.check()[0], [])
                self.assertIn(table, readme.read_text(encoding="utf-8"))


def _wr(title: str) -> dict[str, Any]:
    return {"title": title, "priority": "P2", "problem": "p", "acceptance": ["a"], "agent": "x"}


def _wr_queues() -> dict[str, Any]:
    """Workstream a queues Engine ids, b a Notebook id, c Warehouse and Translator ids, d a Governance id."""
    data = _queues()
    data["new_stories"] = {"WR-101": _wr("Engine one"), "WR-102": _wr("Engine two"), "WR-131": _wr("Notebook one"),
                           "WR-238": _wr("Warehouse one"), "WR-270": _wr("Translator one"), "WR-410": _wr("Audit")}
    data["workstreams"][0]["backlog"] = ["WR-101", "WR-102"]
    data["workstreams"][1]["active"] = {"story": "WR-131"}
    data["workstreams"][2]["active"] = {"story": "WR-238"}
    data["workstreams"][2]["ready"] = [{"story": "WR-270"}]
    data["workstreams"][3]["active"] = {"story": "WR-410"}
    return data


class LedgerTest(_Base):
    def test_ids_in_their_lanes_blocks_pass(self) -> None:
        errors, _ = self.run_check(_wr_queues())
        self.assertEqual(errors, [])

    def test_id_outside_its_workstreams_blocks_names_the_slot_and_the_owner(self) -> None:
        data = _wr_queues()
        data["new_stories"]["WR-250"] = _wr("Translator, old range")
        data["workstreams"][0]["ready"] = [{"story": "WR-250"}]
        errors, _ = self.run_check(data)
        self.assertEqual(errors, [("WR-250 is in the a ready slot, but story-ids.json gives WR-250 to Warehouse "
                                  "(WR-200 to WR-269, workstream c)")])

    def test_nested_block_belongs_to_its_own_lane(self) -> None:
        data = _wr_queues()
        data["workstreams"][0]["backlog"].append("WR-131")
        errors, _ = self.run_check(data)
        self.assertEqual(errors, [("WR-131 is in the a backlog, but story-ids.json gives WR-131 to Notebook "
                                  "(WR-130 to WR-134, workstream b)")])

    def test_id_in_no_block_is_an_error_even_unqueued(self) -> None:
        data = _wr_queues()
        data["new_stories"]["WR-700"] = _wr("Nobody's")
        errors, _ = self.run_check(data)
        self.assertEqual(errors, ["WR-700 (new_stories) is in no block of story-ids.json; take the next id from its lane's block"])

    def test_one_id_for_two_stories_names_both_places(self) -> None:
        data = _wr_queues()
        data["workstreams"][2]["ready"].append({"issue": 950, "title": "WR-238 Something else entirely"})
        errors, _ = self.run_check(data)
        self.assertEqual(errors, [("WR-238 names two stories: new_stories ('Warehouse one') and c ready slot #950 "
                                  "('WR-238 Something else entirely')")])

    def test_same_story_in_new_stories_and_an_issue_slot_is_not_a_collision(self) -> None:
        data = _wr_queues()
        data["workstreams"][2]["ready"].append({"issue": 951, "title": "WR-239: Warehouse one"})
        data["new_stories"]["WR-239"] = _wr("Warehouse one")
        errors, _ = self.run_check(data)
        self.assertEqual(errors, [])

    def test_side_program_story_id_is_checked_against_its_workstream_and_other_stories(self) -> None:
        sp = _side() | {"workstream": "c"}
        sp["stories"] = [{"issue": 1200, "title": "WR-101 Not the engine story", "status": "ready"}]
        errors, _ = self.run_check(_wr_queues() | {"side_programs": [sp]})
        self.assertIn("WR-101 is in the side program pbe #1200, but story-ids.json gives WR-101 to Engine "
                      "(WR-100 to WR-199, workstream a)", errors)
        self.assertIn("WR-101 names two stories: new_stories ('Engine one') and side program pbe #1200 "
                      "('WR-101 Not the engine story')", errors)

    def test_recorded_exception_allows_the_old_collision_and_its_lanes(self) -> None:
        data = _wr_queues()
        data["workstreams"][3]["ready"] = [{"issue": 960, "title": "WR-410 Capacity hard stop"}]
        ledger = _ledger()
        errors, _ = self.run_check(data, ledger)
        self.assertEqual(errors, [])
        ledger["exceptions"] = []
        errors, _ = self.run_check(data, ledger)
        self.assertEqual(errors, ["WR-410 names two stories: new_stories ('Audit') and d ready slot #960 ('WR-410 Capacity hard stop')"])

    def test_exception_lanes_widen_where_an_id_may_sit(self) -> None:
        data = _wr_queues()
        data["new_stories"]["WR-160"] = _wr("Shared by two lanes")
        data["workstreams"][2]["backlog"] = ["WR-160"]
        ledger = _ledger()
        errors, _ = self.run_check(data, ledger)
        self.assertEqual(errors, ["WR-160 is in the c backlog, but story-ids.json gives WR-160 to Engine (WR-100 to WR-199, workstream a)"])
        ledger["exceptions"].append({"first": "WR-160", "last": "WR-160", "lanes": ["eng", "wh"], "note": "both"})
        errors, _ = self.run_check(data, ledger)
        self.assertEqual(errors, [])

    def test_duplicate_key_in_the_queue_file_names_both_titles(self) -> None:
        data = _wr_queues()
        text = json.dumps(data).replace('"WR-102": {"title": "Engine two"', '"WR-101": {"title": "Engine two"')
        errors, _ = self.run_check(data, text=text)
        self.assertEqual(errors, [("workstreams.json defines WR-101 twice in one object: 'Engine one' and 'Engine two'; "
                                  "the second hides the first")])

    def test_bad_ledger_entries_are_errors(self) -> None:
        ledger = _ledger()
        ledger["lanes"].append({"key": "x", "name": "X", "workstream": "zz", "blocks": [{"first": "WR-190", "last": "WR-210"}]})
        ledger["lanes"].append({"key": "y", "name": "Y", "workstream": "e", "blocks": [{"first": "WR-9", "last": "WR-1"}]})
        ledger["lanes"].append({"key": "z", "name": "Z", "workstream": "f", "blocks": [{"first": "WR-400", "last": "WR-499"}]})
        ledger["exceptions"] += [{"first": "WR-410", "last": "WR-411", "lanes": ["nope"]}]
        errors, _ = self.run_check(_wr_queues(), ledger)
        self.assertIn("story-ids.json: lane x: workstream zz doesn't exist", errors)
        self.assertIn("story-ids.json: lane y: a block needs a first and a last WR id, first not after last", errors)
        self.assertIn("story-ids.json: WR-100 to WR-199 (eng) and WR-190 to WR-210 (x) overlap; a block may only sit inside a larger one", errors)
        self.assertIn("story-ids.json: WR-400 to WR-499 (gov) and WR-400 to WR-499 (z) overlap; a block may only sit inside a larger one", errors)
        self.assertIn("story-ids.json: exception WR-410 to WR-411: no note saying what collided", errors)
        self.assertIn("story-ids.json: exception WR-410 to WR-411: lanes must name lanes in the ledger", errors)
        self.assertIn("story-ids.json: WR-410 is in two exceptions", errors)

    def test_real_ledger_is_valid_for_the_real_queues(self) -> None:
        data, _ = queues.load()
        self.assertEqual(queues.check_ids(data, queues.load_ledger()), [])
        self.assertEqual(queues.duplicate_keys(queues.QUEUES.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
