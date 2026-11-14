// Markdown cells (#2579, editor parity N20 to N22): CommonMark, GitHub tables and LaTeX math drawn
// from markdown-it's tokens with h(), under the shell's Content Security Policy. Raw HTML stays text,
// links open only through open_link (https and mailto), and only images the notebook holds are shown.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

let app;
const page = () => app.page;
// markdown(src, opts) in the page, returned as its outerHTML for reading.
const md = (src, opts = {}) => page().evaluate(([s, o]) => {
  const box = markdown(s, o);
  box.className = "md-view";
  document.body.append(box);
  return box.outerHTML;
}, [src, opts]);

before(async () => {
  if (skip) return;
  app = await openApp({ csp: true });
});

after(async () => { if (app) await app.close(); });

test("CommonMark and GitHub tables render", { skip }, async () => {
  const html = await md("#### Four\n\n1. one\n2. two\n   - nested\n\n> quoted\n\n```python\nx = 1 < 2\n```\n\n" +
    "| a | b |\n|:--|--:|\n| 1 | 2 |\n\n~~gone~~ and ---\n\n---");
  assert.match(html, /<h4>Four<\/h4>/);
  assert.match(html, /<ol><li>one<\/li><li>two\n?<ul><li>nested<\/li><\/ul><\/li><\/ol>/);
  assert.match(html, /<blockquote><p>quoted<\/p><\/blockquote>/);
  assert.match(html, /<pre class="md-code"><code>x = 1 &lt; 2\n<\/code><\/pre>/);
  assert.match(html, /<th class="md-left">a<\/th><th class="md-right">b<\/th>/);
  assert.match(html, /<s>gone<\/s>/);
  assert.match(html, /<hr>/);
  assert.match(await md("3. three\n4. four"), /<ol start="3">/);
});

test("raw HTML stays text and no attribute or handler comes from the cell", { skip }, async () => {
  const html = await md('<script>window.__x=1</script>\n\n<img src=x onerror="window.__x=2"> <b onclick="a()">b</b>\n\n' +
    '[x](https://ok.example/" onmouseover="alert(1))');
  assert.strictEqual(await page().evaluate(() => window.__x), undefined);
  assert.match(html, /&lt;script&gt;/);
  const made = await page().evaluate(() => {
    const box = document.querySelector("body > .md-view:last-child");
    return { tags: [...box.querySelectorAll("*")].map((e) => e.localName),
      handlers: [...box.querySelectorAll("*")].flatMap((e) => [...e.attributes].map((a) => a.name)).filter((n) => n.startsWith("on")) };
  });
  assert.deepStrictEqual([...new Set(made.tags)].sort(), ["a", "p"]);
  assert.deepStrictEqual(made.handlers, []);
  assert.deepStrictEqual(app.violations, []);
});

test("links open in the browser through open_link, https and mailto only", { skip }, async () => {
  await page().evaluate(() => { window.__fdShellCalls.length = 0; });
  const html = await md("[site](https://learn.microsoft.com/fabric) [mail](mailto:a@b.example) [plain](http://x.example) " +
    "[js](javascript:alert(1)) [JS](JaVaScRiPt:alert(1)) [ent](&#106;avascript:alert(1)) [vb](vbscript:x) [f](file:///C:/x) " +
    "[d](data:text/html,<b>x</b>) [rel](other.md)");
  assert.doesNotMatch(html, /href=/);
  const links = await page().$$eval("body > .md-view:last-child a.md-link", (as) => as.map((a) => a.textContent));
  assert.deepStrictEqual(links, ["site", "mail"]);
  for (const name of ["site", "mail", "plain", "rel"]) {
    await page().evaluate((n) => [...document.querySelectorAll("body > .md-view:last-child a, body > .md-view:last-child span")]
      .find((x) => x.textContent === n)?.click(), name);
  }
  const opened = await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url));
  assert.deepStrictEqual(opened, ["https://learn.microsoft.com/fabric", "mailto:a@b.example"]);
  assert.match(page().url(), /^http/);  // the app's window never navigated
});

test("a #heading link jumps to that heading", { skip }, async () => {
  await md("## Load the data\n\n[go](#load-the-data)");
  const ok = await page().evaluate(() => {
    const box = document.querySelector("body > .md-view:last-child");
    let jumped = null;
    box.querySelector("h2").scrollIntoView = () => { jumped = "h2"; };
    box.querySelector("a.md-link").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    return jumped;
  });
  assert.strictEqual(ok, "h2");
});

test("only images the notebook holds are shown; nothing is fetched", { skip }, async () => {
  const requests = [];
  page().on("request", (r) => { if (!r.url().startsWith("data:")) requests.push(r.url()); });
  const html = await md(`![pasted](attachment:shot.png) ![inline](data:image/png;base64,${PNG}) ` +
    "![remote](https://example.com/a.png) ![local](C:/a.png) ![bad](attachment:missing.png)",
  { attachments: { "shot.png": { "image/png": PNG }, "evil.html": { "text/html": "<b>x</b>" } } });
  assert.strictEqual((html.match(/<img class="md-img" src="data:image\/png;base64,/g) || []).length, 2);
  assert.match(html, /alt="pasted"/);
  const srcs = await page().$$eval("body > .md-view:last-child img", (imgs) => imgs.map((i) => i.getAttribute("src").slice(0, 22)));
  assert.deepStrictEqual(srcs, ["data:image/png;base64,", "data:image/png;base64,"]);
  assert.match(html, /\[remote\] /);
  assert.match(html, /\[bad\] /);
  await page().waitForTimeout(200);
  assert.deepStrictEqual(requests.filter((u) => /example\.com|a\.png/.test(u)), []);
  // A bundle that isn't an allowed picture type, or isn't base64, shows nothing.
  const odd = await md("![a](attachment:x.png)", { attachments: { "x.png": { "image/png": "not base64!" } } });
  assert.doesNotMatch(odd, /<img/);
});

test("LaTeX math draws as MathML without loading anything", { skip }, async () => {
  const html = await md("Energy $E = mc^2$ here.\n\n$$\\sum_{i=1}^{n} x_i$$ (1)\n\nCost is $5 and $6.");
  assert.match(html, /<span class="md-math"><span class="katex"><math xmlns="http:\/\/www\.w3\.org\/1998\/Math\/MathML">/);
  assert.match(html, /<div class="md-math block"><span class="katex"><math[^>]*display="block"/);
  assert.match(html, /<span class="md-eqno">\(1\)<\/span>/);
  assert.match(html, /Cost is \$5 and \$6\./);
  // \href needs trust, which is off: the link is drawn as text in an error, never as a link.
  const href = await md("$\\href{javascript:alert(1)}{x}$");
  assert.doesNotMatch(href, /href="javascript/i);
  // A broken formula shows its source, not a thrown error.
  assert.match(await md("$\\frac{1}{$"), /md-math/);
  assert.deepStrictEqual(app.violations, []);
  assert.deepStrictEqual(app.errors, []);
});

test("an empty cell shows the placeholder", { skip }, async () => {
  assert.match(await md("   "), /<p class="placeholder">Empty Markdown cell\. Double-click to edit\.<\/p>/);
});
