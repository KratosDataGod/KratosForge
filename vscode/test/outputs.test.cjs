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
