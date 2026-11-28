"""Mutation batch 6: sealed secrets in settings.json (sealed_settings.py) at their edges.

DPAPI is a reversible stand-in, as in test_sealed_settings.py, so nothing touches Windows."""

from __future__ import annotations

import pytest

from fabric_desktop import dpapi, sealed_settings as S

OLD = "dpapi:b2xk"  # a sealed value this account can't read
NEW = "https://example.webhook.office.com/new"


@pytest.fixture
def fake_dpapi(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: True)
    monkeypatch.setattr(dpapi, "protect", lambda data, purpose=dpapi.ENTROPY: purpose + b"|" + data[::-1])

    def unprotect(blob, purpose=dpapi.ENTROPY):
        head, _, body = blob.partition(b"|")
        if head != purpose:
            raise OSError(13, "CryptUnprotectData failed")
        return body[::-1]
    monkeypatch.setattr(dpapi, "unprotect", unprotect)


def test_without_dpapi_a_sealed_value_is_not_decrypted_even_if_it_could_be(fake_dpapi, monkeypatch):
    sealed = S.seal({"teams_webhook": NEW})["teams_webhook"]
    monkeypatch.setattr(dpapi, "available", lambda: False)
    out = S.unseal({"teams_webhook": sealed})
    assert out["teams_webhook"] == "" and S.unreadable(out, "teams_webhook")


def test_a_webhook_set_again_replaces_the_unreadable_one(fake_dpapi):
    settings = {"teams_webhook": NEW, S.UNREADABLE: {"teams_webhook": OLD}}
    out = S.seal(settings)
    assert S.unseal(out)["teams_webhook"] == NEW
    assert S.UNREADABLE not in out


def test_a_webhook_left_empty_keeps_the_unreadable_one(fake_dpapi):
    out = S.seal({"teams_webhook": "", S.UNREADABLE: {"teams_webhook": OLD}})
    assert out["teams_webhook"] == OLD


def test_a_route_given_a_new_address_keeps_it_and_one_left_empty_keeps_the_old(fake_dpapi):
    settings = {S.ROUTES: {"routes": [{"name": "ops", "url": NEW}, {"name": "dev", "url": ""}]},
                S.UNREADABLE: {S.ROUTES: {"ops": OLD, "dev": OLD}}}
    routes = S.seal(settings)[S.ROUTES]["routes"]
    assert S.unseal({S.ROUTES: {"routes": [routes[0]]}})[S.ROUTES]["routes"][0]["url"] == NEW
    assert routes[1]["url"] == OLD


def test_a_route_without_an_address_is_left_as_it_is(fake_dpapi):
    settings = {S.ROUTES: {"routes": [{"name": "ops"}]}}
    assert S.unseal(settings)[S.ROUTES]["routes"] == [{"name": "ops"}]
    assert S.seal(settings)[S.ROUTES]["routes"] == [{"name": "ops"}]
