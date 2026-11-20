"""Boundary kill test for prove_service (mutation testing, batch 166)."""
from __future__ import annotations

from fabric_desktop.service import Service

from test_prove import svc  # noqa: F401 - the fixture is used by name
from test_prove_service_mutation import _bundle


def test_a_bundle_of_exactly_the_size_limit_opens(svc, tmp_path, monkeypatch):  # noqa: F811
    path = _bundle(tmp_path)
    monkeypatch.setattr(type(svc), "MAX_BUNDLE_BYTES", path.stat().st_size)
    assert isinstance(svc, Service)
    assert svc.replay_bundle_open(path=str(path))["name"] == "events replay"
