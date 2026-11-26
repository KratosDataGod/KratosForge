"""Mutation batch 487: the adopted Platform's display name, and the waiting count in the status."""
from __future__ import annotations

import pytest

from fabric_desktop.controlplane import health
from fabric_desktop.optimize import setup

WS = "11111111-2222-3333-4444-555555555555"
LH = "22222222-3333-4444-5555-666666666666"
FN = "33333333-4444-5555-6666-777777777777"


def adopt(platform):
    return health.adopt_preview(name="Ours", lake_connection={"workspace_id": WS, "lakehouse_id": LH},
                                inspections=[{"family": setup.CORE.prefix, "state": setup.READY, "schema_version": 2}],
                                platform=platform, operational_version=5)


@pytest.mark.parametrize("given, shown", [(None, "SQL_Platform"), ("", "SQL_Platform"), ("Our_Platform", "Our_Platform")])
def test_the_adopted_platform_keeps_its_name_or_is_called_sql_platform(given, shown):
    platform = {"workspace_id": WS, "functions_id": FN}
    if given is not None:
        platform["display_name"] = given
    assert adopt(platform)["operational_store"]["display_name"] == shown


def status(kinds):
    return health.status(connected=True, reachable=True, sync={"state": "synced", "kinds": kinds})


def test_one_result_waiting_is_said_in_the_singular():
    got = status({"a": {"pending": 1}})
    assert got["waiting"] == 1 and got["detail"] == "1 result waiting on this PC"
    assert status({"a": {"pending": 2}})["detail"] == "2 results waiting on this PC"


def test_a_kind_that_reports_no_pending_count_has_none_waiting():
    got = status({"a": {}, "b": {"pending": 2}})
    assert got["waiting"] == 2 and got["detail"] == "2 results waiting on this PC"
    assert status({"a": {}})["detail"] == ""
