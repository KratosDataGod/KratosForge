"""Which Windows account is on the other end of a loopback connection to the local API.

On a PC several people sign in to (a shared PC, a Remote Desktop server), everyone shares
127.0.0.1. A bearer token alone can't tell their programs apart: one person's program could use a
token it got hold of, and while the API isn't running another account can listen on its port and be
sent the tokens of this person's tools. So the API only answers programs running as the account it
runs as: Windows says which process owns the connection (GetExtendedTcpTable) and which account owns
that process (its token's user SID). Anything it can't confirm is refused.

Calls ctypes only, so no extra package ships with the engine. Elsewhere (the headless agent's
container) every caller is let through as before.
"""

from __future__ import annotations

import sys
from typing import Any

AF_INET = 2
AF_INET6 = 23
V6_LOOPBACK = bytes(15) + b"\x01"  # ::1
V6_ANY = bytes(16)  # ::, which on a dual-stack socket also takes IPv4 connections
TCP_TABLE_OWNER_PID_CONNECTIONS = 4
TCP_TABLE_OWNER_PID_LISTENER = 3
LOOPBACK = 0x0100007F  # 127.0.0.1 as GetExtendedTcpTable gives it (network byte order, read as a DWORD)
ERROR_INSUFFICIENT_BUFFER = 122
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1  # TokenUser


def checked() -> bool:
    """Whether this system says who owns a connection (Windows); elsewhere every caller passes."""
    return sys.platform == "win32"


def _port(raw: int) -> int:
    """A port from a MIB_TCPROW: network byte order in the low 16 bits."""
    return ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)


def _rows(table_class: int) -> list[tuple[int, int, int, int, int]]:
    """(state, local address, local port, remote port, owning pid) for every IPv4 TCP row."""
    import ctypes
    from ctypes import wintypes

    class Row(ctypes.Structure):  # MIB_TCPROW_OWNER_PID
        _fields_ = [("state", wintypes.DWORD), ("local_addr", wintypes.DWORD), ("local_port", wintypes.DWORD),
                    ("remote_addr", wintypes.DWORD), ("remote_port", wintypes.DWORD), ("pid", wintypes.DWORD)]

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get = iphlpapi.GetExtendedTcpTable
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG,
                    ctypes.c_int, wintypes.ULONG]
    get.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    for _ in range(5):  # the table can grow between asking its size and reading it
        buf = ctypes.create_string_buffer(size.value or 1)
        err = get(buf, ctypes.byref(size), False, AF_INET, table_class, 0)
        if err == 0:
            break
        if err != ERROR_INSUFFICIENT_BUFFER:
            raise OSError(err, "GetExtendedTcpTable failed")
    else:
        raise OSError(ERROR_INSUFFICIENT_BUFFER, "GetExtendedTcpTable kept growing")
    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
    rows = ctypes.cast(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD), ctypes.POINTER(Row * count)).contents
    return [(r.state, r.local_addr, _port(r.local_port), _port(r.remote_port), r.pid) for r in rows]


def _user_sid(pid: int | None = None) -> bytes:
    """The user SID of ``pid``'s token (this process's when None). Raises OSError when Windows
    won't say, which is the case for another account's processes."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    advapi32.IsValidSid.argtypes = [ctypes.c_void_p]
    advapi32.IsValidSid.restype = wintypes.BOOL

    own = pid is None
    process = kernel32.GetCurrentProcess() if own else kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
        try:
            size = wintypes.DWORD(0)
            advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
            if not size.value:
                raise OSError(ctypes.get_last_error(), "GetTokenInformation gave no size")
            buf = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, buf, size, ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
            sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER.User.Sid
            if not sid or not advapi32.IsValidSid(sid):
                raise OSError(0, "The token has no valid user SID")
            return ctypes.string_at(sid, advapi32.GetLengthSid(sid))
        finally:
            kernel32.CloseHandle(token)
    finally:
        if not own:
            kernel32.CloseHandle(process)


_OWN_SID: bytes | None = None


def own_sid() -> bytes:
    global _OWN_SID
    if _OWN_SID is None:
        _OWN_SID = _user_sid()
    return _OWN_SID


def caller_pid(peer_port: int, server_port: int, rows: list[tuple[int, int, int, int, int]]) -> int | None:
    """The process at the client end of the loopback connection from ``peer_port`` to
    ``server_port``, or None when there isn't exactly one."""
    pids = {pid for _state, addr, local, remote, pid in rows
            if addr == LOOPBACK and local == peer_port and remote == server_port}
    return pids.pop() if len(pids) == 1 else None


