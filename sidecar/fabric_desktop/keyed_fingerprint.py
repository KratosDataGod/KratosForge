"""Keyed fingerprints: a short HMAC-SHA256 of text under a random key kept on this PC.

A plain hash of a member's sign-in name, an object id or a short filter can be confirmed by anyone who hashes a
guess (a list of the org's UPNs, a directory listing, ``[Region] = "West"``). Under a key that never leaves this
PC, two fingerprints still match when their text does, within a run and across runs here, but nothing can be
confirmed offline. The key is 32 random bytes in this PC's data folder, made on first use and never returned.
If the folder can't be read or written, a key for this run alone is used instead, so fingerprints still match
within the run."""

from __future__ import annotations

import functools
import hashlib
import hmac
import logging
import os
import secrets
from pathlib import Path

_log = logging.getLogger(__name__)
KEY_BYTES = 32


def key_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "FabricDesktop" / "keys" / "fingerprint.key"


def _read(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    return data if len(data) == KEY_BYTES else None


def _create(path: Path) -> bytes:
    """Write a new key only if none exists (O_EXCL), so two processes starting together keep one key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        found = _read(path)
        if found is None:
            raise ValueError("the fingerprint key file is not a key") from None
        return found
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


@functools.lru_cache(maxsize=4)
def _key_for(path: Path) -> bytes:
    try:
        return _read(path) or _create(path)
    except (OSError, ValueError) as exc:  # unreadable folder or a damaged file: a key for this run only
        _log.warning("Fingerprint key unavailable (%s); using a key for this run only.", type(exc).__name__)
        return secrets.token_bytes(KEY_BYTES)


def digest(text: str) -> str:
    """12 hex characters of HMAC-SHA256(key, text)."""
    mac = hmac.new(_key_for(key_path()), text.encode("utf-8", "surrogatepass"), hashlib.sha256)
    return mac.hexdigest()[:12]


def fingerprint(text: str) -> str:
    """Text as its length and a keyed fingerprint: enough to see that two values differ or match, without the
    value, and without a way to confirm a guessed value away from this PC."""
    return f"{len(text)} characters, fingerprint {digest(text)}"
