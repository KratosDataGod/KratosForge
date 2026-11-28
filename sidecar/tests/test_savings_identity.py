"""D3: the responsible person is the sign-in token's (tid, oid) and nothing else.

Security QC's conditions covered here: 1 (only tid and oid, only from the app's own token, every
other claim dropped and never logged), 3 (canonical lowercase GUIDs or refused, the pair as key, no
token gives None, never the Windows user or machine name) and 4 (email and UPN refused on the write
and import paths; ``test_savings_records`` covers create, transition and history)."""

from __future__ import annotations

import base64
import getpass
import json
import logging
import os
import platform
import socket
from typing import Any

import pytest
import requests

from fabric_desktop import auth
from fabric_desktop.savings import identity as idn

TID = "72f988bf-86f1-41af-91ab-2d7cd011db47"
OID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
UPN = "ann.lee@contoso.com"
IP = "203.0.113.42"


def _seg(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwt(claims: Any, *, header: Any = None) -> str:
    return ".".join([_seg(header or {"alg": "RS256", "typ": "JWT", "kid": "k1"}), _seg(claims), "c2lnbmF0dXJl"])


def full_claims(**over: Any) -> dict[str, Any]:
    return {"aud": "https://api.fabric.microsoft.com", "iss": f"https://sts.windows.net/{TID}/", "tid": TID,
            "oid": OID, "upn": UPN, "email": UPN, "unique_name": UPN, "preferred_username": UPN,
            "name": "Ann Lee", "given_name": "Ann", "family_name": "Lee", "ipaddr": IP, "sub": "abc", "scp": "x",
            "appid": auth.MICROSOFT_CLIENT_ID, **over}


# ---------- condition 1: only tid and oid, from the token's payload

def test_only_tid_and_oid_are_kept_from_a_token_with_upn_email_name_and_ipaddr():
    got = idn.from_token(jwt(full_claims()))
    assert got == {"tid": TID, "oid": OID}
    assert set(got) == {"tid", "oid"}
    text = json.dumps(got)
    for leaked in (UPN, "Ann", "Lee", IP, "abc", "api.fabric"):
        assert leaked not in text


def test_the_signature_is_not_needed_and_nothing_is_fetched(monkeypatch):
    def no_network(*a: Any, **k: Any) -> Any:
        raise AssertionError("from_token must not call the network")
    monkeypatch.setattr(requests.Session, "request", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    token = jwt(full_claims())
    unsigned = token.rsplit(".", 1)[0] + "."
    assert idn.from_token(token) == idn.from_token(unsigned) == {"tid": TID, "oid": OID}


# ---------- condition 3: canonical lowercase GUIDs, or nothing

MALFORMED = [None, "", 42, b"x.y.z", "not a token", "a.b", "a.b.c.d.e", "Bearer " + "x" * 10, "a.!!!!.c",
             "a." + _seg(b"\xff\xfe") + ".c", "a." + _seg(b"not json") + ".c", jwt(["tid", TID]), jwt("tid"),
             jwt(None), "a.A.c", "a." + "A" * (idn.MAX_TOKEN + 1) + ".c"]


@pytest.mark.parametrize("token", MALFORMED, ids=lambda t: repr(t)[:40])
def test_a_malformed_token_gives_none_and_never_raises(token):
    assert idn.from_token(token) is None


@pytest.mark.parametrize("claims", [
    {"oid": OID}, {"tid": TID}, {}, {"upn": UPN, "name": "Ann"},
    full_claims(tid=None), full_claims(oid=None),
    full_claims(tid=TID.upper()), full_claims(oid=OID.upper()), full_claims(oid=OID[:-1] + "F"),
    full_claims(oid="{" + OID + "}"), full_claims(oid=OID.replace("-", "")), full_claims(oid=OID + "\n"),
    full_claims(oid=" " + OID), full_claims(tid="contoso.onmicrosoft.com"), full_claims(oid=UPN),
    full_claims(oid=12345), full_claims(tid=[TID]), full_claims(oid="0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1fg"),
], ids=range(18))
def test_a_missing_claim_uppercase_or_non_guid_gives_none(claims):
    assert idn.from_token(jwt(claims)) is None


def test_uppercase_is_refused_not_lowercased():
    assert idn.from_token(jwt(full_claims(tid=TID.upper(), oid=OID.upper()))) is None
    with pytest.raises(ValueError, match="lowercase"):
        idn.check({"tid": TID.upper(), "oid": OID})


def test_the_pair_is_the_key_never_the_object_id_alone():
    other_tenant = "11111111-2222-3333-4444-555555555555"
    a, b = {"tid": TID, "oid": OID}, {"tid": other_tenant, "oid": OID}
    assert idn.key(a) == (TID, OID) and idn.key(None) is None
    assert idn.same(a, dict(a)) and not idn.same(a, b) and not idn.same(a, None) and idn.same(None, None)
    with pytest.raises(ValueError, match="without its tenant id"):
        idn.key({"oid": OID})


# ---------- current(): the app's own sign-in, and no fallback

class _Tracked(dict):
    """os.environ stand-in that records every name read."""

    def __init__(self, base: Any, read: list[str]):
        super().__init__(base)
        self.read = read

    def __getitem__(self, k: Any) -> Any:
        self.read.append(str(k))
        return super().__getitem__(k)

    def get(self, k: Any, default: Any = None) -> Any:
        self.read.append(str(k))
        return super().get(k, default)

    def __contains__(self, k: Any) -> bool:
        self.read.append(str(k))
        return super().__contains__(k)


@pytest.fixture
def no_os_identity(monkeypatch):
    """Fails the test if anything reads the Windows user or machine name."""
    touched: list[str] = []

    def forbidden(what: str):
        def call(*a: Any, **k: Any) -> Any:
            touched.append(what)
            raise AssertionError(f"{what} must never be read for the responsible person")
        return call

    monkeypatch.setattr(getpass, "getuser", forbidden("getpass.getuser"))
    monkeypatch.setattr(os, "getlogin", forbidden("os.getlogin"))
    monkeypatch.setattr(socket, "gethostname", forbidden("socket.gethostname"))
    monkeypatch.setattr(platform, "node", forbidden("platform.node"))
    read: list[str] = []
    monkeypatch.setattr(os, "environ", _Tracked(os.environ, read))
    yield touched, read
    who = {"USERNAME", "USER", "LOGNAME", "USERDOMAIN", "USERPRINCIPALNAME", "COMPUTERNAME", "HOSTNAME"}
    assert not touched
    assert not who & {r.upper() for r in read}, read


def _signed_in(token: Any = None, *, raises: BaseException | None = None) -> tuple[auth.Authenticator, list]:
    """An Authenticator whose cached token is ``token`` (no MSAL, no file, no network)."""
    cred = object.__new__(auth.Authenticator)
    asked: list = []

    def silent_token(scopes: list[str]) -> str | None:
        asked.append(list(scopes))
        if raises is not None:
            raise raises
        return token

    cred.silent_token = silent_token  # type: ignore[method-assign]
    return cred, asked


def test_current_reads_the_apps_own_fabric_token(no_os_identity):
    cred, asked = _signed_in(jwt(full_claims()))
    assert idn.current(cred) == {"tid": TID, "oid": OID}
    assert asked == [auth.FABRIC_SCOPES]


@pytest.mark.parametrize("case", ["no credential", "signed out", "offline", "cache unreadable", "malformed",
                                  "no oid"])
def test_no_token_or_offline_gives_none_and_never_the_os_user(no_os_identity, case, caplog):
    secret = jwt(full_claims(tid=TID.upper()))
    cred: Any
    cred, _ = {
        "no credential": (None, []),
        "signed out": _signed_in(None),
        "offline": _signed_in(raises=requests.ConnectionError(f"login failed for {UPN} with {secret}")),
        "cache unreadable": _signed_in(raises=OSError("locked")),
        "malformed": _signed_in("not.a.token"),
        "no oid": _signed_in(jwt({"tid": TID, "upn": UPN})),
    }[case]
    with caplog.at_level(logging.DEBUG):
        assert idn.current(cred) is None
    assert UPN not in caplog.text and secret not in caplog.text


def test_an_app_identity_is_not_a_person(no_os_identity):
    class ServicePrincipal:
        def silent_token(self, scopes: list[str]) -> str:
            raise AssertionError("an app identity's token isn't read for a person")

    assert idn.current(ServicePrincipal()) is None


# ---------- check(): every write and import path; condition 4

def test_check_accepts_none_or_exactly_the_pair_and_returns_a_copy():
    assert idn.check(None) is None
    given = {"tid": TID, "oid": OID}
    got = idn.check(given)
    assert got == given and got is not given


@pytest.mark.parametrize("bad", [
    UPN, "ann%40contoso.com", "CONTOSO\\ann", {"tid": TID, "oid": UPN}, {"tid": UPN, "oid": OID},
    {"tid": TID, "oid": OID, "upn": UPN}, {"tid": TID, "oid": OID, UPN: 1}, {"tid": TID, "oid": [UPN]},
    {"tid": TID, "oid": {"mail": UPN}},
], ids=range(9))
def test_at_signs_and_upn_like_values_are_refused_without_echoing_them(bad):
    with pytest.raises(ValueError, match="email address or UPN") as err:
        idn.check(bad)
    assert "ann" not in str(err.value) and "contoso" not in str(err.value)


@pytest.mark.parametrize("bad,why", [
    ({"oid": OID}, "without its tenant id"),
    ({"oid": OID, "x": 1}, "without its tenant id"),
    ({"tid": TID}, "exactly tid and oid"),
    ({"tid": TID, "oid": OID, "name": "Ann"}, "exactly tid and oid"),
    ({}, "exactly tid and oid"),
    ({"tid": TID, "oid": OID.upper()}, "lowercase"),
    ({"tid": TID, "oid": "not-a-guid"}, "GUID"),
    ({"tid": TID, "oid": None}, "GUID"),
    ({"tid": TID, "oid": 7}, "GUID"),
    (OID, "pair"),
    ((TID, OID), "pair"),
    (True, "pair"),
], ids=range(12))
def test_check_refuses_extra_keys_an_oid_without_tid_and_non_guids(bad, why):
    with pytest.raises(ValueError, match=why) as err:
        idn.check(bad)
    assert OID.upper() not in str(err.value) and "Ann" not in str(err.value)


def test_the_module_says_the_ids_are_labels_not_proof_or_authority():
    doc = " ".join((idn.__doc__ or "").split())
    for words in ("pseudonymous labels", "not proof", "not authority", "access decision", "exports, logs and support"):
        assert words in doc, words