def same_user(peer_port: int, server_port: int, *, rows: Any = None, sid_of: Any = None, own: Any = None) -> bool:
    """Whether the program on loopback port ``peer_port``, connected to ``server_port``, runs as
    this account. False whenever that can't be confirmed. ``rows``, ``sid_of`` and ``own`` are for
    tests."""
    try:
        table = rows if rows is not None else _rows(TCP_TABLE_OWNER_PID_CONNECTIONS)
        pid = caller_pid(peer_port, server_port, table)
        if pid is None:
            return False
        return (sid_of or _user_sid)(pid) == (own or own_sid)()
    except OSError:
        return False


def _rows6(table_class: int) -> list[tuple[int, bytes, int, int, int]]:
    """(state, local address, local port, remote port, owning pid) for every IPv6 TCP row."""
    import ctypes
    from ctypes import wintypes

    class Row(ctypes.Structure):  # MIB_TCP6ROW_OWNER_PID
        _fields_ = [("local_addr", ctypes.c_ubyte * 16), ("local_scope", wintypes.DWORD), ("local_port", wintypes.DWORD),
                    ("remote_addr", ctypes.c_ubyte * 16), ("remote_scope", wintypes.DWORD), ("remote_port", wintypes.DWORD),
                    ("state", wintypes.DWORD), ("pid", wintypes.DWORD)]

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get = iphlpapi.GetExtendedTcpTable
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG,
                    ctypes.c_int, wintypes.ULONG]
    get.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    for _ in range(5):
        buf = ctypes.create_string_buffer(size.value or 1)
        err = get(buf, ctypes.byref(size), False, AF_INET6, table_class, 0)
        if err == 0:
            break
        if err != ERROR_INSUFFICIENT_BUFFER:
            raise OSError(err, "GetExtendedTcpTable (IPv6) failed")
    else:
        raise OSError(ERROR_INSUFFICIENT_BUFFER, "GetExtendedTcpTable kept growing")
    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
    # The rows start after the count, aligned for the structure (its DWORDs).
    rows = ctypes.cast(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD), ctypes.POINTER(Row * count)).contents
    return [(r.state, bytes(r.local_addr), _port(r.local_port), _port(r.remote_port), r.pid) for r in rows]


def listener_is_other_user(port: int, *, rows: Any = None, rows6: Any = None, sid_of: Any = None,
                           own: Any = None) -> bool:
    """Whether another account's program is already listening on ``port`` at a loopback address or
    every address, IPv4 or IPv6 (a dual-stack [::] socket takes IPv4 connections too): the API then
    doesn't start, since this person's tools would talk to that program."""
    listeners: list[int] = []
    try:
        listeners += [pid for _s, addr, local, _r, pid in (rows if rows is not None else _rows(TCP_TABLE_OWNER_PID_LISTENER))
                      if local == port and addr in (LOOPBACK, 0)]
    except OSError:
        pass
    try:
        listeners += [pid for _s, addr, local, _r, pid in (rows6 if rows6 is not None else _rows6(TCP_TABLE_OWNER_PID_LISTENER))
                      if local == port and addr in (V6_LOOPBACK, V6_ANY)]
    except OSError:
        pass
    for pid in listeners:
        try:
            if (sid_of or _user_sid)(pid) != (own or own_sid)():
                return True
        except OSError:
            return True  # Windows won't say whose it is: not this account's
    return False
