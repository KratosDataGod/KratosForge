"""#1331: a landing zone isn't deleted while a waiting source request lands in it. Local platform
only: nothing here reaches Fabric."""

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform

ADMIN, DEV = "chris@example.com", "dev@example.com"


@pytest.fixture
def platform(tmp_path):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: ADMIN)
    p.call("save_landing_zone", zone={"name": "Shared"})
    p.call("save_landing_zone", zone={"name": "HR"})
    return p


def _ask(platform, source, zone=None):
    detail = {} if zone is None else {"zone": zone}
    return LocalPlatform(platform.path, lambda: DEV).call("request", kind="source", target=source,
                                                          reason="needed", detail=detail)


def _zones(platform):
    return [z["id"] for z in platform.call("list_landing_zones")]


def test_a_zone_a_waiting_source_request_lands_in_is_kept(platform):
    _ask(platform, "workday", "hr")
    with pytest.raises(core.PlatformError) as err:
        platform.call("delete_landing_zone", id="hr")
    assert str(err.value) == ("HR is where the waiting source request for workday lands: decline it, or wait for it "
                              "to be decided, first")
    assert "hr" in _zones(platform)


def test_several_waiting_requests_are_named_together(platform):
    _ask(platform, "workday", "HR")  # a zone's name reads as its id
    _ask(platform, "adp", "hr")
    with pytest.raises(core.PlatformError, match="waiting source requests for adp, workday land: decline them"):
        platform.call("delete_landing_zone", id="hr")


@pytest.mark.parametrize("decide", ["decline", "withdraw"])
def test_once_the_request_is_decided_the_zone_deletes(platform, decide):
    req = _ask(platform, "workday", "hr")
    if decide == "decline":
        platform.call("decide_request", id=req["id"], approve=False, note="not now")
    else:
        LocalPlatform(platform.path, lambda: DEV).call("withdraw_request", id=req["id"])
    assert _zones(platform) == ["shared", "hr"]
    assert platform.call("delete_landing_zone", id="hr") and "hr" not in _zones(platform)


def test_a_request_without_a_zone_lands_in_shared(platform):
    _ask(platform, "sap")
    with pytest.raises(core.PlatformError, match="Shared is where the waiting source request for sap lands"):
        platform.call("delete_landing_zone", id="shared")
    assert platform.call("delete_landing_zone", id="hr")  # another zone isn't held by it


def test_a_request_naming_no_valid_zone_holds_none(platform):
    _ask(platform, "odd", "!!")
    platform.call("delete_landing_zone", id="hr")
    platform.call("delete_landing_zone", id="shared")
    assert _zones(platform) == []


def test_an_unreadable_request_detail_doesnt_block_zone_deletes(platform):
    req = _ask(platform, "odd", "hr")
    with platform._db() as db:
        db.run(f"UPDATE {db.t('catalog', 'request')} SET detail = ? WHERE id = ?", ("{not json", req["id"]))
        db.conn.commit()
    platform.call("delete_landing_zone", id="hr")
    assert "hr" not in _zones(platform)
