// Header art for each page (home-hero.js), one entry per picture. Data only: adding next month's art is a
// new entry here plus its files, never a code change (docs/design/art/README.md says how).
//
// page         the page whose pool it joins; a page only ever shows its own pool
// id           unique; also the file names: <file>.webp (2172 px) and <file>-1086.webp under app/ui/art/experience
// file         the WebP path without its extension, under app/ui/art/experience
// original     Chris's approved PNG, kept unchanged under docs/design/art
// sky          how far the empty sky reaches into the picture, as a share of its width; the title and buttons stay left of it
// alt          the picture's alt text: "" because every header is decoration beside a real h1 (WCAG H67)
// description  what the picture shows, for the asset log and reviewers; never empty
// published    optional, YYYY-MM-DD: shown from this day on
// retire       optional, YYYY-MM-DD: no longer shown from this day on
//
// Keep the list after "const HERO_ART = " plain JSON, ending with "];": the tests and sidecar/tools/hero_art.py read it.
const HERO_ART = [
  {
    "page": "home",
    "id": "home-hero-v1",
    "file": "home/home-hero-v1",
    "original": "docs/design/art/home/home-hero-v1.png",
    "sky": 0.27,
    "alt": "",
    "description": "Kratos and the wolf at dawn",
    "published": "2026-11-07"
  },
  {
    "page": "create",
    "id": "create-hero-v1",
    "file": "create/create-hero-v1",
    "original": "docs/design/art/create/create-hero-v1.png",
    "sky": 0.3,
    "alt": "",
    "description": "Kratos and the wolf over the blueprint",
    "published": "2026-11-07"
  },
  {
    "page": "workspace",
    "id": "workspace-hero-v1",
    "file": "workspace/workspace-hero-v1",
    "original": "docs/design/art/workspace/workspace-hero-v1.png",
    "sky": 0.4,
    "alt": "",
    "description": "The wolf on the rock in a blue cape",
    "published": "2026-11-07"
  },
  {
    "page": "platform",
    "id": "platform-hero-v1",
    "file": "platform/platform-hero-v1",
    "original": "docs/design/art/platform/platform-hero-v1.png",
    "sky": 0.4,
    "alt": "",
    "description": "Kratos and the wolf beside the floating island network",
    "published": "2026-11-07"
  },
  {
    "page": "datamesh",
    "id": "datamesh-hero-v1",
    "file": "datamesh/datamesh-hero-v1",
    "original": "docs/design/art/datamesh/datamesh-hero-v1.png",
    "sky": 0.4,
    "alt": "",
    "description": "Kratos forging armour on the wolf-crest anvil, the wolf beside him",
    "published": "2026-11-07"
  },
  {
    "page": "optimize",
    "id": "optimize-hero-v1",
    "file": "optimize/optimize-hero-v1",
    "original": "docs/design/art/optimize/optimize-hero-v1.png",
    "sky": 0.4,
    "alt": "",
    "description": "Kratos sharpening a blade on the wolf-crest grindstone, the wolf beside him",
    "published": "2026-11-07"
  },
  {
    "page": "monitor",
    "id": "monitor-hero-v1",
    "file": "monitor/monitor-hero-v1",
    "original": "docs/design/art/monitor/monitor-hero-v1.png",
    "sky": 0.33,
    "alt": "",
    "description": "The caped wolf on a rock above a sunrise valley",
    "published": "2026-11-07"
  },
  {
    "page": "academy",
    "id": "academy-hero-v1",
    "file": "academy/academy-hero-v1",
    "original": "docs/design/art/academy/academy-hero-v1.png",
    "sky": 0.3,
    "alt": "",
    "description": "Kratos reading a glowing wolf codex by the forge",
    "published": "2026-11-07"
  },
  {
    "page": "source",
    "id": "source-hero-v1",
    "file": "source/source-hero-v1",
    "original": "docs/design/art/source/source-hero-v1.png",
    "sky": 0.3,
    "alt": "",
    "description": "Kratos and the wolf at a war-room table with a glowing branch graph",
    "published": "2026-11-07"
  },
  {
    "page": "settings",
    "id": "settings-hero-v1",
    "file": "settings/settings-hero-v1",
    "original": "docs/design/art/settings/settings-hero-v1.png",
    "sky": 0.33,
    "alt": "",
    "description": "Kratos raising a glowing wolf gear above a row of gears, the wolf beside him",
    "published": "2026-11-07"
  },
  {
    "page": "profile",
    "id": "profile-hero-v1",
    "file": "profile/profile-hero-v1",
    "original": "docs/design/art/profile/profile-hero-v1.png",
    "sky": 0.31,
    "alt": "",
    "description": "Kratos with his arms crossed and the wolf beside him at sunrise",
    "published": "2026-11-07"
  }
];
