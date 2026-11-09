"""Databricks SQL's byte-level string functions that DuckDB has no match for, with Python's standard library.

``register`` adds them to a DuckDB connection, and databricks_builtins_sql writes each Databricks call as
one of them. A STRING or BINARY argument goes in as hex(value), the bytes either way, so both bind.

    __kf_url_decode(text, safe)          url_decode, try_url_decode: java.net.URLDecoder's rule
    __kf_make_valid_utf8(hex)            make_valid_utf8: each bad byte sequence as U+FFFD
    __kf_hmac(key hex, message hex, alg) hmac: the MAC as hex
    __kf_bit_reverse(type, value)        bit_reverse: the bits of a value of DuckDB type ``type``

Bad input raises Databricks' error class by name. They read nothing and write nothing.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import re
from typing import Any

# java.net.URLDecoder reads the two characters after % with Integer.parseInt(..., 16) and refuses a
# negative one, so %+1 is byte 1 and %-0 is byte 0 there, and anything else that isn't two hex digits stops
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2}|\+[0-9A-Fa-f]|-0)?")

_HMAC = {"SHA224": "sha224", "SHA256": "sha256", "SHA384": "sha384", "SHA512": "sha512", "SHA1": "sha1",
         "MD5": "md5"}

_WIDTH = {"TINYINT": 8, "SMALLINT": 16, "INTEGER": 32, "BIGINT": 64}


def url_decode(text: str | None, safe: bool) -> str | None:
    """``text`` as application/x-www-form-urlencoded, decoded the way Databricks' url_decode does it:
    + is a space, %XX a byte, and the bytes UTF-8 with U+FFFD for a bad sequence. A % that isn't followed
    by an escape raises CANNOT_DECODE_URL, or gives NULL when ``safe`` (try_url_decode)."""
    if text is None:
        return None
    out = bytearray()
    at = 0
    for match in _ESCAPE.finditer(text):
        if match.group(1) is None:
            if safe:
                return None
            raise ValueError(f"[CANNOT_DECODE_URL] url_decode() can't decode {text!r}: the % at character "
                             f"{match.start() + 1} isn't followed by two hex digits.")
        out += text[at:match.start()].replace("+", " ").encode("utf-8")
        out.append(int(match.group(1), 16))
        at = match.end()
    out += text[at:].replace("+", " ").encode("utf-8")
    return out.decode("utf-8", errors="replace")


def make_valid_utf8(hex_text: str | None) -> str | None:
    """The bytes ``hex_text`` as UTF-8 text, each bad sequence as U+FFFD."""
    if hex_text is None:
        return None
    return bytes.fromhex(hex_text).decode("utf-8", errors="replace")


def hmac(key_hex: str | None, message_hex: str | None, algorithm: str | None) -> str | None:
    """The HMAC of the message's bytes with the key's bytes, as hex; NULL for a NULL."""
    if key_hex is None or message_hex is None or algorithm is None:
        return None
    digest = _HMAC.get(algorithm.upper().replace("-", ""))
    if digest is None:
        raise ValueError(f"[INVALID_PARAMETER_VALUE.HMAC_ALGORITHM] hmac() has no algorithm {algorithm!r}. It "
                         "takes SHA-224, SHA-256, SHA-384, SHA-512, SHA-1 or MD5.")
    if not key_hex:
        raise ValueError("[INVALID_PARAMETER_VALUE.HMAC_CRYPTO_ERROR] hmac() can't use an empty key.")
    return _hmac.new(bytes.fromhex(key_hex), bytes.fromhex(message_hex), getattr(hashlib, digest)).hexdigest()


def bit_reverse(kind: str, value: int | None) -> int | None:
    """``value``'s bits in reverse order, in the two's complement of its type ``kind``."""
    if value is None:
        return None
    width = _WIDTH.get(kind)
    if width is None:
        raise ValueError(f"bit_reverse() takes a TINYINT, SMALLINT, INT or BIGINT in Databricks, not {kind}.")
    bits = int(format(value & ((1 << width) - 1), f"0{width}b")[::-1], 2)
    return bits - (1 << width) if bits >> (width - 1) else bits


def register(con: Any) -> None:
    """Add the __kf_ functions above to DuckDB connection ``con``. A NULL argument gives NULL."""
    from duckdb.sqltypes import BIGINT, BOOLEAN, VARCHAR
    pure: dict[str, Any] = {"side_effects": False}
    con.create_function("__kf_url_decode", url_decode, [VARCHAR, BOOLEAN], VARCHAR, null_handling="special",
                        **pure)  # try_url_decode gives NULL for bad input
    con.create_function("__kf_make_valid_utf8", make_valid_utf8, [VARCHAR], VARCHAR, **pure)
    con.create_function("__kf_hmac", hmac, [VARCHAR, VARCHAR, VARCHAR], VARCHAR, **pure)
    con.create_function("__kf_bit_reverse", bit_reverse, [VARCHAR, BIGINT], BIGINT, **pure)
