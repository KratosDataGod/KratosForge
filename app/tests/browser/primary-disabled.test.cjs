// A primary button that is disabled looks disabled everywhere in the app (styles.css .tool.primary:disabled):
// no accent fill, muted text, and hovering it changes nothing. Seen on the VS Code sync chooser's Save and
// Compare data, which looked pressable while off.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1280, height: 800 },
    mocks: { readiness_check: () => [], xray: () => ({ findings: [], scanned_at: null }) } });
  await startupReads(app);
  await page().evaluate(() => {
    const host = document.createElement("div");
    host.id = "primary-disabled-check";
    host.innerHTML = '<button class="tool primary" id="pd-on">Save</button> <button class="tool primary" id="pd-off" disabled>Save</button> <button class="tool" id="pd-plain">Compare</button>';
    document.querySelector("main, #main, body").appendChild(host);
  });
});
after(async () => { if (app) await app.close(); });

const look = (id) => page().evaluate((id) => {
  const s = getComputedStyle(document.getElementById(id));
  return { background: s.backgroundColor, color: s.color, border: s.borderTopColor };
}, id);
const token = (name) => page().evaluate((name) => {
  const probe = document.createElement("span");
  probe.style.color = `var(${name})`;
  document.body.appendChild(probe);
  const value = getComputedStyle(probe).color;
  probe.remove();
  return value;
}, name);

test("a disabled primary button drops the accent fill and uses muted text", { skip }, async () => {
  const on = await look("pd-on");
  const off = await look("pd-off");
  assert.strictEqual(on.background, await token("--accent"));
  assert.notStrictEqual(off.background, on.background, "a disabled primary button kept the accent fill");
  assert.strictEqual(off.background, await token("--border"));
  assert.strictEqual(off.color, await token("--muted"));
  assert.strictEqual(off.border, await token("--border"));
});

test("hovering a disabled primary button changes nothing, while an enabled one still darkens", { skip }, async () => {
  const off = await look("pd-off");
  await page().hover("#pd-off", { force: true });
  assert.deepStrictEqual(await look("pd-off"), off);
  await page().hover("#pd-on");
  assert.strictEqual((await look("pd-on")).background, await token("--accent-dark"));
});
