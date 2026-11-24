"""No test calls monkeypatch.undo(). It undoes every patch made through the fixture in that test,
conftest's autouse ones too (offline sign-in, the fake event log, the saved policy kept in tmp), so
the rest of the test ran against the real ones. Scope temporary patches with
pytest.MonkeyPatch.context() instead."""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _calls(path: Path) -> list[int]:
    found = []
    tokens = list(tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline))
    for i, tok in enumerate(tokens[:-3]):
        if tok.string == "monkeypatch" and [t.string for t in tokens[i + 1:i + 4]] == [".", "undo", "("]:
            found.append(tok.start[0])
    return found


def test_no_test_undoes_every_patch_at_once():
    found = {p.name: lines for p in sorted(TESTS.rglob("*.py")) if (lines := _calls(p))}
    assert found == {}, found
