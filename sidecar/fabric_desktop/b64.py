"""Base64 from the app, read strictly: the same on every Python 3.12 patch release.

``base64.b64decode(s, validate=True)`` refuses characters outside the alphabet, but on some
Python 3.12 patch releases (3.12.3, for one) it also accepts padding after the data ends
(``"YWJj="`` reads as ``b"abc"``). A file that arrives with the wrong length was cut or joined somewhere, so the
length and the padding are checked here first, without relying on the stdlib's strictness.
"""

from __future__ import annotations

import base64
import binascii


def decode_exact(text: str) -> bytes:
    """The bytes ``text`` encodes, or ValueError unless it is whole, correctly padded base64."""
    if len(text) % 4:
        raise ValueError("base64 length isn't a multiple of 4")
    pad = text.find("=")
    if pad != -1 and (pad < len(text) - 2 or text[pad:] not in ("=", "==")):
        raise ValueError("base64 padding isn't only at the end")
    try:
        return base64.b64decode(text, validate=True)
    except binascii.Error as exc:  # a ValueError subclass; say so in one place
        raise ValueError(str(exc)) from None
