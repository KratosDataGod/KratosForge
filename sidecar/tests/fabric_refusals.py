"""What Fabric refused on a live run, for the fakes that stand in for it.

A fake that accepts what Fabric refuses lets a broken request pass CI: UDF_Platform's definition
with no functions passed every fake and failed on the first live run (#1165). Each rule here is a
refusal seen on a live run, with the run it came from; docs/live-testing.md lists them. A fake
calls ``check_item`` before it accepts an item, and ``check_token`` before it hands out a token.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from fabric_desktop.fabric_api import FabricError

# Live run 2, Run B (2026-10-30): creating a User Data Function whose definition lists no functions.
UDF_NO_FUNCTIONS = "NullOrEmptyArgument: Invalid Argument: Functions. Functions is null or empty"

# Live run 2 (2026-10-30, #1079): Windows sign-in with Microsoft's Fabric CLI client gets no token
# for Azure SQL or Kusto. WAM says only Status_IncorrectConfiguration; Entra's reason is AADSTS65002.
WAM_REFUSED = ("Windows sign-in (WAM) failed: Status_IncorrectConfiguration (3399614466). AADSTS65002: Consent "
               "between first party application and first party resource must be configured via preauthorization")
REFUSED_RESOURCES = ("https://database.windows.net", "https://kusto.kusto.windows.net")


def _text(parts: Any, path: str) -> str | None:
    """One definition file's text, from ``{path: text}`` or Fabric's ``{"parts": [...]}`` (base64)."""
    if isinstance(parts, dict) and path in parts and isinstance(parts[path], str):
        return parts[path]
    for part in (parts or {}).get("parts", []) if isinstance(parts, dict) else []:
        if part.get("path") == path:
            return base64.b64decode(part.get("payload") or "").decode()
    return None


def check_item(item_type: str, parts: Any) -> None:
    """Refuse an item create or definition update the way Fabric did on a live run."""
    if item_type == "UserDataFunction" and parts is not None:
        text = _text(parts, "definition.json")
        functions = (json.loads(text) if text else {}).get("functions")
        if not functions:
            raise FabricError(f"POST failed with 400: {UDF_NO_FUNCTIONS}", "NullOrEmptyArgument", 400)


def check_token(resource: str, microsofts_client: bool = True) -> None:
    """Refuse a token the way Windows sign-in did for Microsoft's client on a live run."""
    if microsofts_client and any(resource.startswith(r) for r in REFUSED_RESOURCES):
        raise RuntimeError(WAM_REFUSED)
