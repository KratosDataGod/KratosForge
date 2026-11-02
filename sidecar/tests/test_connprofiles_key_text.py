"""Profile problems name the file's keys safely (KUDP hardening): a key, id or connection name from
the file goes into a message as "[key]" when it looks like a secret, otherwise folded to one line
and cut to 60 characters, so the problem list never carries a token or a forged line."""

from __future__ import annotations

import pytest

from fabric_desktop import connprofiles as cp

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
TOKEN = "dapi" + "0123456789abcdef" * 2
FORGED = "qa\nconnections[0]: all good"
LONG = "k" * 500


def profile(**changes):
    p = {"id": "p1", "name": "Sales", "kind": "databricks",
         "connections": [{"id": "dbx", "platform": "databricks", "cloud": "azure", "host": HOST}]}
    return p | changes


def check(found, key):
    text = "\n".join(found)
    assert TOKEN not in text and "\nconnections[0]: all good" not in text and LONG not in text
    assert all("\n" not in f for f in found)
    if key == TOKEN:
        assert "[key]" in text
    elif key == LONG:
        assert "k" * 59 + "…" in text
    else:
        assert "qa connections[0]: all good" in text


@pytest.mark.parametrize("key", [TOKEN, FORGED, LONG])
def test_secret_problems_name_a_file_key_safely(key):
    check(cp.secret_problems({"environments": {key: {"password": "plain-value-123"}}}), key)


@pytest.mark.parametrize("key", [TOKEN, FORGED, LONG])
@pytest.mark.parametrize("where", ["environments", "deploy"])
def test_validate_names_an_unknown_environment_safely(key, where):
    with pytest.raises(cp.ProfileError) as exc:
        cp.validate(profile(**{where: {key: {}}}))
    check(exc.value.problems if hasattr(exc.value, "problems") else str(exc.value).split("; "), key)


@pytest.mark.parametrize("key", [TOKEN, FORGED, LONG])
@pytest.mark.parametrize("where", ["environments", "deploy"])
def test_validate_names_an_unknown_connection_id_in_a_mapping_safely(key, where):
    found = cp.problems(profile(**{where: {"dev": {key: "x"}}}))
    check(found, key)


@pytest.mark.parametrize("key", [TOKEN, FORGED, LONG])
def test_validate_names_an_unknown_default_connection_safely(key):
    check(cp.problems(profile(defaults={"connection": key})), key)


@pytest.mark.parametrize("key", [TOKEN, FORGED, LONG])
def test_duplicate_connection_ids_are_named_safely(key):
    conn = {"id": key, "platform": "databricks", "cloud": "azure", "host": HOST}
    check(cp.problems(profile(connections=[conn, dict(conn)])), key)


@pytest.mark.parametrize("key", [TOKEN, FORGED, LONG])
def test_a_stray_snowflake_credential_field_is_named_safely(key):
    conn = {"id": "sf", "platform": "snowflake", "account": "myorg-acct", "credential": {"method": "oauth-local", key: "x"}}
    check(cp.problems(profile(kind="snowflake", connections=[conn])), key)


def test_plain_keys_are_named_as_they_are():
    assert cp._key_text("staging") == "staging" and cp._key_text("x" * 60) == "x" * 60
    found = cp.problems(profile(environments={"qa": {}}))
    assert "environments.qa: environments are dev, test, staging, prod." in found


@pytest.mark.parametrize("schema", [[1], {"v": 1}, "two", 1.5, True])
def test_a_schema_that_isnt_a_whole_number_says_so(schema):
    assert "schema must be a whole number." in cp.problems(profile(schema=schema))
