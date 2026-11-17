"""Open in SSMS: the engine gives the shell a warehouse's server and database as Fabric names them,
checked the way every query's are, plus the signed-in account's name for SSMS's sign-in box. It
reads one item from Fabric and sends no token anywhere. Against fakes: nothing reaches Fabric."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import audit, server
from fabric_desktop import warehouse_ssms_service as wss

from test_warehouse_queries import SERVER, FakeFabric


def svc(fabric, account=None):
    s = SimpleNamespace(fabric=fabric, _saved_account=lambda: account or {})
    s.wh_connection = wss.WarehouseSsmsMixin.wh_connection.__get__(s)
    return s


def test_a_warehouse_gives_its_server_database_and_who_is_signed_in():
    fabric = FakeFabric()
    out = svc(fabric, {"username": "chris@contoso.com"}).wh_connection("ws", "wh1")
    assert out == {"server": SERVER, "database": "Sales WH", "user": "chris@contoso.com"}
    assert fabric.calls == [("warehouse", "ws", "wh1")]


def test_an_endpoint_is_found_through_its_lakehouse():
    out = svc(FakeFabric()).wh_connection("ws", "lh1", "endpoint")
    assert out["database"] == "LH_Gold" and out["user"] == ""


def test_a_server_that_isnt_fabrics_is_never_given_to_the_shell():
    with pytest.raises(ValueError, match="isn't a Fabric SQL endpoint"):
        svc(FakeFabric("evil.example.com")).wh_connection("ws", "wh1")


@pytest.mark.parametrize("name", ["", "not an email", None])
def test_a_sign_in_name_that_isnt_one_is_left_out(name):
    assert svc(FakeFabric(), {"username": name}).wh_connection("ws", "wh1")["user"] == ""


def test_it_is_a_read_and_reachable():
    assert wss.METHODS <= server.METHODS and "wh_connection" in audit.READ_ONLY
