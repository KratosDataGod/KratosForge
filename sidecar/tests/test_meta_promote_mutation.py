"""Promote and roll back (meta/promote.py): what mutation testing found unchecked. promote() itself refuses
what its checks find, rollback() refuses an environment nothing was promoted to, and an environment on
an older release than the project is told to catch up first."""

from __future__ import annotations

import importlib

import pytest

from fabric_desktop.meta.project import GateError
from tests.test_rollback import edit, store, svc  # noqa: F401 - the fixture and its helpers

promote_mod = importlib.import_module("fabric_desktop.meta.promote")  # meta re-exports its promote function


def test_promote_refuses_what_its_checks_find_and_writes_nothing(svc):  # noqa: F811
    project, test = svc._project(), store(svc)
    with pytest.raises(GateError) as err:
        promote_mod.promote(project, test, store(svc, "test"))
    assert "pick a different environment to promote to" in str(err.value)
    assert test.meta("deployments").height == 0 and test.meta("entities").height == 0


def test_rolling_back_where_nothing_was_promoted_says_so(svc):  # noqa: F811
    with pytest.raises(GateError, match="Nothing has been promoted to test yet."):
        promote_mod.rollback(svc._project(), store(svc), "v1")


def test_an_environment_on_an_older_release_is_told_to_catch_up_first(svc):  # noqa: F811
    v1 = svc.meta_promote("dev", "test")["version"]
    edit(svc, 'expr = "amount - coalesce(discount, 0)"', 'expr = "amount"')
    project = svc._project()
    assert project.version != v1
    errors = promote_mod.promote_checks(project, store(svc), "prod")
    assert f"test is not on metadata {project.version}; promote it to test first" in errors
