// Notebook environments (nbenv.js), the paths nbenv-sessions.test leaves out: notebooks that don't run
// in their own process (the dialog says why and opens Settings), picking an environment and going back
// to none (with the restart it may need), installing with Enter, removing from an environment no
// notebook uses, and an organization that doesn't allow installing. The engine's replies are mocked;
// what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const NB = "Daily sales";

const db = {};
let app;
const page = () => app.page;
const dialog = (name) => page().getByRole("dialog", { name });
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

function envs() {
  return [{ name: "Data science", packages: ["scikit-learn==1.5.2"], installed: [{ name: "scikit-learn", version: "1.5.2" }], notebooks: [] }];
}

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    notebook_environments: () => ({ process: db.process, allowed: db.allowed, source: db.source, current: db.current, environments: envs() }),
    environment_attach: (p) => { db.current = p.name; return { restart_needed: !!p.name }; },
    environment_install: (p) => ({ id: "j1", environment: p.name, packages: p.packages, remove: false, state: "done", log: "Successfully installed polars-ds-0.6.0" }),
    environment_uninstall: (p) => ({ id: "j2", environment: p.name, packages: p.packages, remove: true, state: "done", log: "" }),
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, { process: true, allowed: true, source: "", current: null });
  await page().evaluate(() => { closeDialog(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
  app.calls.length = 0;
  app.errors.length = 0;
});

const open = (nb = NB) => page().evaluate((name) => environmentDialog({ name }), nb);

test("without notebooks in their own process the dialog says why and opens Settings", { skip }, async () => {
  db.process = false;
  await open();
  const d = dialog("Notebook environment");
  assert.match(await d.innerText(), /They need Run notebooks in their own process/);
  assert.match(await d.innerText(), /Until then, notebooks use the packages the app ships/);
  await page().evaluate(() => { window.__go = window.go; window.__went = []; window.go = (v) => window.__went.push(v); });
  await d.getByRole("button", { name: "Settings" }).click();
  assert.deepStrictEqual(await page().evaluate(() => { window.go = window.__go; return window.__went; }), ["settings"]);
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("picking an environment asks for a restart; going back to none sends no name", { skip }, async () => {
  db.source = "https://pypi.contoso.com/simple";
  await open();
  const d = dialog("Notebook environment");
  assert.strictEqual(await d.getByRole("button", { name: "Delete environment" }).count(), 0, "no environment picked yet");
  await d.getByRole("combobox", { name: "Environment" }).selectOption("Data science");
  await toastSays(/^Restart the kernel to use it\. Variables are cleared\.$/);
  await d.locator("td", { hasText: "scikit-learn" }).waitFor();
  assert.match(await d.innerText(), /Packages come from: https:\/\/pypi\.contoso\.com\/simple\./);
  await d.getByRole("combobox", { name: "Environment" }).selectOption("");
  await page().waitForFunction(() => document.querySelector('.dialog select[aria-label="Environment"]')?.value === "");
  assert.deepStrictEqual(sent("environment_attach"), [{ notebook: NB, name: "Data science" }, { notebook: NB, name: null }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Enter in the packages box installs after a confirm, and the log shows when it's done", { skip }, async () => {
  db.current = "Data science";
  await open();
  const box = dialog("Notebook environment").getByRole("textbox", { name: "Packages to install" });
  await box.fill("polars-ds, rich");
  await box.press("Enter");
  const confirm = dialog("Install into Data science?");
  assert.match(await confirm.innerText(), /pip downloads polars-ds, rich from PyPI/);
  await confirm.getByRole("button", { name: "Install" }).click();
  const d = dialog("Notebook environment");
  await d.locator(".env-job").getByText("Done.").waitFor();
  assert.strictEqual(await d.locator(".env-log").innerText(), "Successfully installed polars-ds-0.6.0");
  assert.deepStrictEqual(sent("environment_install"), [{ name: "Data science", packages: ["polars-ds", "rich"] }]);
  assert.deepStrictEqual(app.errors, []);
});

test("removing a package from an environment no notebook uses says so; Cancel goes back with nothing sent", { skip }, async () => {
  db.current = "Data science";
  await open();
  await dialog("Notebook environment").getByRole("button", { name: "Remove" }).click();
  const confirm = dialog("Remove scikit-learn from Data science?");
  assert.match(await confirm.innerText(), /Its files are deleted from this PC, so notebooks using it can't import it any more\./);
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await dialog("Notebook environment").waitFor();
  assert.deepStrictEqual(sent("environment_uninstall"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("an organization that doesn't allow installing gets no packages box", { skip }, async () => {
  db.current = "Data science";
  db.allowed = false;
  db.source = "https://pypi.contoso.com/simple";
  await open();
  const d = dialog("Notebook environment");
  await d.getByText("Your organization doesn't allow installing packages on this PC.").waitFor();
  assert.strictEqual(await d.getByRole("textbox", { name: "Packages to install" }).count(), 0);
  assert.strictEqual(await d.getByText(/Packages come from/).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
