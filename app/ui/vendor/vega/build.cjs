// vega-frame.js holds the three libraries' builds as one string, for notebook.js to put inline in a
// chart's sandboxed output frame: the frame can't load scripts, and the app's own page never runs them.
const fs = require("fs");
const path = require("path");

const builds = ["vega/build/vega.min.js", "vega-lite/build/vega-lite.min.js", "vega-embed/build/vega-embed.min.js"];
const source = builds.map((b) => fs.readFileSync(path.join(__dirname, "node_modules", b), "utf8")).join("\n;\n");
if (/<\/script|<!--/i.test(source)) throw new Error("A build has text that would end the frame's script tag.");
const versions = ["vega", "vega-lite", "vega-embed"].map((n) => `${n} ${JSON.parse(fs.readFileSync(path.join(__dirname, "node_modules", n, "package.json"), "utf8")).version}`).join(", ");
fs.writeFileSync(path.join(__dirname, "vega-frame.js"),
  `// Built by build.cjs from ${versions} (BSD-3-Clause, see LICENSE-*). Don't edit.\n` +
  `window.fdVegaSource = ${JSON.stringify(source)};\n`);
