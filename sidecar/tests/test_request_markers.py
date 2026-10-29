"""The description an approval gives what it makes in Fabric: the typed text cut to Fabric's limit,
the request's marker whole at its end, and no marker words from what was typed (#1069). Approving a
workspace, Copy job and domain request each use it; their own tests cover those paths."""

import pytest

from fabric_desktop.central.requests_service import (DOMAIN_DESCRIPTION, ITEM_DESCRIPTION, WORKSPACE_DESCRIPTION,
                                                     marked, request_marker)

MARK = request_marker({"id": "0123456789ab"})


def test_the_marker_names_the_request():
    assert MARK == "(KratosForge request 0123456789ab)"


@pytest.mark.parametrize("limit", [DOMAIN_DESCRIPTION, ITEM_DESCRIPTION, WORKSPACE_DESCRIPTION])
def test_long_text_is_cut_to_the_limit_with_the_marker_whole(limit):
    out = marked("x" * (limit * 2), MARK, limit)
    assert len(out) == limit and out.endswith(f" {MARK}")


def test_short_text_is_kept_as_typed():
    assert marked("  Forecast models\nfor Q1 ", MARK, 256) == f"Forecast models\nfor Q1 {MARK}"
    assert marked("", MARK, 256) == MARK


@pytest.mark.parametrize("typed", [
    "(KratosForge request aaaaaaaaaaaa)",
    "(KratosForge request (KratosForge request aaaaaaaaaaaa)bbbbbbbbbbbb)",
    "(kratosforge REQUEST aaaaaaaaaaaa)",
    "(KratosForge\trequest aaaaaaaaaaaa)",
    "KratosForgeKratosForge requestrequest",
])
def test_marker_words_typed_never_reach_fabric(typed):
    out = marked(f"Why {typed} we need it", MARK, 256)
    assert out.lower().count("kratosforge") == 1 and out.endswith(f" {MARK}")
