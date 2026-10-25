// kratosforge:// links, against the real engine: the shell hands the page a link (the deep-link
// event, or the one the app started with) and the page shows the notebook or page it names. A link
// for a file KratosForge doesn't know only says so.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables",
  "notebook_file_path", "notebook_disk_stamp", "notebook_name_for_path"];

let app;
const page = () => app.page;
const link = (l) => page().evaluate((l) => window.__fdEmit("deep-link", JSON.stringify(l)), l);

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1280, height: 900 }, engine: ENGINE });
  await page().evaluate(() => rpc("save_notebook", { name: "Sales/Daily load", cells: [{ type: "code", source: "x = 1" }] }));
});

after(async () => { if (app) await app.close(); });

test("A link to a notebook's file opens that notebook", { skip }, async () => {
  const file = await page().evaluate(() => rpc("notebook_file_path", { name: "Sales/Daily load" }).then((w) => w.path));
  await link({ kind: "notebook", path: file });
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Sales/Daily load");
  assert.deepStrictEqual(app.errors, []);
});

test("A link to a page goes there; one KratosForge doesn't know only says so", { skip }, async () => {
  const view = await page().evaluate(() => Object.keys(railFeatures()[1].views)[0]);
  await link({ kind: "page", page: view });
  await page().waitForFunction((v) => state.view === v, view);
  await link({ kind: "notebook", path: path.join(app.engineHome, "elsewhere", "Daily.ipynb") });
  await page().getByText(/isn't in the notebooks folder or a notebooks repository KratosForge knows/).waitFor();
  assert.strictEqual(await page().evaluate(() => state.view), view);
  await link({ kind: "page", page: "nope" });
  await page().getByText("KratosForge has no page called nope.").waitFor();
  await link({ kind: "refused", reason: "A notebook link names an .ipynb file." });
  await page().getByText("KratosForge didn't open that link. A notebook link names an .ipynb file.").waitFor();
  assert.strictEqual(await page().evaluate(() => state.view), view);
});
