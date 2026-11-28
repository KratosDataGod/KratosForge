"""Decision patterns (learn/patterns.py), the path the other tests leave: ``python -m fabric_desktop.learn.patterns
<docs>`` writing the JSON from the docs. The write is caught, so the shipped patterns.json is never changed."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from fabric_desktop.learn import patterns

DOC = """# Decision patterns

### DP-01
**Pick an engine**
Use the small one first.

```python
print(1)
```
"""


@pytest.mark.filterwarnings("ignore:'fabric_desktop.learn.patterns' found in sys.modules:RuntimeWarning")
def test_run_as_a_script_it_writes_the_patterns_from_the_docs(tmp_path, monkeypatch):
    (tmp_path / patterns.DOC).write_text(DOC, encoding="utf-8")
    written = {}

    def caught(self, text, encoding=None, **kw):
        written[self] = text
    monkeypatch.setattr(Path, "write_text", caught)
    monkeypatch.setattr(sys, "argv", ["patterns", str(tmp_path)])
    runpy.run_module("fabric_desktop.learn.patterns", run_name="__main__")
    [(path, text)] = written.items()
    assert path == patterns.FILE
    assert json.loads(text) == {"DP-01": {"title": "Pick an engine", "body": "Use the small one first.",
                                          "language": "python", "example": "print(1)"}}
