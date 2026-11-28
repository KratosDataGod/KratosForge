"""Mutation batch 550: model_bpa_service.py, re-scored. The survivor was ``model_bpa_share``'s
``approve`` default: a confirm without ``approve`` must still write nothing."""

from __future__ import annotations

import pytest

from test_model_bpa_share import _status
from test_optimize_service import svc  # noqa: F401 - the fixture is used by name


def test_sharing_with_the_confirm_but_without_approve_writes_nothing(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "model_bpa_status", lambda self, env="test", layer=None, name=None: _status(),
                        raising=False)
    svc.optimize_create("w1", workspace_name="Platform")
    with pytest.raises(PermissionError) as asked:
        svc.model_bpa_share("test")
    confirm = asked.value.would["confirm"]
    with pytest.raises(PermissionError, match="Confirm to continue") as again:
        svc.model_bpa_share("test", confirm=confirm)
    assert not str(again.value).startswith("What would be added changed")
    assert len(svc._optimize_shared().rows("findings")) == 0, "a confirm alone wrote rows"
