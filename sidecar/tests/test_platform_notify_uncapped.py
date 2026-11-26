"""Platform notice edges found by running every mutant of workloads/platform_notify.py (mutation
batch 320, docs/quality/mutation-testing.md): the ledger makes its folder, and a message kept for a
route that failed earlier in the same notice waits and isn't counted as sent."""

from fabric_desktop.workloads.platform_notify import Ledger, PlatformRoute, send

from test_platform_notify import NOON, Posts, event, setup


def test_the_ledger_makes_the_folders_it_lives_in(tmp_path):
    ledger = Ledger(tmp_path / "a" / "b" / "ledger.sqlite")
    ledger.mark("k", "request.waiting", True)
    assert ledger.get("k") == ("request.waiting", True)


def test_a_message_for_a_route_that_just_failed_waits_and_isnt_counted_as_sent(
    tmp_path,
):
    # Two routes under one name (only a routing built by hand can have that): the first post fails,
    # so the route rests, and the second message waits in the outbox without being tried.
    routing, outbox, ledger = setup(
        tmp_path,
        PlatformRoute("sales", "https://a.example/hook"),
        PlatformRoute("sales", "https://b.example/hook"),
    )
    posts = Posts(fail={"https://a.example/hook"})
    out = send([event()], routing, outbox, ledger, posts, now=NOON)
    assert posts == []
    assert out["sent"] == 0 and out["queued"] == 2 and out["waiting"] == 2
    assert out["events"][0]["result"] == "queued"
