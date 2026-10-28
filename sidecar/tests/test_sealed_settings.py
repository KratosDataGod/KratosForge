"""The Teams webhook address in settings.json is stored encrypted with DPAPI where Windows has it."""

import json

import pytest

from fabric_desktop import dpapi, sealed_settings

HOOK = "https://prod-01.westeurope.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=SECRET"


@pytest.fixture
def fake_dpapi(monkeypatch):
    """A reversible stand-in for DPAPI that, like it, refuses another purpose."""
    monkeypatch.setattr(dpapi, "available", lambda: True)
    monkeypatch.setattr(dpapi, "protect", lambda data, purpose=dpapi.ENTROPY: purpose + b"|" + data[::-1])

    def unprotect(blob, purpose=dpapi.ENTROPY):
        head, _, body = blob.partition(b"|")
        if head != purpose:
            raise OSError(13, "CryptUnprotectData failed")
        return body[::-1]
    monkeypatch.setattr(dpapi, "unprotect", unprotect)


def test_a_webhook_is_sealed_and_read_back(fake_dpapi):
    sealed = sealed_settings.seal({"teams_webhook": HOOK, "cost": {"price": 0.18}})
    assert sealed["teams_webhook"].startswith("dpapi:") and "SECRET" not in sealed["teams_webhook"]
    assert sealed["cost"] == {"price": 0.18}
    assert sealed_settings.unseal(sealed)["teams_webhook"] == HOOK
    assert sealed_settings.seal(sealed) == sealed  # sealing twice changes nothing


def test_a_value_this_account_cant_read_is_treated_as_not_set(fake_dpapi):
    other = "dpapi:" + __import__("base64").b64encode(b"another purpose|xyz").decode()
    assert sealed_settings.unseal({"teams_webhook": other})["teams_webhook"] == ""
    assert sealed_settings.unseal({"teams_webhook": "dpapi:not base64!"})["teams_webhook"] == ""


def test_without_dpapi_values_stay_as_they_are(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    assert sealed_settings.seal({"teams_webhook": HOOK}) == {"teams_webhook": HOOK}
    assert not sealed_settings.needs_sealing({"teams_webhook": HOOK})
    assert sealed_settings.unseal({"teams_webhook": "dpapi:abc"})["teams_webhook"] == ""


def test_the_service_never_writes_the_address_in_the_clear(fake_dpapi, tmp_path):
    from fabric_desktop.service import Service
    svc = Service(client_id="x", root=tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"teams_webhook": HOOK}))  # saved by an earlier version
    assert svc.meta_alerts() == {"teams_webhook": HOOK}
    assert "SECRET" not in (tmp_path / "settings.json").read_text()  # sealed on first read
    svc.meta_set_alerts(HOOK.replace("abc", "def"))
    assert "SECRET" not in (tmp_path / "settings.json").read_text()
    assert svc.meta_alerts()["teams_webhook"] == HOOK.replace("abc", "def")
    svc.meta_set_alerts("")
    assert svc.meta_alerts() == {"teams_webhook": ""}


def test_an_address_this_account_cant_read_is_flagged_and_kept_until_set_again(fake_dpapi, tmp_path):
    from fabric_desktop.service import Service
    svc = Service(client_id="x", root=tmp_path)
    foreign = "dpapi:" + __import__("base64").b64encode(b"another purpose|xyz").decode()  # another account's
    (tmp_path / "settings.json").write_text(json.dumps({"teams_webhook": foreign}))
    assert svc.meta_alerts() == {"teams_webhook": "", "unreadable": True}
    assert svc.meta_freshness()["alerts_unreadable"] is True
    svc._save_settings(meta_project="")  # an unrelated save keeps it as it was
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["teams_webhook"] == foreign and sealed_settings.UNREADABLE not in saved
    svc.meta_set_alerts(HOOK)  # entered again
    assert svc.meta_alerts() == {"teams_webhook": HOOK}
    assert svc.meta_freshness()["alerts_unreadable"] is False


@pytest.mark.skipif(not dpapi.available(), reason="Windows DPAPI")
def test_real_dpapi_round_trips_and_refuses_another_purpose():
    blob = dpapi.protect(b"hook", sealed_settings.PURPOSE)
    assert dpapi.unprotect(blob, sealed_settings.PURPOSE) == b"hook"
    with pytest.raises(OSError):
        dpapi.unprotect(blob)  # the sign-in cache's purpose


def test_an_unreadable_value_is_logged_once(fake_dpapi, caplog):
    foreign = "dpapi:" + __import__("base64").b64encode(b"another purpose|once").decode()
    for _ in range(3):
        sealed_settings.unseal({"teams_webhook": foreign})
    assert sum("can't be read" in r.getMessage() for r in caplog.records) == 1
