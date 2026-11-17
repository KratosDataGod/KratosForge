"""Mutation batch 73: the decision patterns reader (learn/patterns.py). It turns the decision
patterns page into the fields the app ships, so these tests use small pages of their own and never
read docs/."""

from __future__ import annotations

import json
import runpy
import shutil
import sys

from fabric_desktop.learn import patterns

PAGE = """# Decision patterns

Intro text.

### DP-02
**Second**
Body two.

```sql
SELECT 2
```

### DP-01
**First pattern**
Line one
  continued here.



After three blank lines.

```python
print(1)
```
Trailing words.

## Next part

Not a pattern.
"""


def test_each_section_gives_its_title_body_language_and_example():
    out = patterns.parse(PAGE)
    assert list(out) == ["DP-02", "DP-01"]   # page order; build() sorts
    assert out["DP-01"] == {"title": "First pattern",
                            "body": "Line one continued here.\n\nAfter three blank lines.\n\nTrailing words.",
                            "language": "python", "example": "print(1)"}
    assert out["DP-02"] == {"title": "Second", "body": "Body two.", "language": "sql", "example": "SELECT 2"}


def test_a_section_ends_at_the_next_heading_or_the_end():
    out = patterns.parse("### DP-01\n**A**\nOne.\n### DP-02\n**B**\nTwo.\n")
    assert out["DP-01"]["body"] == "One." and out["DP-02"]["body"] == "Two."
    assert patterns.parse("### DP-01\n**A**\nOne.\n## Other\nNot this.")["DP-01"]["body"] == "One."


def test_only_two_digit_ids_on_their_own_line_are_patterns():
    assert patterns.parse("### DP-1\n**A**\nx\n### DP-123\n**B**\ny\n### DP-07 extra\n**C**\nz\n") == {}
    assert patterns.parse("Text ### DP-01\n**A**\nx\n") == {}
    assert list(patterns.parse("#### DP-01\n**A**\nx\n")) == []


def test_a_section_without_a_title_or_example_uses_its_id_and_empty_fields():
    out = patterns.parse("### DP-03\nJust text.\n")["DP-03"]
    assert out == {"title": "DP-03", "body": "Just text.", "language": "", "example": ""}
    # A bold line that isn't first, or has no line break after it, isn't the title.
    assert patterns.parse("### DP-04\nIntro\n**Bold**\n")["DP-04"]["title"] == "DP-04"
    assert patterns.parse("### DP-05\n**Bold** words\n")["DP-05"]["title"] == "DP-05"


def test_the_title_is_the_shortest_bold_run_and_the_body_starts_after_it():
    out = patterns.parse("### DP-06\n\n\n**T** and **U**\nBody.\n")["DP-06"]
    assert out["title"] == "T** and **U" and out["body"] == "Body."


def test_a_fence_with_no_language_keeps_its_example_and_only_the_first_fence_is_taken():
    out = patterns.parse("### DP-08\n**T**\nA\n```\nx = 1\n\n```\nB\n```py\ny\n```\n")["DP-08"]
    assert out["language"] == "" and out["example"] == "x = 1"
    assert out["body"] == "A\nB\n```py\ny\n```"


def test_a_list_item_wrapped_with_two_spaces_joins_its_line_but_deeper_or_blank_lines_dont():
    body = patterns.parse("### DP-09\n**T**\n- a\n  b\n-  c\n   d\n  \nend\n")["DP-09"]["body"]
    assert body == "- a b\n-  c\n   d\n  \nend"   # three spaces is deeper: kept as written
    # One blank line stays; two or more become one.
    assert patterns.parse("### DP-09\n**T**\nx\n\ny\n")["DP-09"]["body"] == "x\n\ny"
    assert patterns.parse("### DP-09\n**T**\nx\n\n\ny\n")["DP-09"]["body"] == "x\n\ny"
    assert patterns.parse("### DP-09\n**T**\nx\n\n\n\n\ny\n")["DP-09"]["body"] == "x\n\ny"
    assert patterns.parse("### DP-09\n**T**\nx\ny\n")["DP-09"]["body"] == "x\ny"


def test_build_reads_the_page_from_a_folder_and_sorts_by_id(tmp_path):
    (tmp_path / "decision-patterns.md").write_text(PAGE.replace("print(1)", "print('é')"), encoding="utf-8")
    out = patterns.build(tmp_path)
    assert list(out) == ["DP-01", "DP-02"] and out["DP-01"]["example"] == "print('é')"
    assert patterns.DOC == "decision-patterns.md"


def test_load_reads_the_shipped_file_next_to_the_module():
    assert patterns.FILE.name == "patterns.json" and patterns.FILE.parent.name == "learn"
    shipped = patterns.load()
    assert shipped == json.loads(patterns.FILE.read_text(encoding="utf-8"))
    assert all(set(p) == {"title", "body", "language", "example"} for p in shipped.values())


def test_run_as_a_script_it_writes_the_patterns_next_to_itself(tmp_path, monkeypatch):
    # A copy of the module in a folder of its own, so the shipped patterns.json isn't touched.
    code = tmp_path / "code"
    code.mkdir()
    shutil.copy(patterns.__file__, code / "patterns.py")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "decision-patterns.md").write_text(PAGE.replace("Body two.", "Body twö."), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patterns.py", str(docs)])
    runpy.run_path(str(code / "patterns.py"), run_name="__main__")
    text = (code / "patterns.json").read_text(encoding="utf-8")
    assert text.endswith("}\n") and "Body twö." in text          # not escaped
    assert text.startswith('{\n "DP-01": {\n  "title"')          # one-space indent, sorted
    assert json.loads(text) == patterns.build(docs)
