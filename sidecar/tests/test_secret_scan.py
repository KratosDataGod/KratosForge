"""CI's secret scan (.github/scripts/secret_scan.py): a finding the reviewed baseline doesn't list fails
the build, every baseline entry has been reviewed, and the scanner is installed pinned by hash."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("secret_scan", ROOT / ".github" / "scripts" / "secret_scan.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


scan = _load()


def _secret(kind: str, digest: str, line: int) -> SimpleNamespace:
    return SimpleNamespace(type=kind, secret_hash=digest, line_number=line)


def _doc(**rows: list[dict]) -> dict:
    return {"results": {name: [{"hashed_secret": r["hash"], "type": r["type"], "line_number": r.get("line", 1),
                                **({"is_secret": r["is_secret"]} if "is_secret" in r else {})} for r in entries]
                        for name, entries in rows.items()}}


def test_a_reviewed_finding_passes_wherever_its_line_moves():
    doc = _doc(**{"tests/t.py": [{"type": "Secret Keyword", "hash": "aaa", "line": 3, "is_secret": False}]})
    assert scan.problems(doc, [("tests/t.py", _secret("Secret Keyword", "aaa", 40))]) == []


def test_a_new_finding_fails_naming_where_and_what_kind_but_not_the_value():
    doc = _doc(**{"tests/t.py": [{"type": "Secret Keyword", "hash": "aaa", "is_secret": False}]})
    found = [("app/x.js", _secret("AWS Access Key", "bbb", 7)),  # a new value
             ("tests/u.py", _secret("Secret Keyword", "aaa", 2)),  # a reviewed value, but in another file
             ("tests/t.py", _secret("Base64 High Entropy String", "aaa", 1))]  # another kind
    out = scan.problems(doc, found)
    assert [line.split("::")[1] for line in out] == ["error file=app/x.js,line=7", "error file=tests/t.py,line=1",
                                                     "error file=tests/u.py,line=2"]
    assert "Looks like a secret (AWS Access Key)" in out[0] and "bbb" not in out[0]


def test_an_entry_nobody_reviewed_or_marked_a_secret_fails():
    doc = _doc(**{"a.py": [{"type": "Secret Keyword", "hash": "aaa", "line": 5}],
                  "b.py": [{"type": "Secret Keyword", "hash": "bbb", "is_secret": True}]})
    out = scan.problems(doc, [("a.py", _secret("Secret Keyword", "aaa", 5))])
    assert len(out) == 3 and out[0].startswith("::error file=a.py,line=5::.secrets.baseline lists a Secret Keyword")
    assert out[1].startswith("::error file=b.py,line=1::") and out[2].startswith("::error file=a.py,line=5::Looks like")


def test_every_baseline_entry_is_reviewed_and_the_scan_has_its_plugins():
    doc = json.loads((ROOT / ".secrets.baseline").read_text(encoding="utf-8"))
    assert doc["plugins_used"], "a baseline written outside detect-secrets' settings scans with nothing"
    rows = [entry for entries in doc["results"].values() for entry in entries]
    assert rows and all(entry.get("is_secret") is False for entry in rows)
    assert scan.problems(doc, []) == []


def test_the_scanner_is_installed_pinned_by_hash():
    pins = (ROOT / ".github" / "requirements" / "secret-scan.txt").read_text(encoding="utf-8")
    packages = re.findall(r"^([a-z0-9-]+)==\S+ \\\n((?: +--hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)", pins, re.M)
    assert "detect-secrets" in [name for name, _ in packages]
    assert len(packages) == len(re.findall(r"^[a-z0-9-]+==", pins, re.M)), "every package has its hashes"


def test_ci_scans_a_pull_requests_changed_files_and_main_in_full_before_preview_publishes():
    job = CI.split("\n  secrets:\n", 1)[1].split("\n\n  ", 1)[0]
    assert "--require-hashes -r .github/requirements/secret-scan.txt" in job
    assert 'secret_scan.py added "$RUNNER_TEMP/base-baseline"' in job and "HEAD^1 HEAD | python .github/scripts/secret_scan.py pragmas" in job
    assert "git diff -z --name-only --diff-filter=d HEAD^1 HEAD" in job and "fetch-depth: 2" in job
    assert "xargs -0 python .github/scripts/secret_scan.py check" in job
    assert "            python .github/scripts/secret_scan.py check\n" in job, "a push to main scans every tracked file"
    assert "needs:" not in job, "docs-only changes are scanned too"
    release = CI.split("\n  release:\n", 1)[1]
    assert re.search(r"needs: \[[^\]]*\bsecrets\b", release)


def test_a_file_name_cant_break_out_of_its_error_line():
    out = scan.problems({"results": {}}, [("a,b:c\n::error::x%.py", _secret("Secret Keyword", "aaa", 1))])
    assert out[0].startswith("::error file=a%2Cb%3Ac%0A%3A%3Aerror%3A%3Ax%25.py,line=1::Looks like a secret")


def test_a_pull_request_that_adds_to_the_allow_list_says_so():
    before = _doc(**{"a.py": [{"type": "Secret Keyword", "hash": "aaa", "is_secret": False}]})
    after = _doc(**{"a.py": [{"type": "Secret Keyword", "hash": "aaa", "line": 9, "is_secret": False}],
                    "b.py": [{"type": "AWS Access Key", "hash": "bbb", "line": 4, "is_secret": False}]})
    assert scan.newly_allowed(before, before) == []
    assert scan.newly_allowed(after, before) == [
        "::warning file=b.py,line=4::This pull request adds a AWS Access Key here to .secrets.baseline, so the "
        "secret scan lets it through. Check it isn't a real secret."]
    assert len(scan.newly_allowed(after, {})) == 2, "no baseline on the base branch: every entry is new"


def test_a_pull_request_that_adds_the_skip_pragma_says_so():
    diff = ("diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -3,0 +4,2 @@ def f():\n"
            "+x = 1\n+key = 'v'  # pragma: allowlist secret\n"
            "--- a/u.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old = 1  # pragma: allowlist secret\n"
            '--- a/w y.py\n+++ "b/w\\ty.py"\n@@ -0,0 +1 @@\n+k = 2  # pragma: allowlist secret\n')
    out = scan.pragmas(diff)
    assert [line.split("::")[1] for line in out] == ["warning file=t.py,line=5", "warning file=w\\ty.py,line=1"]
    assert scan.pragmas("--- a/t.py\n+++ b/t.py\n@@ -1 +1 @@\n-a\n+b\n") == []


def test_an_added_line_that_looks_like_a_header_cant_hide_a_pragma():
    diff = ("diff --git a/n.txt b/n.txt\nnew file mode 100644\n--- /dev/null\n+++ b/n.txt\n@@ -0,0 +1,4 @@\n"
            "+++ hidden\n+++ b/decoy.txt\n+@@ -1 +1 @@\n+token = 'abc'  # pragma: allowlist secret\n"
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -2 +2 @@\n-a = 1\n\\ No newline at end of file\n"
            "+a = 2  # pragma: allowlist secret\n")
    out = scan.pragmas(diff)
    assert [line.split("::")[1] for line in out] == ["warning file=n.txt,line=4", "warning file=m.py,line=2"]
