# E49: unit tests for the silver transforms, run in CI before the bundle deploys. Run it with Run,
# check with Check my work.
#
# Each test is a function named test_... that takes t, the transforms module under test, and asserts
# on what its functions return. Write tests that pass on today's transforms.py and fail on each
# release that broke prod (transforms_v41.py, transforms_v42.py, transforms_v43.py).


def test_clean_amount_reads_thousands(t):
    assert t.clean_amount("1,234.50") == 1234.5


def test_blank_amount_stays_missing(t):
    assert t.clean_amount("") is None
    assert t.clean_amount("   ") is None
    assert t.clean_amount(None) is None


def test_region_is_upper_case(t):
    assert t.region_code("uk") == "UK"
    assert t.region_code("") == "UNKNOWN"


def test_latest_row_wins_per_order(t):
    rows = [{"order_id": 2, "updated_at": "2026-03-01T09:00:00Z", "status": "placed"},
            {"order_id": 1, "updated_at": "2026-03-01T08:00:00Z", "status": "placed"},
            {"order_id": 2, "updated_at": "2026-03-02T10:00:00Z", "status": "shipped"}]
    out = t.latest_per_order(rows)
    assert [r["order_id"] for r in out] == [1, 2]
    assert out[1]["status"] == "shipped"


# ---- The CI test step. Don't change anything below this line. --------------------------------
import json  # noqa: E402
import os  # noqa: E402
from types import SimpleNamespace  # noqa: E402

UNDER_TEST = "transforms.py"
BAD_RELEASES = {"v41": "transforms_v41.py", "v42": "transforms_v42.py", "v43": "transforms_v43.py"}


def _load(path):
    scope = {"__name__": "transforms"}
    with open(path, encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"), scope)  # noqa: S102 - the exercise's own module files
    return SimpleNamespace(**scope)


def _run(path, tests):
    module, failures = _load(path), []
    for name, test in tests:
        try:
            test(module)
        except Exception as exc:  # noqa: BLE001 - a test that errors is a failed test, as in pytest
            failures.append(f"{name}: {type(exc).__name__} {exc}".strip())
    return {"passed": len(tests) - len(failures), "failed": len(failures), "failures": failures}


_tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
_report = {"tests": len(_tests), "current": _run(UNDER_TEST, _tests),
           "releases": {k: _run(p, _tests) for k, p in BAD_RELEASES.items()}}
_report["deploy"] = _report["tests"] > 0 and _report["current"]["failed"] == 0
os.makedirs("out", exist_ok=True)
with open("out/test_report.json", "w", encoding="utf-8") as f:
    json.dump(_report, f, indent=2)
print(f"{_report['tests']} tests on {UNDER_TEST}: {_report['current']['passed']} passed, "
      f"{_report['current']['failed']} failed.")
for _f in _report["current"]["failures"]:
    print("  FAILED", _f)
for _k, _r in _report["releases"].items():
    print(f"Release {_k}: {'caught' if _r['failed'] else 'NOT caught'} ({_r['failed']} of {_report['tests']} tests failed).")
print("OK to deploy." if _report["deploy"] else "Deploy blocked: a test failed on today's code.")
