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


ROUTES = {"routes": [{"name": "default", "kinds": [], "url": HOOK},
                     {"name": "finance", "kinds": ["request.waiting"], "url": HOOK.replace("abc", "fin")}],
          "quiet": ""}


def test_platform_route_addresses_are_sealed_and_read_back(fake_dpapi):
    assert sealed_settings.needs_sealing({"platform_notify": ROUTES})
    sealed = sealed_settings.seal({"platform_notify": ROUTES})
    assert "SECRET" not in json.dumps(sealed) and not sealed_settings.needs_sealing(sealed)
    assert [r["name"] for r in sealed["platform_notify"]["routes"]] == ["default", "finance"]
    assert sealed_settings.unseal(sealed)["platform_notify"] == ROUTES
    assert sealed_settings.seal(sealed) == sealed
    assert ROUTES["routes"][0]["url"] == HOOK  # the caller's dict isn't changed


def test_a_route_address_this_account_cant_read_is_flagged_and_kept(fake_dpapi):
    foreign = "dpapi:" + __import__("base64").b64encode(b"another purpose|route").decode()
    saved = {"platform_notify": {"routes": [{"name": "default", "url": foreign}, {"name": "finance", "url": HOOK}]}}
    read = sealed_settings.unseal(sealed_settings.seal(saved))
    assert read["platform_notify"]["routes"][0]["url"] == ""
    assert read[sealed_settings.UNREADABLE] == {"platform_notify": {"default": foreign}}
    written = sealed_settings.seal(read)  # an unrelated save keeps it as it was
    assert written["platform_notify"]["routes"][0]["url"] == foreign
    assert sealed_settings.UNREADABLE not in written


def test_the_service_never_writes_route_addresses_in_the_clear(fake_dpapi, tmp_path):
    from fabric_desktop.service import Service
    svc = Service(client_id="x", root=tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"platform_notify": ROUTES}))  # saved by an earlier version
    shown = svc.platform_notify_settings()
    assert [(r["url_set"], "unreadable" in r) for r in shown["routes"]] == [(True, False), (True, False)]
    assert "SECRET" not in (tmp_path / "settings.json").read_text()  # sealed on first read
    svc.platform_notify_save([{"name": "default", "kinds": []}])  # keeps its address by name
    assert "SECRET" not in (tmp_path / "settings.json").read_text()
    assert svc._pn_raw()["routes"][0]["url"] == HOOK
    foreign = "dpapi:" + __import__("base64").b64encode(b"another purpose|route").decode()
    (tmp_path / "settings.json").write_text(json.dumps({"platform_notify": {"routes": [{"name": "default", "url": foreign}]}}))
    assert svc.platform_notify_settings()["routes"][0] | {"host": ""} == {
        "name": "default", "kinds": [], "domains": [], "url_set": False, "host": "", "unreadable": True}


def test_a_failed_sealing_write_is_logged_once(fake_dpapi, tmp_path, monkeypatch, caplog):
    from fabric_desktop import meta_service
    from fabric_desktop.service import Service
    monkeypatch.setattr(meta_service, "_seal_failed", [])
    svc = Service(client_id="x", root=tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"teams_webhook": HOOK}))

    def refuse(*_a, **_k):
        raise OSError(5, "Access is denied")
    monkeypatch.setattr(meta_service, "write_json", refuse)
    for _ in range(3):
        assert svc.meta_alerts()["teams_webhook"] == HOOK
    assert sum("Couldn't encrypt" in r.getMessage() for r in caplog.records) == 1
