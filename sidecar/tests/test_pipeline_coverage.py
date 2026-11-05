"""The local pipeline coverage list: every activity type in the runner's registry and every function in
Microsoft's pipeline expression reference has one status, each one marked as running has a value check
that passes, each refused one really is refused, and docs/orchestration/coverage.md is up to date."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop.orchestration import coverage, evaluate
from fabric_desktop.orchestration.activities import default_registry

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "orchestration" / "coverage.md"


@pytest.fixture(scope="module")
def cov():
    return coverage.coverage()


def test_every_registered_activity_type_has_one_status(cov):
    assert [a["type"] for a in cov["activities"]] == sorted((e.type for e in default_registry().entries()),
                                                            key=str.lower)
    assert {a["status"] for a in cov["activities"]} <= set(coverage.ACTIVITY_STATUS.values())
    by_type = {a["type"]: a["status"] for a in cov["activities"]}
    assert by_type["ForEach"] == "runs locally" and by_type["Copy"] == "refused"


def test_every_documented_function_and_every_evaluator_function_is_listed(cov):
    names = [r["name"] for r in cov["functions"]]
    assert len(names) == len(set(names)) == 88  # 84 in the reference's tables (json included) + 4 scope
    assert {n.lower() for n in names} >= set(evaluate.FUNCTIONS), "a function the evaluator has isn't listed"
    assert set(coverage.EXAMPLES) <= set(names) and coverage.VARYING <= set(names)
    assert {r["status"] for r in cov["functions"]} <= set(coverage.FUNCTION_STATUSES)


def test_a_function_marked_as_running_gives_the_references_value(cov):
    for r in (r for r in cov["functions"] if r["status"] == "runs"):
        example, expected = coverage.EXAMPLES[r["name"]]
        got = evaluate.evaluate("@" + example, evaluate.Scope())
        assert coverage._canonical(got) == coverage._canonical(expected), r["name"]


def test_a_refused_function_stops_the_activity_by_name(cov):
    refused = [r["name"] for r in cov["functions"] if r["status"] == "refused"]
    assert "xpath" in refused and "ticks" in refused
    for name in refused:
        with pytest.raises(evaluate.ExpressionError, match=rf"{name}\(\) isn't supported in local runs yet"):
            evaluate.call(name, [], evaluate.Scope())


def test_the_page_is_up_to_date(cov):
    expected = coverage.markdown(cov)
    assert PAGE.read_text(encoding="utf-8") == expected, (
        "docs/orchestration/coverage.md is stale: run "
        "`python -c \"from fabric_desktop.orchestration import coverage as c; "
        "open('../docs/orchestration/coverage.md', 'w', encoding='utf-8', newline='\\n')"
        ".write(c.markdown(c.coverage()))\"` in sidecar/")


def test_the_page_is_in_the_docs_site():
    assert '"orchestration/coverage.md"' in (ROOT / "zensical.toml").read_text(encoding="utf-8")
