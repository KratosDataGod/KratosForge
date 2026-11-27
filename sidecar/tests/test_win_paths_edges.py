"""Windows path forms that name the same folder: long-path and device prefixes (either separator, any case of
UNC) come off, shares are told from local folders in every form, and a mapped drive keeps the letter the
person typed. Pure string logic, so it runs on any OS."""

from __future__ import annotations

import pytest

from fabric_desktop import win_paths


@pytest.mark.parametrize(("text", "plain"), [
    ("\\\\?\\C:\\Users\\ann\\proj", "C:\\Users\\ann\\proj"),
    ("\\\\.\\C:\\proj", "C:\\proj"),
    ("//?/C:/proj", "C:/proj"),
    ("//./C:/proj", "C:/proj"),
    ("\\\\?\\UNC\\server\\share\\proj", "\\\\server\\share\\proj"),
    ("\\\\?\\unc\\server\\share", "\\\\server\\share"),
    ("//?/UNC/server/share", "\\\\server/share"),
    ("C:\\proj", "C:\\proj"),
    ("\\\\server\\share", "\\\\server\\share"),
    ("", ""),
])
def test_plain_takes_off_long_path_and_device_prefixes(text, plain):
    assert win_paths.plain(text) == plain


@pytest.mark.parametrize(("text", "share"), [
    ("\\\\?\\C:\\Users\\ann\\proj", False),  # the long-path form of a local folder
    ("\\\\?\\UNC\\server\\share\\proj", True),
    ("//?/UNC/server/share", True),
    ("\\\\server\\share\\proj", True),
    ("//server/share", True),
    ("Z:\\proj", False),  # a drive letter, mapped or not
])
def test_is_share_in_every_form(text, share):
    assert win_paths.is_share(text) is share


def test_a_mapped_drive_keeps_the_letter_typed_with_dot_dot_removed():
    kept = win_paths.keep_drive_letter("Z:\\work\\..\\proj", "\\\\server\\share\\proj")
    assert kept.lower() == "z:\\proj"


def test_a_mapped_drive_in_long_path_form_keeps_the_plain_letter():
    kept = win_paths.keep_drive_letter("\\\\?\\Z:\\proj", "\\\\?\\UNC\\server\\share\\proj")
    assert kept.lower() == "z:\\proj"


def test_a_drive_relative_path_keeps_the_resolved_share():
    assert win_paths.keep_drive_letter("Z:proj", "\\\\server\\share\\proj") == "\\\\server\\share\\proj"


@pytest.mark.parametrize(("given", "resolved"), [
    ("C:\\proj", "C:\\proj"),  # a local folder resolved to itself
    ("\\\\server\\share\\proj", "\\\\server\\share\\proj"),  # typed as a share
    ("proj", "\\\\server\\share\\proj"),  # no drive letter
])
def test_otherwise_the_resolved_path_is_kept(given, resolved):
    assert win_paths.keep_drive_letter(given, resolved) == resolved


def test_too_long_is_none_for_anything_but_an_os_error():
    assert win_paths.too_long(ValueError("not a path problem"), "C:\\x", long_paths=False) is None
