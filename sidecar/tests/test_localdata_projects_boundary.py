"""Local data projects at their exact edges (boundary re-score of batch 3, mutation batch 160,
docs/quality/mutation-testing.md): threads and timeouts at both ends, and a name of exactly 100
characters."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata.projects import ProjectStore, Settings


def test_threads_may_be_0_to_256():
    assert Settings(threads=0).threads == 0
    assert Settings(threads=256).threads == 256
    for bad in (-1, 257):
        with pytest.raises(ValueError, match="between 0 and 256"):
            Settings(threads=bad)


def test_a_timeout_of_0_is_no_limit():
    assert Settings(timeout_seconds=0).timeout_seconds == 0
    with pytest.raises(ValueError, match="between 0 and a week"):
        Settings(timeout_seconds=-1)


def test_a_project_name_of_exactly_100_characters(tmp_path):
    store = ProjectStore(tmp_path / "local-data")
    assert store.create("p" * 100).name == "p" * 100
    with pytest.raises(ValueError, match="up to 100 characters"):
        store.create("q" * 101)
