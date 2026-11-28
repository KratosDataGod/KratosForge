"""Gate 3, promote and roll back (meta/promote.py), the paths the other tests leave: promoting from an environment
that isn't on the project's metadata, promoting an environment to itself, and rolling back where nothing was ever
promoted. On a local project in tmp_path."""

from __future__ import annotations

import pytest

from fabric_desktop.meta.promote import promote_checks, rollback
from fabric_desktop.meta.project import GateError
from test_rollback import store, svc  # noqa: F401 - svc is a fixture


def test_promoting_from_an_environment_not_on_the_metadata_is_refused(svc):  # noqa: F811
    project = svc._project()
    errors = promote_checks(project, store(svc, "test"), "prod")
    assert f"test is not on metadata {project.version}; promote it to test first" in errors


def test_promoting_an_environment_to_itself_is_refused(svc):  # noqa: F811
    errors = promote_checks(svc._project(), store(svc, "dev"), "dev")
    assert errors == ["pick a different environment to promote to"]


def test_rolling_back_where_nothing_was_promoted_is_refused(svc):  # noqa: F811
    with pytest.raises(GateError) as caught:
        rollback(svc._project(), store(svc, "test"), "v1")
    assert "Nothing has been promoted to test yet." in str(caught.value)
