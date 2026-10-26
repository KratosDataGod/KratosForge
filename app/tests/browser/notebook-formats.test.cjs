// Notebook import and export formats (notebook parity L5) against the real engine: import a
// Databricks source file and a .dbc archive, see what each would create first, then export the open
// notebook as Fabric source and as a web page into the engine's own (temporary) Downloads folder.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const DATABRICKS = [
  "# Databricks notebook source", "# MAGIC %md", "# MAGIC # Daily sales", "", "# COMMAND ----------", "",
  "total = 41 + 1", "print(total)", "", "# COMMAND ----------", "", "# MAGIC %sql", "# MAGIC SELECT 1", ""].join("\n");

// A zip with stored (uncompressed) entries, enough for a .dbc archive.
function zip(entries) {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (buf) => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const local = [], central = [];
  let offset = 0;
  for (const [name, text] of entries) {
    const data = Buffer.from(text), fname = Buffer.from(name), sum = crc(data);
    const head = Buffer.alloc(30);
    head.writeUInt32LE(0x04034b50, 0); head.writeUInt16LE(20, 4); head.writeUInt32LE(sum, 14);
    head.writeUInt32LE(data.length, 18); head.writeUInt32LE(data.length, 22); head.writeUInt16LE(fname.length, 26);
    const dir = Buffer.alloc(46);
    dir.writeUInt32LE(0x02014b50, 0); dir.writeUInt16LE(20, 4); dir.writeUInt16LE(20, 6); dir.writeUInt32LE(sum, 16);
    dir.writeUInt32LE(data.length, 20); dir.writeUInt32LE(data.length, 24); dir.writeUInt16LE(fname.length, 28); dir.writeUInt32LE(offset, 42);
    local.push(head, fname, data);
    central.push(dir, fname);
    offset += 30 + fname.length + data.length;
  }
  const cd = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, cd, end]);
}

const doc = (name, commands) => JSON.stringify({ name, language: "python", commands: commands.map((command, position) => ({ command, position })) });

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["notebook_formats_import_check", "notebook_formats_import", "notebook_export", "open_notebook"],
    mocks: { save_notebook: () => ({ saved: true }), list_notebooks: () => [] },
  });
});

after(async () => { if (app) await app.close(); });

test("importing a Databricks source file shows what it creates, then opens the new notebook", { skip }, async () => {
  await page().evaluate(() => { nbfImportDialog(); });
  const dialog = page().locator(".dialog");
  await dialog.getByText("How this works").waitFor();
  assert.match(await dialog.innerText(), /Writes one new notebook file \(\.ipynb\) for each notebook in the file/);
  assert.strictEqual(await dialog.getByRole("button", { name: "Import" }).isDisabled(), true);
  await dialog.locator("input[type=file]").setInputFiles({ name: "Daily sales.py", mimeType: "text/x-python", buffer: Buffer.from(DATABRICKS) });
  await dialog.getByText(/Databricks notebook source: 1 new notebook will be created/).waitFor();
  assert.match(await dialog.innerText(), /Daily sales\s+3 cells, 1 markdown\s+SQL, Python/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-import.png") });
  await dialog.getByRole("button", { name: "Import" }).click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Daily sales");
  const cells = await page().evaluate(() => state.notebook.cells.map((c) => [c.type, c.source]));
  assert.deepStrictEqual(cells, [["markdown", "# Daily sales"], ["code", "total = 41 + 1\nprint(total)"], ["code", "%%sql\nSELECT 1"]]);
});

test("a .dbc archive creates one notebook each, and a taken name gets the next number", { skip }, async () => {
  const dbc = zip([["proj/Daily sales.python", doc("Daily sales", ["x = 1"])], ["proj/Report.python", doc("Report", ["%md Notes", "y = 2"])]]);
  await page().evaluate(() => { nbfImportDialog(); });
  const dialog = page().locator(".dialog");
  await dialog.locator("input[type=file]").setInputFiles({ name: "workspace.dbc", mimeType: "application/zip", buffer: dbc });
  await dialog.getByText(/Databricks archive \(\.dbc\): 2 new notebooks will be created/).waitFor();
  assert.match(await dialog.innerText(), /Daily sales \(2\)\s*Daily sales is taken, so it gets the next free name\./);
  await dialog.getByRole("button", { name: "Import" }).click();
  await page().waitForFunction(() => state.notebook?.name === "Daily sales (2)");
  assert.match(await page().evaluate(() => document.getElementById("toast").textContent), /Imported 2 notebooks: Daily sales \(2\), Report/);
});

test("Export saves Fabric source and an escaped web page as new files, never over one", { skip }, async () => {
  await page().evaluate(() => {
    const c = state.notebook.cells[0];
    c.outputs = [{ type: "text", text: "<script>alert(1)</script>" }];
    c.count = 1;
    renderNotebook();
  });
  const exportOnce = async (label) => {
    await page().evaluate(() => { document.getElementById("toast").textContent = ""; });
    await page().getByRole("button", { name: "Share" }).click();
    await page().getByRole("menuitem", { name: /^Export/ }).click();
    await page().getByRole("menuitem", { name: label }).click();
    const prefix = "Saved Daily sales (2) to ";
    await page().waitForFunction((p) => document.getElementById("toast").textContent.startsWith(p), prefix);
    return (await page().evaluate(() => document.getElementById("toast").textContent)).slice(prefix.length);
  };
  const py = await exportOnce("Fabric source (.py)");
  assert.match(fs.readFileSync(py, "utf8"), /^# Fabric notebook source\n/);
  const again = await exportOnce("Fabric source (.py)");
  assert.notStrictEqual(again, py);
  assert.match(path.basename(again), /\(2\)\.py$/);
  const html = fs.readFileSync(await exportOnce("Web page (.html)"), "utf8");
  assert.doesNotMatch(html, /<script/i);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.deepStrictEqual(app.errors, []);
});
