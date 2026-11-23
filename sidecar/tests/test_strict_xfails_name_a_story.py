"""WR-612: every strict xfail under sidecar/tests names the story or issue that will fix it.

A strict xfail pins a known wrong answer; without an owner it can stay for good. The test below reads each
``pytest.mark.xfail(...)`` in this folder with ast and asks that its reason name a story id (``WR-NNN`` or
``PB-NNN``, as in docs/product/operations/workstreams.json) or a GitHub issue or PR (``#NNNN``).

A reason built at run time (a dict of reasons, or a cases file) is checked where it is written: see
DYNAMIC_REASONS. A strict xfail with no story yet is listed in AWAITING_STORY with what its story would need;
the entry goes when the reason names one, and a stale entry fails the run."""

import ast
import json
import re
from collections.abc import Callable
from pathlib import Path

TESTS = Path(__file__).resolve().parent
STORY = re.compile(r"\b(?:WR|PB)-\d+\b|#\d+\b")

# (file, enclosing top-level name) -> what the story that fixes it would need. Shrinks as stories are assigned.
AWAITING_STORY = {
    ("test_spark_strings_uncapped.py", "test_a_concatenation_with_a_null_inside_another_is_still_a_string"):
        "Spark SQL on DuckDB 1.5: spark_text_results casts a nested concatenation with a NULL to text",
    ("test_local_sql_rescore.py", "test_two_array_columns_named_alike_but_for_case_show_as_json"):
        "Snowflake SQL: two VARIANT or ARRAY columns named alike but for case are shown as JSON",
    ("test_docs_examples.py", "test_local_data_delta_table_statements_run_as_one_script"):
        "local data projects: a script that creates a Delta table and then writes to it runs as one script "
        "(localdata/engine.py _statements)",
    ("test_docs_examples.py", "test_local_data_merge_update_delete_run_in_either_dialect"):
        "local data projects on DuckDB 2.0: a DuckDB SQL MERGE into a project's Delta table takes the Delta path",
    ("test_academy_appcontainer.py", "test_delta_rs_writes_a_table_inside_the_appcontainer"):
        "Academy AppContainer: Delta exercises once delta-rs resolves paths without GetFinalPathNameByHandleW "
        "(upstream delta-rs)",
    ("test_tsql_loud_gaps.py", "test_a_refused_function_would_give_tsqls_answer"):
        "local T-SQL: ISNUMERIC mapped with T-SQL's rules ('$', ',' and '-' alone) instead of refused",
}


def _checker_gaps() -> list[str]:
    """test_docs_practice_examples.CHECKER_GAPS's reasons, read as a literal."""
    tree = ast.parse((TESTS / "test_docs_practice_examples.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "CHECKER_GAPS":
            assert node.value is not None
            return list(ast.literal_eval(node.value).values())
    raise AssertionError("CHECKER_GAPS is gone from test_docs_practice_examples.py")


def _tsql_contract_cases() -> list[str]:
    """tsql_contract/cases.jsonl's xfail reasons (the first line is the file's meta)."""
    lines = (TESTS / "tsql_contract" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()][1:]
    return [r for c in cases for r in (c.get("xfail") or {}).values()]


# (file, enclosing top-level name) of an xfail whose reason is built at run time -> where its reasons are written.
DYNAMIC_REASONS: dict[tuple[str, str], Callable[[], list[str]]] = {
    ("test_docs_practice_examples.py", "_params"): _checker_gaps,
    ("test_tsql_contract.py", "cases_for"): _tsql_contract_cases,
}


def _is_xfail(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "xfail" and isinstance(f.value, ast.Attribute)
            and f.value.attr == "mark")


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def xfails() -> list[tuple[str, str, bool, ast.expr | None]]:
    """Each pytest.mark.xfail(...) under sidecar/tests: (file, enclosing top-level name, strict, reason node)."""
    found = []
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for top in tree.body:
            name = getattr(top, "name", None) or "<module>"
            for node in ast.walk(top):
                if isinstance(node, ast.Call) and _is_xfail(node):
                    strict = _keyword(node, "strict")
                    is_strict = isinstance(strict, ast.Constant) and strict.value is True
                    found.append((path.relative_to(TESTS).as_posix(), name, is_strict, _keyword(node, "reason")))
    return found


def test_the_scan_finds_the_known_xfails():
    keys = {(f, n) for f, n, _, _ in xfails()}
    assert ("test_spark_complex_rescore.py", "test_a_shift_of_a_shift_of_an_int_is_an_int") in keys
    assert set(DYNAMIC_REASONS) <= keys


def test_every_strict_xfail_names_its_story():
    missing, unchecked, seen = [], [], set()
    for file, name, strict, reason in xfails():
        if not strict:
            continue
        key = (file, name)
        seen.add(key)
        if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
            if not STORY.search(reason.value) and key not in AWAITING_STORY:
                missing.append(f"{file}::{name}: {reason.value!r}")
        elif key in DYNAMIC_REASONS:
            missing += [f"{file}::{name}: {r!r}" for r in DYNAMIC_REASONS[key]() if not STORY.search(r)]
        else:
            unchecked.append(f"{file}::{name}")
    assert missing == [], "a strict xfail's reason names no story (WR-NNN, PB-NNN or #NNNN): " + "; ".join(missing)
    assert unchecked == [], ("a strict xfail's reason is built at run time: add where it is written to "
                             "DYNAMIC_REASONS: " + ", ".join(unchecked))
    assert set(AWAITING_STORY) - seen == set(), "AWAITING_STORY names a strict xfail that is gone"


def test_awaiting_story_holds_only_xfails_without_one():
    named = {(f, n) for f, n, strict, r in xfails()
             if strict and isinstance(r, ast.Constant) and isinstance(r.value, str) and STORY.search(r.value)}
    assert named & set(AWAITING_STORY) == set(), "this xfail names its story now: take it out of AWAITING_STORY"
