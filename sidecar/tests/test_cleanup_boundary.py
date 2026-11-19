"""Clean up at its exact edge (mutation batch 166, a boundary re-score of batch 23,
docs/quality/mutation-testing.md): a notebook whose every cell is empty isn't offered to lose them all."""

from __future__ import annotations

import pytest

from test_cleanup import code, tidy


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_empty_cells_are_removed_only_while_one_with_code_stays():
    assert "empty_cells" not in tidy([code(""), code("  ")])
    assert tidy([code(""), code("x = 1")])["empty_cells"]["cells"] == [1]
