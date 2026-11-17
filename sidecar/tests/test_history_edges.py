"""IPython history in a notebook: the replaced-object and %history refusals the main tests don't reach."""

import pytest

from fabric_desktop import history


def test_a_replaced_in_list_starts_again_and_gaps_are_filled():
    ns = {"In": "not a list"}
    history.record_input(ns, 3, "x = 1")
    assert ns["In"] == ["", "", "", "x = 1"] and ns["_i3"] == "x = 1" and ns["_i"] == ""


def test_a_replaced_out_dict_starts_again():
    ns = {"Out": None}
    history.record_output(ns, 1, 42)
    assert ns["Out"] == {1: 42} and ns["_"] == 42


def test_history_refuses_a_missing_count_a_backwards_range_and_unknown_flags():
    ns = {"In": ["", "a", "b", "c"], "Out": {}}
    with pytest.raises(ValueError, match="needs a number"):
        history.command(ns, "-l")
    with pytest.raises(ValueError, match="needs a number"):
        history.command(ns, "-l x")
    with pytest.raises(ValueError, match="runs backwards. Use 1-2"):
        history.command(ns, "2-1")
    with pytest.raises(ValueError, match="-g isn't supported"):
        history.command(ns, "-g")


def test_history_with_replaced_in_and_out_reads_as_empty():
    assert history.command({"In": 7, "Out": []}, "-o") == ""


def test_last_zero_gives_nothing_and_ranges_past_the_end_are_skipped():
    ns = {"In": ["", "a", "b", "c"]}
    assert history.command(ns, "-l 0") == ""
    assert history.command(ns, "0 2 9") == "b"
