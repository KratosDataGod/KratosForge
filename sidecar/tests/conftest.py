"""Shared test setup."""

import faulthandler
import json
import os
import sys
import threading

import msal
import pytest


@pytest.fixture(autouse=True)
def no_interactive_sign_in(monkeypatch):
    """Sign-in needs no setup now, so a test that reaches it would open a browser and wait.
    Make it fail at once instead, as it does for someone who closes the sign-in window."""
    def refuse(self, scopes, **kwargs):
        return {"error": "access_denied", "error_description": "no interactive sign-in in tests"}
    monkeypatch.setattr(msal.PublicClientApplication, "acquire_token_interactive", refuse)


class _NoSignInService:
    """MSAL's HTTP client for tests. Building a PublicClientApplication asks the sign-in service for its
    settings (the authority's OpenID configuration), so every test that made an Authenticator needed the
    internet. This answers that question, and the one about which host names are the same cloud, the way
    login.microsoftonline.com does, and refuses anything else, so no test can reach Microsoft."""

    def get(self, url, params=None, headers=None, **kwargs):
        if url.endswith("/v2.0/.well-known/openid-configuration"):
            base = url[: -len("/v2.0/.well-known/openid-configuration")]
            body = {"issuer": "https://login.microsoftonline.com/{tenantid}/v2.0",
                    "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
                    "token_endpoint": f"{base}/oauth2/v2.0/token",
                    "device_authorization_endpoint": f"{base}/oauth2/v2.0/devicecode"}
            return _Reply(200, json.dumps(body))
        if url.endswith("/common/discovery/instance"):  # which host names are one cloud (for cached accounts)
            hosts = ["login.microsoftonline.com", "login.windows.net", "login.microsoft.com", "sts.windows.net"]
            return _Reply(200, json.dumps({"metadata": [{"preferred_network": hosts[0], "preferred_cache": hosts[1],
                                                         "aliases": hosts}]}))
        raise AssertionError(f"a test reached the sign-in service: GET {url}")

    def post(self, url, params=None, data=None, headers=None, **kwargs):
        raise AssertionError(f"a test reached the sign-in service: POST {url}")

    def close(self):
        pass


class _Reply:
    def __init__(self, status_code, text):
        self.status_code, self.text, self.headers = status_code, text, {}

    def raise_for_status(self):
        pass


@pytest.fixture(autouse=True)
def no_sign_in_service(monkeypatch):
    # The desktop's sign-in, and a service principal's (credentials.py) for agents.
    for name in ("PublicClientApplication", "ConfidentialClientApplication"):
        real = getattr(msal, name)

        class Offline(real):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("http_client", _NoSignInService())
                super().__init__(*args, **kwargs)
        monkeypatch.setattr(msal, name, Offline)


@pytest.fixture(autouse=True)
def saved_policy_in_tmp(monkeypatch, tmp_path):
    """Reading a policy file keeps a copy of it (app_settings.saved_policy_path); keep it out of the
    real profile, so one test's copy can't stand in for another test's broken file."""
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "saved_policy_path", lambda: tmp_path / "saved-policy" / "policy-last-good.json")
    # Test policy files are the test account's own; the check for that has tests of its own.
    monkeypatch.setattr(app_settings, "user_can_change", lambda path: False)


@pytest.fixture(autouse=True)
def no_lakehouse_left_attached(monkeypatch):
    """The attached lakehouse is the engine's one module global (lakehouse._attached). A test that
    attaches one would otherwise leave it for every later test, so a notebook test passed or failed
    depending on which file ran before it."""
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


# A test that hangs would otherwise hold CI until the job's 6-hour limit, with no output (logs only
# come back once the job ends). After TEST_TIME_LIMIT seconds, print which test it was and every
# thread's stack, then end pytest so the job fails with that in its log.
TEST_TIME_LIMIT = float(os.environ.get("FD_TEST_TIME_LIMIT", "300"))


def _over_time(item, capman) -> None:
    if capman is not None:
        capman.suspend_global_capture(in_=True)
    sys.stderr.write(f"\n\n{item.nodeid} ran over {TEST_TIME_LIMIT:g} s; stacks of every thread:\n")
    sys.stderr.flush()
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    sys.stderr.flush()
    os._exit(1)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    capman = item.config.pluginmanager.getplugin("capturemanager")
    timer = threading.Timer(TEST_TIME_LIMIT, _over_time, args=(item, capman))
    timer.daemon = True
    timer.start()
    # Backstop in C for a hang that holds the GIL, where the timer thread can't run.
    faulthandler.dump_traceback_later(TEST_TIME_LIMIT + 60, exit=True, file=sys.__stderr__)
    try:
        yield
    finally:
        timer.cancel()
        faulthandler.cancel_dump_traceback_later()
