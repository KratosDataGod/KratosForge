# Page header art: adding next month's pictures

Every page with a header (Home, Create, Workspaces, Platform, Data mesh, Optimize, Monitor, Academy,
Source control, Settings and Profile) has its own pool of pictures. A page only ever shows a picture
from its own pool. Each pool is meant to grow to 5 to 13 pictures, with new ones added monthly.
Adding a picture changes data only, never code.

## How a page picks

The pick is stable for a day. Each page shows one picture from its pool all day and may show
another tomorrow, so the picture never changes while someone works. It also never repeats across
pages, because each pool holds only that page's pictures. Within a pool, the day and the page's
name choose the picture (`heroArtPick` in `app/ui/home-hero.js`).

- Only pictures that are **live** today take part: on or after `published`, and before `retire`.
- If no picture in the pool is live, the page shows its first picture.
- A page with no pool keeps its plain heading.
- The header art only shows with Preview features on, and never in Professional.

## Rules every picture follows

These are Chris's approved-artwork rules. The tests check the parts marked *(test)*.

- Chris approves it first. Never crop from a composite mockup or a reference sheet.
- It is 3:1, at least 2172 × 724 *(test)*, with the characters on the right and empty navy sky on
  the left.
- The original is kept unchanged under `docs/design/art/<page>/<id>.png` *(test)*.
- The app uses two WebP files made from the original, `<id>.webp` at 2172 px and `<id>-1086.webp`
  at 1086 px, in `app/ui/art/experience/<page>/` *(test)*.
- The picture is never stretched or cropped: it always shows whole at the band's full height.
- The page's title and buttons are real UI on the empty sky, never part of the picture. The `sky`
  value records how far the sky reaches. It must be at least 0.27 *(test)*, and the browser test
  checks that the text ends before it at 12 window widths.
- Nothing in the picture moves. Painted words, icons or labels in it (branch names, gear icons) are
  decoration. No UI depends on them.
- `alt` is `""`, because the picture is decoration beside the page's own h1. `description` says
  what it shows *(test)*.
- It has a row in [assets.md](../assets.md) *(test)*.

## Steps

1. Put Chris's PNG at `docs/design/art/<page>/<page>-hero-v<N>.png`, unchanged. `<page>` is one of
   `home`, `create`, `workspace`, `platform`, `datamesh`, `optimize`, `monitor`, `academy`,
   `source`, `settings` or `profile`.
2. Measure the sky. Open the PNG and find the first column, from the left, where the characters or
   props start (cape, sparks, table, rock). Divide that x by the width, then subtract about 0.01.
   Background mountains and clouds may sit under the text.
3. Add one entry to `app/ui/art/experience/heroes.js`:

   ```json
   {
     "page": "create",
     "id": "create-hero-v2",
     "file": "create/create-hero-v2",
     "original": "docs/design/art/create/create-hero-v2.png",
     "sky": 0.32,
     "alt": "",
     "description": "Kratos and the wolf at the drafting table",
     "published": "2026-12-01"
   }
   ```

   Use `published` to start a picture on the first of the month. Use `retire` (YYYY-MM-DD) to stop
   one; it is no longer shown from that day on.
4. Make the WebP files: `python sidecar/tools/hero_art.py` (needs `pip install pillow`).
5. Add its row to [assets.md](../assets.md), with the same original path.
6. Run the tests and look at the screenshots:

   ```sh
   node --test app/tests/browser/hero-art-manifest.test.cjs
   SCREENSHOT_DIR=shots NODE_PATH="$(npm root -g)" node --test app/tests/browser/page-heroes.test.cjs
   ```

   `page-heroes` checks every picture in every pool at 12 widths and saves `shots/<id>-<width>.png`.
   Home's own checks are in `home-living.test.cjs`.
7. Open one pull request for the month's pictures. UX signs off on the screenshots before it merges.
