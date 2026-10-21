// Actions from KratosForge on a workload page say whether each action and change reached the
// platform's audit, and why not when it didn't (see harness.cjs for how to run it).
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
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

const card = (audit) => page().evaluate((a) => {
  document.querySelector(".wl-audit-shot")?.remove();
  const w = { platform_audit: a, actions: [
    { at: new Date(Date.now() - 600000).toISOString(), action: "run_item", env: "", by: "chris@contoso.com", status: "failed", detail: "Fabric says Failed" },
    { at: new Date(Date.now() - 3600000).toISOString(), action: "run", env: "dev", by: "chris@contoso.com", status: "succeeded", detail: "" }] };
  const section = h("section", { class: "card pad wl-audit-shot" }, h("h2", {}, "Actions from KratosForge"), actionsTable(w), platformAuditNote(w.platform_audit));
  document.querySelector(".page").append(section);
  return section.textContent;
}, audit);

test("lines the platform hasn't taken are counted with the reason", { skip }, async () => {
  const text = await card({ platform: "fabric", waiting: 2, error: "a platform admin may need to run Platform > Settings > Finish or repair once, so the platform records workload actions. UDF_Platform wasn't found." });
  assert.ok(text.includes("2 actions or changes haven't reached Platform > Audit yet"));
  assert.ok(text.includes("Finish or repair once, so the platform records workload actions. UDF_Platform wasn't found. This PC keeps them and sends them again."));
  if (SHOTS) await page().locator(".wl-audit-shot").screenshot({ path: path.join(SHOTS, "workload-audit-waiting.png") });
});

test("sent, and no platform yet", { skip }, async () => {
  assert.ok((await card({ platform: "this_pc", waiting: 0, error: "" })).includes("Each one is also on Platform > Audit."));
  assert.ok((await card({ platform: "none", waiting: 0, error: "" })).includes("Kept on this PC only: there's no platform yet."));
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
