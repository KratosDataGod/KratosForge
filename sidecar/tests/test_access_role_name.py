"""A data access role's name is checked whole: letters and digits, starting with a letter, up to 128."""

import pytest

from fabric_desktop.access import make_role

PEOPLE = dict(paths=["*"], object_ids=["o1"], tenant_id="t1")


@pytest.mark.parametrize("name", ["Sales\n", "Sales\r\n", "\nSales", "Sales ", "Sales-1", "1Sales", "", "S" * 129])
def test_a_role_name_that_isnt_letters_and_digits_alone_is_refused(name):
    with pytest.raises(ValueError, match="letters and digits only"):
        make_role(name, **PEOPLE)


@pytest.mark.parametrize("name", ["S", "SalesReaders", "Sales2026", "S" * 128])
def test_a_plain_role_name_is_kept_as_given(name):
    assert make_role(name, **PEOPLE)["name"] == name
