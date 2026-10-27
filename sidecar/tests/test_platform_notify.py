import datetime as dt

import pytest

from fabric_desktop import app_settings, audit, backup, server
from fabric_desktop import platform_notify_service as pns
from fabric_desktop.service import Service
from fabric_desktop.workloads.notify import Outbox, Quiet
from fabric_desktop.workloads.platform_notify import (Ledger, PlatformRoute, PlatformRouting, message, send,
                                                       validate)

HOOK = "https://contoso.webhook.office.com/hook"
NOON = dt.datetime(2026, 10, 18, 12, 0).astimezone()


def event(kind="request.waiting", id="r1", **extra):
    base = {"kind": kind, "id": id, "at": "2026-10-18T12:00:00+00:00", "title": "Access to LH_Sales",
            "detail": "Because I need the Q3 numbers", "domain": "sales", "env": "prod", "severity": "info",
            "to": {"people": ["ana@contoso.com"], "roles": ["domain_owner:sales"]},
            "open": {"view": "platform", "tab": "requests", "target": id}}
    if kind.startswith("shortcut."):
        base["open"] = {"view": "shortcuts", "tab": "health", "target": id}
        base["detail"] = "Its target table was deleted"
    return validate(base | extra)


class Posts(list):
    def __init__(self, fail=()):
        super().__init__()
        self.fail = set(fail)

    def __call__(self, url, title, lines):
        if url in self.fail:
            raise ConnectionError("no network")
        self.append((url, title, lines))


def setup(tmp_path, *routes, quiet=None):
    routing = PlatformRouting(list(routes) or [PlatformRoute("default", HOOK)], quiet)
    return routing, Outbox(tmp_path / "o.sqlite"), Ledger(tmp_path / "o.sqlite")


def test_events_are_checked_against_governances_shape():
    e = event()
    assert e["dedupe"] == "request.waiting:r1" and e["to"]["people"] == ["ana@contoso.com"]
    bad = [({"kind": "request.lost"}, "kind is one of"),
           ({"to": {"people": ["Ana Lopez"]}}, "UPNs"),
           ({"to": {"roles": ["admins"]}}, "to.roles"),
           ({"to": {}}, "at least one"),
           ({"open": {"view": "platform", "tab": "health"}}, "open is"),
           ({"at": "yesterday"}, "ISO 8601"),
           ({"severity": "critical"}, "severity"),
           ({"title": ""}, "title is required")]
    for change, words in bad:
        with pytest.raises(ValueError, match=words):
            event(**change)


def test_a_requests_reason_never_reaches_teams_but_a_shortcuts_break_does():
    title, lines = message(event())
    assert title == "Waiting on you: Access to LH_Sales"
    assert not any("Q3" in line for line in lines)
    assert lines[0] == "For ana@contoso.com, owners of domain sales."
    assert lines[-1] == "Open it in KratosForge > Data mesh > Requests."
    title, lines = message(event("shortcut.broken", "s1"))
    assert title.startswith("Shortcut broken") and "Its target table was deleted" in lines


def test_routes_by_kind_and_domain_with_the_default_for_the_rest(tmp_path):
    routing, outbox, ledger = setup(tmp_path, PlatformRoute("default", "https://d"),
                                    PlatformRoute("sales-requests", "https://s", kinds=("request.waiting",), domains=("Sales",)),
                                    PlatformRoute("shortcuts", "https://sc", kinds=("shortcut.broken", "shortcut.fixed")))
    posts = Posts()
    out = send([event(), event(id="r2", domain="hr"), event("shortcut.broken", "s1")], routing, outbox, ledger, posts, now=NOON)
    assert [p[0] for p in posts] == ["https://s", "https://d", "https://sc"]
    assert [r["routes"] for r in out["events"]] == [["sales-requests"], ["default"], ["shortcuts"]]


def test_each_change_is_posted_once_and_a_fix_closes_its_break(tmp_path):
    routing, outbox, ledger = setup(tmp_path)
    posts = Posts()
    send([event("shortcut.broken", "s1")], routing, outbox, ledger, posts, now=NOON)
    again = send([event("shortcut.broken", "s1")], routing, outbox, ledger, posts, now=NOON)
    assert len(posts) == 1 and again["events"][0]["reason"] == "already posted and still open"
    assert [k["key"] for k in ledger.open_keys()] == ["shortcut.broken:s1"]
    send([event("shortcut.fixed", "s1")], routing, outbox, ledger, posts, now=NOON)
    assert posts[-1][1].startswith("Shortcut fixed") and ledger.open_keys() == []
    dup = send([event("shortcut.fixed", "s1")], routing, outbox, ledger, posts, now=NOON)
    assert dup["events"][0]["reason"] == "its break wasn't posted" and len(posts) == 2
    send([event("shortcut.broken", "s1")], routing, outbox, ledger, posts, now=NOON)
    assert len(posts) == 3  # broken again is news again
    never = send([event("shortcut.fixed", "s9")], routing, outbox, ledger, posts, now=NOON)
    assert never["events"][0]["result"] == "skipped"


