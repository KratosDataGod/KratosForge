"""Shared test setup."""

import faulthandler
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


@pytest.fixture(autouse=True)
def saved_policy_in_tmp(monkeypatch, tmp_path):
    """Reading a policy file keeps a copy of it (app_settings.saved_policy_path); keep it out of the
    real profile, so one test's copy can't stand in for another test's broken file."""
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "saved_policy_path", lambda: tmp_path / "saved-policy" / "policy-last-good.json")
    # Test policy files are the test account's own; the check for that has tests of its own.
    monkeypatch.setattr(app_settings, "user_can_change", lambda path: False)


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
