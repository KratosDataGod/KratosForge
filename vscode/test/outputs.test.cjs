// KratosForge's cell outputs as VS Code notebook outputs (src/outputs.ts).
const { test } = require("node:test");
const assert = require("node:assert");
const { convert, failed } = require("../out/outputs.js");

test("each kind of output becomes the mime types VS Code renders", () => {
  assert.deepStrictEqual(convert({ type: "stream", text: "hi\n" }), { items: [{ mime: "application/vnd.code.notebook.stdout", text: "hi\n" }] });
  assert.deepStrictEqual(convert({ type: "text", text: "42" }).items[0], { mime: "text/plain", text: "42" });
  assert.strictEqual(convert({ type: "markdown", text: "# T" }).items[0].mime, "text/markdown");
  const png = convert({ type: "image", mime: "image/png", data: Buffer.from([137, 80]).toString("base64") }).items[0];
  assert.deepStrictEqual([png.mime, [...png.bytes]], ["image/png", [137, 80]]);
  const svg = convert({ type: "image", mime: "image/svg+xml", data: Buffer.from("<svg/>").toString("base64") }).items[0];
  assert.deepStrictEqual(svg, { mime: "image/svg+xml", text: "<svg/>" });
  assert.deepStrictEqual(convert({ type: "error", ename: "ValueError", evalue: "bad", traceback: "Traceback..." }),
    { error: { name: "ValueError", message: "bad", stack: "Traceback..." } });
  assert.strictEqual(convert({ type: "plotly", figure: { data: [] } }).items[0].mime, "application/vnd.plotly.v1+json");
  assert.strictEqual(convert({ type: "something new" }).items[0].mime, "text/plain");
});

test("a table is HTML with its cells escaped, and plain text", () => {
  const [html, plain] = convert({ type: "table", columns: ["name", "<b>"], rows: [["<script>", 1], [null, { a: 1 }]], total_rows: 1200 }).items;
  assert.match(html.text, /<th>&lt;b&gt;<\/th>/);
  assert.match(html.text, /<td>&lt;script&gt;<\/td>/);
  assert.doesNotMatch(html.text, /<script>/);
  assert.match(html.text, /2 of 1,200 rows/);
  assert.strictEqual(plain.text.split("\n")[0], "name\t<b>");
});

test("a cell failed when it ended in error or showed one", () => {
  assert.strictEqual(failed("ok", [{ type: "text" }]), false);
  assert.strictEqual(failed("error"), true);
  assert.strictEqual(failed("ok", [{ type: "error" }]), true);
});

// Hostile outputs: what a cell can display, written to break the window or reach past the renderers.
const { MAX_ITEM } = require("../out/outputs.js");

function deep(depth) {
  let v = 1;
  for (let i = 0; i < depth; i++) v = [v];
  return v;
}

test("script tags and javascript: links in a table stay text", () => {
  const evil = ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "<a href=\"javascript:alert(1)\">x</a>", "\"><svg onload=alert(1)>"];
  const [html, plain] = convert({ type: "table", columns: evil, rows: [evil, [{ "<script>": "</td><script>" }]] }).items;
  assert.doesNotMatch(html.text, /<(script|img|a|svg)\b/i);
  assert.doesNotMatch(html.text, /<td>[^<]*"/, "a quote was left unescaped in a cell");
  assert.match(html.text, /&lt;a href=&quot;javascript:alert\(1\)&quot;&gt;/);
  assert.ok(plain.text.includes("<script>alert(1)</script>"), "the plain text lost the value");
  assert.strictEqual(convert({ type: "table", columns: "nope", rows: 3 }).items[0].mime, "text/plain");
});

test("JSON nested too deeply, or a chart that is, shows a line instead of stopping the run", () => {
  const value = deep(100_000);
  for (const out of [{ type: "json", data: value }, { type: "vegalite", spec: value }, { type: "plotly", figure: value },
    { type: "something new", value }, { type: "table", columns: ["a"], rows: [[value]] }]) {
    const got = convert(out);
    assert.ok("items" in got, out.type);
    assert.strictEqual(got.items[0].mime, out.type === "table" ? "text/html" : "text/plain", out.type);
    if (out.type !== "table") assert.match(got.items[0].text, /nested too deeply/, out.type);
  }
  assert.strictEqual(convert({ type: "json", data: deep(50) }).items[0].mime, "application/json");
});

test("an output past the item limit shows a line saying so", () => {
  const huge = "x".repeat(MAX_ITEM + 1);
  for (const out of [{ type: "html", html: huge }, { type: "markdown", text: huge }, { type: "text", text: huge },
    { type: "stream", text: huge }, { type: "json", data: huge }, { type: "plotly", figure: { huge } },
    { type: "image", mime: "image/png", data: "A".repeat(Math.ceil((MAX_ITEM + 3) / 3) * 4) },
    { type: "image", mime: "image/svg+xml", data: Buffer.from(`<svg>${huge}</svg>`).toString("base64") }]) {
    const { items } = convert(out);
    assert.strictEqual(items.length, 1, out.type);
    assert.strictEqual(items[0].mime, "text/plain", out.type);
    assert.match(items[0].text, /MB, more than VS Code is sent/, out.type);
  }
  const fits = convert({ type: "image", mime: "image/png", data: Buffer.alloc(1_000_000).toString("base64") }).items[0];
  assert.strictEqual(fits.bytes.length, 1_000_000);
});

test("control characters in text and tracebacks are dropped; tabs, newlines and colors stay", () => {
  const text = "a\tb\r\nc\x1b[31mred\x1b[0m\b\x00\x07\x0b\x0c\x7f\x9b2J\u0085d";
  assert.strictEqual(convert({ type: "stream", text }).items[0].text, "a\tb\r\nc\x1b[31mred\x1b[0m\b2Jd");
  assert.strictEqual(convert({ type: "text", text }).items[0].text, "a\tb\r\nc\x1b[31mred\x1b[0m\b2Jd");
  const { error } = convert({ type: "error", ename: "Bad\x00Name" + "N".repeat(1000), evalue: "v\x07", traceback: "t\x1b[1m\x00" });
  assert.ok(error.name.startsWith("BadName") && error.name.length <= 200);
  assert.deepStrictEqual([error.message, error.stack], ["v", "t\x1b[1m"]);
});
