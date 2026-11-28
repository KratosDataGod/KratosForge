"""Mutation batch 519, a re-score of orchestration/mashup.py from batches 56 and 249 (docs/quality/mutation-testing.md):
a definition part Fabric didn't send as whole base64 text is named by its first 100 characters, or as "A part"
when its name has no printable character."""

from __future__ import annotations

from fabric_desktop.orchestration import mashup

UNREAD = (" couldn't be read because Fabric didn't send it as whole base64 UTF-8 text, so the dataflow can't be "
          "shown in full. Open it in Fabric to check it still loads.")


def _problems(path):
    return mashup.describe({"definition": {"parts": [{"path": path, "payload": "!!"}]}})["problems"]


def test_an_unread_part_is_named_by_its_first_100_characters():
    assert _problems("q" * 150 + ".pq")[0] == "q" * 99 + "…" + UNREAD


def test_an_unread_part_whose_name_has_nothing_printable_is_a_part():
    assert _problems("\t")[0] == "A part" + UNREAD
