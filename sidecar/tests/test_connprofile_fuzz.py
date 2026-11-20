"""Seeded fuzz tests for connection profiles and the CLI files they come from: Databricks workspace
addresses, Snowflake account identifiers, secrets where a reference belongs, profiles of the wrong shape,
~/.databrickscfg and the Snowflake CLI's connections.toml. Every input is accepted only when it is valid,
or refused with a ProfileError (or a DatabricksAuthError) naming the problem, never a crash, and a
profile that should be refused (an insecure host, a secret in plain text) is never accepted.

The generators use the random module with fixed seeds, so a failure names a case that can be run again.
"""

from __future__ import annotations

import copy
import random
import re
from urllib.parse import urlparse

import pytest

from fabric_desktop import connprofiles as cp
from fabric_desktop.engines import databricks_auth

GOOD_HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
HOST_ATOMS = ["https://", "http://", "file://", "HTTPS://", "javascript:", "adb-1", ".7", ".azuredatabricks.net",
              ".evil.com", "@", "/", "?", "#", ";", ":443", ":8443", ":", "\n", "\r", "\t", " ", "%2e", "．", "\u200b",
              "\\", "[", "]", "..", "-", "a", "ａ", "\x00", " ", "x" * 70, "﻿", "'", '"', "token=", "dapi" + "0" * 32]
ACCOUNT_ATOMS = ["myorg", "-", "acct", ".", "us-east-1", "aws", "@", "/", "?", "#", ":", "\n", " ", "_", "ａ", "\u200b",
                 "https://", "file://", ".snowflakecomputing.com", "x" * 160, "%2f", "\\"]
# A made-up token, joined at run time so secret scanners don't read it as a real one.
FAKE_PAT = "dapi" + "0123456789abcdef" * 2
SECRET_VALUES = [FAKE_PAT, "ghp_" + "a" * 36, "-----BEGIN PRIVATE KEY-----\nMIIabc",  # pragma: allowlist secret (made up for the test)
                 "password=hunter2xyz"]
CASES = 6000


def _host_ok(host: str) -> bool:
    """The oracle: an https Azure Databricks workspace address and nothing else."""
    if not host.isascii() or any(not c.isprintable() or c.isspace() or c == "#" for c in host):
        return False
    try:
        url = urlparse(host)
        port = url.port
    except ValueError:
        return False
    return (url.scheme == "https" and port in (None, 443) and url.path in ("", "/")
            and not (url.params or url.query or url.fragment or url.username or url.password)
            and re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+azuredatabricks\.net", url.hostname or "") is not None)


def _databricks(host: object, **cred: object) -> dict[str, object]:
    return {"name": "P", "kind": "databricks",
            "connections": [{"platform": "databricks", "host": host, "credential": {"method": "oauth-u2m", **cred}}]}


def _accepted(profile: dict[str, object]) -> bool:
    try:
        cp.validate(profile)
    except cp.ProfileError as exc:
        assert exc.problems and all(isinstance(p, str) and p for p in exc.problems)
        return False
    return True


def test_a_databricks_host_is_accepted_only_when_it_is_an_https_workspace_address():
    for case in range(CASES):
        r = random.Random(20000 + case)
        tail = "".join(r.choice(HOST_ATOMS) for _ in range(r.randint(1, 8)))
        host = GOOD_HOST + tail if r.random() < 0.5 else tail
        assert _accepted(_databricks(host)) == _host_ok(host), (case, host)
        if _host_ok(host):
            assert databricks_auth.check_host(host) == "https://" + (urlparse(host).hostname or "")
        else:
            with pytest.raises(databricks_auth.DatabricksAuthError):
                databricks_auth.check_host(host)


def test_a_snowflake_account_is_never_a_url_or_another_host():
    for case in range(CASES):
        r = random.Random(30000 + case)
        account = "".join(r.choice(ACCOUNT_ATOMS) for _ in range(r.randint(1, 6)))
        profile = {"name": "S", "kind": "snowflake",
                   "connections": [{"platform": "snowflake", "account": account, "credential": {"method": "oauth-local"}}]}
        if not _accepted(profile):
            continue
        host = cp.snowflake_host(account)
        url = urlparse(host)
        assert host.isascii() and url.scheme == "https" and not (url.path or url.username or url.port), (case, account)
        assert (url.hostname or "").endswith(".snowflakecomputing.com"), (case, account)
        assert re.fullmatch(r"[A-Za-z0-9_.-]+", account), (case, account)


JUNK = [None, 1, 2.5, True, "", "x", [], {}, [1], {"a": 1}, "\n", "é", "\x00", 10**30, float("nan")]
FIELDS = ["id", "label", "host", "cloud", "account", "tenant_id", "user", "role", "warehouse", "database", "schema",
          "catalog", "compute", "workspace", "credential"]
CRED = ["method", "secret", "passphrase", "client_id", "profile", "connection", "private_key_file", "needs_setup",
        "token", "pwd", "private_key_raw"]
VALID = [_databricks(GOOD_HOST),
         {"name": "F", "kind": "fabric", "connections": [{"platform": "fabric"}]},
         {"name": "S", "kind": "snowflake", "connections": [{"platform": "snowflake", "account": "myorg-myacct",
                                                             "credential": {"method": "pat", "secret": "env:SF_PAT"}}]},  # pragma: allowlist secret (made up for the test)
         {"name": "H", "kind": "hybrid", "connections": [{"platform": "fabric"}, {"platform": "databricks", "host": GOOD_HOST}]}]


SECRET_KEYS = ("secret", "passphrase", "token", "pwd", "private_key_raw")


def _strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _strings(v)]
    if isinstance(value, list):
        return [x for v in value for x in _strings(v)]
    return [value] if isinstance(value, str) else []


