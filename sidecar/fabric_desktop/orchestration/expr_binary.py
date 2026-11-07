"""Binary values in local pipeline runs: binary(), base64ToBinary(), dataUriToBinary(), decodeDataUri()
and uriComponentToBinary().

A binary value is carried the way Azure Data Factory and Logic Apps write one in a run's JSON:

    {"$content-type": "application/octet-stream", "$content": "<the bytes, base64-encoded>"}

so it fits in a variable or an activity's output, and base64(), string() and xml() read the bytes
back. Microsoft's expression reference prints each of these functions' results as a string of bits
instead, and for base64ToBinary(), dataUriToBinary() and decodeDataUri() the bits it prints are those of
the argument's own text, not of the bytes it decodes to. Which form Fabric writes hasn't been checked
(docs/orchestration/coverage.md marks them "differs"); a local run decodes, as the functions' names say.
"""

from __future__ import annotations

import base64
import binascii
import urllib.parse
from typing import Any

OCTETS = "application/octet-stream"


class BinaryError(Exception):
    """A value a binary function can't read; the message says why."""


def is_binary(v: Any) -> bool:
    return isinstance(v, dict) and set(v) == {"$content-type", "$content"} and isinstance(v["$content"], str)


def binary_value(data: bytes, content_type: str = OCTETS) -> dict[str, str]:
    return {"$content-type": content_type or OCTETS, "$content": base64.b64encode(data).decode("ascii")}


def binary_bytes(v: dict[str, Any]) -> bytes:
    try:
        return base64.b64decode(v["$content"], validate=True)
    except (binascii.Error, ValueError):
        raise BinaryError("a binary value's $content isn't base64") from None


def binary_text(v: dict[str, Any]) -> str:
    """A binary value's bytes read as UTF-8 text (string() of a binary value)."""
    try:
        return binary_bytes(v).decode("utf-8")
    except UnicodeDecodeError:
        raise BinaryError("the binary value isn't UTF-8 text") from None


def to_binary(text: str) -> dict[str, str]:
    """binary(): the UTF-8 bytes of the text."""
    return binary_value(text.encode("utf-8"))


def base64_to_binary(text: str) -> dict[str, str]:
    try:
        return binary_value(base64.b64decode(text, validate=True))
    except (binascii.Error, ValueError):
        raise BinaryError("base64ToBinary() was given text that isn't base64") from None


def data_uri_to_binary(text: str, fn: str) -> dict[str, str]:
    """dataUriToBinary() and decodeDataUri(): the bytes a data URI holds, with its media type."""
    head, sep, data = text.partition(",")
    if not head.lower().startswith("data:") or not sep:
        raise BinaryError(f"{fn}() was given text that isn't a data URI")
    params = [p.strip() for p in head[5:].split(";")]
    media = params[0] if params and "/" in params[0] else OCTETS
    try:
        raw = base64.b64decode(data, validate=True) if "base64" in (p.lower() for p in params[1:]) \
            else urllib.parse.unquote_to_bytes(data)
    except (binascii.Error, ValueError):
        raise BinaryError(f"{fn}() couldn't read the data URI's data") from None
    return binary_value(raw, media)


def uri_component_to_binary(text: str) -> dict[str, str]:
    """uriComponentToBinary(): the bytes of the text with its %XX escapes undone (a + stays a +)."""
    return binary_value(urllib.parse.unquote_to_bytes(text))
