"""Issuing keys leaves one platform audit row per call that issued new ones: who, which key table and
how many. A run that only looked its keys up (every key already issued) adds none, so retries and
reruns don't fill the audit."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from fabric_desktop.central.client import LocalPlatform

ADMIN = "admin@example.com"


def _issued(path):
    with closing(sqlite3.connect(path)) as conn:
        return [(by, target, json.loads(detail)) for by, target, detail in conn.execute(
            """SELECT by_user, target, detail FROM "catalog__audit" WHERE action = 'issue keys' ORDER BY id""")]


def test_one_row_per_call_that_issued_keys(tmp_path):
    platform = LocalPlatform(tmp_path / "platform.sqlite", user=lambda: ADMIN)
    platform.call("ensure_key_table", name="customer")
    first = platform.call("issue_keys", name="customer", business_keys=["c1", "c2", "c3"])
    assert first["inserted"] == 3
    assert platform.call("issue_keys", name="customer", business_keys=["c1", "c2"])["inserted"] == 0  # a rerun
    assert platform.call("issue_keys", name="customer", business_keys=[])["inserted"] == 0
    platform.call("issue_keys", name="customer", business_keys=["c2", "c4"])
    assert _issued(platform.path) == [(ADMIN, "keys:customer", {"count": 3}), (ADMIN, "keys:customer", {"count": 1})]
    assert "c1" not in json.dumps(_issued(platform.path))  # business keys stay out of the audit
