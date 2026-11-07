"""Make the WebP files for the page header art in app/ui/art/experience/heroes.js.

Run by hand when new art is added, never in CI. Needs Pillow (pip install pillow):

    python sidecar/tools/hero_art.py            # make any WebP that is missing
    python sidecar/tools/hero_art.py --force    # make them all again
    python sidecar/tools/hero_art.py --check    # only report what is missing or wrong

For each manifest entry it reads Chris's original PNG (left unchanged), checks it is 3:1 and at least
2172 px wide, and writes <file>.webp at 2172 x 724 and <file>-1086.webp at 1086 x 362, quality 82,
the two widths the app's srcset names. Nothing is cropped or stretched. docs/design/art/README.md has the whole how-to.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "app" / "ui" / "art" / "experience"
MANIFEST = ART / "heroes.js"
QUALITY = 82
SIZES = (("", (2172, 724)), ("-1086", (1086, 362)))


def entries() -> list[dict]:
    """The manifest: plain JSON after the line starting 'const HERO_ART = '."""
    m = re.search(r"^const HERO_ART = (\[.*\]);\s*$", MANIFEST.read_text(encoding="utf-8"), re.M | re.S)
    if not m:
        raise SystemExit(f"{MANIFEST} has no const HERO_ART = [ ... ];")
    return json.loads(m.group(1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="make every WebP again")
    parser.add_argument("--check", action="store_true", help="report only; write nothing")
    args = parser.parse_args()
    from PIL import Image

    problems = 0
    for e in entries():
        original = ROOT / e["original"]
        if not original.is_file():
            print(f"{e['id']}: original {e['original']} is missing")
            problems += 1
            continue
        with Image.open(original) as im:
            width, height = im.size
            if abs(width / height - 3) >= 0.02 or width < 2172:
                print(f"{e['id']}: {width} x {height} is not 3:1 at 2172 px wide or more")
                problems += 1
                continue
            rgb = im.convert("RGB")
            for suffix, size in SIZES:
                out = ART / f"{e['file']}{suffix}.webp"
                if out.is_file() and not args.force:
                    continue
                if args.check:
                    print(f"{e['id']}: {out.relative_to(ROOT)} is missing")
                    problems += 1
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                img = rgb if size == (width, height) else rgb.resize(size, Image.LANCZOS)
                img.save(out, "WEBP", quality=QUALITY, method=6)
                print(f"{e['id']}: wrote {out.relative_to(ROOT)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
