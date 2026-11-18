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
| Home hero, Kratos and the wolf at dawn | `docs/design/art/home/home-hero-v1.png` (2172 × 724) | Home hero (Living Experience, Preview): characters on the right, text-safe navy on the left | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/home/home-hero-v1.webp` (full size), `home-hero-v1-1086.webp` (half) | approved |
| Create header, Kratos and the wolf over the blueprint | `docs/design/art/create/create-hero-v1.png` (2172 × 724) | Create page header (Living Experience, Preview): characters and blueprint on the right, text-safe navy on the left (the cape starts near 31%) | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/create/create-hero-v1.webp` (full size), `create-hero-v1-1086.webp` (half) | approved |
| Workspaces header, the wolf on the rock in a blue cape | `docs/design/art/workspace/workspace-hero-v1.png` (2172 × 724) | Workspaces page header (Living Experience, Preview): the wolf and its cape on the right, text-safe navy on the left (the grass starts near 45%) | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/workspace/workspace-hero-v1.webp` (full size), `workspace-hero-v1-1086.webp` (half) | approved |
| Platform header, Kratos and the wolf beside the island network | `docs/design/art/platform/platform-hero-v1.png` (2172 × 724) | Platform page header (Living Experience, Preview): Kratos, the wolf and the floating island network on the right, text-safe navy on the left (the islands start near 43%); static, the network never animates | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/platform/platform-hero-v1.webp` (full size), `platform-hero-v1-1086.webp` (half) | approved |
| Data mesh header, Kratos forging armour on the wolf-crest anvil | `docs/design/art/datamesh/datamesh-hero-v1.png` (2172 × 724) | Data mesh page header (Living Experience, Preview): Kratos, the anvil, the armour and the wolf on the right, text-safe navy on the left (the sparks start near 43%); static, the sparks never move | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/datamesh/datamesh-hero-v1.webp` (full size), `datamesh-hero-v1-1086.webp` (half) | approved |
| Optimize header, Kratos sharpening the blade on the wolf-crest grindstone | `docs/design/art/optimize/optimize-hero-v1.png` (2172 × 724) | Optimize page header (Living Experience, Preview): Kratos, the blade, the grindstone and the wolf on the right, text-safe navy on the left (the sparks start near 44%); static, the sparks never move | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/optimize/optimize-hero-v1.webp` (full size), `optimize-hero-v1-1086.webp` (half) | approved |
| Monitor header, the caped wolf above the sunrise valley | `docs/design/art/monitor/monitor-hero-v1.png` (2172 × 724) | Monitor page header (Living Experience, Preview): the wolf and its cape on the right, text-safe navy on the left (the valley starts near 34%); not shown with the Forge Activity band or the docked Fabric hub | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/monitor/monitor-hero-v1.webp` (full size), `monitor-hero-v1-1086.webp` (half) | approved |
| Academy header, Kratos reading the wolf codex by the forge | `docs/design/art/academy/academy-hero-v1.png` (2172 × 724) | Academy page header (Living Experience, Preview): Kratos, the codex and the stacked books on the right, text-safe navy on the left (the stone ledge starts near 32%); static, the glow never moves | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/academy/academy-hero-v1.webp` (full size), `academy-hero-v1-1086.webp` (half) | approved |
| Source control header, Kratos and the wolf at the branch-graph table | `docs/design/art/source/source-hero-v1.png` (2172 × 724) | Source control page header (Living Experience, Preview): Kratos, the wolf and the whole graph table on the right, text-safe navy on the left (the table starts near 32%); static, the glow never moves. The painted branch names are decoration only: no UI reads them and the alt text stays empty | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/source/source-hero-v1.webp` (full size), `source-hero-v1-1086.webp` (half) | approved |
| Settings header, Kratos raising the wolf gear | `docs/design/art/settings/settings-hero-v1.png` (2172 × 724) | Settings page header (Living Experience, Preview): Kratos, the wolf and the whole row of gears on the right, text-safe navy on the left (the mountains start near 34%); static, the glow never moves. The gear icons are decoration only: none is clickable or stands for a section | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/settings/settings-hero-v1.webp` (full size), `settings-hero-v1-1086.webp` (half) | approved |
| Profile header, Kratos and the wolf at sunrise | `docs/design/art/profile/profile-hero-v1.png` (2172 × 724) | Profile page header (Living Experience, Preview): Kratos, the wolf and the banner on the right, text-safe navy on the left (the mountains start near 32%) | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/profile/profile-hero-v1.webp` (full size), `profile-hero-v1-1086.webp` (half) | approved |
| Monitor banner, Kratos and the wolf at the forge | `docs/design/art/monitor/monitor-banner-v1.png` (2172 × 724) | Monitor page or Forge Activity view banner, static unless a workload runs | Chris, 2026-11-07, supplied as approved title artwork | KratosBI | `app/ui/art/experience/monitor/monitor-banner.webp` (full size), `monitor-banner-1086.webp` (half) | approved |
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
