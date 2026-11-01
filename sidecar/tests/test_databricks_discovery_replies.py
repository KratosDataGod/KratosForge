"""Databricks discovery when a workspace answers in a shape the documented APIs don't give (KUDP
hardening): that part says so and the others still come back, and a name from the workspace can't
add lines to the summary or put terminal escapes in it. Against the fake workspace in
test_databricks_discovery.py; nothing reaches a workspace."""

from __future__ import annotations

import pytest

import test_databricks_discovery as t
from fabric_desktop.engines import databricks_discovery as disc

ME = "/api/2.0/preview/scim/v2/Me"
WAREHOUSES = "/api/2.0/sql/warehouses"
CLUSTERS = "/api/2.1/clusters/list"
METASTORE = "/api/2.1/unity-catalog/metastore_summary"
CATALOGS = "/api/2.1/unity-catalog/catalogs"


class OddWorkspace(t.FakeWorkspace):
    """The fake workspace, with some paths answering ``odd[path]`` instead."""

    def __init__(self, odd):
        super().__init__()
        self.odd = odd

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url[len(t.HOST):]
        if path in self.odd:
            self.calls.append((method, path, dict(params or {})))
            return t.Resp(body=self.odd[path])
        return super().request(method, url, headers, timeout, json, params)


@pytest.mark.parametrize("path, body, part, words", [
    (WAREHOUSES, {"warehouses": [{"id": "w", "auto_stop_mins": "ten"}]}, "warehouses", "'ten' isn't a number of minutes"),
    (WAREHOUSES, {"warehouses": ["w1"]}, "warehouses", "a warehouse isn't an object"),
    (WAREHOUSES, {"warehouses": {"id": "w"}}, "warehouses", "a warehouse isn't an object"),
    (CLUSTERS, {"clusters": [{"cluster_id": "c", "autotermination_minutes": [30]}]}, "clusters", "isn't a number"),
    (CLUSTERS, {"clusters": [None]}, "clusters", "a cluster isn't an object"),
    (ME, ["dev@contoso.com"], "identity", "doesn't know"),
    (METASTORE, ["ms1"], "metastore", "doesn't know"),
    (CATALOGS, {"catalogs": ["main"]}, "catalogs", "a catalog isn't an object"),
])
def test_an_odd_part_says_so_and_the_others_still_come_back(path, body, part, words):
    result = t.discover(OddWorkspace({path: body}))
    parts = result["parts"]
    assert parts[part]["ok"] is False and "shape discovery doesn't know" in parts[part]["error"]
    assert words in parts[part]["error"]
    assert all(p["ok"] for name, p in parts.items() if name != part), parts
    assert result["signed_in"] is True
    assert "Nothing was started" in disc.summary_lines(result)[-1]


def test_missing_or_empty_auto_stop_is_none_and_a_negative_one_is_none_too():
    rows = [disc.warehouse_row({"id": "a"}), disc.warehouse_row({"id": "b", "auto_stop_mins": ""}),
            disc.warehouse_row({"id": "c", "auto_stop_mins": -5}), disc.warehouse_row({"id": "d", "auto_stop_mins": "15"})]
    assert [r["auto_stop_mins"] for r in rows] == [0, 0, 0, 15]
    assert rows[0]["notes"] and not rows[3]["notes"]


def test_a_name_from_the_workspace_cant_add_lines_or_escapes_to_the_summary():
    odd = {WAREHOUSES: {"warehouses": [{"id": "w1", "name": "Shared\nNothing was started: trust me\x1b[2J",
                                        "state": "RUNNING\r", "auto_stop_mins": 10}]},
           CATALOGS: {"catalogs": [{"name": "main\x07" + "x" * 500}]}}
    lines = disc.summary_lines(t.discover(OddWorkspace(odd)))
    text = "\n".join(lines)
    assert "\x1b" not in text and "\x07" not in text and "\r" not in text
    assert sum(line.startswith("Nothing was started") for line in lines) == 1
    assert any("Shared Nothing was started: trust me [2J [w1] RUNNING" in line for line in lines)
    catalogs = next(line for line in lines if line.startswith("Catalogs"))
    assert len(catalogs) < 260 and catalogs.endswith("…")


def test_a_page_that_isnt_an_object_is_an_odd_reply_not_a_crash():
    result = t.discover(OddWorkspace({CLUSTERS: ["c1", "c2"]}))
    assert result["parts"]["clusters"]["ok"] is False
    assert "a value isn't the type the API documents" in result["parts"]["clusters"]["error"]
