"""The integration store's approval lifecycle and client bookkeeping, on a store in tmp_path with a clock the
test moves: one request per identical call, an answer given once, an approval used once and only for the
exact call it was given for, a recent "no" standing for the same call, a tool's last use recorded, an
unknown request, and disconnected tools listed only when asked for."""

from __future__ import annotations

import pytest

from fabric_desktop.integrations import store as store_mod
from fabric_desktop.integrations.store import IntegrationStore, args_hash


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path, clock) -> IntegrationStore:
    return IntegrationStore(tmp_path / "integrations" / "integrations.sqlite", clock=clock)


def _ask(store: IntegrationStore, client: dict, arguments: dict | None = None) -> dict:
    return store.request_approval(client, "fabric.run", "Run a notebook", "run", {"item": "nb"},
                                  arguments if arguments is not None else {"item": "nb"})


def test_at_keeps_the_file_under_the_data_root(tmp_path):
    st = IntegrationStore.at(tmp_path)
    assert st.path == tmp_path / "integrations" / "integrations.sqlite"
    assert st.path.exists()


def test_touch_records_when_a_tool_was_last_used(store, clock):
    client, _ = store.add_client("Editor")
    assert client["last_used_at"] is None
    clock.now += 42
    store.touch(client["id"])
    assert store.client(client["id"])["last_used_at"] == clock.now


def test_an_unknown_request_is_none(store):
    assert store.approval("ap_missing") is None


def test_the_same_call_while_waiting_gets_the_same_request(store, clock):
    client, _ = store.add_client("Editor")
    first = _ask(store, client)
    clock.now += 5
    again = _ask(store, client)
    other = _ask(store, client, {"item": "other"})
    assert again["id"] == first["id"] and again["state"] == "pending"
    assert other["id"] != first["id"]


def test_approving_sets_how_long_it_may_be_used(store, clock):
    client, _ = store.add_client("Editor")
    ap = _ask(store, client)
    clock.now += 10
    decided = store.decide(ap["id"], True, note="ok")
    assert decided["state"] == "approved" and decided["note"] == "ok"
    assert decided["decided_at"] == clock.now
    assert decided["expires_at"] == clock.now + store_mod.APPROVED_SECONDS


def test_a_request_is_answered_only_once(store):
    client, _ = store.add_client("Editor")
    ap = _ask(store, client)
    store.decide(ap["id"], False)
    with pytest.raises(ValueError, match="already denied"):
        store.decide(ap["id"], True)
    assert store.approval(ap["id"])["state"] == "denied"


def test_answering_an_unknown_request_is_a_lookup_error(store):
    with pytest.raises(LookupError):
        store.decide("ap_missing", True)


def test_a_recent_no_stands_for_the_same_call(store, clock):
    client, _ = store.add_client("Editor")
    ap = _ask(store, client)
    store.decide(ap["id"], False)
    clock.now += 60
    assert _ask(store, client)["id"] == ap["id"]
    clock.now += store_mod.APPROVED_SECONDS  # past the denial's expiry: a new request
    assert _ask(store, client)["id"] != ap["id"]


def test_an_approval_is_used_once_for_its_exact_call(store, clock):
    client, _ = store.add_client("Editor")
    other, _ = store.add_client("Other tool")
    ap = _ask(store, client)
    store.decide(ap["id"], True)
    digest = args_hash("fabric.run", {"item": "nb"})

    assert store.consume(ap["id"], client["id"], args_hash("fabric.run", {"item": "other"})) is False
    assert store.consume(ap["id"], other["id"], digest) is False
    assert store.approval(ap["id"])["state"] == "approved"

    clock.now += 3
    assert store.consume(ap["id"], client["id"], digest) is True
    assert store.approval(ap["id"])["state"] == "used"
    assert store.consume(ap["id"], client["id"], digest) is False  # once only

    uses = store.approval_uses(0)
    assert [(u["id"], u["used_at"]) for u in uses] == [(ap["id"], clock.now)]


def test_a_pending_request_cannot_be_used(store):
    client, _ = store.add_client("Editor")
    ap = _ask(store, client)
    assert store.consume(ap["id"], client["id"], ap["args_hash"]) is False
    assert store.approval_uses(0) == []


def test_an_approval_not_used_in_time_expires(store, clock):
    client, _ = store.add_client("Editor")
    ap = _ask(store, client)
    store.decide(ap["id"], True)
    clock.now += store_mod.APPROVED_SECONDS + 1
    assert store.consume(ap["id"], client["id"], ap["args_hash"]) is False
    assert store.approval(ap["id"])["state"] == "expired"


def test_disconnected_tools_are_listed_only_when_asked_for(store):
    kept, _ = store.add_client("Kept")
    gone, _ = store.add_client("Gone")
    store.revoke_client(gone["id"])
    assert [c["id"] for c in store.clients()] == [kept["id"]]
    assert {c["id"] for c in store.clients(include_revoked=True)} == {kept["id"], gone["id"]}
