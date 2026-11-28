"""Check each Academy pack's study guide against its registry (ACD-020).

Run by .github/workflows/academy-guide-check.yml on a schedule, from the sidecar folder:

    python tools/academy_guide_check.py --out <folder> [--page <pack>=<saved page.html> ...]

For each pack whose guide is a Learn page, it fetches the page (or reads the saved copy given with
--page, for tests) and writes into --out:

    summary.json                 {changed, failed, <pack>: {status, detail}}; status is changed,
                                 unchanged, failed or not_checked
    <pack>.md                    the draft PR body, for a changed pack only
    <pack>.crosswalk.json        the draft crosswalk (old objective id -> new ids), for a changed pack only

It always exits 0 once it has written summary.json: a page that can't be fetched or read is reported
there ("failed"), and the workflow says so in its summary instead of failing. Nothing in the repo is
changed; the workflow opens a draft PR with the report for people to act on.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fabric_desktop.academy import guide_check, registry  # noqa: E402

TIMEOUT = 30


def fetch(url: str) -> str:
    """The page's text, read up to guide_check.MAX_PAGE_BYTES; only Learn pages over HTTPS."""
    if not url.startswith(guide_check.LEARN_HOST):
        raise ValueError(f"{url} isn't a Learn page")
    req = urllib.request.Request(url, headers={"User-Agent": "academy-guide-check", "Accept": "text/html"})  # noqa: S310 - https Learn only, checked above
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - the https Learn host only, checked above
        if not resp.geturl().startswith(guide_check.LEARN_HOST):
            raise ValueError("the page redirected away from Learn")
        raw = resp.read(guide_check.MAX_PAGE_BYTES + 1)
    if len(raw) > guide_check.MAX_PAGE_BYTES:
        raise ValueError("the page is over 4 MB")
    return raw.decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--page", action="append", default=[], help="<pack>=<saved page.html>, instead of fetching")
    args = ap.parse_args(argv)
    saved = dict(p.split("=", 1) for p in args.page)
    args.out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {"changed": False, "failed": False}
    for pid in registry.pack_ids():
        cert = registry.certification(pid)
        if not guide_check.checkable(cert):
            summary[pid] = {"status": "not_checked", "detail": "Its guide isn't a Learn page; check it by hand."}
            continue
        try:
            html = Path(saved[pid]).read_text(encoding="utf-8") if pid in saved else fetch(cert["source"]["url"])
            got = guide_check.check(cert, html)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            summary[pid] = {"status": "failed", "detail": f"The study guide couldn't be checked: {exc}"}
            summary["failed"] = True
            continue
        if got["changed"]:
            (args.out / f"{pid}.md").write_text(got["report"], encoding="utf-8")
            (args.out / f"{pid}.crosswalk.json").write_text(
                json.dumps({cert["version"]: got["diff"]["crosswalk"]}, indent=2) + "\n", encoding="utf-8")
            summary[pid] = {"status": "changed", "detail": f"The page (updated {got['updated']}) differs from the registry."}
            summary["changed"] = True
        else:
            summary[pid] = {"status": "unchanged", "detail": "The page's skills list matches the registry."}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
