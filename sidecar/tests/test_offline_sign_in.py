"""The suite runs without the internet (conftest.no_sign_in_service): MSAL's apps are built without
asking login.microsoftonline.com, and any other call to the sign-in service fails the test."""

import msal
import pytest

from fabric_desktop.auth import Authenticator


def test_sign_in_apps_are_built_without_the_sign_in_service(tmp_path):
    auth = Authenticator(cache_path=tmp_path / "cache.bin")
    assert auth.app.get_accounts() == []
    sp = msal.ConfidentialClientApplication("11111111-2222-3333-4444-555555555555", client_credential="secret",
                                            authority="https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47")
    with pytest.raises(AssertionError, match="a test reached the sign-in service: POST"):
        sp.acquire_token_for_client(["https://api.fabric.microsoft.com/.default"])
