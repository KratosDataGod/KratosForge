// The header art manifest (art/experience/heroes.js): every picture is complete, follows the approved-art
// rules, and the daily pick keeps each page to its own pool. Needs no browser.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { ROOT, MANIFEST, heroArt } = require("./hero-art.cjs");

const ART = path.join(ROOT, "app", "ui", "art", "experience");
const DAY = /^\d{4}-\d{2}-\d{2}$/;
// The sky the text column needs: the title and buttons end at 27% of the picture at the narrowest width it shows.
const MIN_SKY = 0.27;

// Width and height from a PNG's IHDR chunk.
function pngSize(file) {
  const b = fs.readFileSync(file);
  assert.strictEqual(b.toString("ascii", 1, 4), "PNG", `${file} is a PNG`);
  return { width: b.readUInt32BE(16), height: b.readUInt32BE(20) };
}

// Width and height from a WebP header (lossy VP8, lossless VP8L or extended VP8X).
function webpSize(file) {
  const b = fs.readFileSync(file);
  assert.ok(b.toString("ascii", 0, 4) === "RIFF" && b.toString("ascii", 8, 12) === "WEBP", `${file} is a WebP`);
  const kind = b.toString("ascii", 12, 16);
  if (kind === "VP8 ") return { width: b.readUInt16LE(26) & 0x3fff, height: b.readUInt16LE(28) & 0x3fff };
  if (kind === "VP8L") { const v = b.readUInt32LE(21); return { width: (v & 0x3fff) + 1, height: ((v >> 14) & 0x3fff) + 1 }; }
  return { width: b.readUIntLE(24, 3) + 1, height: b.readUIntLE(27, 3) + 1 };
}

test("every header picture is complete: page, files, sky, alt and description", () => {
  const art = heroArt();
  assert.ok(art.length > 0);
  const ids = new Set();
  for (const e of art) {
    const at = e.id || JSON.stringify(e);
    assert.match(e.page || "", /^[a-z]+$/, `${at}: page`);
    assert.match(e.id || "", /^[a-z0-9-]+$/, `${at}: id`);
    assert.ok(!ids.has(e.id), `${at}: id used twice`);
    ids.add(e.id);
    assert.strictEqual(e.file, `${e.page}/${e.id}`, `${at}: file is <page>/<id>, so a page's pictures live in its own folder`);
    assert.strictEqual(e.original, `docs/design/art/${e.page}/${e.id}.png`, `${at}: original is kept under docs/design/art/<page>`);
    assert.ok(typeof e.sky === "number" && e.sky >= MIN_SKY && e.sky < 1, `${at}: sky must be measured, at least ${MIN_SKY}`);
    assert.strictEqual(typeof e.alt, "string", `${at}: alt is required ("" for decoration beside the page's h1)`);
    assert.ok(typeof e.description === "string" && e.description.trim().length > 10, `${at}: description`);
    for (const k of ["published", "retire"]) if (e[k] !== undefined) assert.match(e[k], DAY, `${at}: ${k} is YYYY-MM-DD`);
    if (e.published && e.retire) assert.ok(e.published < e.retire, `${at}: retired before it was published`);
  }
});

test("every picture is Chris's original at 3:1 with both WebP sizes made from it", () => {
  for (const e of heroArt()) {
    const png = path.join(ROOT, e.original);
    assert.ok(fs.existsSync(png), `${e.id}: original ${e.original} is missing`);
    const o = pngSize(png);
    assert.ok(o.width === 3 * o.height && o.width >= 2172, `${e.id}: the original is ${o.width} x ${o.height}, not exactly 3:1 at 2172 px or wider`);
    // The two widths the app's srcset names.
    for (const [suffix, width] of [["", 2172], ["-1086", 1086]]) {
      const file = path.join(ART, `${e.file}${suffix}.webp`);
      assert.ok(fs.existsSync(file), `${e.id}: ${e.file}${suffix}.webp is missing (sidecar/tools/hero_art.py makes it)`);
      const w = webpSize(file);
      assert.ok(Math.abs(w.width - width) <= 1 && Math.abs(w.width / w.height - o.width / o.height) < 0.01,
        `${e.id}: ${e.file}${suffix}.webp is ${w.width} x ${w.height}, not the original scaled to ${width}`);
    }
  }
});

test("every picture is in the asset log, and every page that shows art has a pool", () => {
  const log = fs.readFileSync(path.join(ROOT, "docs", "design", "assets.md"), "utf8");
  for (const e of heroArt()) assert.ok(log.includes(e.original), `${e.id}: add it to docs/design/assets.md`);
  const pages = new Set(heroArt().map((e) => e.page));
  for (const page of ["academy", "profile"]) {
    assert.ok(pages.has(page), `${page} has no header art`);
  }
});

// home-hero.js's picker, run on its own with a made-up manifest.
function picker(art) {
  const ctx = { HERO_ART: art };
  vm.createContext(ctx);
  vm.runInContext(`${fs.readFileSync(path.join(ROOT, "app", "ui", "home-hero.js"), "utf8")}
    this.pick = heroArtPick; this.pin = heroArtPin;`, ctx);
  return ctx;
}

test("the pick: a page's own pool only, the same all day, published and retired dates kept", () => {
  const pool = (page, n) => Array.from({ length: n }, (_, i) => ({ page, id: `${page}-hero-v${i + 1}` }));
  const p = picker([...pool("academy", 7), ...pool("home", 5),
    { page: "solo", id: "solo-1" }, { page: "dated", id: "old", retire: "2026-12-01" }, { page: "dated", id: "new", published: "2026-12-01" },
    { page: "later", id: "later-1", published: "2027-01-01" }, { page: "later", id: "later-2", published: "2027-02-01" }]);
  const seen = new Set();
  for (let d = 1; d <= 28; d++) {
    const day = `2026-11-${String(d).padStart(2, "0")}`;
    const pick = p.pick("academy", day);
    assert.strictEqual(pick.page, "academy");
    assert.strictEqual(p.pick("academy", day), pick, "the same picture all day");
    seen.add(pick.id);
    assert.strictEqual(p.pick("home", day).page, "home");
  }
  assert.ok(seen.size >= 4, `a month shows most of the pool, not one picture: ${[...seen]}`);
  assert.strictEqual(p.pick("solo", "2026-11-07").id, "solo-1", "a pool of one");
  assert.strictEqual(p.pick("dated", "2026-11-30").id, "old");
  assert.strictEqual(p.pick("dated", "2026-12-01").id, "new", "retired the day the new one is published");
  assert.strictEqual(p.pick("later", "2026-11-07").id, "later-1", "nothing published yet: the pool's first");
  assert.strictEqual(p.pick("nothing", "2026-11-07"), null, "no pool: the page keeps its plain heading");
  p.pin.academy = "academy-hero-v3";
  assert.strictEqual(p.pick("academy", "2026-11-07").id, "academy-hero-v3");
});

test("the manifest is plain JSON inside a script the app loads", () => {
  const text = fs.readFileSync(MANIFEST, "utf8");
  assert.match(fs.readFileSync(path.join(ROOT, "app", "ui", "index.html"), "utf8"), /<script src="art\/experience\/heroes\.js"><\/script>\s*<script src="home-hero\.js">/);
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(`${text}; this.art = HERO_ART;`, ctx);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.art)), heroArt());
});
