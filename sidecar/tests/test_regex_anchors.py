"""A pattern that ends in $ also matches just before a final line break, so `match` takes "name\n".
Validators check the whole value with `fullmatch` instead (WR-611; the first case was a data access
role name, #2819)."""

import ast
import pathlib
import re

import pytest

import fabric_desktop

ROOT = pathlib.Path(fabric_desktop.__file__).parent

# Modules still to move. Empty: a new pattern ending in $ uses fullmatch, or goes in DENY with its reason.
NOT_YET: set[str] = set()  # every module now uses fullmatch (WR-611 done)

# Deny lists: a match means "refuse", so match (which also takes a final line break) is the stricter check.
DENY = {
    ("academy/gitlab.py", "_SHORT_NAME"), ("environments.py", "_RESERVED"), ("sql_policy.py", "_FILE_FUNCS"),
    ("sql_templates_service.py", "_RESERVED"), ("warehouse_project_service.py", "_RESERVED"),
}


# Inline calls that keep match: parsers whose last group must not fail on an odd line, never validators.
INLINE_KEEP = {
    ("notebook.py", r"((?:\s*-(?:[nr]\s*\d+|[A-Za-z])(?=\s|$))*)\s*(.*)$"),  # %timeit: every line splits into options and code
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


@pytest.mark.parametrize("value", ["2\n", "2-3\n", "2+\n"])
def test_a_mesh_accepts_value_ending_in_a_line_break_is_still_read_after_its_strip(value):
    from fabric_desktop.meta import mesh
    assert mesh.parse_accepts(value)[0] == 2  # stripped first: the same answer as before


def test_a_batch_or_cell_id_ending_in_a_line_break_is_refused():
    from fabric_desktop import comments_service, notebook_batch_service
    assert comments_service._CELL_ID.fullmatch("cell-1") and not comments_service._CELL_ID.fullmatch("cell-1\n")
    assert not notebook_batch_service._BATCH_ID.fullmatch("0123456789ab\n")


def test_academy_and_learn_ids_ending_in_a_line_break_are_refused():
    from fabric_desktop.learn import content
    assert content.is_id("LM-01") and not content.is_id("LM-01\n")


def test_a_dax_or_sql_name_ending_in_a_line_break_is_quoted():
    from fabric_desktop import semantic, warehouse_compare
    assert semantic._PLAIN.fullmatch("Sales") and not semantic._PLAIN.fullmatch("Sales\n")
    assert not warehouse_compare._PLAIN.fullmatch("orders\n")


def test_only_a_validate_run_id_names_a_sandbox_folder_to_remove():
    from fabric_desktop import validate_service
    good = "validate-20261117-070000-abc123"
    assert validate_service._ID.fullmatch(good) and not validate_service._ID.fullmatch(good + "\n")


def test_a_platform_notice_to_a_person_or_role_ending_in_a_line_break_is_refused():
    from fabric_desktop.workloads import platform_notify
    for name in ("_UPN", "_ROLE"):
        pattern = getattr(platform_notify, name)
        good = "ada@contoso.com" if name == "_UPN" else "platform_admin"
        assert pattern.fullmatch(good) and not pattern.fullmatch(good + "\n")


def test_a_mail_recipient_ending_in_a_line_break_is_refused():
    from fabric_desktop.workloads import deliver
    assert deliver._EMAIL.fullmatch("ada@contoso.com") and not deliver._EMAIL.fullmatch("ada@contoso.com\n")


def _ends_in_dollar(text):
    return text.endswith("$") and not text.endswith("\\$")


def _multiline(call):
    flags = list(call.args[2:3]) + [k.value for k in call.keywords if k.arg == "flags"]
    return any(isinstance(n, ast.Attribute) and n.attr in ("M", "MULTILINE") for f in flags for n in ast.walk(f))


def _inline(source, rel=""):
    """Lines of ``re.match("...$", ...)``, and of ``re.search("^...$", ...)`` without MULTILINE (the same check)."""
    out = []
    for n in ast.walk(ast.parse(source)):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("match", "search")
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "re" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                and _ends_in_dollar(n.args[0].value) and (rel, n.args[0].value) not in INLINE_KEEP):
            pattern = n.args[0].value
            if n.func.attr == "match" or (pattern.startswith("^") and not _multiline(n)):
                out.append(n.lineno)
    return sorted(out)


def test_no_inline_pattern_ending_in_dollar_is_used_with_match():
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if not rel.startswith("_vendor/"):
            found += [f"{rel}:{n}" for n in _inline(path.read_text(encoding="utf-8"), rel)]
    assert found == [], "use re.fullmatch: a pattern ending in $ also takes a value ending in a line break: " + ", ".join(found)


def test_inline_keep_entries_still_exist():
    for rel, pattern in INLINE_KEEP:
        assert _inline((ROOT / rel).read_text(encoding="utf-8")) != [], (rel, pattern)


def test_the_inline_guard_sees_match_and_whole_value_search_only():
    src = ('import re\nre.match(r"^a$", x)\nre.fullmatch(r"^a$", x)\nre.match(r"^a\\$", x)\n'
           're.search(r"^a$", x)\nre.search(r"^a$", x, re.M)\nre.search(r"a$", x)\nre.search(r"^a$", x, flags=re.MULTILINE)\n')
    assert _inline(src) == [2, 5]


def test_inline_validators_refuse_a_final_line_break():
    from fabric_desktop import spark_stream
    from fabric_desktop.academy import registry
    assert spark_stream._ident("view_1") == "view_1"
    with pytest.raises(ValueError):
        spark_stream._ident("view_1\n")
    plat = registry.PLATFORMS[registry.DEFAULT_PLATFORM]
    link = f"https://{sorted(plat['doc_hosts'])[0]}/fabric/overview"
    assert registry._doc_ok(link, plat) and not registry._doc_ok(link + "\n", plat)
