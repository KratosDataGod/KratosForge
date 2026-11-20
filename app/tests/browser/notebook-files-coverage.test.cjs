// All notebooks and the notebook file actions (notebook-files.js) beyond notebook-files.test.cjs and
// notebook-folders.test.cjs, with every engine call mocked: the list's failed and empty states, the
// Saved in chips, search with its options, problems and failures, Rename from the list and the warning
// when parts don't follow, Delete's facts, Restore under another name, the folder menu's actions and
// their refusals, the explorer's Notebooks tab, Open a notebook file, Edit on this PC for a Fabric
// notebook, the check before Publish when Fabric changed, and the command bar's commands
// (see harness.cjs for how to run it).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const REPO = { root: "C:/repos/analytics", label: "Analytics" };
const OPS = { root: "C:/repos/ops", label: "Ops" };
const PLACES = [{ root: null, label: "This PC" }, REPO];
const T = Math.floor(Date.now() / 1000) - 3600;
const nb = (name, place = null, published_to = null) => ({ name, place, published_to, modified: T });
const FOLDERS = [{ path: "Sales", place: null }, { path: "Sales/EMEA", place: null }, { path: "Sales/EMEA/Q1", place: null },
  { path: "Models", place: REPO }];
const LISTING = [nb("Sales/EMEA/Daily load"), nb("Loose", null, { workspace_name: "Sales WS" }), nb("Scratch", null, { workspace_id: "w2" }),
  nb("Models/Churn", REPO), nb("Nightly", OPS)];
const CELLS = [{ type: "code", cell_id: "k1", source: "x = 1", meta: {} }, { type: "code", cell_id: "k2", source: "total = orders.sum()", meta: {} },
  { type: "markdown", cell_id: "k3", source: "# orders", meta: {} }];

let over; // method -> function(params), in place of the default answer below
let listing, folders, deleted;
let app;
const page = () => app.page;

function mocks() {
  const defaults = {
    notebook_listing: () => listing,
    list_notebooks: () => listing,
    notebook_folders: () => ({ folders }),
    notebook_deleted: () => deleted,
    notebook_places: () => ({ places: PLACES }),
    open_notebook: ({ name }) => ({ name, cells: CELLS }),
    save_notebook: () => ({ saved: true }),
    notebook_dependencies: () => ({ cells: [] }),
    rename_notebook: () => ({ renamed: true, problems: [] }),
    notebook_search: (p) => ({ query: p.query, searched: listing.length, notebooks: [] }),
    new_notebook: ({ folder }) => ({ name: `${folder ? `${folder}/` : ""}Notebook 1`, cells: [] }),
    notebook_folder_create: ({ path }) => ({ path }),
    notebook_folder_delete: () => ({ deleted: true }),
  };
  const out = {};
  const names = new Set([...Object.keys(defaults), "notebook_delete_check", "notebook_delete", "notebook_restore", "notebook_move_to_folder",
    "notebook_folder_rename", "notebook_import_check", "notebook_import", "notebook_fabric_check", "notebook_from_fabric", "notebook_publish_check"]);
  for (const m of names) {
    out[m] = (p) => {
      if (over[m]) return over[m](p);
      if (defaults[m]) return defaults[m](p);
      throw new Error(`${m} isn't set for this test`);
    };
  }
  return out;
}

