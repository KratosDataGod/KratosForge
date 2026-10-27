// Folders for notebooks (lifecycle parity row 14) against the real engine: All notebooks groups
// notebooks by folder, New folder, Move to folder (refusing a name that's taken), Rename folder,
// Delete folder only when empty, and the explorer's Notebooks tab.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "rename_notebook", "notebook_places", "notebook_listing",
  "notebook_search", "notebook_deleted", "pc_schedules_for", "compute_check", "kernel_variables", "new_notebook",
  "notebook_folders", "notebook_folder_create", "notebook_folder_rename", "notebook_folder_delete", "notebook_move_to_folder"];

let app;
const page = () => app.page;
const names = () => page().evaluate(() => rpc("list_notebooks").then((l) => l.map((n) => n.name).sort()));
const group = (name) => page().locator(".nbf-group .nbf-fold", { hasText: name });

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, engine: ENGINE });
  await page().evaluate(async () => {
    const save = (name, source) => rpc("save_notebook", { name, cells: [{ type: "code", source }] });
    await save("Daily load", "orders = 1");
    await save("Returns", "returns = 1");
    go("notebooks");
  });
});

after(async () => { if (app) await app.close(); });

test("New folder makes an empty folder and the list groups by folder with counts", { skip }, async () => {
  await page().getByRole("heading", { name: "All notebooks" }).waitFor();
  await page().getByRole("button", { name: "Daily load", exact: true }).waitFor();
  await page().getByRole("button", { name: "New folder" }).click();
  const dialog = page().getByRole("dialog", { name: "New folder" });
  await dialog.getByRole("textbox", { name: "Folder name" }).fill("Sales");
  await dialog.getByRole("button", { name: "Make folder" }).click();
  await group("Sales").waitFor();
  assert.strictEqual((await group("Sales").locator(".nbf-count").innerText()).trim(), "0");
  assert.strictEqual((await group("Not in a folder").locator(".nbf-count").innerText()).trim(), "2");
  // Making it again is refused in the dialog, and nothing changes.
  await page().getByRole("button", { name: "New folder" }).click();
  const again = page().getByRole("dialog", { name: "New folder" });
  await again.getByRole("textbox", { name: "Folder name" }).fill("Sales");
  await again.getByRole("button", { name: "Make folder" }).click();
  await again.getByRole("alert").getByText("already in the app's notebooks folder", { exact: false }).waitFor();
  await again.getByRole("button", { name: "Cancel" }).click();
  // The How folders work panel names every write.
  const how = await page().locator(".nbf-page").innerText();
  for (const action of ["New folder", "Move to folder", "Rename folder", "Delete folder", "Publish"]) assert.match(how, new RegExp(action));
  assert.deepStrictEqual(app.errors, []);
});

