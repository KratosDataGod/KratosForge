"""Databricks SQL's IP address functions (ip_*, try_ip_*) on this PC, with Python's ipaddress module.

DuckDB has no IP functions without its inet extension, which a closed session doesn't load, so
``register`` adds two scalar functions to a DuckDB connection, and databricks_builtins_sql writes each
Databricks call as one of them:

    __kf_ip(op, kind, hex)                  one address or CIDR block: text, or hex for BINARY
    __kf_ip_contains(kind, hex, kind, hex)  ip_cidr_contains

An argument goes in as its DuckDB type name and hex(value), so a STRING and a BINARY both bind. The
forms are Databricks' canonical ones: IPv6 compressed and lower case, an IPv4-mapped address as
::ffff:a.b.c.d, a CIDR block at its network address, and BINARY as 4 or 16 bytes with one more for a
block's prefix length. Bad input raises Databricks' error class by name; the try_ ones give NULL.
They read nothing and write nothing.
"""

from __future__ import annotations

import ipaddress
from typing import Any

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# op: (Databricks error class for bad input, what the input must be: "any", "cidr" or "host")
OPS = {
    "ip_as_binary": ("INVALID_IP_ADDRESS_OR_CIDR_BLOCK", "any"),
    "ip_as_string": ("INVALID_IP_ADDRESS_OR_CIDR_BLOCK", "any"),
    "ip_version": ("INVALID_IP_ADDRESS_OR_CIDR_BLOCK", "any"),
    "ip_cidr": ("INVALID_CIDR_BLOCK", "cidr"),
    "ip_network": ("INVALID_CIDR_BLOCK", "cidr"),
    "ip_network_first": ("INVALID_CIDR_BLOCK", "cidr"),
    "ip_network_last": ("INVALID_CIDR_BLOCK", "cidr"),
    "ip_prefix_length": ("INVALID_CIDR_BLOCK", "cidr"),
    "ip_host": ("INVALID_IP_ADDRESS", "host"),
}


class _Bad(ValueError):
    pass


def _read(kind: str, hex_text: str) -> Address | Network:
    raw = bytes.fromhex(hex_text)
    if kind == "BLOB":
        if len(raw) in (4, 16):
            return ipaddress.ip_address(raw)
        if len(raw) in (5, 17) and raw[-1] <= (len(raw) - 1) * 8:
            return ipaddress.ip_network((ipaddress.ip_address(raw[:-1]), raw[-1]), strict=False)
        raise _Bad
    try:
        text = raw.decode("utf-8").strip()
        if "%" in text:  # an IPv6 zone (fe80::1%eth0): Python reads it, but it has no BINARY form
            raise _Bad
        return ipaddress.ip_network(text, strict=False) if "/" in text else ipaddress.ip_address(text)
    except ValueError:  # silent-ok: a bad address is Databricks' error class, raised by the caller
        raise _Bad from None


def _address_text(address: Address) -> str:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return f"::ffff:{address.ipv4_mapped}"  # Python 3.12 writes it in hex; Databricks keeps the dots
    return address.compressed


def _text(value: Address | Network) -> str:
    if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return f"{_address_text(value.network_address)}/{value.prefixlen}"
    return _address_text(value)


def _hex(value: Address | Network) -> str:
    if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return (value.network_address.packed + bytes([value.prefixlen])).hex().upper()
    return value.packed.hex().upper()


def ip(op: str, kind: str, hex_text: str | None) -> str | None:
    """``op`` ("ip_host", or "try_ip_host"; then ":text", ":binary" or ":fixed" for the result) of one
    value; NULL for NULL."""
    if hex_text is None:
        return None
    name, _, out = op.partition(":")
    safe = name.startswith("try_")
    base = name.removeprefix("try_")
    error, must = OPS[base]
    try:
        value = _read(kind, hex_text)
        if must == "cidr" and not isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            raise _Bad
        if must == "host" and isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            raise _Bad
    except _Bad:
        if safe:
            return None
        raise ValueError(f"[{error}] {name}() can't read its argument as an IP address"
                         f"{'' if must == 'host' else ' or CIDR block' if must == 'any' else ' CIDR block'}.") from None
    if out == "text" and kind == "BLOB":
        raise ValueError(f"{name}() gives BINARY for a BINARY argument in Databricks; this PC can't tell this one is "
                         "BINARY before it runs. Write the argument as CAST(... AS BINARY).")
    if base == "ip_version":
        return str(value.version)
    if base == "ip_prefix_length":
        assert isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network))  # must == "cidr"
        return str(value.prefixlen)
    if base in ("ip_network", "ip_network_first", "ip_network_last"):
        assert isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network))
        value = value.broadcast_address if base == "ip_network_last" else value.network_address
    return _hex(value) if out == "binary" or base == "ip_as_binary" else _text(value)


def _as_v6(value: Address | Network) -> Any:
    """``value`` as IPv6, an IPv4 one as its IPv4-mapped form (Databricks matches ::ffff:a.b.c.d/120 to a.b.c.d)."""
    if isinstance(value, ipaddress.IPv4Address):
        return ipaddress.IPv6Address(f"::ffff:{value}")
    if isinstance(value, ipaddress.IPv4Network):
        return ipaddress.IPv6Network(f"::ffff:{value.network_address}/{96 + value.prefixlen}")
    return value


def contains(cidr_kind: str, cidr_hex: str | None, needle_kind: str, needle_hex: str | None) -> bool | None:
    """ip_cidr_contains: whether ``needle`` (an address or block) is inside ``cidr``; NULL for a NULL."""
    if cidr_hex is None or needle_hex is None:
        return None
    try:
        block = _read(cidr_kind, cidr_hex)
        if not isinstance(block, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            raise _Bad
    except _Bad:
        raise ValueError("[INVALID_CIDR_BLOCK] ip_cidr_contains() can't read its first argument as a CIDR block.") from None
    try:
        needle = _read(needle_kind, needle_hex)
    except _Bad:
        raise ValueError("[INVALID_IP_ADDRESS_OR_CIDR_BLOCK] ip_cidr_contains() can't read its second argument as an "
                         "IP address or CIDR block.") from None
    outer: Any = _as_v6(block) if block.version != needle.version else block
    inner: Any = _as_v6(needle) if block.version != needle.version else needle
    if isinstance(inner, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return bool(inner.subnet_of(outer))
    return bool(inner in outer)


def register(con: Any) -> None:
    """Add __kf_ip and __kf_ip_contains to DuckDB connection ``con``. They see NULLs themselves
    (null_handling special), since a try_ one gives NULL for bad input too."""
    from duckdb.sqltypes import BOOLEAN, VARCHAR
    con.create_function("__kf_ip", ip, [VARCHAR, VARCHAR, VARCHAR], VARCHAR, null_handling="special",
                        side_effects=False)
    con.create_function("__kf_ip_contains", contains, [VARCHAR, VARCHAR, VARCHAR, VARCHAR], BOOLEAN,
                        null_handling="special", side_effects=False)
