"""excellence/catalog_artifacts.py: evidence that never inspected the code or the pipeline is not evaluated."""

from types import SimpleNamespace

import pytest

from fabric_desktop.excellence import catalog_artifacts as ca


def _ctx(payload):
    record = {"id": "ev-1", "payload": payload}
    return SimpleNamespace(one=lambda kind: record if kind == ca.KIND else None)


@pytest.mark.parametrize("payload", [{}, {"rules": None}, {"rules": ["NB001"]}, None])
def test_a_notebook_whose_code_wasnt_inspected_is_not_evaluated(payload):
    for control in (ca._secret, ca._fixed_paths, ca._swallowed, ca._unpinned, ca._short_vacuum):
        out = control(_ctx(payload))
        assert out == {"outcome": "not_evaluated", "evidence_ids": [], "text": "The notebook's code wasn't inspected.",
                       "needs": "the notebook's definition"}


@pytest.mark.parametrize("payload", [{"kind": "notebook", "activities": 2}, {"kind": "pipeline", "activities": "2"},
                                     {"kind": "pipeline"}])
def test_a_pipeline_whose_activities_werent_inspected_is_not_evaluated(payload):
    for control in (ca._deactivated, ca._fixed_workspace):
        out = control(_ctx(payload))
        assert out["outcome"] == "not_evaluated" and out["needs"] == "the pipeline's definition"
        assert out["text"] == "The pipeline's activities weren't inspected."