def test_a_decision_closes_the_wait_and_a_new_wait_makes_the_next_decision_news(tmp_path):
    routing, outbox, ledger = setup(tmp_path)
    posts = Posts()
    send([event(), event("request.decided", to={"roles": ["requester"]})], routing, outbox, ledger, posts, now=NOON)
    assert [p[1].split(":")[0] for p in posts] == ["Waiting on you", "Decided"]
    assert posts[1][2][0] == "For the requester." and ledger.open_keys() == []
    send([event("request.decided")], routing, outbox, ledger, posts, now=NOON)
    assert len(posts) == 2
    send([event(), event("request.withdrawn")], routing, outbox, ledger, posts, now=NOON)
    assert len(posts) == 4


def test_unreachable_teams_queues_and_quiet_hours_hold_until_they_end(tmp_path):
    routing, outbox, ledger = setup(tmp_path)
    out = send([event()], routing, outbox, ledger, Posts(fail=[HOOK]), now=NOON)
    assert out["queued"] == 1 and out["events"][0]["result"] == "queued" and "ConnectionError" in out["error"]
    posts = Posts()
    send([], routing, outbox, ledger, posts, now=NOON)
    assert posts[0][1] == "Delayed: Waiting on you: Access to LH_Sales" and outbox.pending() == []
    routing.quiet = Quiet.parse("11:00-13:00", ())
    held = send([event(id="r2")], routing, outbox, ledger, posts, now=NOON)
    assert held["held"] == 1 and len(posts) == 1 and outbox.pending()[0]["not_before"] > NOON.timestamp()
    assert send([event(id="r2")], routing, outbox, ledger, posts, now=NOON)["events"][0]["result"] == "skipped"


def test_bad_routes_are_refused():
    for raw, words in [({"routes": [{"name": "a", "url": "http://x"}]}, "https://"),
                       ({"routes": [{"name": "a", "kinds": ["incident"]}]}, "isn't a kind"),
                       ({"routes": [{"name": "a"}, {"name": "a"}]}, "unique"),
                       ({"routes": [], "quiet": "late"}, "22:00-07:00"),
                       ({"routes": [{"url": HOOK}]}, "name")]:
        with pytest.raises(ValueError, match=words):
            PlatformRouting.parse(raw)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    posts = Posts()
    monkeypatch.setattr(pns, "post_teams", posts)
    s = Service(root=tmp_path / "app")
    s.posts = posts
    return s


def raw(**extra):
    return dict(event(), **extra) | {"to": {"people": ["ana@contoso.com"]},
                                       "open": {"view": "platform", "tab": "requests"}}


def test_the_service_keeps_webhooks_out_of_the_ui_and_backups(svc):
    assert svc.platform_notify([raw()])["skipped"] == "no platform Teams route set" and svc.posts == []
    svc.platform_notify_save([{"name": "default", "url": HOOK, "kinds": []}], quiet="22:00-07:00", test="default")
    assert svc.posts[0][1] == "KratosForge platform notices are on"
    shown = svc.platform_notify_settings()
    assert HOOK not in str(shown) and shown["routes"][0] == {"name": "default", "kinds": [], "domains": [],
                                                             "url_set": True, "host": "contoso.webhook.office.com"}
    svc.platform_notify_save([{"name": "default", "kinds": ["request.waiting"]}])  # no url: keeps the address
    assert svc._pn_raw()["routes"][0]["url"] == HOOK
    assert "platform_notify" in backup.SECRET_KEYS
    assert audit.target({"routes": [{"url": HOOK}]}) == {"routes": "(given)"}


def test_the_service_checks_every_event_before_posting_any(svc):
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    with pytest.raises(ValueError, match="event 2: kind"):
        svc.platform_notify([raw(), raw(kind="nope")])
    assert svc.posts == []
    out = svc.platform_notify([raw()])
    assert out["sent"] == 1 and svc.platform_notify_open()["open"][0]["key"] == "request.waiting:r1"


def test_methods_are_served_and_reads_are_not_audited():
    assert pns.METHODS <= server.METHODS
    assert {"platform_notify_settings", "platform_notify_open"} <= audit.READ_ONLY
    assert audit.audited("platform_notify") and audit.audited("platform_notify_save")


LEAKY = "https://contoso.webhook.office.com/workflows/abc/invoke?api-version=1&sv=1.0&sig=SECRET123"


