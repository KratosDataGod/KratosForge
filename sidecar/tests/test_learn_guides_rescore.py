"""Re-score tests for learn/guides.py (docs/quality/mutation-testing.md, batch 247).

The build script, run by hand as ``python -m fabric_desktop.learn.guides ../docs``, writes the
guides from the docs folder it is given, as indented JSON that keeps non-ASCII text as it is and
ends in a newline. It runs here on a copy of the module, so the shipped rule_guides.json is never
rewritten.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

from fabric_desktop.learn import guides


def test_the_build_script_writes_the_guides_of_the_docs_it_is_given(tmp_path):
    docs = tmp_path / "docs"
    for name, rid in zip(guides.DOCS, ["SQ002", "NB001", "SM001", "PG1"], strict=True):
        (docs / name).parent.mkdir(parents=True, exist_ok=True)
        (docs / name).write_text(f"### {rid}\n\nWhy: {rid} – naïve.\n", encoding="utf-8")
    script = tmp_path / "pkg" / "guides.py"
    script.parent.mkdir()
    shutil.copyfile(guides.__file__, script)
    subprocess.run([sys.executable, str(script), str(docs)], check=True, timeout=60, cwd=tmp_path)
    text = (script.parent / "rule_guides.json").read_text(encoding="utf-8")
    want = guides.build(docs)
    assert json.loads(text) == want and len(want) == 4
    assert text == json.dumps(want, indent=1, ensure_ascii=False) + "\n"
    assert "naïve" in text and text.endswith("}\n") and '\n "NB001"' in text
