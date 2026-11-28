"""file_stem keeps at most 60 characters of a name people chose (filenames.py)."""

from __future__ import annotations

from fabric_desktop.filenames import file_stem


def test_a_long_name_keeps_exactly_sixty_characters():
    assert file_stem("a" * 61) == "a" * 60
    assert file_stem("b" * 60) == "b" * 60


def test_the_cut_comes_after_unsafe_characters_are_dropped():
    assert file_stem("x/" * 40) == "x" * 40
    assert file_stem("Sales report " + "q" * 70) == ("Sales report " + "q" * 70)[:60]