def _holds_secret(p: dict[str, object]) -> bool:
    """A secret in plain text: a token-like value anywhere, or a secret-named field that isn't a reference."""
    if any(s in SECRET_VALUES for s in _strings(p)):
        return True
    creds = [c["credential"] for c in p["connections"] if isinstance(c, dict) and isinstance(c.get("credential"), dict)]
    return any(cred.get(k) not in (None, "", [], {}) and not (isinstance(cred[k], str) and cp._REF.fullmatch(cred[k]))  # noqa: SLF001
               for cred in creds for k in SECRET_KEYS)


def _mutated(r: random.Random) -> dict[str, object]:
    """A valid profile with one or two values replaced."""
    p = copy.deepcopy(r.choice(VALID))
    for _ in range(r.randint(1, 2)):
        conn = r.choice(p["connections"])
        value = copy.deepcopy(r.choice(JUNK + SECRET_VALUES + ["env:OK", "credman:ok"]))
        where = r.random()
        if where < 0.4 and isinstance(conn.get("credential"), dict):
            conn["credential"][r.choice(CRED)] = value
        elif where < 0.8:
            conn[r.choice(FIELDS)] = value
        else:
            p[r.choice(["name", "kind", "description", "schema", "defaults", "runtime", "notes"])] = value
    return p


def test_mutated_profiles_are_valid_or_refused_and_a_plain_secret_is_never_accepted():
    for case in range(CASES):
        r = random.Random(40000 + case)
        p = _mutated(r)
        secret = _holds_secret(p)
        doc = {"kind": "kratosforge.connection-profile", "profile": p}
        for check in (lambda p=p: cp.validate(p), lambda doc=doc: cp.import_(doc), lambda p=p: cp.export(p)):
            try:
                got = check()
            except cp.ProfileError as exc:
                assert exc.problems, case
                continue
            assert not secret, (case, p)
            text = repr(got)
            assert not any(s[:12] in text for s in SECRET_VALUES), case


@pytest.mark.parametrize("key", ["pwd", "private_key_raw", "privateKey", "PAT", "token", "client_secret"])
def test_a_secret_named_field_in_plain_text_is_refused(key):
    with pytest.raises(cp.ProfileError, match="must be a reference"):
        cp.validate(_databricks(GOOD_HOST, **{key: "hunter2"}))
    with pytest.raises(cp.ProfileError, match="must be a reference"):
        cp.import_({"kind": "kratosforge.connection-profile", "profile": _databricks(GOOD_HOST, **{key: "hunter2"})})


def test_a_keypair_profile_still_names_its_key_file():
    profile = {"name": "S", "kind": "snowflake", "connections": [{"platform": "snowflake", "account": "myorg-myacct",
               "credential": {"method": "keypair", "private_key_file": "/keys/sf.p8", "passphrase": "env:SF_PASS"}}]}
    assert cp.validate(profile)["connections"][0]["credential"]["private_key_file"] == "/keys/sf.p8"


def test_a_host_with_url_parameters_is_refused():
    assert not _accepted(_databricks(GOOD_HOST + "/;x"))
    with pytest.raises(databricks_auth.DatabricksAuthError):
        databricks_auth.check_host(GOOD_HOST + "/;x")


