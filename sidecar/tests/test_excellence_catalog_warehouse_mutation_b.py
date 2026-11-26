"""Mutation batch 485: the warehouse and SQL endpoint controls' exact wording (one or more
warehouses, the names listed and how many more), the list cap, and which items count as a
lakehouse or an endpoint."""
from __future__ import annotations

import pytest

from fabric_desktop.excellence import adapters, assess, catalog
from fabric_desktop.excellence import catalog_warehouse as cw

AT = "2026-11-20T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
LH = "1a1a1a1a-0000-4000-8000-000000000001"
EP = "2b2b2b2b-0000-4000-8000-000000000001"
A = "Latin1_General_100_BIN2_UTF8"
B = "Latin1_General_100_CI_AS_KS_WS_SC_UTF8"


def wh_id(i):
    return f"3c3c3c3c-0000-4000-8000-{i:012d}"


def ws():
    return {"id": WSID, "displayName": "Sales"}


def item(i, kind, name=None):
    return {"id": i, "type": kind, "displayName": name or i}


def wh(collation):
    return {"properties": {"collationType": collation}} if collation else {"properties": {}}


def text_of(collations, *, items=None, subject=0):
    """FEA-WH-002's text for warehouse ``subject``; ``collations`` per warehouse (None: not read)."""
    items = items or [item(wh_id(i), "Warehouse", f"WH_{i}") for i in range(len(collations))]
    read = {wh_id(i): wh(c) for i, c in enumerate(collations) if c is not None}
    records = adapters.workspace(ws(), items, collected_at=AT) + cw.evidence(ws(), items, {}, read, collected_at=AT)
    snap = assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [{"id": WSID}]})
    got = [o for o in snap["outcomes"] if o["control_id"] == "FEA-WH-002" and o["subject"]["id"] == wh_id(subject)]
    assert len(got) == 1
    return got[0]["outcome"], got[0]["text"]


def test_the_only_warehouse_passes_saying_so():
    assert text_of([A]) == ("pass", f"The only warehouse in this workspace ({A}).")


@pytest.mark.parametrize("n, word", [(1, "warehouse"), (2, "warehouses")])
def test_matching_warehouses_pass_with_the_count_of_the_others(n, word):
    assert text_of([A] * (n + 1)) == ("pass", f"Same collation ({A}) as the other {n} {word} in the workspace.")


def test_one_different_warehouse_is_named_in_the_singular():
    out, text = text_of([A, B])
    assert out == "fail"
    assert text == f"This warehouse uses {A}, and 1 other warehouse in the workspace uses another collation: WH_1 ({B})."


def test_several_different_warehouses_are_named_in_the_plural():
    out, text = text_of([A, B, B])
    assert out == "fail" and f"and 2 other warehouses in the workspace use another collation: WH_1 ({B}), WH_2 ({B})." in text


@pytest.mark.parametrize("n, tail", [(5, ""), (6, " and 1 more"), (8, " and 3 more")])
def test_at_most_five_different_warehouses_are_named_then_how_many_more(n, tail):
    out, text = text_of([A] + [B] * n)
    names = ", ".join(f"WH_{i} ({B})" for i in range(1, 6))
    assert out == "fail" and text.endswith(f"another collation: {names}{tail}.")


def test_one_unread_warehouse_is_named_in_the_singular():
    assert text_of([A, None]) == ("not_evaluated", ("1 other warehouse in the workspace wasn't read or didn't report a "
                                                    "collation, so it can't be compared."))


def test_several_unread_warehouses_are_named_in_the_plural():
    assert text_of([A, None, None]) == ("not_evaluated", ("2 other warehouses in the workspace weren't read or didn't "
                                                          "report a collation, so they can't be compared."))


def test_the_workspace_record_lists_at_most_50_warehouses():
    items = [item(wh_id(i), "Warehouse", f"WH_{i}") for i in range(51)]
    recs = cw.warehouse_properties(ws(), items, {wh_id(i): wh(A) for i in range(51)}, collected_at=AT)
    listed = [r for r in recs if r["kind"] == cw.WAREHOUSES][0]["payload"]
    assert listed["count"] == 51 and listed["read"] == 51 and len(listed["warehouses"]) == 50


def lakehouse_answer(endpoint, status="Success"):
    return {"properties": {"sqlEndpointProperties": {"id": endpoint, "provisioningStatus": status}}}


def test_only_a_lakehouse_names_an_endpoint_and_only_an_sql_endpoint_item_is_one():
    other = "4d4d4d4d-0000-4000-8000-000000000001"
    items = [item(LH, "Lakehouse"), item(EP, "SQLEndpoint"), item(other, "Warehouse"), item(WSID, "Notebook")]
    # a lakehouse that names a warehouse's id as its endpoint gives no record
    assert cw.endpoint_properties(WSID, items, {LH: lakehouse_answer(other)}, collected_at=AT) == []
    # a notebook read as if it were a lakehouse gives no record either
    assert cw.endpoint_properties(WSID, items, {WSID: lakehouse_answer(EP)}, collected_at=AT) == []
    got = cw.endpoint_properties(WSID, items, {LH: lakehouse_answer(EP)}, collected_at=AT)
    assert [r["subject"]["id"] for r in got] == [EP]


@pytest.mark.parametrize("status", ["", 5, None])
def test_an_endpoint_status_that_isnt_text_is_kept_as_none(status):
    items = [item(LH, "Lakehouse"), item(EP, "SQLEndpoint")]
    got = cw.endpoint_properties(WSID, items, {LH: lakehouse_answer(EP, status)}, collected_at=AT)
    assert got[0]["payload"]["provisioning_status"] is None
