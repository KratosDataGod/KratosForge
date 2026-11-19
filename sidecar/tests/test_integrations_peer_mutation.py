"""Mutation kills for integrations/peer.py (batch 93). The call tests' Windows fake checks the engine against
peer's own constants, so a wrong constant passes there; these pin the documented Win32 values as literals
and record the arguments every Win32 call gets."""

import ctypes

import pytest

from fabric_desktop.integrations import peer
from tests.test_integrations_peer_calls import v4_table, v6_table, win  # noqa: F401 - the fixture is used by name


def record(w, name):
    """Record (sizeof buf or None, initial size value, *other args) of every call to the fake's ``name``."""
    seen, inner = [], getattr(w, name)

    def call(*args):
        if name == "GetExtendedTcpTable":
            buf, size_ref, *rest = args
            seen.append((ctypes.sizeof(buf), size_ref._obj.value, *rest))
        elif name == "GetTokenInformation":
            token, info_class, buf, length, size_ref = args
            seen.append((info_class, buf is None, int(getattr(length, "value", length)), size_ref._obj.value))
        else:
            seen.append(args)
        return inner(*args)

    setattr(w, name, call)
    return seen


def test_the_win32_constants_are_the_documented_values():
    assert (peer.AF_INET, peer.AF_INET6) == (2, 23)
    assert peer.V6_LOOPBACK == bytes.fromhex("00000000000000000000000000000001")
    assert peer.V6_MAPPED_LOOPBACK == bytes.fromhex("00000000000000000000ffff7f000001")
    assert peer.V6_MAPPED_ANY == bytes.fromhex("00000000000000000000ffff00000000")
    assert peer.ESTABLISHED == 5
    assert (peer.TCP_TABLE_OWNER_PID_CONNECTIONS, peer.TCP_TABLE_OWNER_PID_LISTENER) == (4, 3)
    assert peer.LOOPBACK == 0x0100007F
    assert peer.ERROR_INSUFFICIENT_BUFFER == 122
    assert peer.NO_IPV6 == (50, 87)
    assert (peer.PROCESS_QUERY_LIMITED_INFORMATION, peer.TOKEN_QUERY, peer.TOKEN_USER_CLASS) == (0x1000, 8, 1)


@pytest.mark.parametrize("platform, expected", [("win32", True), ("linux", False), ("cygwin", False)])
def test_only_windows_is_checked(monkeypatch, platform, expected):
    monkeypatch.setattr(peer.sys, "platform", platform)
    assert peer.checked() is expected


def test_the_ipv4_table_is_asked_for_unsorted_with_a_one_byte_probe_first(win):
    seen = record(win, "GetExtendedTcpTable")
    win.tables[2] = v4_table([(5, 0x0100007F, 51000, 8765, 42)])
    assert peer._rows(4) == [(5, 0x0100007F, 51000, 8765, 42)]
    assert seen[0][:2] == (1, 0)  # a 1-byte buffer, size 0: Windows answers with the size it needs
    assert seen[1][0] == seen[1][1] > 1
    for _buf, _size, order, family, cls, reserved in seen:
        assert (order, family, cls, reserved) == (False, 2, 4, 0) and order is False


def test_the_ipv6_table_is_asked_for_unsorted_with_a_one_byte_probe_first(win):
    seen = record(win, "GetExtendedTcpTable")
    win.tables[23] = v6_table([(2, bytes(16), 8765, 0, 7)])
    assert peer._rows6(3) == [(2, bytes(16), 8765, 0, 7)]
    assert seen[0][:2] == (1, 0)
    assert seen[1][0] == seen[1][1] > 1
    for _buf, _size, order, family, cls, reserved in seen:
        assert (order, family, cls, reserved) == (False, 23, 3, 0) and order is False


@pytest.mark.parametrize("read, family", [(peer._rows, 2), (peer._rows6, 23)])
def test_a_table_that_keeps_growing_is_asked_for_five_times(win, read, family):
    seen = record(win, "GetExtendedTcpTable")
    win.grow = 100
    with pytest.raises(OSError) as e:
        read(3)
    assert len(seen) == 5 and e.value.errno == 122
    assert "kept growing" in str(e.value)


def test_another_process_is_opened_for_query_only_and_not_inherited(win):
    seen = record(win, "OpenProcess")
    win.sids[42] = b"\x01\x02other"
    assert peer._user_sid(42) == b"\x01\x02other"
    assert seen == [(0x1000, False, 42)] and seen[0][1] is False


def test_the_token_is_opened_for_query_and_asked_its_user_size_first(win):
    tokens = record(win, "OpenProcessToken")
    info = record(win, "GetTokenInformation")
    win.sids[42] = b"\x01\x02other"
    peer._user_sid(42)
    assert tokens[0][1] == 8
    assert info[0] == (1, True, 0, 0)  # TokenUser, no buffer, length 0, size starts at 0
    assert info[1] == (1, False, 16, 16)


def test_a_token_without_a_valid_sid_is_errno_0(win):
    win.invalid_sid = True
    with pytest.raises(OSError) as e:
        peer._user_sid()
    assert e.value.errno == 0


def test_no_listener_on_the_port_is_a_plain_false(win):
    assert peer.listener_is_other_user(8765, rows=[], rows6=[], sid_of=lambda pid: b"x", own=lambda: b"x") is False
    assert peer.listener_is_other_user(8765) is False
