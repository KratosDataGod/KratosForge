# Visual asset manifest

Every image the identity depends on, where it lives, what it is for, where it came from and
whether it may ship. An asset not in this table is not approved for the app. Add a row in the
same pull request that adds the file. Rules are in the
[visual style guide](KRATOSFORGE_VISUAL_STYLE_GUIDE.md#generating-artwork).

**Status:** `approved` may ship in the app and docs; `reference` is for direction and review
only, never shipped in the app; `placeholder` is a stand-in that must be replaced before a
Stable build; `needed` does not exist yet.

## Canonical reference

| Asset | Path | Use | Source | Ownership and licence | Variants | Status |
| --- | --- | --- | --- | --- | --- | --- |
| The nine-panel storyboard | `docs/design/reference/kratosforge-storyboard.png` | The approved creative direction; attach to every image-generation request | Chris, 2026-10-22, generated to his brief | KratosBI; internal reference | `kratosforge-storyboard-web.jpg` (same size, JPEG); `reference/panels/01..09-*.jpg`, one per panel | reference |

## Brand marks (authoritative where they differ from the guide)

| Asset | Path | Use | Source | Ownership and licence | Variants | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Wolf and forge emblem | `docs/brand/wolf-forge-emblem-2048.png` | App icon, docs logo, the minimal wolf silhouette, the Titan reduced-motion still | KratosBI brand | KratosBI | `wolf-forge-emblem.png` (512), `wolf-forge-emblem-32.png`; copies in `app/ui/` | approved |
| KratosForge wordmark | `docs/brand/kratosforge-wordmark.png` | Launch screen, title bars, docs | KratosBI brand | KratosBI | `app/ui/kratosforge-lettering.png` | approved |
| KratosBI logo and mark | `docs/brand/kratosbi-logo.svg`, `docs/brand/kratosbi-mark.svg` | Company attribution; the navy and orange source values | KratosBI brand | KratosBI | none | approved |
| UI icon set | `app/ui/icons.js` | Rail, commands, status | Repository | KratosForge contributors, repository licence | none | approved |

## Painted art

| Asset | Path | Use | Source | Ownership and licence | Variants | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Wolf portrait, calm | storyboard panels 1 and 4 | Launch, SQL, the calm state | Storyboard crop | KratosBI; crop only, low resolution | needs a full-size original | placeholder |
| Wolf running in fire | storyboard panels 2 and 9 | Notebook execution, navigation streak | Storyboard crop | as above | needs a full-size original | placeholder |
| Wolf howling | storyboard panel 7 | Achievements, the Titan | Storyboard crop | as above | needs a full-size original | placeholder |
| Wolf snarling, red | storyboard panel 8 | Error states (still only) | Storyboard crop | as above | needs a full-size original | placeholder |
| Kratos at the anvil | storyboard panel 5 | Deployment, the Titan | Storyboard crop | as above | needs a full-size original | placeholder |
| Hammer on anvil, sparks | storyboard panel 6 | Optimization, the Forge strike still, the Titan | Storyboard crop | as above | needs a full-size original | placeholder |
| Wolf emerging from forge energy | none | Launch at Expressive, onboarding | to generate from the [character sheet](wolf-character-sheet.md) | | | needed |
| Wolf celebrating | none | Achievement badges below Titan | to generate | | | needed |
| Wolf warning | none | Warnings that are not failures (quota, Preview) | to generate | | | needed |
| Wolf, minimal silhouette | emblem | Favicons, tray, 16 to 32 px | brand | KratosBI | | approved |
| Wolf, monochrome ink | none | Print, dark-on-light docs, watermark | to generate | | | needed |

## Examples and samples

| Asset | Path | Use | Source | Ownership and licence | Status |
| --- | --- | --- | --- | --- | --- |
| Forge motion samples page | `docs/design/examples/forge-motion-samples.html` | A playable sample of every category and the Titan; opens in any browser, no build | This repository; art embedded from the storyboard crops | KratosForge; the embedded art is placeholder | placeholder art, final motion |
| Titan key frames | `docs/design/examples/titan-first-deployment-frames.png` | Review reference for the achievement Titan | Rendered from the sample page | KratosForge | reference |
| Sample page overview | `docs/design/examples/forge-motion-samples-page.png` | Review reference | Rendered from the sample page | KratosForge | reference |

## How to add an asset

1. Generate or commission it with the instruction in the guide, the storyboard attached, and the
   relevant character-sheet pose attached.
2. Save the original (PNG, 2000 px or more on the long edge, transparent or flat `#0B1420`) under
   `docs/design/art/<subject>/` and the shipping version (WebP, under 150 KB) under `app/ui/art/`.
3. Add a row here with the prompt or brief, the generator or artist, the licence and the status.
4. Answer the eight consistency checks in the pull request's Visual identity line.
