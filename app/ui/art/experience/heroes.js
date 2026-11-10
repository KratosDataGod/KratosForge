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
