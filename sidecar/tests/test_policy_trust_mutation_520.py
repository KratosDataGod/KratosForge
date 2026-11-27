"""policy_trust.py (mutation batch 520): reading Group Policy's key is no right to change it, and an
entry that applies to the path itself and is also inherited still counts."""

from __future__ import annotations

import pytest

from fabric_desktop import policy_trust as pt

KEY_QUERY_VALUE, KEY_ENUMERATE_SUB_KEYS, KEY_NOTIFY, READ_CONTROL = 0x1, 0x8, 0x10, 0x2_0000
KEY_READ = READ_CONTROL | KEY_QUERY_VALUE | KEY_ENUMERATE_SUB_KEYS | KEY_NOTIFY  # 0x20019, what Users get
OBJECT_INHERIT, CONTAINER_INHERIT, NO_PROPAGATE = 0x1, 0x2, 0x4


def test_the_key_rights_have_the_values_windows_defines():
    assert (pt.KEY_SET_VALUE, pt.KEY_CREATE_SUB_KEY) == (0x2, 0x4)
    assert pt.INHERIT_ONLY == 0x8


@pytest.mark.parametrize("rights", [pt.KEY_RIGHTS, pt.PARENT_KEY_RIGHTS])
def test_users_reading_the_key_and_its_parent_is_trusted(rights):
    dacl = [(pt.ACCESS_ALLOWED, 0, pt.GENERIC_ALL, True), (pt.ACCESS_ALLOWED, 0, KEY_READ, False)]
    assert pt.judge("HKLM\\k", True, dacl, rights) is None


@pytest.mark.parametrize("mask, rights", [(0x2, pt.KEY_RIGHTS), (0x4, pt.KEY_RIGHTS), (0x4, pt.PARENT_KEY_RIGHTS)])
def test_users_setting_values_or_adding_subkeys_is_not_trusted(mask, rights):
    dacl = [(pt.ACCESS_ALLOWED, 0, mask, False)]
    assert "someone other than SYSTEM and Administrators" in pt.judge("HKLM\\k", True, dacl, rights)


@pytest.mark.parametrize("flags", [OBJECT_INHERIT, CONTAINER_INHERIT, OBJECT_INHERIT | CONTAINER_INHERIT,
                                   NO_PROPAGATE | OBJECT_INHERIT])
def test_an_entry_inherited_by_children_still_applies_to_the_path_itself(flags):
    dacl = [(pt.ACCESS_ALLOWED, flags, pt.FILE_WRITE_DATA, False)]
    assert "someone other than SYSTEM and Administrators" in pt.judge("p", True, dacl, pt.FILE_RIGHTS)
    dacl = [(pt.ACCESS_ALLOWED, flags | pt.INHERIT_ONLY, pt.FILE_WRITE_DATA, False)]
    assert pt.judge("p", True, dacl, pt.FILE_RIGHTS) is None
