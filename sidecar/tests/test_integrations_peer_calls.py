"""The local API's caller check (integrations/peer.py) through its Win32 calls, on any OS: ctypes.WinDLL is
stood in for by fakes of iphlpapi, kernel32 and advapi32 that fill the real MIB_TCPROW / MIB_TCP6ROW tables
and TOKEN_USER the engine reads. The decisions on top (same_user, listener_is_other_user) are in
test_integrations_peer.py; this file checks the reads under them, and that anything Windows won't
confirm is refused. Nothing here opens a socket or a real process."""

import ctypes
import struct
from ctypes import wintypes

import pytest

from fabric_desktop.integrations import peer


def _net(port):
    """A port as a MIB row holds it: network byte order in the low 16 bits."""
    return ((port & 0xFF) << 8) | (port >> 8)


# A DWORD as this Python's ctypes lays it out: 4 bytes on Windows, the size of a C long elsewhere, so the
# tables are built the way the engine's own structures read them here.
D = {4: "I", 8: "Q"}[ctypes.sizeof(wintypes.DWORD)]


def v4_table(rows):
    return struct.pack(f"<{D}", len(rows)) + b"".join(
        struct.pack(f"<6{D}", state, addr, _net(lport), 0, _net(rport), pid) for state, addr, lport, rport, pid in rows)


def v6_table(rows):
    return struct.pack(f"<{D}", len(rows)) + b"".join(
        addr + struct.pack(f"<2{D}", 0, _net(lport)) + bytes(16) + struct.pack(f"<4{D}", 0, _net(rport), state, pid)
        for state, addr, lport, rport, pid in rows)


class Fn:
    def __init__(self, impl):
        self.impl, self.argtypes, self.restype = impl, None, None

    def __call__(self, *args):
        return self.impl(*args)


class Windows:
    def __init__(self):
        self.tables = {peer.AF_INET: v4_table([]), peer.AF_INET6: v6_table([])}
        self.errors = {}  # family -> error code GetExtendedTcpTable keeps answering
        self.grow = 0  # times the table grows between the size call and the read
        self.sids = {}  # pid -> SID bytes; a pid missing here can't be opened
        self.own = b"\x01\x05\x00\x00\x00\x00\x00\x05\x15\x00\x00\x00own-account"
        self.last_error, self.closed, self.opened, self.calls = 0, [], [], []
        self.token_fail = self.info_fail = self.invalid_sid = self.no_size = False
        self._keep = []

    # ---- iphlpapi
    def GetExtendedTcpTable(self, buf, size_ref, order, family, table_class, reserved):
        self.calls.append((family, table_class))
        if family in self.errors:
            return self.errors[family]
        data = self.tables[family]
        if size_ref._obj.value < len(data) or self.grow:
            if size_ref._obj.value >= len(data):
                self.grow -= 1  # a connection opened since the size was given
            size_ref._obj.value = max(size_ref._obj.value, len(data)) + 64
            return peer.ERROR_INSUFFICIENT_BUFFER
        ctypes.memmove(buf, data, len(data))
        return 0

    # ---- kernel32
    def GetCurrentProcess(self):
        return 0xFFFF  # a pseudo handle, never closed

    def OpenProcess(self, access, inherit, pid):
        assert access == peer.PROCESS_QUERY_LIMITED_INFORMATION  # never more than it needs
        if pid not in self.sids:
            self.last_error = 5  # ERROR_ACCESS_DENIED: another account's process
            return 0
        self.opened.append(pid)
        return 1000 + pid

    def CloseHandle(self, handle):
        self.closed.append(handle.value if hasattr(handle, "value") else handle)
        return 1

    # ---- advapi32
    def OpenProcessToken(self, process, access, token_ref):
        assert access == peer.TOKEN_QUERY
        if self.token_fail:
            self.last_error = 5
            return 0
        token_ref._obj.value = 5000 + (process if process != 0xFFFF else 0)
        return 1

    def _sid_for(self, token):
        pid = token.value - 5000 - 1000
        return self.own if token.value == 5000 else self.sids[pid]

    def GetTokenInformation(self, token, info_class, buf, length, size_ref):
        assert info_class == peer.TOKEN_USER_CLASS
        if buf is None:
            size_ref._obj.value = 0 if self.no_size else 16
            self.last_error = 122
            return 0
        if self.info_fail:
            self.last_error = 87
            return 0
        sid = ctypes.create_string_buffer(self._sid_for(token), len(self._sid_for(token)))
        self._keep.append(sid)
        ctypes.memmove(buf, struct.pack("<QI", 0 if self.invalid_sid == "null" else ctypes.addressof(sid), 0), 12)
        return 1

    def IsValidSid(self, sid):
        return 0 if self.invalid_sid is True else 1

    def GetLengthSid(self, sid):
        return next(len(b.raw) for b in self._keep if ctypes.addressof(b) == sid)

    def dll(self, name, use_last_error=False):
        assert use_last_error
        funcs = {"iphlpapi": ["GetExtendedTcpTable"], "kernel32": ["GetCurrentProcess", "OpenProcess", "CloseHandle"],
                 "advapi32": ["OpenProcessToken", "GetTokenInformation", "IsValidSid", "GetLengthSid"]}[name]
        return type(name, (), {f: Fn(getattr(self, f)) for f in funcs})()


