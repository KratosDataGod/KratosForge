"""A record of the organization's policy file changing (#1670).

The policy file (``%ProgramData%\\FabricDesktop\\policy.json``) is advisory against someone who
controls the PC: an administrator can always change it. What this adds is visibility. Each time the
engine starts, and each time Settings is opened, it compares the file's SHA-256 and which copy applies
(the file, the last copy that could be read, or nothing) with what it saw last, kept in
``<data>/policy-seen.json``:

- the first look writes ``policy_file_seen`` to the audit trail, a baseline;
- any change writes ``policy_file_changed``, with the hash before and after, which copy applies, and
  ``managed``: false when the account running the app could change or replace the file at the time,
  which is not how IT pushes it (as SYSTEM, so only administrators can change it);
- an unchanged file writes nothing.

Settings > Help and about shows the hash, when it last changed and whether that change was managed,
so IT can compare it with the hash of the file they shipped (Get-FileHash). Nothing is blocked. A
change made by a local administrator can't be told apart from IT's own push; the hash comparison is
what finds it. Both the audit trail and policy-seen.json are in the signed-in account's own
folder, so that account can edit them and hide a change: ship the audit files off the PC when that
matters. See enterprise-install.md, "Changes to the policy file".
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import app_settings, jsonfile
from .audit import AuditLog

log = logging.getLogger(__name__)

SEEN = "policy-seen.json"
_lock = threading.Lock()  # engine start and the first Settings read can look at once: one records


def _seen(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(jsonfile.read_text(path))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:  # unreadable: treated as a first look, which records a baseline
        log.warning("Couldn't read %s (%s); recording the policy file again", path, exc)
        return None
    return data if isinstance(data, dict) else None


def check(root: Path, audit: AuditLog) -> dict[str, Any]:
    """Compares the policy file with what was seen last, records a change in the audit trail and returns
    the current record: sha256, state, managed, changed_at, previous (the hash before the change)."""
    with _lock:
        return _check(root, audit)


def _check(root: Path, audit: AuditLog) -> dict[str, Any]:
    now = app_settings.policy_fingerprint()
    path = root / SEEN
    seen = _seen(path)
    registry = now.get("registry_sha256")
    if (seen is not None and seen.get("sha256") == now["sha256"] and seen.get("state") == now["state"]
            and seen.get("registry_sha256") == registry):
        return seen
    record = {"sha256": now["sha256"], "state": now["state"], "managed": not now["user_can_change"],
              "changed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "previous": seen.get("sha256") if seen else None, "first": seen is None,
              "registry_sha256": registry, "registry_previous": seen.get("registry_sha256") if seen else None}
    audit.record("policy_file_seen" if seen is None else "policy_file_changed", "ok", 0.0,
                 {"sha256": now["sha256"], "previous": record["previous"], "state": now["state"],
                  "managed": record["managed"], "file": str(app_settings.policy_path()),
                  "registry_sha256": registry, "registry_previous": record["registry_previous"]})
    try:
        jsonfile.write_text(path, json.dumps(record, indent=2))
    except OSError as exc:  # recorded in the audit trail already; the next look records it again
        log.warning("Couldn't save %s: %s", path, exc)
    return record
