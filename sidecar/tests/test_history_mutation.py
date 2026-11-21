"""Mutation-testing follow-up for history.py (docs/quality/mutation-testing.md, batch 54)."""

from fabric_desktop import history


def test_the_cells_before_the_first_are_empty_even_when_in_0_holds_text():
    ns = {"In": ["not a cell"]}
    history.record_input(ns, 1, "a = 1")
    assert (ns["_i"], ns["_ii"], ns["_iii"]) == ("", "", "")
    history.record_input(ns, 2, "b = 2")
    history.record_input(ns, 3, "c = 3")
    history.record_input(ns, 4, "d = 4")
    assert (ns["_i"], ns["_ii"], ns["_iii"]) == ("c = 3", "b = 2", "a = 1")
    assert ns["_i4"] == "d = 4"


def test_clear_output_removes_each_numbered_result_and_works_with_no_out():
    ns = {"In": ["", "1", "2"]}
    history.record_output(ns, 1, "one")
    history.record_output(ns, 2, "two")
    history.clear_output(ns)
    assert ns["Out"] == {}
    assert "_1" not in ns and "_2" not in ns and "_" not in ns
    assert ns["In"] == ["", "1", "2"]
    empty: dict = {}
    history.clear_output(empty)
    assert empty == {"Out": {}}


def test_a_range_lists_each_cell_in_it():
    ns = {"In": ["", "a = 1", "b = 2", "c = 3", "%history 2-3"]}
    assert history.command(ns, "2-3") == "b = 2\nc = 3"
    assert history.command(ns, "-n 1-2") == "  1: a = 1\n  2: b = 2"


def test_a_cell_number_past_the_last_is_skipped():
    ns = {"In": ["", "a = 1", "%history 1-2"]}
    assert history.command(ns, "1-3") == "a = 1\n%history 1-2"
    assert history.command(ns, "3") == ""
