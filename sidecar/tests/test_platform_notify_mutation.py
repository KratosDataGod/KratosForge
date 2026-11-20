"""Platform notice edges the main tests don't pin down (mutation batch 144, docs/quality/mutation-testing.md):
the scope line, a decision that is news again after a new wait, and the key a fix closes when the
event names its own key."""

import dataclasses

import pytest

from fabric_desktop.workloads.platform_notify import Ledger, PlatformRoute, _opened_key, decide, message, send

from test_platform_notify import HOOK, NOON, Posts, event, setup


def test_the_scope_line_is_there_only_when_the_event_has_a_domain_or_environment():
    _, lines = message(event(domain="sales", env="prod"))
    assert lines[1] == "Domain sales · Prod"
    _, lines = message(event(domain="", env=""))
    assert lines == ["For ana@contoso.com, owners of domain sales.", "Open it in KratosForge: Data mesh > Requests."]


def test_a_decision_is_news_again_after_a_new_wait(tmp_path):
    routing, outbox, ledger = setup(tmp_path)
    posts = Posts()
    send([event(), event("request.decided")], routing, outbox, ledger, posts, now=NOON)
    send([event(), event("request.decided")], routing, outbox, ledger, posts, now=NOON)
    assert [p[1].split(":")[0] for p in posts] == ["Waiting on you", "Decided", "Waiting on you", "Decided"]


def test_a_fix_closes_the_break_its_own_key_names_or_the_one_with_its_id():
    assert _opened_key({"kind": "shortcut.fixed", "dedupe": "shortcut.fixed:s1:v2", "id": "s1"}) == "shortcut.broken:s1:v2"
    assert _opened_key({"kind": "shortcut.fixed", "dedupe": "custom-key", "id": "s1"}) == "shortcut.broken:s1"


def test_a_new_wait_with_its_own_key_makes_the_decision_with_the_matching_key_news(tmp_path):
    routing, outbox, ledger = setup(tmp_path)
    posts = Posts()
    wait, decided = event(dedupe="request.waiting:r1:2"), event("request.decided", dedupe="request.decided:r1:2")
    send([wait, decided], routing, outbox, ledger, posts, now=NOON)
    send([wait, decided], routing, outbox, ledger, posts, now=NOON)
    assert len(posts) == 4


def test_the_outbox_and_the_ledger_must_be_one_file(tmp_path):
    routing, outbox, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="one file"):
        send([], routing, outbox, Ledger(tmp_path / "other.sqlite"), Posts(), now=NOON)


def test_titles_and_ids_are_kept_to_200_characters_and_details_to_500():
    e = event(id="i" * 250, title="t" * 250, detail="d" * 600)
    assert len(e["id"]) == 200 and len(e["title"]) == 200 and len(e["detail"]) == 500


def test_a_route_cant_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        PlatformRoute("default", HOOK).url = "https://elsewhere"  # type: ignore[misc]


def test_the_ledger_gives_a_keys_kind_and_whether_it_is_open_and_forgets_it(tmp_path):
    _, _, ledger = setup(tmp_path)
    ledger.mark("request.waiting:r1", "request.waiting", True)
    assert ledger.get("request.waiting:r1") == ("request.waiting", True)
    ledger.forget("request.waiting:r1")
    assert ledger.get("request.waiting:r1") is None


def test_a_fix_for_a_break_that_is_closed_already_isnt_posted(tmp_path):
    _, _, ledger = setup(tmp_path)
    ledger.mark("shortcut.broken:s1", "shortcut.broken", False)
    assert decide(event("shortcut.fixed", id="s1"), ledger) == "its break wasn't posted"


def test_a_notice_for_a_resting_route_is_queued(tmp_path):
    routing, outbox, ledger = setup(tmp_path)
    outbox.route_failed("default", HOOK, "down")
    posts = Posts()
    out = send([event()], routing, outbox, ledger, posts, now=NOON)
    assert out["queued"] == 1 and out["events"][0]["result"] == "queued" and posts == []
