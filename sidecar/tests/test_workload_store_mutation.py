"""Workload records edges the main tests don't pin down (mutation batch 148, docs/quality/mutation-testing.md):
the schema version and limits as numbers, default page sizes, links with a part missing or the same
item again, when an action counts as ended, and errors cut to their length."""

import json

import pytest

from fabric_desktop.workloads import store
from fabric_desktop.workloads.model import notebook_id
from fabric_desktop.workloads.store import ManagementStore


@pytest.fixture
def s(tmp_path):
    return ManagementStore(tmp_path / "workloads.sqlite")


def test_the_schema_version_and_the_actions_kept_are_these_numbers(s):
    with s._tx() as db:
        assert db.execute("SELECT value FROM meta WHERE name = 'schema_version'").fetchone()["value"] == "1"
    assert store.ACTION_KEEP == 10000


def test_the_folder_is_made_with_its_parents(tmp_path):
    ManagementStore(tmp_path / "a" / "b" / "workloads.sqlite").cache_put("x", 1)
    assert (tmp_path / "a" / "b" / "workloads.sqlite").exists()


def test_assign_returns_what_the_workload_now_has(s):
    out = s.assign("w1", {"team": "Sales", "tags": ["b", "a"]}, by="me")
    assert out["team"]["value"] == "Sales" and out["tags"]["value"] == ["a", "b"] and out["team"]["set_by"] == "me"


def test_history_shows_the_newest_100_by_default(s):
    for n in range(101):
        s.assign("w1", {"team": f"t{n}"})
    rows = s.history("w1")
    assert len(rows) == 100 and rows[0]["after"] == "t100"


def test_a_link_with_a_part_missing_is_not_kept(s):
    assert s.record_link("nb", "ws", "") == ("", "")
    assert s.record_link("", "ws", "item") == ("", "")
    assert s.record_link("nb", "", "item") == ("", "")
    assert s.links() == {}


def test_the_same_item_again_writes_no_history_and_another_does(s):
    s.record_link("nb.ipynb", "ws", "item1", "Sales")
    s.record_link("nb.ipynb", "ws", "item1", "Sales")
    wid = notebook_id("nb.ipynb")
    assert len(s.history(wid)) == 1
    s.record_link("nb.ipynb", "ws", "item2", "Sales")
    assert [h["after"] for h in s.history(wid)] == ["Sales / item2", "Sales / item1"]


def test_unlinking_a_notebook_that_isnt_linked_says_so_with_empty_text(s):
    assert s.unlink("nb.ipynb") == ""


def test_an_action_that_doesnt_exist_is_a_key_error(s):
    with pytest.raises(KeyError, match="no action"):
        s.update_action("nope", status="succeeded")


def test_an_action_has_a_12_digit_id_and_ends_only_with_an_ending_status(s):
    a = s.start_action("w1", "run")
    assert len(a["id"]) == 12
    assert s.update_action(a["id"], status="running")["ended_at"] is None
    assert s.update_action(a["id"], status="succeeded")["ended_at"]


def test_a_reference_is_merged_into_the_one_kept(s):
    a = s.start_action("w1", "run")
    s.update_action(a["id"], ref={"job": "j1"})
    assert s.update_action(a["id"], ref={"url": "u"})["ref"] == {"job": "j1", "url": "u"}


def test_actions_and_waiting_audit_lines_page_at_50_by_default(s):
    for n in range(51):
        s.start_action("w1", "run")
        s.audit_queue(f"k{n}", "w1", "run", "ok", {"n": n})
    assert len(s.actions()) == 50 and len(s.audit_waiting()) == 50


def test_a_failed_audit_try_keeps_500_characters_of_why(s):
    s.audit_queue("k1", "w1", "run", "ok", {})
    s.audit_result("k1", error="e" * 600)
    [line] = s.audit_waiting()
    assert len(line["error"]) == 500 and line["tries"] == 1 and json.dumps(line["entry"]) == "{}"
