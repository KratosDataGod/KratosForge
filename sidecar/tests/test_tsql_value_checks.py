"""WR-701: every value check behind docs/tsql-functions.md runs on the local engine here, not only when the
page is written again. A check gives T-SQL's documented answer, or the local engine refuses it by name.
Another answer, or DuckDB's own error, fails the run, unless WRONG names the check (a strict xfail, so a
fix that makes it pass has to take it out).

The checks are tsql_coverage.checks(): the T-SQL contract cases Fabric accepts with known rows, and the
documented answers in test_tsql_loud_gaps.py.
"""

from __future__ import annotations

import pytest

from tests import tsql_coverage

# check id -> the function and why it differs here. Empty: every check gives T-SQL's answer or is refused.
WRONG: dict[str, str] = {}


def _params() -> list:
    out = []
    for c in tsql_coverage.checks():
        marks = [pytest.mark.xfail(reason=WRONG[c["id"]], strict=True)] if c["id"] in WRONG else []
        out.append(pytest.param(c, id=c["id"], marks=marks))
    return out


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    r = tsql_coverage.Runner(tmp_path_factory.mktemp("value-checks") / "local-data")
    yield r
    r.close()


@pytest.mark.parametrize("check", _params())
def test_a_value_check_gives_tsqls_answer_or_is_refused_by_name(runner, check):
    outcome, note = runner.check(check)
    assert outcome in ("same", "refused"), note


def test_wrong_names_only_checks_that_exist():
    assert set(WRONG) <= {c["id"] for c in tsql_coverage.checks()}
