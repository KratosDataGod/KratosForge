"""The Before/After examples on the two best-practice pages say what the checker does.

Each rule's section (``### NB001``, ``### SQ001``) has a fenced block whose ``# Before`` / ``-- Before``
part shows the problem and whose ``# After`` / ``-- After`` part shows the fix. The Before part, run
through the app's own checker, gives a finding with that section's rule id; the After part doesn't.
A section without such a block is listed in EXCEPTIONS with the reason.
"""

from __future__ import annotations

import re

import pytest
from docexamples import DOCS, PRACTICE_PAGES, Block, fenced_blocks

from fabric_desktop import notebook_practices
from fabric_desktop.sqlcompat import practices as sql_practices

NOTEBOOK_PAGE, SQL_PAGE = "notebook-best-practices.md", "sql-best-practices.md"
assert set(PRACTICE_PAGES) == {NOTEBOOK_PAGE, SQL_PAGE}

# Rules checked only when the check is told the notebook runs on a schedule.
SCHEDULED = {r["id"] for r in notebook_practices.rules() if r["scheduled_only"]}

# Sections with no Before/After block the checker can read, and why.
EXCEPTIONS = {
    ("notebook-best-practices.md", "NB008"): "no example block: the fix is an attached environment, not code",
    ("sql-best-practices.md", "SQ005"): "no example block: the section describes the fix in prose",
    ("sql-best-practices.md", "SQ007"): "the Before is a comment ('-- Before: a cursor that updates ...'), not code",
    ("sql-best-practices.md", "SQ009"): "no example block: the section describes the fix in prose",
}

_SECTION = re.compile(r"^### ([A-Z]{2}\d{3})\s*$", re.M)


def _sections(page: str) -> list[tuple[str, int]]:
    """(rule id, 1-based heading line) for each rule section on the page, in order."""
    text = (DOCS / page).read_text(encoding="utf-8")
    return [(m.group(1), text.count("\n", 0, m.start()) + 1) for m in _SECTION.finditer(text)]


def _section_blocks() -> dict[tuple[str, str], list[Block]]:
    found: dict[tuple[str, str], list[Block]] = {}
    for page in PRACTICE_PAGES:
        sections = _sections(page)
        for rule_id, _line in sections:
            found[(page, rule_id)] = []
        for b in fenced_blocks():
            if b.page != page:
                continue
            owner = [rid for rid, line in sections if line < b.line]
            if owner:
                found[(page, owner[-1])].append(b)
    return found


SECTIONS = _section_blocks()


def _parts(block: Block) -> tuple[str, str] | None:
    """The block's Before and After code, or None when it hasn't both (a comment-only part counts as none)."""
    comment = "--" if block.lang == "sql" else "#"
    marker = re.compile(rf"^\s*{re.escape(comment)}\s*(Before|After)\b")
    parts: dict[str, list[str]] = {}
    current = None
    for ln in block.code.splitlines():
        m = marker.match(ln)
        if m:
            current = m.group(1)
            parts.setdefault(current, [])
            continue
        if current:
            parts[current].append(ln)
    before, after = ("\n".join(parts.get(k, [])).strip() for k in ("Before", "After"))
    return (before, after) if before and after else None


def _rule_ids(page: str, lang: str, code: str, rule_id: str) -> set[str]:
    if page == NOTEBOOK_PAGE:
        source = f"%%sql\n{code}" if lang == "sql" else code
        result = notebook_practices.check([source], scheduled=rule_id in SCHEDULED)
    else:
        result = sql_practices.check(code)
    return {f["rule_id"] for f in result["findings"]}


def _examples() -> list[tuple[str, str, Block]]:
    return [(page, rule_id, b) for (page, rule_id), blocks in SECTIONS.items() for b in blocks if _parts(b)]


EXAMPLES = _examples()


def test_every_section_has_an_example_or_an_exception():
    with_example = {(page, rule_id) for page, rule_id, _b in EXAMPLES}
    missing = sorted(k for k in SECTIONS if k not in with_example and k not in EXCEPTIONS)
    assert not missing, f"rule sections with no Before/After block and no exception: {missing}"
    stale = sorted(k for k in EXCEPTIONS if k in with_example or k not in SECTIONS)
    assert not stale, f"exceptions for sections that now have an example, or don't exist: {stale}"


def test_sections_were_found():
    assert len(EXAMPLES) >= 25


# Afters the checker still reports, though the page and the rule's own advice call them the fix. Each is a
# checker gap, not a docs error, so the test fails (strict xfail) once the checker is fixed.
CHECKER_GAPS = {
    ("notebook-best-practices.md", "NB003"): "the checker reports toPandas() after groupBy().agg(), though NB003's "
                                             "own description says 'or aggregate first'",
}


def _params(after: bool) -> list:
    marks = lambda k: [pytest.mark.xfail(reason=CHECKER_GAPS[k], strict=True)] if after and k in CHECKER_GAPS else []
    return [pytest.param(p, r, b, id=f"{p.split('-')[0]}-{r}", marks=marks((p, r))) for p, r, b in EXAMPLES]


@pytest.mark.parametrize(("page", "rule_id", "block"), _params(after=False))
def test_before_gives_the_rule(page, rule_id, block):
    before, _after = _parts(block)  # type: ignore[misc]
    assert rule_id in _rule_ids(page, block.lang, before, rule_id), f"{page}:{block.line}: the Before doesn't give {rule_id}"


@pytest.mark.parametrize(("page", "rule_id", "block"), _params(after=True))
def test_after_does_not_give_the_rule(page, rule_id, block):
    _before, after = _parts(block)  # type: ignore[misc]
    assert rule_id not in _rule_ids(page, block.lang, after, rule_id), f"{page}:{block.line}: the After still gives {rule_id}"
