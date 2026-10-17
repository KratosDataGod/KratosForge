"""Reading the status records agents publish (``agents/<name>.json``, written by agent_status.py)."""

from __future__ import annotations

import json
from typing import Any


def read_status(backend: Any) -> list[dict[str, Any]]:
    """Every agent status record in one environment's control area. A record that isn't valid
    JSON is skipped; a listing that fails raises, so the caller can tell "none" from "unreadable"."""
    out = []
    for name in backend.list("agents"):
        stored = backend.read(f"agents/{name}")
        if stored is None:
            continue
        try:
            body = json.loads(stored.data or b"{}")
        except ValueError:
            continue
        if isinstance(body, dict) and body.get("kind") == "agent_status":
            out.append(body)
    return out
