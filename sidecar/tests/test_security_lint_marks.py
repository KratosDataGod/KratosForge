"""Every use of a risky call that ruff's security rules allow in the engine is a reviewed one, with its
reason on the line.

pyproject.toml turns on the bandit rules that find real risks (exec and eval, shell=True, weak hashes
and so on). A new use fails lint unless it is marked ``# noqa: S102 - <reason>``. This test keeps the
reason required, so ``grep -rn "noqa: S" fabric_desktop`` is the list a reviewer reads.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

SIDECAR = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))
SELECTED = [code for code in CONFIG["tool"]["ruff"]["lint"]["select"] if re.fullmatch(r"S\d+", code)]
CODE = r"[A-Z]+[0-9]+"
LIST = re.compile(rf"#\s*noqa:\s*(?P<codes>{CODE}(?:\s*,\s*{CODE})*)(?P<rest>.*)$")
REASON = re.compile(r"^\s*-\s*(?P<reason>.+)$")
MIN_REASON = 10  # characters, as tests/test_blind_except.py asks


def _missing_reason(line: str, codes: list[str]) -> bool:
    mark = LIST.search(line)
    if mark is None or not set(re.split(r"\s*,\s*", mark.group("codes"))) & set(codes):
        return False
    reason = REASON.match(mark.group("rest"))
    return reason is None or len(reason.group("reason").strip()) < MIN_REASON


def test_ruff_checks_the_security_rules():
    for code in ("S102", "S307", "S324", "S501", "S506", "S602", "S604"):
        assert code in SELECTED
    assert "ISC004" in CONFIG["tool"]["ruff"]["lint"]["select"]


def test_every_security_mark_in_the_engine_says_why():
    bare = []
    for path in sorted((SIDECAR / "fabric_desktop").rglob("*.py")):
        if "_vendor" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _missing_reason(line, SELECTED):
                bare.append(f"{path.relative_to(SIDECAR).as_posix()}:{number}")
    assert not bare, "mark each as `# noqa: <code> - <why this use is safe here>`:\n" + "\n".join(bare)


@pytest.mark.parametrize("line", [
    "exec(body, ns)  # noqa: S102",
    "exec(body, ns)  # noqa: S102 - ok",
    "eval(x)  # noqa: E501, S307",
    "run(c, shell=True)  # noqa: S604 because",
])
def test_a_mark_without_a_reason_is_caught(line):
    assert _missing_reason(line, SELECTED)


@pytest.mark.parametrize("line", [
    "exec(body, ns)  # noqa: S102 - the person's own cell",
    "eval(x)  # noqa: E501, S307 - the cell's last expression",
    "subprocess.Popen(args)  # noqa: S603",  # not one of the selected rules
    "except Exception:  # noqa: BLE001 - shown to the person as the reason",
])
def test_a_mark_with_a_reason_or_another_rule_passes(line):
    assert not _missing_reason(line, SELECTED)
