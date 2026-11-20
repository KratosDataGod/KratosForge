"""fabric_run.py edges at their exact boundaries (mutation batch 163, a boundary re-score of batch 17,
docs/quality/mutation-testing.md): how deep a SQL text is followed, a comment that starts exactly at a
match, quoted lines of exactly 60 characters, the first and last line of a cell, notebooks run exactly
MAX_RUN_DEPTH deep, a Built-in file of exactly the size limit, the shortest and longest schedule
interval, and .option() given only its name."""

from __future__ import annotations

import ast

import pytest

from fabric_desktop import fabric_run
from fabric_desktop.fabric_run import compute_check
from fabric_desktop.notebook import MAX_RUN_DEPTH

LH = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_a_sql_text_built_five_additions_deep_is_still_read_whole():
    node = ast.parse('"a" + "b" + "c" + "d" + "e" + "f"', mode="eval").body  # "a" sits five levels down
    assert fabric_run._sql_texts(node, {}) == ["abcdef"]
    # One more: the sum of "a" and "b" sits six levels down, so both are known only at run time.
    deeper = ast.parse('"a" + "b" + "c" + "d" + "e" + "f" + "g"', mode="eval").body
    assert fabric_run._sql_texts(deeper, {}) == [f"{fabric_run._VALUE}0{fabric_run._VALUE}1cdefg"]


def test_a_comment_that_starts_exactly_at_the_offset_counts():
    source = "x = 1  # spark.sql('DELETE FROM t')\n"
    assert fabric_run._in_comment(source, source.index("#")) is True
    assert fabric_run._in_comment(source, source.index("#") - 1) is False


def test_a_quoted_line_of_exactly_60_characters_is_kept_whole():
    line = "x" * 60
    assert fabric_run._snippet(line) == line
    assert fabric_run._snippet(line + "y") == "x" * 57 + "..."


def test_the_first_and_last_lines_of_a_cell_are_quoted_and_line_0_is_the_cell():
    source = "first\nsecond\nlast"
    assert fabric_run._line(source, 1) == "first"
    assert fabric_run._line(source, 3) == "last"
    assert fabric_run._line(source, 0) == "first"  # not the last line, as lines[-1] would be
    assert fabric_run._line(source, 4) == "first"


def test_notebooks_run_exactly_max_run_depth_deep_stop_the_check():
    loaded = []

    def load(name):
        loaded.append(name)
        return []

    deep = tuple(f"nb{i}" for i in range(MAX_RUN_DEPTH))
    out = fabric_run._followed("next", "%run", LH, load, deep, None)
    assert loaded == [] and "more than" in out["local"][1]
    out = fabric_run._followed("next", "%run", LH, load, deep[:-1], None)
    assert loaded == ["next"] and out == {"local": None, "fabric": None}


def test_a_builtin_file_of_exactly_the_size_limit_is_read(tmp_path, monkeypatch):
    (tmp_path / "builtin").mkdir()
    f = tmp_path / "builtin" / "helpers.py"
    f.write_text("x = 1\n", newline="\n")
    monkeypatch.setattr(fabric_run, "_BUILTIN_MAX", f.stat().st_size)
    assert fabric_run._builtin_cells("helpers.py", str(tmp_path)) is not None
    monkeypatch.setattr(fabric_run, "_BUILTIN_MAX", f.stat().st_size - 1)
    assert fabric_run._builtin_cells("helpers.py", str(tmp_path)) is None


def test_every_one_minute_and_the_longest_interval_are_taken():
    assert fabric_run.schedule_configuration("cron", every_minutes=1)["interval"] == 1
    longest = fabric_run.MAX_EVERY_MINUTES
    assert fabric_run.schedule_configuration("cron", every_minutes=longest)["interval"] == longest
    for wrong in (0, longest + 1):
        with pytest.raises(ValueError, match="Pick how many minutes"):
            fabric_run.schedule_configuration("cron", every_minutes=wrong)


def test_an_option_given_only_its_name_doesnt_stop_the_check():
    out = compute_check([{"type": "code", "source": 'df = spark.read.option("header").csv("Files/a.csv")',
                          "meta": {}}], LH)
    assert set(out) == {"local", "fabric"}
