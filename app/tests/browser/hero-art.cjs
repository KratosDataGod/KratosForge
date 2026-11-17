// Reads the header art manifest (app/ui/art/experience/heroes.js) the way sidecar/tools/hero_art.py does:
// the plain JSON between the line starting "const HERO_ART = " and the closing "];".
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "..", "..");
const MANIFEST = path.join(ROOT, "app", "ui", "art", "experience", "heroes.js");

function heroArt() {
  const text = fs.readFileSync(MANIFEST, "utf8");
  const m = /^const HERO_ART = (\[[\s\S]*\]);\s*$/m.exec(text);
  if (!m) throw new Error("heroes.js has no const HERO_ART = [ ... ];");
  return JSON.parse(m[1]);
}

module.exports = { ROOT, MANIFEST, heroArt };