def leaky_post(url, title, lines):
    raise ConnectionError(f"Max retries exceeded with url: {url.split('.com', 1)[1]}")


def test_a_webhooks_signature_never_reaches_a_result_or_the_outbox(tmp_path):
    routing, outbox, ledger = setup(tmp_path, PlatformRoute("default", LEAKY))
    out = send([event()], routing, outbox, ledger, leaky_post, now=NOON)
    assert out["queued"] == 1 and "sig=[removed]" in out["error"]
    again = send([], routing, outbox, ledger, leaky_post, now=NOON)  # the retry fails the same way
    assert "SECRET123" not in str(out) + str(again) + (tmp_path / "o.sqlite").read_bytes().decode("latin-1")


def test_incident_notices_keep_the_signature_out_too(tmp_path):
    from fabric_desktop.workloads import notify
    change = {"change": "opened", "incident": {"id": "i1", "env": "prod", "title": "Sales load failed",
                                               "kind": "run_failed", "criticality": "high"}}
    box = Outbox(tmp_path / "alerts.sqlite")
    out = notify.send([change], notify.single(LEAKY), box, "test", leaky_post, now=NOON)
    notify.send([], notify.single(LEAKY), box, "test", leaky_post, now=NOON)
    assert out["queued"] == 1 and "SECRET123" not in str(out)
    assert "SECRET123" not in (tmp_path / "alerts.sqlite").read_bytes().decode("latin-1")


def test_the_bell_names_each_open_notice_without_a_requests_detail(svc):
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    svc.platform_notify([raw(), dict(raw(kind="shortcut.broken", id="s1", title="Customers_Silver",
                                         detail="Its target folder no longer exists"),
                                     open={"view": "shortcuts", "tab": "health"}, dedupe="shortcut.broken:s1")])
    shown = svc.platform_notify_open()
    assert [(n["kind"], n["title"]) for n in shown["open"]] == [("shortcut.broken", "Customers_Silver"),
                                                                ("request.waiting", "Access to LH_Sales")]
    request = shown["open"][1]
    assert request["whom"] == "ana@contoso.com" and request["detail"] == "" and request["open"]["tab"] == "requests"
    assert "Q3" not in str(shown)  # a request's reason stays out, as it does in Teams
    assert shown["open"][0]["detail"] == "Its target folder no longer exists"
    assert shown["waiting"] == 0 and shown["error"] == "" and shown["routes"] is True
    svc.platform_notify([dict(raw(kind="request.decided"), dedupe="request.decided:r1")])
    assert [n["kind"] for n in svc.platform_notify_open()["open"]] == ["shortcut.broken"]


def test_notices_teams_didnt_take_are_counted_with_why(svc, monkeypatch):
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    monkeypatch.setattr(pns, "post_teams", leaky_post)
    svc.platform_notify([raw()])
    shown = svc.platform_notify_open()
    assert shown["waiting"] == 1 and shown["held"] == 0 and "ConnectionError" in shown["error"]
    assert "SECRET" not in str(shown) and HOOK not in str(shown)
    assert shown["open"][0]["title"] == "Access to LH_Sales"  # queued counts as posted


def test_a_token_or_sas_address_in_a_notice_never_reaches_teams_the_bell_or_the_record(svc):
    sas = "https://acct.blob.core.windows.net/c/x?sv=2024&sig=SASSECRET"
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    svc.platform_notify([dict(raw(kind="shortcut.broken", id="s1", title="T Bearer eyJhbGciOiJIUzI1NiJ9.e30.TOKENSECRET",
                                  detail=f"target {sas}"), open={"view": "shortcuts", "tab": "health"},
                              dedupe="shortcut.broken:s1")])
    seen = str(svc.platform_notify_open()) + str(svc.posts) + (svc.root / "platform_outbox.sqlite").read_bytes().decode("latin-1")
    assert "TOKENSECRET" not in seen and "SASSECRET" not in seen and "[removed]" in seen


def test_one_damaged_notice_row_doesnt_hide_the_rest(svc):
    import sqlite3
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    svc.platform_notify([raw(), dict(raw(kind="shortcut.broken", id="s1", title="Customers_Silver"),
                                     open={"view": "shortcuts", "tab": "health"}, dedupe="shortcut.broken:s1")])
    db = sqlite3.connect(svc.root / "platform_outbox.sqlite")
    db.execute("UPDATE notice SET open = '{bad'")
    db.commit()
    db.close()
    shown = {n["kind"]: n["open"] for n in svc.platform_notify_open()["open"]}
    assert shown == {"request.waiting": {"view": "platform", "tab": "requests"},
                     "shortcut.broken": {"view": "shortcuts", "tab": "health"}}
