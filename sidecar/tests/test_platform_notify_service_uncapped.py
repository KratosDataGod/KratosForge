"""Platform notices in the app, edges found by running every mutant of platform_notify_service.py
(mutation batch 324, docs/quality/mutation-testing.md): what the save calls return, the settings
the UI reads back, the mail server's port and saved password, a test message to a route that isn't
there, the bell's details and where a notice opens, the last error, and how long dedupe keys last."""

import time

import pytest

from fabric_desktop import app_settings, credman, sealed_settings
from fabric_desktop import platform_notify_service as pns
from fabric_desktop.service import Service
from fabric_desktop.workloads import deliver
from fabric_desktop.workloads.platform_notify import Ledger

from test_platform_notify import HOOK, Posts, event

DAY = 86400


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    posts = Posts()
    monkeypatch.setattr(pns, "post_teams", posts)
    vault: dict[str, tuple[str, str]] = {}
    monkeypatch.setattr(
        pns.credman, "write", lambda t, u, p: vault.__setitem__(t, (u, p))
    )
    monkeypatch.setattr(
        pns.credman, "read", lambda t: vault[t][1] if t in vault else None
    )
    monkeypatch.setattr(pns.credman, "delete", lambda t: vault.pop(t, None))
    sent: list[tuple] = []
    monkeypatch.setattr(
        deliver,
        "send_email",
        lambda mail, pw, to, title, lines, smtp=None: sent.append(
            (mail.host, pw, to, title)
        ),
    )
    s = Service(root=tmp_path / "app")
    s.posts, s.vault, s.sent = posts, vault, sent
    return s


def raw(**extra):
    return dict(event(), **extra) | {
        "to": {"people": ["ana@contoso.com"]},
        "open": {"view": "platform", "tab": "requests"},
    }


def broken(**extra):
    return (
        dict(
            raw(
                kind="shortcut.broken",
                id="s1",
                title="Customers_Silver",
                detail="Its target is gone",
                domain="sales",
                env="prod",
            ),
            open={"view": "shortcuts", "tab": "health"},
            dedupe="shortcut.broken:s1",
        )
        | extra
    )


def test_events_must_be_a_list(svc):
    with pytest.raises(ValueError, match="^events is a list$"):
        svc.platform_notify("not a list")


def test_with_no_route_nothing_is_sent_held_or_queued(svc):
    out = svc.platform_notify([raw()])
    assert (out["sent"], out["queued"], out["held"]) == (0, 0, 0)


def test_saving_returns_the_settings_with_each_routes_filters_and_the_quiet_hours(svc):
    shown = svc.platform_notify_save(
        [
            {
                "name": "default",
                "url": HOOK,
                "kinds": ["shortcut.broken"],
                "domains": ["sales"],
            }
        ],
        quiet="22:00-07:00",
        quiet_exempt=["warn"],
    )
    assert shown == svc.platform_notify_settings()
    route = shown["routes"][0]
    assert (
        route["kinds"] == ["shortcut.broken"]
        and route["domains"] == ["sales"]
        and route["url_set"] is True
    )
    assert shown["quiet"] == "22:00-07:00" and shown["quiet_exempt"] == ["warn"]


def test_settings_with_nothing_set_read_as_empty(svc):
    shown = svc.platform_notify_settings()
    assert (
        shown["routes"] == [] and shown["quiet"] == "" and shown["quiet_exempt"] == []
    )
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    route = svc.platform_notify_settings()["routes"][0]
    assert route["kinds"] == [] and route["domains"] == [] and "unreadable" not in route


def test_each_route_says_how_it_sends(svc):
    svc.platform_notify_save(
        [
            {"name": "default", "url": HOOK},
            {"name": "mail", "url": "mailto:ana@contoso.com"},
        ]
    )
    assert [r["kind"] for r in svc.platform_notify_settings()["routes"]] == [
        "teams",
        "email",
    ]


def test_a_route_this_account_cant_decrypt_is_named_unreadable(svc, monkeypatch):
    saved = {
        pns.SETTING: {"routes": [{"name": "default", "url": ""}]},
        sealed_settings.UNREADABLE: {pns.SETTING: {"default": "sealed"}},
    }
    monkeypatch.setattr(svc, "_settings", lambda: saved)
    assert svc.platform_notify_settings()["routes"][0]["unreadable"] is True


def test_a_test_message_to_a_route_that_isnt_there_is_refused(svc):
    with pytest.raises(ValueError, match="route sales has nowhere to send a test"):
        svc.platform_notify_save([{"name": "default", "url": HOOK}], test="sales")
    assert svc.posts == [] and svc.platform_notify_settings()["routes"] == []