@pytest.fixture
def win(monkeypatch):
    w = Windows()
    monkeypatch.setattr(ctypes, "WinDLL", w.dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: w.last_error, raising=False)
    monkeypatch.setattr(peer, "_OWN_SID", None)
    return w


# --------------------------------------------------------------------------- the TCP tables

def test_ipv4_rows_come_back_with_ports_in_host_order(win):
    win.tables[peer.AF_INET] = v4_table([(peer.ESTABLISHED, peer.LOOPBACK, 51000, 8765, 42),
                                         (2, 0, 8765, 0, 7)])
    assert peer._rows(peer.TCP_TABLE_OWNER_PID_CONNECTIONS) == [(peer.ESTABLISHED, peer.LOOPBACK, 51000, 8765, 42),
                                                                (2, 0, 8765, 0, 7)]
    assert win.calls[-1] == (peer.AF_INET, peer.TCP_TABLE_OWNER_PID_CONNECTIONS)


def test_a_table_that_grows_while_it_is_read_is_asked_for_again(win):
    win.tables[peer.AF_INET] = v4_table([(peer.ESTABLISHED, peer.LOOPBACK, 1, 2, 3)])
    win.grow = 2
    assert len(peer._rows(peer.TCP_TABLE_OWNER_PID_CONNECTIONS)) == 1
    assert len(win.calls) == 4  # size, grew, grew, read


def test_a_table_that_keeps_growing_or_fails_is_an_oserror(win):
    win.grow = 10
    with pytest.raises(OSError, match="kept growing"):
        peer._rows(peer.TCP_TABLE_OWNER_PID_CONNECTIONS)
    assert len(win.calls) == 5
    win.errors[peer.AF_INET] = 87
    with pytest.raises(OSError) as err:
        peer._rows(peer.TCP_TABLE_OWNER_PID_LISTENER)
    assert err.value.errno == 87


def test_ipv6_rows_keep_the_address_bytes(win):
    win.tables[peer.AF_INET6] = v6_table([(2, peer.V6_ANY, 8765, 0, 9), (2, peer.V6_LOOPBACK, 8765, 0, 10)])
    assert peer._rows6(peer.TCP_TABLE_OWNER_PID_LISTENER) == [(2, peer.V6_ANY, 8765, 0, 9),
                                                              (2, peer.V6_LOOPBACK, 8765, 0, 10)]


@pytest.mark.parametrize("err", peer.NO_IPV6)
def test_a_pc_without_ipv6_has_no_ipv6_listeners(win, err):
    win.errors[peer.AF_INET6] = err
    assert peer._rows6(peer.TCP_TABLE_OWNER_PID_LISTENER) == []


