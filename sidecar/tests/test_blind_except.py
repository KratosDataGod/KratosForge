"""Every broad handler ruff's BLE rule allows is a reviewed one, with its reason on the line.

ruff runs with BLE on (pyproject.toml), so a new ``except Exception`` that doesn't re-raise fails lint
unless it is marked ``# noqa: BLE001 - <reason>``. This test keeps the reason required, so the marks
stay a list a reviewer can read: ``grep -rn "BLE001"``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

SIDECAR = Path(__file__).resolve().parents[1]
# BLE001 anywhere in a noqa list (`noqa: BLE001`, `noqa: E501, BLE001, S110`); the reason follows the list.
CODE = r"[A-Z]+[0-9]+"
MARK = re.compile(rf"#\s*noqa:\s*(?:{CODE}\s*,\s*)*BLE001\b(?:\s*,\s*{CODE})*(?P<rest>.*)$")
REASON = re.compile(r"^\s*(?:-|#\s*silent-ok:)\s*(?P<reason>.+)$")
MIN_REASON = 10  # characters, as tests/test_silent_except.py asks of silent-ok


def _missing_reason(line: str) -> bool:
    mark = MARK.search(line)
    if mark is None:
        return False
    reason = REASON.match(mark.group("rest"))
    return reason is None or len(reason.group("reason").strip()) < MIN_REASON


def test_ruff_checks_blind_excepts():
    config = tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))
    assert "BLE" in config["tool"]["ruff"]["lint"]["select"]


def test_every_blind_except_mark_says_why():
    bare = []
    for path in sorted(SIDECAR.rglob("*.py")):
        if path.name == Path(__file__).name or any(part in {".venv", "build", "node_modules"} for part in path.parts):
            continue  # this file's own examples of bad marks
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _missing_reason(line):
                bare.append(f"{path.relative_to(SIDECAR).as_posix()}:{number}")
    assert not bare, "mark each as `# noqa: BLE001 - <why a broad except is right here>`:\n" + "\n".join(bare)


@pytest.mark.parametrize("line", [
    "except Exception:  # noqa: BLE001",
    "except Exception:  # noqa: BLE001 - ok",
    "except Exception:  # noqa: BLE001 because",
    "except Exception:  # noqa: E501, BLE001",
    "except Exception:  # noqa: BLE001, S110  ok",
])
def test_a_mark_without_a_reason_is_caught(line):
    assert _missing_reason(line)


@pytest.mark.parametrize("line", [
    "except Exception:  # noqa: BLE001 - shown to the person as the reason",
    "except Exception:  # noqa: BLE001  # silent-ok: best-effort cleanup of a temp file",
    "except Exception:  # noqa: BLE001, S110  # silent-ok: wrong arguments mean the call exists",
    "except Exception:  # noqa: E501, BLE001 - shown to the person as the reason",
    "except Exception:  # noqa: B023",
])
def test_a_mark_with_a_reason_passes(line):
    assert not _missing_reason(line)
