"""engines/contract.py values that mutation testing (batch 55, docs/quality/mutation-testing.md)
showed no test pinned: the preview and result limits, the frozen config and request, a result's
table left out of its text and its comparisons, and the seconds it reports."""

import dataclasses

import pytest

from fabric_desktop.engines import contract
from fabric_desktop.engines.contract import CellRequest, CellResult, SessionConfig


def test_the_limits():
    assert contract.PREVIEW_ROWS == 1000
    assert CellRequest("select 1").preview_rows == 1000
    assert contract.RESULT_BYTES == 268_435_456  # 256 MiB


def test_a_session_config_and_a_cell_request_cant_be_changed():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SessionConfig("nb").database = "other.duckdb"
    with pytest.raises(dataclasses.FrozenInstanceError):
        CellRequest("select 1").result_rows = 10


def test_a_results_table_is_left_out_of_its_text_and_comparisons():
    marker = object()
    a = CellResult("ok", table=marker)
    assert "table" not in repr(a)
    assert a == CellResult("ok")
    assert "table" not in a.as_dict()


def test_seconds_are_given_to_four_places():
    assert CellResult("ok", seconds=1.234567).as_dict()["seconds"] == 1.2346
