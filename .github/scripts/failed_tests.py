"""Name the failed tests in the job summary: each test's name and the first line of its error.

A long run's log can lose its failures. GitHub serves at most the last lines of a job's log, so a
test that failed early in a long browser run had no name anywhere (2026-11-09). Each test step keeps
a copy of its results, and this writes the failures to the job's summary page, which shows at the
top of the run.

  python .github/scripts/failed_tests.py junit  <file.xml>...   pytest's --junitxml
  python .github/scripts/failed_tests.py tap    <file.tap>...   node --test's TAP output
  python .github/scripts/failed_tests.py cargo  <file.txt>...   cargo test's output

A file that is missing (the step didn't get that far) is skipped. It always exits 0: the test step's
own exit code is what fails the job. The table goes to $GITHUB_STEP_SUMMARY, or stdout without it.
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET  # noqa: S405  # our own runners' result files, not someone else's XML
from pathlib import Path

MAX_ROWS = 50
MAX_TEXT = 200


def _first_line(text: str) -> str:
    """The error's first non-blank line. A line ending in ':' (an assertion's heading) takes the
    lines after it too, up to MAX_TEXT, since alone it says nothing."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    out = lines[0]
    if out.endswith(":"):
        for ln in lines[1:]:
            if len(out) >= MAX_TEXT:
                break
            out += " " + ln
    return out


def junit(path: Path) -> list[tuple[str, str]]:
    """pytest's --junitxml: each testcase with a failure or error child."""
    found = []
    for case in ET.parse(path).iter("testcase"):  # noqa: S314  # see the import
        for kind in ("failure", "error"):
            bad = case.find(kind)
            if bad is None:
                continue
            classname, name = case.get("classname", ""), case.get("name", "")
            module = classname.replace(".", "/") + ".py" if classname else ""
            found.append((f"{module}::{name}" if module else name,
                          _first_line(bad.get("message") or bad.text or kind)))
            break
    return found


_NOT_OK = re.compile(r"^(\s*)not ok \d+ - (.*)$")
_LOCATION = re.compile(r"^\s*location: '(.*)'$")
_ERROR = re.compile(r"^(\s*)error: (\|-?)?\s*'?(.*?)'?$")


def tap(path: Path) -> list[tuple[str, str]]:
    """node --test's TAP: each 'not ok' line, with the location and error from its YAML block. A
    parent that failed only because a subtest did is left out; the subtest is named."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = []
    for i, line in enumerate(lines):
        m = _NOT_OK.match(line)
        if not m:
            continue
        name, where, error = m.group(2).strip(), "", ""
        for j in range(i + 1, len(lines)):
            text = lines[j]
            if text.strip() == "...":
                break
            if (loc := _LOCATION.match(text)) and not where:
                where = loc.group(1)
            elif (err := _ERROR.match(text)) and not error:
                if err.group(2):  # a block: the lines indented under it
                    indent = len(err.group(1)) + 2
                    body = []
                    for k in range(j + 1, len(lines)):
                        if lines[k].strip() and len(lines[k]) - len(lines[k].lstrip()) < indent:
                            break
                        body.append(lines[k])
                    error = _first_line("\n".join(body))
                else:
                    error = err.group(3)
        if _SUBTESTS.match(error):
            continue
        found.append((f"{_short(where)} {name}" if where else name, error))
    return found


_SUBTESTS = re.compile(r"^\d+ subtests? failed$")


def _short(location: str) -> str:
    """A location from the repository root on: app/tests/browser/x.test.cjs:58."""
    loc = location.replace("\\", "/")
    for marker in ("/app/", "/vscode/", "/sidecar/"):
        if marker in loc:
            loc = marker[1:] + loc.split(marker, 1)[1]
            break
    return re.sub(r":\d+$", "", loc)  # the column


_CARGO_FAILED = re.compile(r"^test (\S+) \.\.\. FAILED$")
_CARGO_OUTPUT = re.compile(r"^---- (\S+) stdout ----$")
_PANIC = re.compile(r"panicked at (.+?):?$")


def cargo(path: Path) -> list[tuple[str, str]]:
    """cargo test's output: each 'test x ... FAILED', with the panic message from its stdout section."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    names = [m.group(1) for ln in lines if (m := _CARGO_FAILED.match(ln.strip()))]
    messages: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = _CARGO_OUTPUT.match(line.strip())
        if not m:
            continue
        for j in range(i + 1, min(i + 40, len(lines))):
            if _CARGO_OUTPUT.match(lines[j].strip()) or lines[j].strip() == "failures:":
                break
            if _PANIC.search(lines[j]):
                where = _PANIC.search(lines[j]).group(1)  # type: ignore[union-attr]  # matched above
                rest = [ln.strip() for ln in lines[j + 1:j + 4] if ln.strip()]
                messages[m.group(1)] = _first_line("\n".join(rest[:1])) + (f" ({where})" if where else "")
                break
    return [(name, messages.get(name, "")) for name in dict.fromkeys(names)]


READERS = {"junit": junit, "tap": tap, "cargo": cargo}


def _cell(text: str) -> str:
    text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace("`", "'") or " "


def table(found: list[tuple[str, str]], title: str) -> str:
    if not found:
        return ""
    rows = [f"| `{_cell(name)}` | {_cell(error)} |" for name, error in found[:MAX_ROWS]]
    more = f"\n\n{len(found) - MAX_ROWS} more failed; the job's log has them all." if len(found) > MAX_ROWS else ""
    return (f"### {_cell(title)}: {len(found)} failed\n\n| Test | First line of the error |\n| --- | --- |\n"
            + "\n".join(rows) + more + "\n")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in READERS:
        print(__doc__, file=sys.stderr)
        return 2
    kind, files = argv[0], [Path(a) for a in argv[1:]]
    found = []
    for f in files:
        if not f.is_file():
            continue
        try:
            found += READERS[kind](f)
        except (OSError, ET.ParseError) as exc:  # a half-written file from a crashed run: say so, fail nothing
            found.append((f.name, f"couldn't be read ({exc}); see the job's log"))
    text = table(found, os.environ.get("FAILED_TESTS_TITLE") or f"{kind} tests")
    if not text:
        return 0
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
