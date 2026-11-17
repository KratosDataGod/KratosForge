"""A DefaultReader whose paths aren't in exactly one place is refused (optimize/roles.py)."""

import pytest

from tests.test_optimize_roles import plan, reader


@pytest.mark.parametrize("permission", [
    [{"attributeName": "Action", "attributeValueIncludedIn": ["Read"]}],
    [{"attributeName": "Path", "attributeValueIncludedIn": ["/Files"]},
     {"attributeName": "Path", "attributeValueIncludedIn": ["*"]}],
])
def test_a_default_reader_without_exactly_one_path_list_is_left_alone(permission):
    role = reader(["*"])
    role["decisionRules"][0]["permission"] = permission
    with pytest.raises(PermissionError, match="doesn't say which paths it reads in one place"):
        plan([role])
