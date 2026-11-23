"""Mutation batch 246: an indented %%python line is dropped, not kept as an indented statement."""

from __future__ import annotations

from fabric_desktop import intellisense


def test_an_indented_python_cell_magic_is_not_an_indent_error():
    assert intellisense.syntax_problems("  %%python\nx = 1\n") == []
    (problem,) = intellisense.syntax_problems("  %%python\nx = (1,\n")
    assert (problem["line"], problem["col"]) == (2, 4)
    assert "never closed" in problem["message"]


def test_a_cell_of_only_blank_lines_has_no_problem():
    assert intellisense.syntax_problems("   \n  \n") == []
    assert intellisense.syntax_problems("") == []