test("Move to folder moves a notebook in, and refuses a name the folder already has", { skip }, async () => {
  await page().getByRole("button", { name: "More actions for Daily load" }).click();
  await page().getByRole("menuitem", { name: "Move to folder…" }).click();
  const dialog = page().getByRole("dialog", { name: "Move Daily load" });
  await dialog.getByRole("combobox", { name: "Folder" }).selectOption("Sales");
  assert.match(await dialog.innerText(), /It becomes Sales\/Daily load, in This PC/);
  assert.match(await dialog.innerText(), /Preview/);  // publishing from a folder is untested on a tenant
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-folders-move.png") });
  await dialog.getByRole("button", { name: "Move" }).click();
  await page().getByRole("button", { name: "More actions for Sales/Daily load" }).waitFor();
  assert.deepStrictEqual(await names(), ["Returns", "Sales/Daily load"]);
  assert.strictEqual((await group("Sales").locator(".nbf-count").innerText()).trim(), "1");
  // A notebook named Returns is now in Sales too: moving the other one there is refused, in place.
  await page().evaluate(() => rpc("save_notebook", { name: "Sales/Returns", cells: [{ type: "code", source: "theirs = 1" }] }));
  await page().getByRole("button", { name: "More actions for Returns" }).click();
  await page().getByRole("menuitem", { name: "Move to folder…" }).click();
  const second = page().getByRole("dialog", { name: "Move Returns" });
  await second.getByRole("combobox", { name: "Folder" }).selectOption("Sales");
  await second.getByRole("button", { name: "Move" }).click();
  await second.getByRole("alert").getByText("Sales already has a notebook named 'Returns'. Nothing was moved.").waitFor();
  await second.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(await names(), ["Returns", "Sales/Daily load", "Sales/Returns"]);
  // A folder folds away with its arrow.
  await page().evaluate(() => render());
  await group("Sales").click();
  await page().getByRole("button", { name: "More actions for Sales/Daily load" }).waitFor({ state: "detached" });
  assert.strictEqual(await group("Sales").getAttribute("aria-expanded"), "false");
  await group("Sales").click();
  await page().getByRole("button", { name: "More actions for Sales/Daily load" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Rename folder renames its notebooks; a folder with notebooks can't be deleted; an empty one can", { skip }, async () => {
  await page().getByRole("button", { name: "Folder actions for Sales" }).click();
  await page().getByRole("menuitem", { name: "Rename folder…" }).click();
  const dialog = page().getByRole("dialog", { name: "Rename folder Sales" });
  assert.match(await dialog.innerText(), /2 notebooks move with it/);
  await dialog.getByRole("textbox", { name: "Name" }).fill("Sales EMEA");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-folders-rename.png") });
  await dialog.getByRole("button", { name: "Rename" }).click();
  await group("Sales EMEA").waitFor();
  assert.deepStrictEqual(await names(), ["Returns", "Sales EMEA/Daily load", "Sales EMEA/Returns"]);

  await page().getByRole("button", { name: "Folder actions for Sales EMEA" }).click();
  await page().getByRole("menuitem", { name: "Delete folder…" }).click();
  const refusal = page().getByRole("dialog", { name: "Can't delete Sales EMEA" });
  assert.match(await refusal.innerText(), /has 2 notebooks in it\. Move or delete them first/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-folders-delete-refused.png") });
  await refusal.getByRole("button", { name: "OK" }).click();
  assert.deepStrictEqual(await names(), ["Returns", "Sales EMEA/Daily load", "Sales EMEA/Returns"]);

  await page().evaluate(() => rpc("notebook_folder_create", { path: "Scratch" }).then(() => render()));
  await page().getByRole("button", { name: "Folder actions for Scratch" }).click();
  await page().getByRole("menuitem", { name: "Delete folder…" }).click();
  const confirm = page().getByRole("dialog", { name: "Delete folder Scratch?" });
  assert.ok(await confirm.locator(".danger-solid").count(), "the delete button is red");
  await confirm.getByRole("button", { name: "Delete" }).click();
  await group("Scratch").waitFor({ state: "detached" });
  const left = await page().evaluate(() => rpc("notebook_folders").then((o) => o.folders.map((f) => f.path)));
  assert.deepStrictEqual(left, ["Sales EMEA"]);
  assert.deepStrictEqual(app.errors, []);
});

test("the notebook explorer's Notebooks tab groups by folder", { skip }, async () => {
  await page().evaluate(() => openNotebook("Sales EMEA/Daily load"));
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Sales EMEA/Daily load");
  await page().evaluate(() => { state.showExplorer = true; renderNotebook(); });
  await page().locator(".explorer-tabs").getByRole("button", { name: "Notebooks" }).click();
  const folder = page().locator(".nb-explorer-folder", { hasText: "Sales EMEA" });
  await folder.waitFor();
  assert.strictEqual((await folder.locator(".nbf-count").innerText()).trim(), "2");
  assert.ok(await page().locator(".nb-explorer-book.active", { hasText: "Daily load" }).count());
  await page().locator(".nb-explorer-book", { hasText: "Returns" }).first().waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-folders-explorer.png") });
  assert.deepStrictEqual(app.errors, []);
});
