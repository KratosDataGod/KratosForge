// The Platform tab of a platform repository groups what it compares by kind, in the order a deploy
// applies them, landing zones and policy templates among them (docs/source-control.md). The diff is given.
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { if (app) await app.close(); });

const item = (kind, id, state, changes = []) => ({ kind, id, state, changes });
const DIFF = { env: "prod", problems: [], policies_set_up: true, labels: {},
  counts: { same: 3, changed: 1, only_git: 1, only_deployed: 1 },
  items: [item("domains", "hr", "same"), item("landing_zones", "hr_secure", "changed", [{ field: "label", git: "Confidential", deployed: "Highly Confidential" }]),
    item("landing_zones", "shared", "only_deployed"), item("sources", "workday", "same"),
    item("policy_templates", "short-names", "only_git"), item("policies", "table-names", "same")] };

test("landing zones and policy templates have their own groups on the Platform tab", { skip }, async () => {
  app = await openApp({ mocks: { git_platform_diff: () => DIFF } });
  const { page } = app;
  await page.waitForFunction(() => typeof gitPlatformTab === "function");
  const got = await page.evaluate((d) => {
    Object.assign(gitPage.platform, { env: "prod", key: "C:/repo|prod", diff: d, error: null });
    const el = gitPlatformTab({ root: "C:/repo" });
    document.body.append(el);
    return { groups: [...el.querySelectorAll("h2")].map((x) => x.textContent),
      zones: [...el.querySelectorAll("section")].find((s) => s.querySelector("h2")?.textContent === "Landing zones")?.textContent };
  }, DIFF);
  assert.deepStrictEqual(got.groups, ["Domains", "Landing zones", "Source systems", "Policy templates", "Policies"]);
  assert.match(got.zones, /hr_secure.*changed/);
  assert.match(got.zones, /shared.*removed in Git/);
  assert.deepStrictEqual(app.errors, []);
});
