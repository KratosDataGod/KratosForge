// UI checks that run without a browser: `node --test app/tests`.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ui = path.join(__dirname, "..", "ui");

test("every UI script parses", () => {
  for (const name of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    assert.doesNotThrow(() => new vm.Script(fs.readFileSync(path.join(ui, name), "utf8"), { filename: name }), name);
  }
});

// esc and inlineMd from app.js, without the rest of the page.
function markdownHelpers() {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const code = src.match(/function esc\([\s\S]*?\n}\n/)[0] + src.match(/function inlineMd\([\s\S]*?\n}\n/)[0];
  const ctx = {};
  vm.runInNewContext(`${code}; this.esc = esc; this.inlineMd = inlineMd;`, ctx);
  return ctx;
}

test("a Markdown link can't add attributes or handlers", () => {
  const { inlineMd } = markdownHelpers();
  const html = inlineMd('[x](a" style="position:fixed" onmouseover="alert(1)) and [y](b\' onclick=\'z)');
  assert.ok(!/"\s+on\w+=|'\s+on\w+=|" style=/.test(html), html);
  assert.match(html, /title="a&quot; style=&quot;/);
});

test("Markdown still formats", () => {
  const { inlineMd } = markdownHelpers();
  assert.strictEqual(inlineMd("**b** *i* `c` [ok](https://x.y) <b>"),
    '<strong>b</strong> <em>i</em> <code>c</code> <u title="https://x.y">ok</u> &lt;b&gt;');
});

// The pure helpers at the top of prove.js, without the rest of the page.
function proveHelpers() {
  const src = fs.readFileSync(path.join(ui, "prove.js"), "utf8");
  const code = src.slice(0, src.indexOf("// ---------- state ----------"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { proveNames, proveRulesFrom, proveKind, proveVerdict, proveDiffRows, proveDifference });`, ctx);
  return ctx;
}

test("proof rules from the form keep only what was filled in", () => {
  const { proveRulesFrom } = proveHelpers();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(proveRulesFrom({ keys: " id, region ,", ignore: "", abs: "", rel: "1e-6", timestamps: "us" }))),
    { keys: ["id", "region"], rel: 1e-6 });
  assert.ok(Number.isNaN(proveRulesFrom({ abs: "abc" }).abs), "a bad number goes to the engine, which refuses it");
});

test("a scorecard never shows an estimate or a missing number as measured", () => {
  const { proveKind } = proveHelpers();
  assert.strictEqual(proveKind("estimated")[0], "Estimate");
  assert.strictEqual(proveKind("not available")[0], "Not available");
  assert.strictEqual(proveKind(undefined)[0], "Not available");
  assert.strictEqual(proveKind("measured")[0], "Measured");
});

test("the difference grid lists each changed column, and whole rows by side", () => {
  const { proveDiffRows, proveDifference, proveVerdict } = proveHelpers();
  const keyed = { examples: [
    { key: { id: 2 }, status: "changed", columns: { value: { pc: 20.5, fabric: 0 }, name: { pc: "a", fabric: "b" } } },
    { key: { id: 9 }, status: "only_pc" }] };
  assert.strictEqual(proveDiffRows(keyed, "changed").length, 2);
  assert.strictEqual(proveDiffRows(keyed, "only_pc")[0].key.id, 9);
  assert.strictEqual(proveDiffRows(keyed, "only_fabric").length, 0);
  const whole = { examples: [{ row: { a: 1 }, status: "only_fabric", times: 2 }] };
  assert.strictEqual(proveDiffRows(whole, "only_fabric")[0].times, 2);
  assert.strictEqual(proveDifference(20.5, 0), "20.5");
  assert.strictEqual(proveDifference("a", "b"), "");
  assert.strictEqual(proveVerdict({ state: "done", verdict: { verdict: "inconclusive" } })[1], "Inconclusive");
  assert.strictEqual(proveVerdict({ state: "cancelled" })[1], "Cancelled");
});
