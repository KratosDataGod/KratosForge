// The notebook's Side by side (sidebyside.js): how an upload's size is written on the comparison card,
// beyond the GB case in sidebyside-coverage.test.cjs. Megabytes keep one decimal under 100 MB and none
// from 100 MB; anything under 1 MB is in kilobytes, and never "0 KB".
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
  await page().waitForFunction(() => typeof sbsSize === "function");
});
after(async () => { await app?.close(); });

test("sizes under a gigabyte are written in MB or KB", { skip }, async () => {
  const sizes = [1e6, 2.54e6, 99.94e6, 1e8, 456.7e6, 999e6, 42e3, 1499, 400, 0];
  const got = await page().evaluate((ns) => ns.map((n) => sbsSize(n)), sizes);
  assert.deepStrictEqual(got, ["1.0 MB", "2.5 MB", "99.9 MB", "100 MB", "457 MB", "999 MB", "42 KB", "1 KB", "1 KB", "1 KB"]);
  assert.deepStrictEqual(app.errors, []);
});
