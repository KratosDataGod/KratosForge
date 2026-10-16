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

test("a run says where it ran: Fabric, an agent or this PC", () => {
  const src = fs.readFileSync(path.join(ui, "activity.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function ranOn\([\s\S]*?\n}\n/)[0]}; this.ranOn = ranOn;`, ctx);
  assert.strictEqual(ctx.ranOn({ ran_on: "Agent etl-01 on srv1" }), "Agent etl-01 on srv1");
  assert.strictEqual(ctx.ranOn({ compute: "fabric" }), "Fabric");  // an engine from before ran_on
  assert.strictEqual(ctx.ranOn({}), "This PC");
});
