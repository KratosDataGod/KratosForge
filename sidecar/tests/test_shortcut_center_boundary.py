"""Boundary kill test for shortcut_center (mutation testing, batch 167)."""
from __future__ import annotations

from fabric_desktop.shortcut_center import health_from_probe


def test_a_300_answer_is_not_healthy():
    s = {"target_type": "AdlsGen2", "target": "https://acct.dfs.core.windows.net/c/p"}
    assert health_from_probe(s, 200, 299)["health"] == "healthy"
    assert health_from_probe(s, 200, 300)["health"] != "healthy"
