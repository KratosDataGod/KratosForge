// Item 18 follow-up: Monitor > Operations > Queue, Incidents and Run as show How this works beside
// the list (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { readiness_check: () => [] } });
});
after(async () => { if (app) await app.close(); });

for (const [panel, words] of [["queue", "never polls your tenant"], ["incidents", "Check now never posts"], ["runas", "renews a certificate"]]) {
  test(`${panel} has How this works beside the list`, { skip }, async () => {
    await page().evaluate((p) => { monitorPage.panel = p; go("home"); go("monitor"); }, panel);
    await page().waitForSelector(".mon-how .how-it-works");
    const text = await page().textContent(".mon-how .how-it-works");
    for (const part of ["What it reads", "What it changes", "Where results go", words]) assert.ok(text.includes(part), `${panel}: ${part}`);
    if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `monitor-${panel}-how.png`) });
  });
}
