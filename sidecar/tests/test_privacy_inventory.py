"""The personal-data inventory (docs/enterprise-privacy.md) names the databases and logs the engine keeps.
Each database or log file name written as a quoted literal in the engine's code (.sqlite, .sqlite3, .db,
.jsonl) must be in the inventory, or be listed here with why it holds nothing personal of its own, so a new
database or log can't ship unlisted.

What this check doesn't see: JSON stores (such as editor/pairings.json) and names built at run time (with
with_suffix or from parts). Those are kept in the inventory by review, not by this test."""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
INVENTORY = Path(__file__).resolve().parents[2] / "docs" / "enterprise-privacy.md"
STORE = re.compile(r"""["']([A-Za-z0-9_.{}-]+\.(?:sqlite3?|jsonl|db))["']""")
# Store-like names in the code that aren't stores on a person's PC, each with why.
NOT_A_STORE = {
    "copy.sqlite": "a temporary copy inside a backup being made, deleted when it's done (backup.py)",
    "{name}.sqlite3": "a live test's throwaway queue in a temporary folder (livetest.py)",
    "thumbs.db": "a Windows file name that local data discovery skips; never written",
    "platform-{CATALOG_ENV}.sqlite": "the same file as platform-<env>.sqlite, named by its environment",
    "platform-{env}.sqlite": "listed in the inventory as platform-<env>.sqlite",
}
# Stores that aren't files named in code, each with the inventory text that covers it.
OTHER_STORES = {
    "the incident list": "incidents/incidents.json",
    "the audit trail's anchor in the Windows event log": "Windows Application event log",
    "the kept ids beside an outbox": ".sent-not-removed.json",
}


def _names() -> set[str]:
    return {m for path in PACKAGE.rglob("*.py") for m in STORE.findall(path.read_text(encoding="utf-8"))}


def test_every_store_the_engine_writes_is_in_the_personal_data_inventory():
    inventory = INVENTORY.read_text(encoding="utf-8")
    missing = sorted(n for n in _names() if n not in NOT_A_STORE and n not in inventory)
    assert missing == [], f"add each to docs/enterprise-privacy.md, or say in NOT_A_STORE why it isn't one: {missing}"


def test_stores_without_a_file_name_in_code_are_in_the_inventory_too():
    inventory = INVENTORY.read_text(encoding="utf-8")
    assert [what for what, text in OTHER_STORES.items() if text not in inventory] == []


def test_the_not_a_store_list_has_no_stale_names():
    assert set(NOT_A_STORE) <= _names(), sorted(set(NOT_A_STORE) - _names())