def test_a_profile_nested_too_deep_is_refused_by_name():
    deep: list[object] = []
    for _ in range(600):
        deep = [deep]
    p = {"name": "P", "kind": "local", "runtime": {"packages": deep}}
    assert cp.problems(p) == ["The profile is nested more than 32 levels deep, so it isn't a connection profile."]
    with pytest.raises(cp.ProfileError, match="nested more than 32 levels"):
        cp.import_({"kind": "kratosforge.connection-profile", "profile": p})


# ---- the CLI files

CFG = b"""[DEFAULT]
host = https://adb-1.7.azuredatabricks.net
token = FAKE_PAT_HERE

[dev]
host = https://adb-2.7.azuredatabricks.net/
auth_type = databricks-cli
cluster_id = 0101-abc
serverless_compute_id = auto

[m2m]
host=https://adb-3.7.azuredatabricks.net
client_id = abc
client_secret = s3cr3tvalue
""".replace(b"FAKE_PAT_HERE", FAKE_PAT.encode())
TOML = b"""default_connection_name = "dev"
[dev]
account = "myorg-myacct"
user = "me"
authenticator = "SNOWFLAKE_JWT"
private_key_file = "/k.p8"
password = "hunter2xyz"  # pragma: allowlist secret (made up)
"""


def _mutate(r: random.Random, data: bytes) -> bytes:
    b = bytearray(data)
    for _ in range(r.randint(1, 5)):
        op = r.random()
        if op < 0.3 and b:
            b[r.randrange(len(b))] ^= 1 << r.randrange(8)
        elif op < 0.6:
            at = r.randint(0, len(b))
            b[at:at] = r.choice([b"\x00", b"\xff", b"\xef\xbb\xbf", b"\n", b"[", b"]", b"=", b"%(x)s", b"\r", b'"', b"\\",
                                 b"https://[", b"\xc3"])
        elif op < 0.8:
            del b[r.randint(0, len(b)):]
        else:
            b[:0] = b"\xef\xbb\xbf"
    return bytes(b)


def test_mutated_cli_files_are_read_or_refused_and_never_return_a_secret(tmp_path):
    for case in range(1500):
        r = random.Random(50000 + case)
        (tmp_path / "cfg").write_bytes(_mutate(r, CFG))
        (tmp_path / "connections.toml").write_bytes(_mutate(r, TOML))
        dbx = cp.databricks_cli_profiles(tmp_path / "cfg")
        for entry in dbx["profiles"]:
            assert "dapi0123456789" not in entry["host"], case
            conn = cp.connection_from_cli_profile(entry)
            assert conn["credential"] == {"method": "cli-profile", "profile": entry["name"]}, case
        sf = cp.snowflake_cli_connections(tmp_path)
        assert "hunter2xyz" not in repr(sf), case
        for entry in sf["connections"]:
            cp.connection_from_cli_connection(entry)


def test_a_cli_profile_with_a_broken_host_is_listed_and_refused_when_used(tmp_path):
    (tmp_path / "cfg").write_bytes(b"[bad]\nhost = https://[adb-1.7.azuredatabricks.net\n[good]\nhost = " + GOOD_HOST.encode())
    profiles = cp.databricks_cli_profiles(tmp_path / "cfg")["profiles"]
    conns = [cp.connection_from_cli_profile(p) for p in profiles]  # one broken host doesn't stop the list
    assert [c["host"] for c in conns] == ["https://[adb-1.7.azuredatabricks.net", GOOD_HOST]
    with pytest.raises(databricks_auth.DatabricksAuthError):
        databricks_auth.check_host(conns[0]["host"])


def test_a_databrickscfg_with_a_bom_is_read(tmp_path):
    (tmp_path / "cfg").write_bytes(b"\xef\xbb\xbf" + CFG)
    got = cp.databricks_cli_profiles(tmp_path / "cfg")
    assert [p["name"] for p in got["profiles"]] == ["DEFAULT", "dev", "m2m"] and "error" not in got
    assert databricks_auth._cfg_section("dev", tmp_path / "cfg")["cluster_id"] == "0101-abc"  # noqa: SLF001


@pytest.mark.parametrize("credential", [None, {"method": "oauth-m2m", "client_id": "c", "secret": ["env:A"]}])
def test_a_hybrid_profile_with_a_credential_of_the_wrong_shape_is_refused(credential):
    profile = {"name": "H", "kind": "hybrid", "connections": [
        {"platform": "fabric"}, {"platform": "databricks", "host": GOOD_HOST, "credential": credential}]}
    with pytest.raises(cp.ProfileError):
        cp.validate(profile)
