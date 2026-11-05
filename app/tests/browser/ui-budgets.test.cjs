// The app window's performance budgets (ui-budgets.json, docs/performance.md#the-app-window): 
// one warm-up run, then one counted run of ui-benchmark.cjs, each number held to its budget. Fakes only; see ui-benchmark.cjs for what each means.
const { test } = require("node:test");
const assert = require("node:assert");
const { playwright } = require("./harness.cjs");
const { measure } = require("./ui-benchmark.cjs");
const budgets = require("./ui-budgets.json");

const skip = !playwright && "Playwright is not installed";

test("the app window stays within its performance budgets", { skip, timeout: 240000 }, async () => {
  const got = await measure(1);
  const over = Object.entries(budgets).filter(([k]) => !k.startsWith("_"))
    .filter(([k, limit]) => !(got[k] <= limit)).map(([k, limit]) => `${k}: ${got[k]} (budget ${limit})`);
  assert.deepStrictEqual(over, [], `Over budget:\n${over.join("\n")}\nAll numbers: ${JSON.stringify(got)}`);
});