def test_the_mail_server_calls_return_what_the_ui_shows_with_the_default_port(svc):
    shown = svc.mail_server_save("smtp.contoso.com", "kf@contoso.com")
    assert shown == svc.mail_server_settings()
    assert (
        shown["set"] is True
        and shown["port"] == 587
        and shown["password_saved"] is False
    )
    assert svc.mail_server_forget() == {"set": False}


def test_a_password_is_saved_only_for_a_server_with_a_user_name(svc):
    svc.mail_server_save("smtp.contoso.com", "kf@contoso.com")
    svc.vault[deliver.PASSWORD_TARGET] = ("", "left over")
    assert svc.mail_server_settings()["password_saved"] is False


def test_a_password_that_cant_be_read_isnt_shown_as_saved(svc, monkeypatch):
    svc.mail_server_save(
        "smtp.contoso.com",
        "kf@contoso.com",
        username="svc-kf",
        password="hunter2",  # pragma: allowlist secret
    )

    def locked(target):
        raise credman.CredentialError("Credential Manager is locked")

    monkeypatch.setattr(pns.credman, "read", locked)
    assert svc.mail_server_settings()["password_saved"] is False


def test_a_test_email_uses_the_saved_password_when_none_is_given(svc):
    svc.mail_server_save(
        "smtp.contoso.com",
        "kf@contoso.com",
        username="svc-kf",
        password="hunter2",  # pragma: allowlist secret
    )
    svc.mail_server_save(
        "smtp.contoso.com",
        "kf@contoso.com",
        username="svc-kf",
        test_to="ana@contoso.com",
    )
    assert svc.sent[-1][:2] == ("smtp.contoso.com", "hunter2")


def test_the_bell_shows_a_notices_domain_environment_and_detail(svc):
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    svc.platform_notify([broken()])
    row = svc.platform_notify_open()["open"][0]
    assert (row["domain"], row["env"], row["detail"]) == (
        "sales",
        "prod",
        "Its target is gone",
    )


def test_an_open_key_without_its_bell_details_still_shows(svc):
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    svc.platform_notify([broken()])
    with pns.transaction(svc.root / "platform_outbox.sqlite") as db:
        db.execute("DELETE FROM notice")
    row = svc.platform_notify_open()["open"][0]
    assert (row["title"], row["whom"], row["domain"], row["env"], row["detail"]) == (
        "",
        "",
        "",
        "",
        "",
    )
    assert row["open"] == {"view": "shortcuts", "tab": "health"}


def test_where_a_notice_opens_is_what_was_recorded_or_its_kinds_page():
    stored = '{"view": "metadata", "tab": "links", "target": "x"}'
    assert pns._target("product.deprecated", stored) == {
        "view": "metadata",
        "tab": "links",
        "target": "x",
    }
    assert pns._target("request.waiting", None) == {
        "view": "platform",
        "tab": "requests",
    }
    assert pns._target("shortcut.broken", None) == {
        "view": "shortcuts",
        "tab": "health",
    }
    assert pns._target("request.waiting", '{"view": "nowhere", "tab": "links"}') == {
        "view": "platform",
        "tab": "requests",
    }
    assert pns._target("request.waiting", '{"view": "metadata", "tab": "health"}') == {
        "view": "platform",
        "tab": "requests",
    }


def test_the_bell_gives_the_last_error(svc, monkeypatch):
    svc.platform_notify_save(
        [{"name": "a", "url": HOOK + "/a"}, {"name": "b", "url": HOOK + "/b"}]
    )

    def refuse(url, title, lines):
        raise ConnectionError(
            "first refusal" if url.endswith("/a") else "second refusal"
        )

    monkeypatch.setattr(pns, "post_teams", refuse)
    svc.platform_notify([raw()])
    shown = svc.platform_notify_open()
    assert shown["waiting"] == 2 and "second refusal" in shown["error"]


def test_a_closed_key_is_kept_365_days_then_deleted(svc):
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    svc.platform_notify([raw()])  # makes the file
    path = svc.root / "platform_outbox.sqlite"
    Ledger(path, clock=lambda: time.time() - 365 * DAY - 100).mark(
        "old", "request.decided", False
    )
    Ledger(path, clock=lambda: time.time() - 365 * DAY + 100).mark(
        "young", "request.decided", False
    )
    svc._pn_tidy()
    ledger = Ledger(path)
    assert ledger.get("old") is None and ledger.get("young") == (
        "request.decided",
        False,
    )
