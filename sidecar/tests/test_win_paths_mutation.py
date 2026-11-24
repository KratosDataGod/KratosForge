"""win_paths.too_long at its edges, from the mutation run (docs/quality/mutation-testing.md): a path of
exactly 260 characters is already past what Windows allows, and the POSIX "name too long" errno (36)
counts the same as Windows' own answers."""

from __future__ import annotations

import os

from fabric_desktop import win_paths


def _path(length: int) -> str:
    root = os.path.abspath(os.sep)  # "/" here, the drive's root ("D:\\") on Windows
    path = root + "a" * (length - len(root))
    assert len(os.path.abspath(path)) == length
    return path


def test_a_path_of_exactly_260_characters_is_named():
    exc = FileNotFoundError(2, "The system cannot find the path specified")
    exc.winerror = 3  # type: ignore[attr-defined]
    assert win_paths.too_long(exc, _path(260), "saved", False) is not None
    assert win_paths.too_long(exc, _path(259), "saved", False) is None


def test_name_too_long_errno_without_a_winerror_is_named():
    exc = OSError(36, "File name too long")
    message = win_paths.too_long(exc, _path(300), "saved", False)
    assert message is not None and "300 characters" in message
    assert win_paths.too_long(OSError(37, "other"), _path(300), "saved", False) is None
