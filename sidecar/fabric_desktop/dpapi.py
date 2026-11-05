"""Windows DPAPI for files that hold secrets, such as the sign-in token cache (HD-013).

``protect`` encrypts bytes so only the same Windows account on the same PC can read them back
(CryptProtectData with the user's key, not the machine's). Another account, an administrator
reading the profile from another sign-in, or a copy of the file on another PC gets an error.
Calls ctypes only, so no extra package ships with the engine.
"""

from __future__ import annotations

import sys

# Kept with the data so a different purpose can't be handed one of these blobs by mistake.
ENTROPY = b"KratosForge sign-in cache v1"
_UI_FORBIDDEN = 0x1  # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt from the engine


def available() -> bool:
    return sys.platform == "win32"


def _call(name: str, data: bytes, purpose: bytes = ENTROPY) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):  # DATA_BLOB
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> tuple[Blob, ctypes.Array]:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = getattr(crypt32, name)
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    data_in, _keep = blob(data)
    entropy, _keep_entropy = blob(purpose)
    out = Blob()
    if not fn(ctypes.byref(data_in), None, ctypes.byref(entropy), None, None, _UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), f"{name} failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(data: bytes, purpose: bytes = ENTROPY) -> bytes:
    return _call("CryptProtectData", data, purpose)


def unprotect(data: bytes, purpose: bytes = ENTROPY) -> bytes:
    """Raises OSError when this account can't decrypt it (another account, another PC, damaged, or
    another ``purpose``)."""
    return _call("CryptUnprotectData", data, purpose)
