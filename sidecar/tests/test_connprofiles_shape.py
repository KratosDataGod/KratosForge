"""A connection profile that isn't shaped like one (KUDP hardening): a hand-edited or imported file
with a list where an object goes, or a number for an address, gets a ProfileError naming the part,
never an AttributeError from part way through the check."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop import connprofiles as cp

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"


def profile(**changes):
    p = {"id": "p1", "name": "Sales", "kind": "databricks",
         "connections": [{"platform": "databricks", "cloud": "azure", "host": HOST}]}
    return p | changes


def conn(**changes):
    return profile(connections=[profile()["connections"][0] | changes])


@pytest.mark.parametrize("data, says", [
    ([], "A profile is a JSON object."),
    ("profile", "A profile is a JSON object."),
    (profile(connections={"a": 1}), "connections must be a list."),
    (profile(connections=[1]), "connections[0] must be an object."),
    (profile(connections=["x", None]), "connections[1] must be an object."),
    (conn(credential="pat"), "connections[0].credential must be an object."),
    (conn(compute="abc"), "connections[0].compute must be an object."),
    (conn(host=5), "connections[0].host must be text."),
    (conn(platform=["databricks"]), "connections[0].platform must be text."),
    (profile(defaults=[]), "defaults must be an object."),
    (profile(execution="ask"), "execution must be an object."),
    (profile(execution={"budget": 5}), "execution.budget must be an object."),
    (profile(environments={"dev": ["x"]}), "environments.dev must be an object."),
    (profile(deploy={"prod": "x"}), "deploy.prod must be an object."),
    (profile(validation="ok"), "validation must be an object."),
])
def test_a_profile_that_isnt_shaped_like_one_says_which_part(data, says):
    assert says in cp.problems(data)
    with pytest.raises(cp.ProfileError) as exc:
        cp.validate(data)
    assert says in str(exc.value)


def test_a_well_shaped_profile_still_validates():
    assert cp.validate(profile())["connections"][0]["host"] == HOST


def test_an_imported_file_shaped_wrong_is_a_profile_error():
    document = {"kind": "kratosforge.connection-profile", "schema": 1,
                "profile": profile(connections=[{"platform": "databricks", "credential": "pat"}])}
    with pytest.raises(cp.ProfileError, match="credential must be an object"):
        cp.import_(document)
    for bad in ([1], {"connections": "x"}):
        doc = copy.deepcopy(document) | {"profile": bad}
        with pytest.raises(cp.ProfileError):
            cp.import_(doc)


def test_shape_problems_are_listed_with_secret_problems_and_never_repeat_a_value():
    data = conn(credential="pat") | {"token": "plain-value-123"}
    found = cp.problems(data)
    assert any("token must be a reference" in f for f in found)
    assert "connections[0].credential must be an object." in found
    assert not any("plain-value-123" in f for f in found)
