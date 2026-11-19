"""Mutation batch 118: excellence/evidence.py (docs/quality/mutation-testing.md). The survivors were the
canonical text of non-ASCII values, a subject with an empty id, an item subject missing its workspace or
type, the length of a record's id and the set of ids a bundle holds."""

from __future__ import annotations

import re

import pytest

from fabric_desktop.excellence import evidence as ev

AT = "2026-11-18T02:00:00+00:00"


def test_canonical_text_keeps_non_ascii_characters_as_they_are():
    assert ev.canonical({"b": "Café", "a": 1}) == '{"a":1,"b":"Café"}'


def test_a_subject_needs_a_non_empty_text_id():
    with pytest.raises(ValueError, match="needs an id"):
        ev.subject("workspace", "")
    with pytest.raises(ValueError, match="needs an id"):
        ev.subject("workspace", 7)  # type: ignore[arg-type]
    assert ev.subject("workspace", "w1") == {"type": "workspace", "id": "w1", "name": "w1"}


def test_a_record_id_is_ev_and_sixteen_hex_digits():
    rec = ev.record("workspace.info", ev.subject("workspace", "w1"), source="GET x",
                    confidence="measured", collected_at=AT, payload={"a": 1})
    assert re.fullmatch(r"ev-[0-9a-f]{16}", rec["id"])


def test_a_bundle_lists_the_ids_it_kept():
    a = ev.record("k.one", ev.subject("workspace", "w1"), source="s", confidence="measured", collected_at=AT, payload=1)
    b = ev.record("k.two", ev.subject("workspace", "w1"), source="s", confidence="measured", collected_at=AT, payload=2)
    bundle = ev.Bundle([a, b, a, {"id": "bad"}])
    assert bundle.ids() == {a["id"], b["id"]}
    assert ev.Bundle([]).ids() == set()


def test_an_item_subject_needs_both_its_workspace_and_its_type():
    with pytest.raises(ValueError, match="workspace_id and item_type"):
        ev.subject("item", "i1", workspace_id="w1")
    with pytest.raises(ValueError, match="workspace_id and item_type"):
        ev.subject("item", "i1", item_type="Notebook")
    assert ev.subject("item", "i1", "Load", workspace_id="w1", item_type="Notebook") == {
        "type": "item", "id": "i1", "name": "Load", "workspace_id": "w1", "item_type": "Notebook"}
