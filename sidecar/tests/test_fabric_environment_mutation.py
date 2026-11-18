"""Fabric Environment drift (fabric_environment.py), pinned where mutation testing found the tests
quiet (docs/quality/mutation-testing.md, batch 91): the reason a refused read gives, drift from one
kind of difference alone, versions a specifier or pip can't read, pre-releases a specifier allows,
and the summary's first line and its staged changes without a runtime change."""

from __future__ import annotations

import pytest

from fabric_desktop import fabric_environment as fe


class Refused(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_read_says_to_ask_for_access(status):
    assert fe._reason(Refused(status)) == ("You can't read this Environment item: ask its workspace admin for at "
                                           "least Viewer access.")


def test_a_missing_item_and_any_other_failure_say_so():
    assert fe._reason(Refused(404)).startswith("There's no Environment item with that id")
    assert fe._reason(Refused(402)).startswith("Couldn't read the Environment item: ")


def test_a_version_pip_cant_read_is_compared_as_written():
    assert fe._version(" not-a-version ") == "not-a-version"
    assert fe._same_version("not-a-version", "not-a-version ") is True
    assert fe._same_version("not-a-version", "1.0") is False


def test_a_specifier_that_cant_be_read_is_compared_as_written():
    assert fe._same_version(">=abc def", ">=abc def ") is True
    assert fe._same_version(">=abc def", "1.0") is False


def test_a_specifier_allows_a_pre_release_here():
    assert fe._same_version(">=1.0", "2.0rc1") is True
    assert fe._same_version(">=3.0", "2.0rc1") is False


def _item(**over):
    return {"status": "read", "libraries": [], "custom": [], "runtime": None, "python": None} | over


@pytest.mark.parametrize("item, here", [
    (_item(libraries=[{"name": "polars", "version": "1.0"}]), {}),  # missing here only
    (_item(libraries=[{"name": "polars", "version": "1.0"}]),
     {"polars": {"name": "polars", "version": "2.0", "from": "engine"}}),  # a different version only
    (_item(), {"tqdm": {"name": "tqdm", "version": "4.0", "from": "environment"}}),  # only here
    (_item(runtime="1.2", python="3.10"), {}),  # another Python only
])
def test_any_one_kind_of_difference_alone_is_drift(item, here):
    assert fe.drift(item, here, python="3.12")["drifted"] is True


def test_no_difference_is_no_drift():
    out = fe.drift(_item(runtime="1.3", python="3.12"), {}, python="3.12")
    assert out["drifted"] is False


def test_no_drift_comes_first_in_the_summary_before_the_staged_changes():
    result = fe.drift(_item(staged={"status": "read", "added": [{"name": "rich", "version": "13.0"}], "removed": [],
                                     "changed": [], "runtime": None, "pending": True}), {}, python="3.12")
    lines = fe.summary(result).splitlines()
    assert lines == ["No drift: 0 published libraries match this PC.",
                     "The Environment item has unpublished changes; publishing it adds rich==13.0."]


def test_one_matching_library_is_counted_in_the_singular():
    result = fe.drift(_item(libraries=[{"name": "polars", "version": "1.0"}]),
                      {"polars": {"name": "polars", "version": "1.0", "from": "engine"}}, python="3.12")
    assert fe.summary(result) == "No drift: 1 published library match this PC."


def test_no_drift_comes_before_a_runtime_that_wasnt_read():
    result = fe.drift(_item(runtime_problem="HTTP 500"), {}, python="3.12")
    assert fe.summary(result).splitlines() == ["No drift: 0 published libraries match this PC.",
                                               "Runtime not read: HTTP 500"]
