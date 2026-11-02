// Settings > Help and about > Save support bundle (PB-062), against the real engine: the dialog lists
// the zip's files with sizes (a log not written yet says so), Save writes the zip and closes with a toast
// whose Show in folder reveals it, a folder that can't be written keeps the dialog open with the reason,
// and axe finds nothing serious in the dialog.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const dialog = () => page().getByRole("dialog", { name: "Save support bundle" });
const revealed = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "support_bundle_preview", "support_bundle_save"],
    // The real reveal opens Explorer; here it only records what it was asked to show.
    mocks: { support_bundle_reveal: ({ path }) => { revealed.push(path); return { shown: path }; } },
  });
  await page().waitForSelector("#rail-main .rail-item");
});

after(async () => { await app?.close(); });

async function open() {
  await page().evaluate(() => { closeDialog(); saveSupportBundle(); });
  await dialog().waitFor();
}

test("the dialog lists each file with its size and says the engine log isn't there yet", { skip }, async () => {
  await open();
  const rows = await dialog().locator("table.bundle-files tbody tr").allInnerTexts();
  assert.strictEqual(rows.length, 3, rows.join("\n"));
  assert.match(rows[0], /^about\.json\s+App and engine build/);
  assert.match(rows[1], /^sidecar\.log\s+Not on this PC\s+The engine hasn't written a log yet/);
  assert.match(rows[2], /^CONTENTS\.txt\s+What is in the zip/);
  assert.match(await dialog().innerText(), /Nothing is sent from KratosForge/);
  assert.match(await dialog().innerText(), /Save still works: about\.json and CONTENTS\.txt are always there/);
  const folder = await dialog().getByRole("textbox", { name: "Folder" }).inputValue();
  assert.ok(folder.endsWith(`${path.sep}Downloads`), folder);
  const toggle = dialog().getByRole("button", { name: "Show CONTENTS.txt" });
  await toggle.click();
  assert.strictEqual(await dialog().getByRole("button", { name: "Hide CONTENTS.txt" }).getAttribute("aria-expanded"), "true");
  assert.match(await dialog().locator("pre").innerText(), /Taken out|taken out/);
});

test("Save writes the zip, closes the dialog and Show in folder reveals it", { skip }, async () => {
  await open();
  const folder = await dialog().getByRole("textbox", { name: "Folder" }).inputValue();
  await dialog().getByRole("button", { name: "Save", exact: true }).click();
  await dialog().waitFor({ state: "detached" });
  const toast = page().locator("#toast");
  assert.match(await toast.innerText(), /^Saved fabric-desktop-support-\d{8}-\d{6}\.zip in /);
  const saved = fs.readdirSync(folder).filter((n) => n.startsWith("fabric-desktop-support-"));
  assert.strictEqual(saved.length, 1, saved.join(", "));
  await toast.getByRole("button", { name: "Show in folder" }).click();
  await page().waitForFunction(() => true);
  assert.deepStrictEqual(revealed, [path.join(folder, saved[0])]);
});

test("a folder that can't be written keeps the dialog open and says nothing was saved", { skip }, async () => {
  await open();
  await page().evaluate(() => { document.getElementById("sb-folder").value = "bundles"; });  // not a full path
  await dialog().getByRole("button", { name: "Save", exact: true }).click();
  const alert = dialog().getByRole("alert");
  await alert.waitFor();
  assert.match(await alert.innerText(), /Not saved\s+Choose a full folder path/);
  assert.ok(await dialog().isVisible());
});

test("Save support bundle has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await open();
  const found = await axeFindings(page(), "#dialog-root .dialog");
  assert.deepStrictEqual(found, []);
});
