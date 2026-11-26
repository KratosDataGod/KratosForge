"""Platform notices in the app (platform_notify_service.py), mutation batch 279: what its first
mutation score found untested. A call that isn't a list, the counts when no route is set, quiet hours
and exemptions shown back, a test to a route that isn't there, the mail server's defaults and a
password that can't be read, the bell's fields for each notice, the latest reason a notice wasn't
taken, and where a notice opens."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import app_settings
from fabric_desktop import platform_notify_service as pns
from fabric_desktop.service import Service
from fabric_desktop.workloads import deliver
from test_platform_notify import HOOK, Posts, raw

OTHER = "https://contoso.webhook.office.com/other"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    posts = Posts()
    monkeypatch.setattr(pns, "post_teams", posts)
    vault: dict[str, tuple[str, str]] = {}
    monkeypatch.setattr(pns.credman, "write", lambda t, u, p: vault.__setitem__(t, (u, p)))
    monkeypatch.setattr(pns.credman, "read", lambda t: vault[t][1] if t in vault else None)
    monkeypatch.setattr(pns.credman, "delete", lambda t: vault.pop(t, None))
    sent = []
    monkeypatch.setattr(deliver, "send_email", lambda mail, pw, to, title, lines, smtp=None: sent.append((mail.host, pw, to)))
    s = Service(root=tmp_path / "app")
    s.posts, s.vault, s.sent = posts, vault, sent
    return s


def test_events_that_arent_a_list_are_refused_as_such(svc):
    with pytest.raises(ValueError, match="^events is a list$"):
        svc.platform_notify("request.waiting")


def test_with_no_route_nothing_is_counted(svc):
    out = svc.platform_notify([raw()])
    assert (out["sent"], out["queued"], out["held"], out["error"]) == (0, 0, 0, None)


def test_quiet_hours_and_their_exemptions_are_saved_and_shown_back(svc):
    shown = svc.platform_notify_save([{"name": "default", "url": HOOK}], quiet="22:00-07:00",
                                     quiet_exempt=["request.waiting"])
    assert shown["quiet"] == "22:00-07:00" and shown["quiet_exempt"] == ["request.waiting"]
    assert svc._pn_raw()["quiet_exempt"] == ["request.waiting"]
    shown = svc.platform_notify_save([{"name": "default", "url": HOOK}])
    assert shown["quiet"] == "" and shown["quiet_exempt"] == []


def test_a_test_to_a_route_that_isnt_there_is_refused_and_nothing_is_saved(svc):
    with pytest.raises(ValueError, match="route nope has nowhere to send a test"):
        svc.platform_notify_save([{"name": "default", "url": HOOK}], test="nope")
    assert svc._pn_raw() == {} and svc.posts == []


def test_an_email_route_is_shown_as_email(svc):
    svc.mail_server_save("smtp.contoso.com", "kf@contoso.com")
    shown = svc.platform_notify_save([{"name": "default", "url": "mailto:ana@contoso.com"}])
    assert shown["routes"][0]["kind"] == "email"


def test_the_mail_server_defaults_to_port_587_and_each_change_returns_what_is_set(svc):
    shown = svc.mail_server_save("smtp.contoso.com", "kf@contoso.com")
    assert shown["set"] is True and shown["port"] == 587 and shown["password_saved"] is False
    assert svc.mail_server_forget() == {"set": False}


def test_a_relay_without_a_user_name_never_shows_or_sends_a_password(svc, monkeypatch):
    svc.vault[deliver.PASSWORD_TARGET] = ("old", "stale")  # pragma: allowlist secret (test value)
    monkeypatch.setattr(pns.credman, "delete", lambda t: None)  # left behind, as by an older build
    svc.mail_server_save("smtp.contoso.com", "kf@contoso.com", test_to="ana@contoso.com")
    assert svc.sent == [("smtp.contoso.com", None, ["ana@contoso.com"])]
    assert svc.mail_server_settings()["password_saved"] is False


def test_a_password_that_cant_be_read_shows_as_not_saved(svc, monkeypatch):
    svc.mail_server_save("smtp.contoso.com", "kf@contoso.com", username="svc", password="pw")  # pragma: allowlist secret (test value)

    def locked(target):
        raise pns.credman.CredentialError("locked")
    monkeypatch.setattr(pns.credman, "read", locked)
    shown = svc.mail_server_settings()
    assert shown["set"] is True and shown["password_saved"] is False


def test_the_bell_shows_each_field_of_a_notice_and_blanks_for_one_whose_row_is_gone(svc):
    import sqlite3
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    svc.platform_notify([raw()])
    [row] = svc.platform_notify_open()["open"]
    assert (row["title"], row["whom"], row["domain"], row["env"], row["detail"]) == \
        ("Access to LH_Sales", "ana@contoso.com", "sales", "prod", "")
    db = sqlite3.connect(svc.root / "platform_outbox.sqlite")
    db.execute("DELETE FROM notice")
    db.commit()
    db.close()
    [row] = svc.platform_notify_open()["open"]
    assert (row["title"], row["whom"], row["domain"], row["env"], row["detail"]) == ("", "", "", "", "")


def test_the_bell_gives_the_latest_reason_a_notice_wasnt_taken(svc, monkeypatch):
    def down(url, title, lines):
        raise ConnectionError("first route down" if url == HOOK else "second route down")
    monkeypatch.setattr(pns, "post_teams", down)
    svc.platform_notify_save([{"name": "a", "url": HOOK, "kinds": ["request.waiting"]},
                              {"name": "b", "url": OTHER, "kinds": ["shortcut.broken"]}])
    svc.platform_notify([raw(), dict(raw(kind="shortcut.broken", id="s1", title="T"),
                                     open={"view": "shortcuts", "tab": "health"}, dedupe="shortcut.broken:s1")])
    shown = svc.platform_notify_open()
    assert shown["waiting"] == 2 and "second route down" in shown["error"]


def test_a_notice_opens_where_it_was_recorded_and_otherwise_on_its_kinds_page():
    target = {"view": "platform", "tab": "requests", "target": "r1"}
    assert pns._target("request.waiting", json.dumps(target)) == target
    assert pns._target("request.waiting", json.dumps({"view": "nowhere", "tab": "requests"})) == \
        {"view": "platform", "tab": "requests"}
    assert pns._target("shortcut.broken", json.dumps({"view": "platform", "tab": "nowhere"})) == \
        {"view": "shortcuts", "tab": "health"}
    assert pns._target("shortcut.fixed", None) == {"view": "shortcuts", "tab": "health"}


def test_closed_notices_are_kept_a_year():
    assert pns.SENT_KEEP_SECONDS == 365 * 24 * 3600
