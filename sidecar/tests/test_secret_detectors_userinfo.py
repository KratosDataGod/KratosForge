"""Callers that use redact.secrets as a "holds a secret" test (connection profiles, App Studio
projects and provider connections) now count a URL with a user name and no password as one,
since a Git remote carries a token there (https://<token>@github.com/...). Ordinary addresses
without user info, and the values these files really hold, still pass."""

from __future__ import annotations

import pytest

from fabric_desktop import connprofiles, redact
from fabric_desktop.appstudio import project, providers

J = "".join
TOKEN_URL = "https://" + J(["gh", "p_", "A" * 36]) + "@github.com/org/repo.git"
USER_URL = "https://contoso@dev.azure.com/contoso/proj/_git/repo"
PLAIN = ["https://dev.azure.com/contoso/proj/_git/repo", "https://adb-1234567890123456.7.azuredatabricks.net",
         "https://contoso.openai.azure.com", "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables",
         "ann@contoso.com", "Sales Dev", "env:DATABRICKS_TOKEN", "credman:KratosForge/x", "wasbs://raw@acct.blob.core.windows.net/x"]


@pytest.mark.parametrize("value", [TOKEN_URL, USER_URL])
def test_a_url_with_user_info_counts_as_a_secret_everywhere(value):
    assert redact.secrets(value) != value
    assert connprofiles.secret_problems({"notes": value})
    assert project.secret_problems({"notes": value})
    base = {"name": "c", "provider": "anthropic", "model": "m", "key_ref": "credman:KratosForge/x"}
    assert any("looks like a key" in p for p in providers.problems(base | {"notes": value}))


@pytest.mark.parametrize("value", PLAIN)
def test_ordinary_values_still_pass(value):
    # abfss://ws@onelake... is OneLake's own address form: the part before @ names a workspace.
    assert redact.secrets(value) == value
    assert connprofiles.secret_problems({"notes": value}) == []
    assert project.secret_problems({"notes": value}) == []