before(async () => {
  if (skip) return;
  over = {};
  app = await openApp({ mocks: mocks() });
  await page().evaluate(() => { state.user = "chris@contoso.com"; });
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  over = {};
  listing = LISTING.map((n) => ({ ...n }));
  folders = FOLDERS.map((f) => ({ ...f }));
  deleted = [];
  await page().evaluate(() => {
    closeMenu(); closeDialog();
    document.getElementById("palette")?.remove();
    document.querySelector("#toast")?.replaceChildren();
    disposeEditors(); state.notebook = null;
    Object.assign(nbFiles, { query: "", savedIn: null, namesOnly: false });
    nbFiles.collapsed.clear();
    go("home");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const sent = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const dialog = (name) => page().getByRole("dialog", { name });
const results = () => page().locator(".nbf-results");
const rows = () => results().locator("tbody tr").allInnerTexts();
async function toastSays(re) {
  await page().waitForFunction((src) => new RegExp(src).test(document.querySelector("#toast")?.innerText || ""), re.source);
  return page().locator("#toast").innerText();
}
// Waits for a condition on the calls the engine got (they are recorded here, not in the page).
async function until(fn, what) {
  for (const end = Date.now() + 10000; !fn(); await new Promise((r) => setTimeout(r, 20))) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
  }
}
async function allNotebooks() {
  await page().evaluate(() => go("notebooks"));
  await page().getByRole("heading", { name: "All notebooks" }).waitFor();
  await results().locator("table, p:not(.muted-text), .nbf-found").first().waitFor();
}
async function menu(label, item) {
  await page().getByRole("button", { name: label, exact: true }).click();
  await page().getByRole("menuitem", { name: item }).click();
}
async function openNb(name, more = {}) {
  await page().evaluate(({ name, cells, more }) => { loadIntoState({ name, cells, ...more }); go("notebook"); }, { name, cells: CELLS, more });
}

test("All notebooks says when the list or Recently deleted can't be read, and when there is nothing yet", { skip }, async () => {
  over.notebook_listing = () => { throw new Error("OSError: the notebooks folder can't be read"); };
  over.notebook_deleted = () => { throw new Error("OSError: no"); };
  await page().evaluate(() => go("notebooks"));
  await results().getByText("Couldn't list notebooks.").waitFor();
  await page().getByText("Couldn't list deleted notebooks.").waitFor();
  over = {};
  listing = [];
  folders = [];
  await page().evaluate(() => go("notebooks"));
  await results().getByText("No notebooks here yet. Create one, or open a notebook file.").waitFor();
  await page().getByText("Nothing deleted. A deleted notebook is kept here for 30 days.").waitFor();
  assert.strictEqual(await page().getByRole("group", { name: "Saved in" }).locator("button").count(), 0, "no chips for one place");
  assert.deepStrictEqual(app.errors, []);
});

test("Saved in chips count each place and filter the list and its folders", { skip }, async () => {
  await allNotebooks();
  const chips = page().getByRole("group", { name: "Saved in" });
  assert.deepStrictEqual(await chips.locator("button").allInnerTexts(), ["All 5", "This PC 3", "Analytics 1", "Ops 1"]);
  const text = (await rows()).join("\n");
  assert.match(text, /Loose\s+This PC\s+Sales WS/);
  assert.match(text, /Scratch\s+This PC\s+Fabric/);
  assert.match(text, /Nightly\s+Ops\s+Not published/);
  // Folders in two places are labelled with their place; notebooks in none are grouped last.
  assert.match(text, /Models\s+Analytics\s+1/);
  assert.match(text, /Not in a folder\s+3/);
  await chips.getByRole("button", { name: "Analytics 1" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".nbf-results tbody tr").length === 2);
  assert.strictEqual(await chips.getByRole("button", { name: "Analytics 1" }).getAttribute("aria-pressed"), "true");
  assert.deepStrictEqual((await rows()).map((r) => r.split(/\s+/)[0]), ["Models", "Churn"]);
  await chips.getByRole("button", { name: "This PC 3" }).click();
  await results().getByRole("button", { name: "Loose", exact: true }).waitFor();
  assert.strictEqual(await results().getByText("Models").count(), 0, "the repository's folder is left out");
  // A place that has gone falls back to All.
  await chips.getByRole("button", { name: "Ops 1" }).click();
  await page().waitForFunction(() => nbFiles.savedIn === "C:/repos/ops");
  listing = listing.filter((n) => n.place?.root !== OPS.root);
  await page().evaluate(() => go("notebooks"));
  await page().getByRole("button", { name: "All 4", pressed: true }).waitFor();
  // A folder folds away and back.
  const fold = results().getByRole("button", { name: /^Sales\b/ }).first();
  assert.strictEqual(await fold.getAttribute("aria-expanded"), "true");
  await fold.click();
  await results().getByRole("button", { name: /^Sales\b/ }).first().and(page().locator("[aria-expanded=false]")).waitFor();
  assert.strictEqual(await results().getByRole("button", { name: "Daily load", exact: true }).count(), 0);
  await results().getByRole("button", { name: /^Sales\b/ }).first().click();
  await results().getByRole("button", { name: "Daily load", exact: true }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Search sends the open notebook's unsaved cells, marks each hit, and Names only searches names", { skip }, async () => {
  over.notebook_search = (p) => (p.query === "zzz" ? { query: "zzz", searched: 5, notebooks: [] } : {
    query: p.query, searched: 5, more: true,
    notebooks: [
      { name: "Daily sales", place: null, name_match: false, more: true, hits: [
        { cell: 2, cell_id: "k2", type: "code", text: "total = orders.sum()", start: 8, end: 14 },
        { cell: 3, cell_id: "gone", type: "markdown", text: "# orders", start: 2, end: 8 }] },
      { name: "Orders report", place: REPO, name_match: true, hits: [] }],
    problems: [{ name: "Broken", text: "isn't a notebook file" }] });
  await openNb("Draft");
  await page().evaluate(() => { state.notebook.dirty = true; go("notebooks"); });
  await page().getByRole("searchbox", { name: "Search notebook names and code" }).fill("orders");
  await page().getByText("3 matches in 2 of 5 notebooks (showing the first ones)").waitFor();
  const p = sent("notebook_search")[0];
  assert.deepStrictEqual([p.query, p.names_only, p.saved_in], ["orders", false, null]);
  assert.strictEqual(p.open_notebook.name, "Draft");
  assert.deepStrictEqual(p.open_notebook.cells.map((c) => c.source), CELLS.map((c) => c.source));
  const found = page().locator(".nbf-found");
  const text = await found.innerText();
  assert.match(text, /Daily sales\s+This PC · 2\+ in cells/);
  assert.match(text, /Orders report\s+Analytics · name matches/);
  assert.match(text, /Cell 3 \(Markdown\)/);
  assert.match(text, /Broken: isn't a notebook file/);
  assert.deepStrictEqual(await found.locator("mark").allInnerTexts(), ["orders", "orders"]);
  await page().getByRole("button", { name: "Names only" }).click();
  await until(() => sent("notebook_search").length === 2, "the second search");
  assert.strictEqual(sent("notebook_search")[1].names_only, true);
  assert.strictEqual(await page().getByRole("button", { name: "Names only" }).getAttribute("aria-pressed"), "true");
  // Go to a cell whose id has gone uses its number.
  await page().getByText("3 matches in 2 of 5 notebooks").waitFor();
  await page().getByRole("button", { name: "Go to cell 3" }).click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Daily sales");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.indexOf(selectedCell()) + 1), 3);
  assert.ok(sent("save_notebook").length >= 1, "the unsaved notebook is saved before another opens");
  // A search that finds nothing, and one that fails.
  await page().evaluate(() => go("notebooks"));
  await page().getByRole("searchbox", { name: "Search notebook names and code" }).fill("zzz");
  await page().getByText('No notebook matches "zzz".').waitFor();
  over.notebook_search = () => { throw new Error("OSError: the disk went away"); };
  await page().getByRole("searchbox", { name: "Search notebook names and code" }).fill("zzzz");
  await page().getByText(/^Couldn't search: /).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Open on a search result opens that notebook", { skip }, async () => {
  over.notebook_search = (p) => ({ query: p.query, searched: 5, notebooks: [{ name: "Orders report", place: REPO, name_match: true, hits: [] }] });
  await page().evaluate(() => { nbFiles.query = "orders"; go("notebooks"); });
  await page().getByText("1 match in 1 of 5 notebooks").waitFor();
  await page().locator(".nbf-book").getByRole("button", { name: "Open" }).click();
  await page().waitForFunction(() => state.notebook?.name === "Orders report");
  assert.deepStrictEqual(sent("open_notebook"), [{ name: "Orders report" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Rename from the list renames the open notebook too, and says what didn't follow", { skip }, async () => {
  over.rename_notebook = ({ new_name }) => ({ renamed: true, problems: [
    { notebook: new_name, was: "Loose", step: "schedules", error: "the task is locked" },
    { notebook: new_name, was: "Loose", step: "something new", error: "busy" }] });
  await openNb("Loose");
  await allNotebooks();
  await menu("More actions for Loose", "Rename…");
  const ask = dialog("Rename notebook");
  assert.strictEqual(await ask.getByRole("textbox").inputValue(), "Loose");
  await ask.getByRole("button", { name: "Rename" }).click();  // the same name: nothing to do
  await ask.waitFor({ state: "detached" });
  assert.strictEqual(sent("rename_notebook").length, 0);
  await menu("More actions for Loose", "Rename…");
  await ask.getByRole("textbox").fill("Loose v2");
  await ask.getByRole("button", { name: "Rename" }).click();
  const warn = dialog("Moved, but some things didn't follow");
  await warn.waitFor();
  assert.deepStrictEqual(sent("rename_notebook"), [{ name: "Loose", new_name: "Loose v2" }]);
  assert.ok(sent("save_notebook").length >= 1, "the open notebook is saved first");
  assert.deepStrictEqual(await warn.locator("li").allInnerTexts(), [
    "Loose v2 (was Loose): its schedules didn't follow (the task is locked). Open Monitor > KratosForge > Schedules and point its schedule on this PC at the new name.",
    "Loose v2 (was Loose): its something new didn't follow (busy).",
  ]);
  assert.strictEqual(await page().evaluate(() => state.notebook.name), "Loose v2");
  await warn.getByRole("button", { name: "OK" }).click();
  await warn.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("Delete names what stays behind, Cancel sends nothing, and a refusal says why", { skip }, async () => {
  let check = { keep_days: 30, fabric_item: { workspace_name: "" }, schedules: [{ text: "Daily 06:00" }, { text: "" }], place: REPO };
  over.notebook_delete_check = () => check;
  over.notebook_delete = () => { throw new Error("PermissionError: Models/Churn is open in another app"); };
  await allNotebooks();
  await menu("More actions for Models/Churn", "Delete…");
  const confirm = dialog("Delete Models/Churn?");
  const text = await confirm.innerText();
  assert.match(text, /Models\/Churn goes to Recently deleted on this PC\. You can restore it for 30 days; after that it's gone\./);
  assert.match(text, /• Its Fabric item in its workspace isn't deleted\. Delete it in Fabric if you want it gone too\./);
  assert.match(text, /• It has 2 schedules on this PC \(Daily 06:00\)\. They are turned off, not deleted, so Restore brings them back\./);
  assert.match(text, /• It's removed from the Analytics repository's folder, which shows as a change to commit there\./);
  assert.match(await confirm.getByRole("button", { name: "Delete" }).getAttribute("class"), /danger/);
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await confirm.waitFor({ state: "detached" });
  assert.strictEqual(sent("notebook_delete").length, 0);
  check = { keep_days: 7, fabric_item: { workspace_name: "Sales WS" }, schedules: [{ text: "" }], place: null };
  await menu("More actions for Models/Churn", "Delete…");
  const text2 = await confirm.innerText();
  assert.match(text2, /It has 1 schedule on this PC \(on this PC\)\. It is turned off, not deleted, so Restore brings it back\./);
  assert.match(text2, /Its Fabric item in Sales WS isn't deleted/);
  await confirm.getByRole("button", { name: "Delete" }).click();
  await toastSays(/^Couldn't delete the notebook: /);
  assert.deepStrictEqual(sent("notebook_delete"), [{ name: "Models/Churn" }]);
  // A check that fails stops before the confirm.
  over.notebook_delete_check = () => { throw new Error("LookupError: No notebook Models/Churn"); };
  await menu("More actions for Models/Churn", "Delete…");
  await toastSays(/^Couldn't check the notebook: /);
  assert.strictEqual(await confirm.count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Restore asks for another name when one is taken, and says what came back with it", { skip }, async () => {
  deleted = [{ id: "d1", name: "Old", place: REPO, deleted_at: new Date(Date.now() - 86400000).toISOString(), kept_until: "2026-12-19" }];
  over.notebook_restore = ({ name }) => {
    if (!name) throw new Error("FileExistsError: A notebook named Old already exists");
    return { name, moved_to_pc: true, schedules_on: ["s1"], schedules_left_off: ["s2"] };
  };
  await allNotebooks();
  const row = page().locator("tr", { has: page().getByRole("button", { name: "Restore Old" }) });
  assert.match(await row.innerText(), /^Old\s+Analytics/);
  await page().getByRole("button", { name: "Restore Old" }).click();
  const ask = dialog("Restore Old");
  assert.strictEqual(await ask.getByRole("textbox").inputValue(), "Old (restored)");
  await ask.getByRole("button", { name: "Cancel" }).click();
  await ask.waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("notebook_restore"), [{ id: "d1" }], "Cancel restores nothing");
  await page().getByRole("button", { name: "Restore Old" }).click();
  await ask.getByRole("button", { name: "Restore" }).click();
  const said = await toastSays(/^Restored Old \(restored\)\./);
  assert.match(said, /Its repository isn't open any more, so it's back in the app's own folder\./);
  assert.match(said, /Its schedule on this PC is on again\./);
  assert.match(said, /Its schedule on this PC stays off: it runs the notebook by its old name\./);
  assert.deepStrictEqual(sent("notebook_restore").at(-1), { id: "d1", name: "Old (restored)" });
  // Any other refusal is said plainly.
  over.notebook_restore = () => { throw new Error("OSError: the recycle folder is gone"); };
  await allNotebooks();
  await page().getByRole("button", { name: "Restore Old" }).click();
  await toastSays(/^Couldn't restore the notebook: /);
  assert.deepStrictEqual(app.errors, []);
});

test("New folder makes it where the person picks, and a refusal shows in the dialog until they type", { skip }, async () => {
  let refuse = true;
  over.notebook_folder_create = ({ path }) => {
    if (refuse) throw new Error(`FileExistsError: ${path} is already there`);
    return { path };
  };
  await allNotebooks();
  await page().getByRole("button", { name: "New folder", exact: true }).click();
  const d = dialog("New folder");
  await d.getByRole("combobox", { name: "Make it in" }).selectOption({ label: "Analytics repository" });
  await d.getByRole("textbox", { name: "Folder name" }).fill("Archive");
  await d.getByRole("button", { name: "Make folder" }).click();
  const alert = d.getByRole("alert");
  await alert.waitFor();
  assert.match(await alert.innerText(), /Archive is already there/);
  assert.deepStrictEqual(sent("notebook_folder_create"), [{ path: "Archive", place: REPO.root }]);
  await d.getByRole("textbox", { name: "Folder name" }).fill("Archive 2");
  assert.strictEqual(await alert.isHidden(), true);
  refuse = false;
  await d.getByRole("textbox", { name: "Folder name" }).press("Enter");
  await toastSays(/^Made the folder Archive 2\. Use Move to folder on a notebook's menu to put notebooks in it\./);
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("A folder's menu makes a notebook or a folder inside it, but no deeper than three levels", { skip }, async () => {
  await allNotebooks();
  await menu("Folder actions for Q1", "Rename folder…");  // the deepest folder's menu has no New folder inside
  await dialog("Rename folder Q1").getByRole("button", { name: "Cancel" }).click();
  await page().getByRole("button", { name: "Folder actions for Q1" }).click();
  assert.deepStrictEqual(await page().getByRole("menuitem").allInnerTexts(), ["New notebook here", "Rename folder…", "Delete folder…"]);
  await page().evaluate(() => closeMenu());
  await menu("Folder actions for Sales", "New folder inside…");
  const d = dialog("New folder in Sales");
  assert.match(await d.innerText(), /Makes an empty folder inside Sales on this PC\. Nothing in Fabric changes\./);
  assert.strictEqual(await d.getByRole("combobox", { name: "Make it in" }).count(), 0, "it goes in the folder's own place");
  await d.getByRole("textbox", { name: "Folder name" }).fill("Archive");
  await d.getByRole("button", { name: "Make folder" }).click();
  await toastSays(/^Made the folder Sales\/Archive\./);
  assert.deepStrictEqual(sent("notebook_folder_create"), [{ path: "Sales/Archive", place: null }]);
  await menu("Folder actions for Models", "New notebook here");
  await page().waitForFunction(() => state.notebook?.name === "Models/Notebook 1");
  assert.deepStrictEqual(sent("new_notebook"), [{ place: REPO.root, folder: "Models" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Move to folder shows the new name, refuses in place, and says when there's nowhere to go", { skip }, async () => {
  let refuse = true;
  over.notebook_move_to_folder = ({ name, folder }) => {
    if (refuse) throw new Error(`FileExistsError: ${folder} already has a notebook named ${name}`);
    return { name: `${folder}/${name}`, schedules_moved: ["s1"], problems: [] };
  };
  await allNotebooks();
  await menu("More actions for Nightly", "Move to folder…");
  await toastSays(/^There are no folders in Ops yet\. Use New folder on All notebooks first\./);
  await menu("More actions for Loose", "Move to folder…");
  const d = dialog("Move Loose");
  const folder = d.getByRole("combobox", { name: "Folder" });
  assert.deepStrictEqual(await folder.locator("option").allInnerTexts(), ["Not in a folder", "Sales", "Sales/EMEA", "Sales/EMEA/Q1"]);
  assert.match(await d.innerText(), /It becomes Loose, in This PC\./);
  // Moving to where it is closes without a call.
  await d.getByRole("button", { name: "Move" }).click();
  await d.waitFor({ state: "detached" });
  assert.strictEqual(sent("notebook_move_to_folder").length, 0);
  await menu("More actions for Loose", "Move to folder…");
  await folder.selectOption("Sales/EMEA");
  assert.match(await d.innerText(), /It becomes Sales\/EMEA\/Loose, in This PC\./);
  await d.getByRole("button", { name: "Move" }).click();
  await d.getByRole("alert").waitFor();
  assert.match(await d.getByRole("alert").innerText(), /Sales\/EMEA already has a notebook named Loose/);
  await folder.selectOption("Sales");
  assert.strictEqual(await d.getByRole("alert").isHidden(), true);
  refuse = false;
  await d.getByRole("button", { name: "Move" }).click();
  await toastSays(/^Loose is now Sales\/Loose\. Its schedules on this PC follow it\./);
  assert.deepStrictEqual(sent("notebook_move_to_folder").at(-1), { name: "Loose", folder: "Sales" });
  assert.deepStrictEqual(app.errors, []);
});

test("Rename folder saves an open notebook in it first, renames it, and refuses in place", { skip }, async () => {
  let refuse = true;
  over.notebook_folder_rename = ({ path, new_name }) => {
    if (refuse) throw new Error(`FileExistsError: ${new_name} is already there`);
    return { new_path: new_name, moved: [{ from: "Sales/EMEA/Daily load", to: `${new_name}/EMEA/Daily load` }],
      problems: [{ notebook: `${new_name}/EMEA/Daily load`, was: "Sales/EMEA/Daily load", step: "environment", error: "not found" }] };
  };
  await openNb("Sales/EMEA/Daily load");
  await page().evaluate(() => { state.notebook.dirty = true; go("notebooks"); });
  await results().locator("table").waitFor();
  await menu("Folder actions for Sales", "Rename folder…");
  const d = dialog("Rename folder Sales");
  assert.match(await d.innerText(), /1 notebook move with it, and its names change to the new folder\./);
  await d.getByRole("button", { name: "Rename" }).click();  // unchanged
  await d.waitFor({ state: "detached" });
  assert.strictEqual(sent("notebook_folder_rename").length, 0);
  await menu("Folder actions for Sales", "Rename folder…");
  const saves = sent("save_notebook").length;
  await d.getByRole("textbox", { name: "Name" }).fill("Revenue");
  await d.getByRole("button", { name: "Rename" }).click();
  await d.getByRole("alert").waitFor();
  assert.match(await d.getByRole("alert").innerText(), /Revenue is already there/);
  await d.getByRole("textbox", { name: "Name" }).fill("Revenue 2");
  assert.strictEqual(await d.getByRole("alert").isHidden(), true);
  refuse = false;
  await d.getByRole("textbox", { name: "Name" }).press("Enter");
  await toastSays(/^Renamed Sales to Revenue 2\. 1 notebook moved with it\./);
  assert.ok(sent("save_notebook").length > saves, "the open notebook in the folder is saved first");
  assert.deepStrictEqual(sent("notebook_folder_rename").at(-1), { path: "Sales", new_name: "Revenue 2", place: null });
  assert.strictEqual(await page().evaluate(() => state.notebook.name), "Revenue 2/EMEA/Daily load");
  const warn = dialog("Moved, but some things didn't follow");
  assert.match(await warn.innerText(), /its environment didn't follow \(not found\)\. Pick its environment again on the notebook's status bar\./);
  // An empty folder says so.
  await warn.getByRole("button", { name: "OK" }).click();
  await menu("Folder actions for Q1", "Rename folder…");
  assert.match(await dialog("Rename folder Q1").innerText(), /It has no notebooks in it\./);
  assert.deepStrictEqual(app.errors, []);
});

test("Delete folder refuses a folder with notebooks, asks for an empty one, and shows the engine's refusal", { skip }, async () => {
  let refuse = true;
  over.notebook_folder_delete = ({ path }) => {
    if (refuse) throw new Error(`ValueError: ${path} has a file in it, so it wasn't deleted. Move or delete it first.`);
    return { deleted: true };
  };
  await allNotebooks();
  await menu("Folder actions for Sales", "Delete folder…");
  const no = dialog("Can't delete Sales");
  assert.match(await no.innerText(), /Sales has 1 notebook in it\. Move or delete it first: a folder is deleted only when it's empty/);
  await no.getByRole("button", { name: "OK" }).click();
  assert.strictEqual(sent("notebook_folder_delete").length, 0);
  await menu("Folder actions for Q1", "Delete folder…");
  const confirm = dialog("Delete folder Sales/EMEA/Q1?");
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await confirm.waitFor({ state: "detached" });
  assert.strictEqual(sent("notebook_folder_delete").length, 0);
  await menu("Folder actions for Q1", "Delete folder…");
  await confirm.getByRole("button", { name: "Delete" }).click();
  await dialog("Can't delete Q1").getByText(/Sales\/EMEA\/Q1 has a file in it, so it wasn't deleted/).waitFor();
  await dialog("Can't delete Q1").getByRole("button", { name: "OK" }).click();
  refuse = false;
  await menu("Folder actions for Q1", "Delete folder…");
  await confirm.getByRole("button", { name: "Delete" }).click();
  await toastSays(/^Deleted the folder Sales\/EMEA\/Q1\./);
  assert.deepStrictEqual(sent("notebook_folder_delete"), [{ path: "Sales/EMEA/Q1", place: null }, { path: "Sales/EMEA/Q1", place: null }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Move to another place opens the notebook's move dialog", { skip }, async () => {
  await allNotebooks();
  await menu("More actions for Scratch", "Move to another place…");
  const d = dialog("Move Scratch");
  await d.getByRole("combobox", { name: "Move to" }).waitFor();
  assert.deepStrictEqual(sent("open_notebook"), [{ name: "Scratch" }]);
  assert.deepStrictEqual(await d.getByRole("combobox", { name: "Move to" }).locator("option").allInnerTexts(), ["Analytics"]);
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("The explorer's Notebooks tab folds folders, opens a notebook, and says when it can't list them", { skip }, async () => {
  await openNb("Loose");
  await page().evaluate(() => { state.showExplorer = true; renderNotebook(); });
  await page().locator(".explorer-tabs").getByRole("tab", { name: "Notebooks" }).click();
  const box = page().locator(".nb-explorer-books");
  const sales = box.locator(".nb-explorer-folder", { hasText: "Sales" }).first();
  await sales.waitFor();
  assert.ok(await box.locator(".nb-explorer-book.active", { hasText: "Loose" }).count(), "the open notebook is marked");
  await box.locator(".nb-explorer-book", { hasText: "Daily load" }).waitFor();
  await sales.click();
  await box.locator(".nb-explorer-folder[aria-expanded=false]", { hasText: "Sales" }).waitFor();
  assert.strictEqual(await box.locator(".nb-explorer-book", { hasText: "Daily load" }).count(), 0);
  await box.locator(".nb-explorer-folder", { hasText: "Sales" }).first().click();
  await box.locator(".nb-explorer-book", { hasText: "Daily load" }).click();
  await page().waitForFunction(() => state.notebook?.name === "Sales/EMEA/Daily load");
  over.list_notebooks = () => { throw new Error("OSError: no"); };
  await page().evaluate(() => renderNotebook());
  await page().locator(".nb-explorer-books").getByText("Couldn't list notebooks.").waitFor();
  over.list_notebooks = () => [];
  over.notebook_folders = () => ({ folders: [] });
  await page().evaluate(() => renderNotebook());
  await page().locator(".nb-explorer-books").getByText("No notebooks yet.").waitFor();
  await page().locator(".nb-explorer-books").getByRole("button", { name: "All notebooks" }).click();
  await page().waitForFunction(() => state.view === "notebooks");
  await page().evaluate(() => { state.showExplorer = false; });
  assert.deepStrictEqual(app.errors, []);
});

test("Open a notebook file refuses a file over 20 MB, and adds a copy where the person picks", { skip }, async () => {
  over.notebook_import_check = ({ file_name }) => {
    if (file_name === "bad.ipynb") throw new Error("ValueError: bad.ipynb isn't a Jupyter notebook");
    return { name: "Churn 2", file_name: "Churn", taken: true, cells: 3, code: 2, markdown: 1, outputs_dropped: true,
      lakehouse: { name: "SalesLH" }, workspace: { workspace_name: "Sales WS" }, notes: ["It installs packages with %pip, which runs on this PC."] };
  };
  over.notebook_import = ({ name }) => ({ name });
  await allNotebooks();
  const pick = async (file) => {
    const chooser = page().waitForEvent("filechooser");
    await page().getByRole("button", { name: "Open a notebook file…" }).click();
    await (await chooser).setFiles(file);
  };
  await pick({ name: "big.ipynb", mimeType: "application/json", buffer: Buffer.alloc(21 * 1024 * 1024, 32) });
  await toastSays(/^big\.ipynb is over 20 MB, so it isn't opened\. Pick a file of 20 MB or less\./);
  assert.strictEqual(sent("notebook_import_check").length, 0);
  await pick({ name: "bad.ipynb", mimeType: "application/json", buffer: Buffer.from("{}") });
  await toastSays(/^Couldn't open bad\.ipynb: /);
  const content = JSON.stringify({ cells: [], metadata: {}, nbformat: 4, nbformat_minor: 5 });
  await pick({ name: "churn.ipynb", mimeType: "application/json", buffer: Buffer.from(content) });
  const d = dialog("Open a notebook file");
  await d.getByRole("textbox", { name: "Name" }).waitFor();
  const text = await d.innerText();
  assert.match(text, /churn\.ipynb/);
  assert.match(text, /You already have a notebook named Churn, so this name is suggested\./);
  assert.match(text, /3 cells: 2 code, 1 Markdown\. Outputs in the file are left out; run it to see them here\./);
  assert.match(text, /Its default lakehouse, SalesLH, is attached\./);
  assert.match(text, /It publishes to Sales WS, as saved in the file\./);
  assert.match(text, /It installs packages with %pip, which runs on this PC\./);
  assert.strictEqual(await d.getByRole("textbox", { name: "Name" }).inputValue(), "Churn 2");
  await d.getByRole("combobox", { name: "Save in" }).selectOption({ label: "Analytics repository" });
  await d.getByRole("button", { name: "Add and open" }).click();
  await toastSays(/^Added Churn 2\. churn\.ipynb wasn't changed\./);
  assert.deepStrictEqual(sent("notebook_import"), [{ content, name: "Churn 2", place: REPO.root }]);
  await page().waitForFunction(() => state.notebook?.name === "Churn 2");
  assert.deepStrictEqual(app.errors, []);
});

test("Edit on this PC copies a Fabric notebook here, or opens the copy already here", { skip }, async () => {
  const item = { name: "Revenue", workspace_id: "ws1", item_id: "it1", workspace_name: "Sales WS" };
  let check = { here: "Revenue" };
  over.notebook_fabric_check = () => check;
  over.notebook_from_fabric = (p) => ({ name: p.name });
  await page().evaluate((item) => { nbEditFromFabric(item); }, item);
  await toastSays(/^Revenue is already on this PC; opening it\./);
  await page().waitForFunction(() => state.notebook?.name === "Revenue");
  assert.deepStrictEqual(sent("notebook_fabric_check"), [{ workspace_id: "ws1", item_id: "it1", name: "Revenue" }]);
  check = { taken: true };
  await page().evaluate((item) => { nbEditFromFabric(item); }, item);
  await toastSays(/^You already have a notebook named Revenue that isn't this Fabric item\. Rename it first/);
  check = { cells: 4, lakehouse: { name: "SalesLH" }, notes: ["It uses mssparkutils, which runs only in Fabric."] };
  await page().evaluate((item) => { nbEditFromFabric(item); }, item);
  const d = dialog("Edit Revenue on this PC");
  await d.getByRole("button", { name: "Copy to this PC" }).waitFor();
  const text = await d.innerText();
  assert.match(text, /4 cells\. It keeps the name Revenue and stays linked to this Fabric item/);
  assert.match(text, /Its default lakehouse, SalesLH, is attached\./);
  assert.match(text, /It uses mssparkutils, which runs only in Fabric\./);
  assert.strictEqual(sent("notebook_from_fabric").length, 0, "nothing is copied before the person confirms");
  await d.getByRole("combobox", { name: "Save in" }).selectOption({ label: "Analytics repository" });
  await d.getByRole("button", { name: "Copy to this PC" }).click();
  await toastSays(/^Revenue is on this PC\. Publish updates the same Fabric item\./);
  assert.deepStrictEqual(sent("notebook_from_fabric"), [{ workspace_id: "ws1", item_id: "it1", name: "Revenue", workspace_name: "Sales WS", place: REPO.root }]);
  over.notebook_fabric_check = () => { throw new Error("PermissionError: 403"); };
  await page().evaluate((item) => { nbEditFromFabric(item); }, item);
  await toastSays(/^Couldn't read Revenue from Fabric: /);
  assert.deepStrictEqual(app.errors, []);
});

test("Before Publish: unchanged goes on; changed in Fabric offers a copy of Fabric's version or a confirmed replace", { skip }, async () => {
  let check = { changed: false };
  over.notebook_publish_check = () => check;
  over.notebook_from_fabric = (p) => ({ name: p.copy_name });
  await openNb("Revenue", { workspace: { workspace_id: "ws1", workspace_name: "Sales WS" } });
  const gate = () => page().evaluate(() => { window.__gate = nbFabricChangeGate(state.notebook); });
  const answer = () => page().evaluate(() => window.__gate);
  await gate();
  assert.deepStrictEqual(await answer(), { replace: false });
  assert.strictEqual(sent("notebook_publish_check")[0].name, "Revenue");
  check = { changed: true, item_id: "it9", published_at: null,
    fabric: [{ kind: "reordered" }, { kind: "other item" }, { kind: "added", diff: [] }, { kind: "changed", position: 2, diff: ["-a = 1", "+a = 2", " b"] }],
    mine: [{ kind: "changed" }] };
  await gate();
  const d = dialog("Revenue changed in Fabric");
  await d.getByRole("heading", { name: "Changed in Fabric" }).waitFor();
  assert.deepStrictEqual(await d.locator(".nbf-change b").allInnerTexts(), ["Cells were reordered",
    "A different notebook in Fabric now has this name (the one published from here was renamed or deleted)", "A cell: added", "Cell 2: changed"]);
  assert.deepStrictEqual(await d.locator(".nbf-diff span.add").allInnerTexts(), ["+a = 2"]);
  assert.deepStrictEqual(await d.locator(".nbf-diff span.del").allInnerTexts(), ["-a = 1"]);
  assert.match(await d.innerText(), /Someone changed it in Sales WS after it was last published from here\. Publishing now would replace those changes/);
  assert.match(await d.innerText(), /Here, the notebook changed since then\. Nothing has been published yet\./);
  await d.getByRole("button", { name: "Copy Fabric's version here first" }).click();
  await toastSays(/^Saved Fabric's version as Revenue \(Fabric\)\. Nothing was published; compare them, then publish again\./);
  assert.strictEqual(await answer(), null);
  assert.deepStrictEqual(sent("notebook_from_fabric"), [{ workspace_id: "ws1", item_id: "it9", name: "Revenue", workspace_name: "Sales WS", copy_name: "Revenue (Fabric)" }]);
  // Replace asks again; Cancel there publishes nothing, Replace with mine goes on.
  await gate();
  await d.getByRole("button", { name: "Replace with mine" }).click();
  const sure = dialog("Replace the changes in Fabric?");
  assert.match(await sure.innerText(), /Publishing replaces Revenue in Sales WS with the notebook here\./);
  await sure.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await answer(), null);
  await gate();
  await d.getByRole("button", { name: "Replace with mine" }).click();
  await sure.getByRole("button", { name: "Replace with mine" }).click();
  assert.deepStrictEqual(await answer(), { replace: true });
  // A check that fails stops.
  over.notebook_publish_check = () => { throw new Error("PermissionError: 403"); };
  await gate();
  assert.strictEqual(await answer(), null);
  await toastSays(/^Couldn't check whether Revenue changed in Fabric: /);
  assert.deepStrictEqual(app.errors, []);
});

test("The command bar's notebook file commands", { skip }, async () => {
  const run = async (label) => {
    await page().keyboard.press("Control+k");
    await page().locator("#palette").waitFor();
    await page().keyboard.type(label);
    await page().locator("#palette .palette-item", { hasText: label }).first().click();
  };
  await run("Save a copy of this notebook");
  await toastSays(/^Open a notebook first\./);
  await page().evaluate(() => document.querySelector("#toast")?.replaceChildren());
  await run("Move this notebook to a folder");
  await toastSays(/^Open a notebook first\./);
  await run("New notebook folder");
  await dialog("New folder").getByRole("button", { name: "Cancel" }).click();
  await run("Search inside notebooks");
  await page().waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Search notebook names and code");
  await page().evaluate(() => go("home"));
  await run("All notebooks");
  await page().waitForFunction(() => state.view === "notebooks");
  await openNb("Loose");
  await run("Move this notebook to a folder");
  await dialog("Move Loose").getByRole("combobox", { name: "Folder" }).waitFor();
  await dialog("Move Loose").getByRole("button", { name: "Cancel" }).click();
  await run("Save a copy of this notebook");
  await dialog("Save a copy of Loose").getByRole("button", { name: "Cancel" }).click();
  const chooser = page().waitForEvent("filechooser");
  await run("Open a notebook file");
  assert.ok(await chooser);
  assert.deepStrictEqual(app.errors, []);
});
