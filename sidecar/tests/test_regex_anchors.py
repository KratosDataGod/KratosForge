"""A pattern that ends in $ also matches just before a final line break, so `match` takes "name\n".
Validators check the whole value with `fullmatch` instead (WR-611; the first case was a data access
role name, #2819)."""

import ast
import pathlib
import re

import pytest

import fabric_desktop

ROOT = pathlib.Path(fabric_desktop.__file__).parent

# Modules still to move, slice by slice. Remove a module when its patterns use fullmatch.
NOT_YET = {
    "academy/exam.py", "academy/exercises.py", "academy/gitlab.py", "academy/guide_check.py",
    "academy/notebookutils_lab.py", "academy/pyspark_lab.py", "academy/quizlab.py", "academy/registry.py",
    "academy/sqlprojlab.py", "comments_service.py", "governance_notices.py", "history.py",
    "integrations/extensions.py", "integrations/registry.py", "learn/content.py", "learn/dismissals.py",
    "learn/fixes.py", "learn/guides.py", "localdata/discovery.py", "localdata/projects.py",
    "localdata/transform.py", "magics.py", "meta/keys.py", "meta/mesh.py", "meta/project.py", "notebook.py",
    "notebook_batch_service.py", "notebook_formats.py", "notebookutils_local.py", "optimize/local_runs.py",
    "optimize/schema.py", "orchestration/activities.py", "orchestration/copyjob_build.py",
    "orchestration/gotchas.py", "pip_magic.py", "replay.py", "semantic.py", "spark_compat.py",
    "sql_function_help.py", "sql_queries_service.py", "sql_templates_service.py", "sql_view.py", "tmdl.py",
    "usage/reader.py", "validate_service.py", "warehouse_compare.py", "warehouse_project_service.py",
    "wizard.py", "workloads/deliver.py", "workloads/impact.py", "workloads/platform_notify.py",
    "access.py",  # #2821
}

# Deny lists: a match means "refuse", so match (which also takes a final line break) is the stricter check.
DENY = {
    ("environments.py", "_RESERVED"), ("sql_policy.py", "_FILE_FUNCS"),
    ("sql_templates_service.py", "_RESERVED"), ("warehouse_project_service.py", "_RESERVED"),
}


def _anchored(tree):
    """Module-level ``NAME = re.compile("...$")`` patterns."""
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = node.value
            if (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "compile"
                    and isinstance(v.func.value, ast.Name) and v.func.value.id == "re" and v.args
                    and isinstance(v.args[0], ast.Constant) and isinstance(v.args[0].value, str)
                    and v.args[0].value.endswith("$") and not v.args[0].value.endswith("\\$")):
                out.add(node.targets[0].id)
    return out


def _matched(tree, names):
    """Lines that call ``NAME.match(...)`` on one of ``names``."""
    return sorted(n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "match"
                  and isinstance(n.func.value, ast.Name) and n.func.value.id in names)


def _found(source, rel=""):
    tree = ast.parse(source)
    return _matched(tree, {n for n in _anchored(tree) if (rel, n) not in DENY})


def test_no_pattern_ending_in_dollar_is_used_with_match():
    found, done = [], []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("_vendor/"):
            continue  # vendored as published
        lines = _found(path.read_text(encoding="utf-8"), rel)
        if lines and rel not in NOT_YET:
            found += [f"{rel}:{n}" for n in lines]
        elif not lines and rel in NOT_YET:
            done.append(rel)
    assert found == [], "use fullmatch: a pattern ending in $ also takes a value ending in a line break: " + ", ".join(found)
    assert done == [], "these use fullmatch now; take them out of NOT_YET: " + ", ".join(done)


def test_the_guard_sees_match_and_leaves_fullmatch_and_escaped_dollars_alone():
    src = ('import re\nA = re.compile(r"^a$")\nB = re.compile(r"^b\\$")\nC = re.compile(r"^c")\n'
           'A.match(x)\nA.fullmatch(x)\nB.match(x)\nC.match(x)\n')
    assert _found(src) == [5]


def test_deny_lists_keep_match_so_a_final_line_break_is_still_refused():
    from fabric_desktop import environments
    with pytest.raises(ValueError):
        environments.check_name("con")
    for rel, name in DENY:
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert f"{name}.match(" in source and f"{name}.fullmatch(" not in source, (rel, name)


def test_why_match_isnt_enough():
    pattern = re.compile(r"^[a-z]+$")
    assert pattern.match("sales\n") and not pattern.fullmatch("sales\n")


@pytest.mark.parametrize("value", ["sales\n", "sales\r\n"])
def test_a_delta_table_name_ending_in_a_line_break_is_refused(value):
    from fabric_desktop.localdata import delta
    with pytest.raises(ValueError):
        delta.check_name("dbo", value)
    with pytest.raises(ValueError):
        delta.check_name(value, "orders")


def test_a_receipt_id_ending_in_a_line_break_is_refused(tmp_path):
    from fabric_desktop.receipts import ReceiptError, ReceiptStore
    with pytest.raises(ReceiptError):
        ReceiptStore(tmp_path).get("a" * 64 + "\n")


def test_a_platform_key_ending_in_a_line_break_is_quoted_in_toml():
    from fabric_desktop.git import platform_files
    assert platform_files._key("owner") == "owner"
    assert platform_files._key("owner\n") != "owner\n"


def test_a_sign_in_name_ending_in_a_line_break_isnt_one():
    from fabric_desktop.central import core
    assert core.is_sign_in_name("ada@contoso.com") and not core.is_sign_in_name("ada@contoso.com\n")
