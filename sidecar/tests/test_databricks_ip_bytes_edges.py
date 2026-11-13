"""Databricks' IP and byte functions on this PC: the refusals and odd inputs the main tests don't reach.
Pure functions: they read and write nothing."""

import pytest

from fabric_desktop import databricks_bytes as dbb
from fabric_desktop import databricks_ip as dip


def h(text: str) -> str:
    return text.encode().hex()


def test_ip_host_refuses_a_block_and_the_try_form_gives_null():
    with pytest.raises(ValueError, match=r"\[INVALID_IP_ADDRESS\] ip_host\(\) can't read its argument as an IP address\.$"):
        dip.ip("ip_host:text", "VARCHAR", h("10.0.0.0/8"))
    assert dip.ip("try_ip_host:text", "VARCHAR", h("10.0.0.0/8")) is None


def test_a_text_result_for_a_binary_argument_says_to_cast_it():
    with pytest.raises(ValueError, match="CAST\\(... AS BINARY\\)"):
        dip.ip("ip_as_string:text", "BLOB", bytes([10, 0, 0, 1]).hex())


def test_a_v4_block_inside_a_v6_block_is_compared_in_its_mapped_form():
    v6 = h("::ffff:10.0.0.0/104")
    assert dip.contains("VARCHAR", v6, "VARCHAR", h("10.1.0.0/16")) is True
    assert dip.contains("VARCHAR", v6, "VARCHAR", h("11.0.0.0/16")) is False
    assert dip.contains("VARCHAR", v6, "VARCHAR", h("10.2.3.4")) is True


def test_contains_refuses_an_address_as_its_block_and_a_bad_second_argument():
    with pytest.raises(ValueError, match="INVALID_CIDR_BLOCK"):
        dip.contains("VARCHAR", h("10.0.0.1"), "VARCHAR", h("10.0.0.1"))
    with pytest.raises(ValueError, match="INVALID_IP_ADDRESS_OR_CIDR_BLOCK"):
        dip.contains("VARCHAR", h("10.0.0.0/8"), "VARCHAR", h("not an ip"))
    assert dip.contains("VARCHAR", None, "VARCHAR", h("10.0.0.1")) is None


def test_binary_input_of_the_wrong_length_or_prefix_is_refused():
    with pytest.raises(ValueError, match="INVALID_IP_ADDRESS_OR_CIDR_BLOCK"):
        dip.ip("ip_as_string:fixed", "BLOB", "0A0000")
    assert dip.ip("try_ip_as_string:fixed", "BLOB", "0A00000021") is None  # a /33 on four bytes
    assert dip.ip("ip_as_string:fixed", "BLOB", "0A00000008") == "10.0.0.0/8"


def test_url_decode_reads_java_style_signed_escapes_and_refuses_a_bare_percent():
    assert dbb.url_decode(None, False) is None
    assert dbb.url_decode("a%+1b", False) == "a\x01b" and dbb.url_decode("%-0", False) == "\x00"
    with pytest.raises(ValueError, match="character 2"):
        dbb.url_decode("a%zz", False)
    assert dbb.url_decode("a%zz", True) is None


def test_make_valid_utf8_hmac_and_bit_reverse_edges():
    assert dbb.make_valid_utf8(None) is None and dbb.make_valid_utf8("ff41") == "�A"
    assert dbb.hmac(None, "00", "SHA256") is None
    with pytest.raises(ValueError, match="HMAC_ALGORITHM"):
        dbb.hmac("00", "00", "SHA3")
    with pytest.raises(ValueError, match="empty key"):
        dbb.hmac("", "00", "sha-256")
    assert dbb.bit_reverse("TINYINT", None) is None
    with pytest.raises(ValueError, match="not DOUBLE"):
        dbb.bit_reverse("DOUBLE", 1)
    assert dbb.bit_reverse("TINYINT", 1) == -128
