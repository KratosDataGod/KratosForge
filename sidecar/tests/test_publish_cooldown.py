"""Publishing an item again inside Fabric's cooldown (live run 9, 2026-11-11): Fabric refused UDF_Platform's
second publish with DeployCooldownBlock, "Please wait 48 second(s) before publishing again". save_item waits
the seconds Fabric names and sends it once more; any other refusal, or a cooldown too long to wait out, is
raised as before. Against a fake HTTP session: nothing reaches Fabric."""

from __future__ import annotations

import pytest

from fabric_desktop import fabric_api as F
from fabric_desktop.fabric_api import API, FabricClient, FabricError

from test_fabric_api import FakeSession, Resp

UPDATE = f"{API}/workspaces/ws1/items/f1/updateDefinition"
FOUND = {"value": [{"id": "f1", "type": "UserDataFunction", "displayName": "fx"}]}


def client(*responses):
    slept: list[float] = []
    session = FakeSession(responses)
    return FabricClient(lambda: "tok", session=session, sleep=slept.append), session, slept


def cooldown(seconds="48"):
    return Resp(400, {"errorCode": "DeployCooldownBlock",
                      "message": f"Please wait {seconds} second(s) before publishing again"})


def test_a_publish_inside_the_cooldown_waits_what_fabric_names_and_is_sent_once_more():
    c, s, slept = client(Resp(200, FOUND), cooldown(), Resp(200, {}))
    assert c.save_item("ws1", "UserDataFunction", "fx", {"a.py": "y"})["created"] is False
    assert [call[:2] for call in s.calls[1:]] == [("POST", UPDATE), ("POST", UPDATE)]
    assert s.calls[1][2] == s.calls[2][2]  # the same definition
    assert slept[-1] == 49


def test_a_second_refusal_after_the_wait_is_raised():
    c, s, _ = client(Resp(200, FOUND), cooldown(), cooldown())
    with pytest.raises(FabricError, match="DeployCooldownBlock"):
        c.save_item("ws1", "UserDataFunction", "fx", {"a.py": "y"})
    assert len(s.calls) == 3


def test_a_cooldown_too_long_to_wait_out_is_raised_at_once():
    c, s, slept = client(Resp(200, FOUND), cooldown(str(F.PUBLISH_COOLDOWN_MAX + 5)))
    with pytest.raises(FabricError, match="DeployCooldownBlock"):
        c.save_item("ws1", "UserDataFunction", "fx", {"a.py": "y"})
    assert len(s.calls) == 2 and not slept


def test_any_other_refusal_is_raised_without_a_wait():
    c, s, slept = client(Resp(200, FOUND), Resp(400, {"errorCode": "InvalidDefinition", "message": "bad"}))
    with pytest.raises(FabricError, match="InvalidDefinition"):
        c.save_item("ws1", "UserDataFunction", "fx", {"a.py": "y"})
    assert len(s.calls) == 2 and not slept


@pytest.mark.parametrize("message, wait", [("Please wait 48 second(s) before publishing again", 49.0),
                                           ("Publishing is blocked for now", 60.0),
                                           ("Please wait 9999 second(s)", None)])
def test_the_wait_comes_from_fabrics_message(message, wait):
    assert F._publish_cooldown(FabricError(message, "DeployCooldownBlock")) == wait


def test_a_new_item_is_never_held_for_a_cooldown():
    assert F._publish_cooldown(FabricError("Please wait 48 second(s)", "ItemNotFound")) is None
