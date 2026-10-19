"""No new silent broad exception handlers in the engine (HD-055, finding AR-4).

A broad handler (``except:``, ``except Exception``, ``except BaseException``, or a tuple naming one
of them) whose body only passes, continues, breaks or returns a constant hides failures: nothing
reaches the log, so a problem a user reports can't be traced. Such a handler must log, narrow the
exception type, or re-raise.

Where silence really is right (best-effort cleanup, a probe whose failure means "no"), mark the
``except`` line with ``# silent-ok: <reason>``. The reason is required, so a reviewer sees why.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
BROAD = {"Exception", "BaseException"}
MARKER = re.compile(r"#\s*silent-ok\b:?(?P<reason>.*)$")
MIN_REASON = 10  # characters: "best effort" passes, "ok" or "todo" doesn't


def _is_broad(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in BROAD
    if isinstance(node, ast.Attribute):
        return node.attr in BROAD
    if isinstance(node, ast.Tuple):
        return any(_is_broad(e) for e in node.elts)
    return False


def _is_constant(node: ast.expr | None) -> bool:
    if node is None or isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant)


def _is_silent(body: list[ast.stmt]) -> bool:
    for stmt in body:
        if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        if isinstance(stmt, ast.Return) and _is_constant(stmt.value):
            continue
        return False
    return True


def silent_handlers(source: str) -> list[tuple[int, str]]:
    """(line, problem) for each silent broad handler in ``source`` that lacks a valid marker."""
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.ExceptHandler) and _is_broad(node.type) and _is_silent(node.body)):
            continue
        # The marker goes on the ``except ...:`` line; it may wrap, so look up to the body's first line.
        header = lines[node.lineno - 1:node.body[0].lineno - 1] or [lines[node.lineno - 1]]
        marks = [m for m in (MARKER.search(line) for line in header) if m]
        if not marks:
            found.append((node.lineno, "silent broad except: log, narrow or re-raise "
                                       "(or add '# silent-ok: <reason>')"))
        elif len(marks[0].group("reason").strip()) < MIN_REASON:
            found.append((node.lineno, "'# silent-ok:' needs a reason"))
    return sorted(found)


def test_no_silent_broad_except_in_engine():
    problems = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for line, problem in silent_handlers(path.read_text(encoding="utf-8")):
            problems.append(f"{path.relative_to(PACKAGE.parent).as_posix()}:{line}: {problem}")
    assert not problems, f"{len(problems)} silent broad exception handler(s):\n" + "\n".join(problems)


@pytest.mark.parametrize("handler", [
    "except:\n    pass",
    "except Exception:\n    pass",
    "except BaseException:\n    continue",
    "except (ValueError, Exception):\n    return None",
    "except builtins.Exception as e:\n    return {}",
    "except Exception:\n    '''why'''\n    return -1",
])
def test_check_flags_silent_handlers(handler):
    source = "def f():\n  for _ in x:\n    try:\n        g()\n    " + handler.replace("\n", "\n    ") + "\n"
    assert len(silent_handlers(source)) == 1


@pytest.mark.parametrize("handler", [
    "except Exception:\n    log.exception('g failed')",
    "except Exception:\n    raise",
    "except ValueError:\n    pass",
    "except OSError:\n    return None",
    "except Exception as e:\n    return str(e)",
    "except Exception:  # silent-ok: best-effort cleanup of a temp file\n    pass",
])
def test_check_allows_handled_or_marked(handler):
    source = "def f():\n  for _ in x:\n    try:\n        g()\n    " + handler.replace("\n", "\n    ") + "\n"
    assert silent_handlers(source) == []


@pytest.mark.parametrize("comment", ["# silent-ok", "# silent-ok:", "# silent-ok: ok"])
def test_marker_needs_a_reason(comment):
    source = f"try:\n    g()\nexcept Exception:  {comment}\n    pass\n"
    assert silent_handlers(source) == [(3, "'# silent-ok:' needs a reason")]


def test_engine_log_lines_reach_stderr_without_secrets(monkeypatch, capsys):
    import logging

    from fabric_desktop import server
    logger = logging.getLogger("fabric_desktop")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logging.NOTSET)
    server._log_to_stderr()
    server._log_to_stderr()  # a second call adds no second handler
    assert len(logger.handlers) == 1
    logging.getLogger("fabric_desktop.schedule_service").info("read failed: Bearer eyJhbGciOiJIUzI1NiJ9.e30.abc")
    err = capsys.readouterr().err
    assert "INFO fabric_desktop.schedule_service: read failed" in err and "eyJhbGciOiJIUzI1NiJ9" not in err


def test_describe_survives_an_unreadable_message():
    from fabric_desktop.redact import describe

    class Odd(Exception):
        def __str__(self):
            raise RuntimeError("no")

    assert describe(Odd()) == "Odd: (message unreadable)"
