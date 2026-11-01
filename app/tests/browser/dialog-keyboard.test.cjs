// Dialogs from the keyboard, in a real browser (#1283): each common dialog, opened with Enter on a
// button, starts with focus inside; Tab and Shift+Tab wrap inside it and never reach the page behind;
// Escape closes it; and focus goes back to the button that opened it (app.js, dialogFocus and trapFocus).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const APPROVAL = { id: "ap_1", client_name: "Codex", title: "Run Python", permission: "EXECUTE_LOCAL_CODE", created_at: Date.now() / 1000,
  summary: { action: "Run Python on this PC", target: { workspace_id: "ws1" }, content: { text: "print(1)" } } };

// Each dialog, as the code that opens it in the app.
const DIALOGS = [
  ["Report a problem", "reportProblem()"],
  ["Keyboard shortcuts", "shortcutsDialog()"],
  ["An AI tool approval", `showApproval(${JSON.stringify(APPROVAL)}, null)`],
  ["A text prompt", 'askText("Rename notebook", "New name", "Daily sales", "Rename")'],
  ["The confirm dialog", 'confirmDialog("Delete Orders?", "Its files are removed from OneLake.", "Delete", { danger: true })'],
];

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings"] });  // Report a problem reads the support contact from settings
  await page().waitForSelector("#rail-main .rail-item");
  await page().evaluate(() => loadSettings());
});

after(async () => { await app?.close(); });

const where = () => page().evaluate(() => {
  const a = document.activeElement;
  return { inside: !!document.getElementById("dialog-root").contains(a), opener: a?.id === "opener", label: a?.getAttribute("aria-label") || a?.textContent?.trim().slice(0, 40) || a?.tagName };
});

// The dialog's Tab stops in order, as trapFocus counts them.
const stops = () => page().evaluate(() => [...document.getElementById("dialog-root").querySelectorAll(FOCUSABLE)]
  .filter((el) => el.offsetParent !== null).length);

for (const [name, open] of DIALOGS) {
  test(`${name}: focus starts inside, Tab wraps inside it, Escape closes it and focus goes back`, { skip }, async () => {
    await page().evaluate((open) => {
      document.getElementById("opener")?.remove();
      const b = h("button", { id: "opener", onclick: () => (0, eval)(open) }, "Open");
      document.getElementById("view").prepend(b);
      b.focus();
    }, open);
    await page().keyboard.press("Enter");
    await page().locator("#dialog-root .dialog").waitFor();
    assert.ok((await where()).inside, "focus didn't move into the dialog");

    // Tab through every stop and one more: it never leaves, and comes back to where it started.
    const n = await stops();
    assert.ok(n >= 2, `only ${n} Tab stops`);
    const start = (await where()).label;
    const seen = [];
    for (let i = 0; i < n; i++) {
      await page().keyboard.press("Tab");
      const at = await where();
      assert.ok(at.inside, `Tab ${i + 1} left the dialog for ${at.label}`);
      seen.push(at.label);
    }
    assert.strictEqual(seen[n - 1], start, `after ${n} Tabs focus is on ${seen[n - 1]}, not back on ${start}: ${seen.join(" > ")}`);
    // And backwards: Shift+Tab the same number of times also comes back round.
    for (let i = 0; i < n; i++) {
      await page().keyboard.press("Shift+Tab");
      assert.ok((await where()).inside, `Shift+Tab ${i + 1} left the dialog`);
    }
    assert.strictEqual((await where()).label, start);

    await page().keyboard.press("Escape");
    await page().locator("#dialog-root .dialog").waitFor({ state: "detached" });
    assert.ok((await where()).opener, `focus went to ${(await where()).label}, not the button that opened the dialog`);
    assert.deepStrictEqual(app.errors, []);
  });
}
