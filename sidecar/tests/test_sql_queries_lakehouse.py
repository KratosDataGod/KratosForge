"""Saved queries on a lakehouse's SQL page (S37): sql_queries_service.py with a lakehouse in place of a warehouse."""

from pathlib import Path

import pytest

from fabric_desktop import sql_queries_service as sq
from fabric_desktop.central import checks_service as cs
from fabric_desktop.service import Service

LH_ID = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return Service(client_id="x", root=tmp_path / "app")


def test_a_lakehouse_query_names_its_lakehouse_and_reads_back():
    meta = {"title": "Top customers", "lakehouse": "Sales", "lakehouse_id": LH_ID, "description": "", "sql": "SELECT 1",
            "warehouse": ""}
    text = sq.render(meta)
    assert text == f"-- query: Top customers\n-- lakehouse: Sales\n-- lakehouse_id: {LH_ID}\n\nSELECT 1\n"
    assert sq.parse(text, "x") == meta


def test_a_warehouse_query_is_written_and_read_as_before():
    meta = {"title": "Revenue", "warehouse": "WH_Gold", "description": "Monthly", "sql": "SELECT 1"}
    assert sq.render(meta) == "-- query: Revenue\n-- warehouse: WH_Gold\n-- description: Monthly\n\nSELECT 1\n"
    assert sq.parse(sq.render(meta), "x") == meta, "no lakehouse keys on a warehouse query"
    assert sq.render({**meta, "lakehouse": "", "lakehouse_id": ""}) == sq.render(meta)


def test_a_lakehouse_query_is_checked_before_it_is_written():
    base = {"title": "A", "sql": "SELECT 1"}
    assert sq.problem({**base, "lakehouse": "Sales", "lakehouse_id": LH_ID}) == ""
    assert sq.problem({**base, "lakehouse_id": "../x"}) == "That lakehouse id isn't one Fabric gives."
    assert sq.problem({**base, "lakehouse_id": "a\nb"}) == "That lakehouse id isn't one Fabric gives."
    assert sq.problem({**base, "lakehouse_id": "x" * 65}) == "That lakehouse id isn't one Fabric gives."
    assert sq.problem({**base, "lakehouse": "Sales"}) == "A query saved on a lakehouse needs the lakehouse's id."
    assert sq.problem({**base, "warehouse": "WH", "lakehouse_id": LH_ID}) == "A saved query belongs to a warehouse or a lakehouse, not both."
    assert sq.problem({**base, "lakehouse": "a\nb", "lakehouse_id": LH_ID}).startswith("Use a lakehouse of up to")


def test_save_list_and_save_again_on_a_lakehouse(svc, tmp_path):
    saved = svc.sql_query_save("mine", "Top customers", "SELECT * FROM sales LIMIT 10", lakehouse="Sales", lakehouse_id=LH_ID)
    wh = svc.sql_query_save("mine", "Revenue", "SELECT 1", warehouse="WH_Gold")
    listed = {q["name"]: q for q in svc.sql_queries_list()["queries"]}
    lq = listed[saved["name"]]
    assert (lq["title"], lq["lakehouse"], lq["lakehouse_id"], lq["warehouse"]) == ("Top customers", "Sales", LH_ID, "")
    assert "lakehouse_id" not in listed[wh["name"]], "a warehouse query has no lakehouse"
    again = svc.sql_query_save("mine", "Top customers", "SELECT * FROM sales LIMIT 20", lakehouse="Sales", lakehouse_id=LH_ID,
                               name=saved["name"], expect=lq["version"])
    text = (tmp_path / "app" / "sql-queries" / again["name"]).read_text(encoding="utf-8")
    assert text.startswith(f"-- query: Top customers\n-- lakehouse: Sales\n-- lakehouse_id: {LH_ID}\n\n") and "LIMIT 20" in text
    with pytest.raises(ValueError, match="not both"):
        svc.sql_query_save("mine", "Mixed", "SELECT 1", warehouse="WH_Gold", lakehouse_id=LH_ID)


class _Svc(cs.ChecksMixin):
    def __init__(self, queries):
        self.sql_queries_list = lambda: {"notes": [], "queries": queries}
        self.wh_projects_list = lambda: {"projects": []}


def test_the_t_sql_practice_checks_leave_out_lakehouse_queries():
    svc = _Svc([{"place_label": "My queries", "name": "wh.sql", "sql": "SELECT a"},
                {"place_label": "My queries", "name": "lh.sql", "sql": "SELECT * FROM sales LIMIT 5", "lakehouse_id": LH_ID}])
    problems: list[str] = []
    assert [i["id"] for i in svc._gc_sql_items(problems)] == ["My queries/wh.sql"]
    assert problems == []
