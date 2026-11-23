"""Failure classification edges the main tests don't pin down (mutation batch 134,
docs/quality/mutation-testing.md): whether retrying alone helps, for each category."""

import pytest

from fabric_desktop.workloads.classify import classify


@pytest.mark.parametrize("message, category, retry", [
    ("HTTP 403 Forbidden", "access", False),
    ("429 Too Many Requests", "throttled", True),
    ("HTTP 503 from the service", "network", True),
    ("LeaseBusy: another writer holds silver.orders", "busy", True),
    ("glob matched no files", "source_missing", False),
    ("KeyError: 'region'", "schema", False),
    ("2 checks failed", "data_quality", False),
    ("ZeroDivisionError: division by zero", "code", False),
    ("the moon is in the wrong phase", "unrecognized", None),
])
def test_each_category_says_whether_retrying_alone_helps(message, category, retry):
    out = classify(message)
    assert out["category"] == category and out["retry_helps"] is retry
