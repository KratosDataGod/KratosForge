"""databricks_ip.py paths that mutation testing (batch 35, docs/quality/mutation-testing.md) showed no test
pinned: which BINARY lengths read, how an IPv4 block is mapped into IPv6, the error each kind of
input names, and how the functions are registered. Pure functions; nothing is read or written."""

from __future__ import annotations

import ipaddress

import pytest

from fabric_desktop import databricks_ip as dip


def h(text: str) -> str:
    return text.encode().hex()


def test_a_binary_value_of_any_other_length_is_refused_even_if_its_bytes_spell_an_address():
    # b"1.2.3.4" is seven bytes: not 4, 5, 16 or 17, so it isn't read as the text it happens to be
    with pytest.raises(ValueError, match="INVALID_IP_ADDRESS_OR_CIDR_BLOCK"):
        dip.ip("ip_version", "BLOB", h("1.2.3.4"))
    assert dip.ip("try_ip_version", "BLOB", h("1.2.3.4")) is None


def test_a_seventeen_byte_binary_value_is_an_ipv6_block():
    block = ipaddress.ip_network("2001:db8::/64")
    raw = (block.network_address.packed + bytes([64])).hex()
    assert dip.ip("ip_prefix_length", "BLOB", raw) == "64"
    assert dip.ip("ip_cidr:fixed", "BLOB", raw) == "2001:db8::/64"


def test_an_ipv4_block_is_compared_as_its_mapped_block_96_bits_longer():
    v4 = h("10.0.0.0/24")  # ::ffff:10.0.0.0/120 when compared with IPv6
    assert dip.contains("VARCHAR", v4, "VARCHAR", h("::ffff:10.0.0.200")) is True
    assert dip.contains("VARCHAR", v4, "VARCHAR", h("::ffff:10.0.0.128/121")) is True
    assert dip.contains("VARCHAR", v4, "VARCHAR", h("::ffff:10.0.1.1")) is False


def test_each_kind_of_input_names_what_it_must_be():
    with pytest.raises(ValueError, match=r"ip_version\(\) can't read its argument as an IP address or CIDR block\.$"):
        dip.ip("ip_version", "VARCHAR", h("nope"))
    with pytest.raises(ValueError, match=r"^\[INVALID_CIDR_BLOCK\] ip_cidr\(\) .* CIDR block\.$") as cidr:
        dip.ip("ip_cidr", "VARCHAR", h("10.0.0.1"))
    assert " or CIDR" not in str(cidr.value)


def test_both_functions_handle_null_themselves_and_have_no_side_effects():
    made = []

    class Con:
        def create_function(self, name, fn, args, result, **kw):
            made.append((name, fn, kw))
    dip.register(Con())
    assert [(name, fn) for name, fn, _ in made] == [("__kf_ip", dip.ip), ("__kf_ip_contains", dip.contains)]
    assert all(kw == {"null_handling": "special", "side_effects": False} for _, _, kw in made)