def test_any_other_ipv6_failure_is_an_oserror(win):
    win.errors[peer.AF_INET6] = 1
    with pytest.raises(OSError, match="IPv6"):
        peer._rows6(peer.TCP_TABLE_OWNER_PID_LISTENER)
    del win.errors[peer.AF_INET6]
    win.grow = 10
    with pytest.raises(OSError, match="kept growing"):
        peer._rows6(peer.TCP_TABLE_OWNER_PID_LISTENER)


# --------------------------------------------------------------------------- whose a process is

def test_a_process_sid_is_read_from_its_token_and_every_handle_is_closed(win):
    win.sids[42] = b"\x01\x05sid-of-42"
    assert peer._user_sid(42) == b"\x01\x05sid-of-42"
    assert win.opened == [42] and sorted(win.closed) == [1042, 6042]  # the token, then the process


def test_this_processs_own_sid_is_read_once_and_its_pseudo_handle_never_closed(win):
    assert peer.own_sid() == win.own
    assert win.closed == [5000]  # only the token
    win.own = b"changed"
    assert peer.own_sid() != b"changed"  # cached


def test_another_accounts_process_cant_be_opened(win):
    with pytest.raises(OSError) as err:
        peer._user_sid(7)
    assert err.value.errno == 5 and win.closed == []


@pytest.mark.parametrize("fault, message", [("token_fail", "OpenProcessToken"), ("no_size", "no size"),
                                            ("info_fail", "GetTokenInformation failed")])
def test_a_token_windows_wont_read_is_an_oserror_and_handles_still_close(win, fault, message):
    win.sids[42] = b"sid"
    setattr(win, fault, True)
    with pytest.raises(OSError, match=message):
        peer._user_sid(42)
    assert 1042 in win.closed and (fault == "token_fail" or 6042 in win.closed)


@pytest.mark.parametrize("invalid", [True, "null"])
def test_a_token_without_a_valid_user_sid_is_refused(win, invalid):
    win.sids[42] = b"sid"
    win.invalid_sid = invalid
    with pytest.raises(OSError, match="no valid user SID"):
        peer._user_sid(42)
    assert sorted(win.closed) == [1042, 6042]


# --------------------------------------------------------------------------- the decisions, end to end

def test_the_caller_on_this_account_passes_and_another_accounts_is_refused(win):
    win.sids[42] = win.own
    win.sids[43] = b"\x01\x05someone-else"
    win.tables[peer.AF_INET] = v4_table([(peer.ESTABLISHED, peer.LOOPBACK, 51000, 8765, 42),
                                         (peer.ESTABLISHED, peer.LOOPBACK, 51001, 8765, 43),
                                         (peer.ESTABLISHED, peer.LOOPBACK, 51002, 8765, 44)])
    assert peer.same_user(51000, 8765)
    assert not peer.same_user(51001, 8765)
    assert not peer.same_user(51002, 8765)  # a process Windows won't open
    assert not peer.same_user(51003, 8765)  # no such connection
    win.errors[peer.AF_INET] = 87
    assert not peer.same_user(51000, 8765)  # no table: refused


def test_a_listener_of_another_account_on_any_local_address_stops_the_start(win):
    win.sids[9] = win.own
    win.sids[10] = b"\x01\x05someone-else"
    win.tables[peer.AF_INET] = v4_table([(2, peer.LOOPBACK, 8765, 0, 9)])
    assert not peer.listener_is_other_user(8765)
    win.tables[peer.AF_INET6] = v6_table([(2, peer.V6_MAPPED_ANY, 8765, 0, 10)])
    assert peer.listener_is_other_user(8765)
    win.tables[peer.AF_INET6] = v6_table([(2, b"\xfe\x80" + bytes(14), 8765, 0, 10)])  # a link-local address
    assert not peer.listener_is_other_user(8765)
    win.errors[peer.AF_INET6] = peer.NO_IPV6[0]
    assert not peer.listener_is_other_user(8765)
    win.errors[peer.AF_INET6] = 1
    assert peer.listener_is_other_user(8765)  # a table Windows won't give: don't start unchecked
