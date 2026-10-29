"""Tests for the CI type check's ratchet (.github/scripts/mypy_ratchet.py). mypy itself isn't run here."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mypy_ratchet", ROOT / ".github" / "scripts" / "mypy_ratchet.py")
mypy_ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mypy_ratchet)

OUTPUT = """\
fabric_desktop/cli.py:223: error: Incompatible types in assignment (expression has type "int", variable has type "str")  [assignment]
fabric_desktop/cli.py:223: note: See https://mypy.readthedocs.io
fabric_desktop/cli.py:301:5: error: Incompatible types in assignment  [assignment]
fabric_desktop\\agent.py:12: error: "Foo" has no attribute "bar"  [attr-defined]
fabric_desktop/agent.py:40: error: An error without a code
"""


def test_errors_are_counted_per_file_and_code_and_notes_are_not():
    assert mypy_ratchet.count(OUTPUT) == {"fabric_desktop/agent.py": {"attr-defined": 1, "misc": 1},
                                          "fabric_desktop/cli.py": {"assignment": 2}}


def test_more_errors_of_a_kind_fail_and_fewer_are_only_reported():
    baseline = {"fabric_desktop/cli.py": {"assignment": 2, "arg-type": 3}}
    now = {"fabric_desktop/cli.py": {"assignment": 3, "arg-type": 1}, "fabric_desktop/new.py": {"misc": 1}}
    worse, better = mypy_ratchet.compare(now, baseline)
    assert worse == ["fabric_desktop/cli.py: 3 [assignment] errors, 2 before", "fabric_desktop/new.py: 1 [misc] errors, 0 before"]
    assert better == ["fabric_desktop/cli.py: 1 [arg-type] errors, 3 before"]
    assert mypy_ratchet.compare(baseline, baseline) == ([], [])


def test_the_baseline_is_a_count_per_file_and_code():
    baseline = json.loads((ROOT / "sidecar" / "mypy-baseline.json").read_text(encoding="utf-8"))
    assert baseline and all(f.startswith("fabric_desktop/") and "_vendor" not in f for f in baseline)
    assert all(isinstance(n, int) and n > 0 for codes in baseline.values() for n in codes.values())
