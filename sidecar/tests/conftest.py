"""Shared test setup."""

import msal
import pytest


@pytest.fixture(autouse=True)
def no_interactive_sign_in(monkeypatch):
    """Sign-in needs no setup now, so a test that reaches it would open a browser and wait.
    Make it fail at once instead, as it does for someone who closes the sign-in window."""
    def refuse(self, scopes, **kwargs):
        return {"error": "access_denied", "error_description": "no interactive sign-in in tests"}
    monkeypatch.setattr(msal.PublicClientApplication, "acquire_token_interactive", refuse)
