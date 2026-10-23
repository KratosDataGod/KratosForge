// Panel and dialog transitions (docs/forge-animations.md, KF-MO-003): dialogs, the activity tray and
// side panes slide and fade in at Minimal and above, never at Off or with reduced motion. Focus is in a
// dialog at once, Escape takes it away at once, and what fades after a close is a blank, hidden card.
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const apps = [];
after(async () => { for (const app of apps) await app.close(); });

async function open({ level = 2, reducedMotion = "no-preference" } = {}) {
  const app = await openApp({ mocks: { kernel_variables: () => [] } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion });
  await app.page.evaluate((level) => {
    state.settings = { ...state.settings, motion_level: { value: level } };
    applyMotion();
    // A button that opens a confirmation, so focus has somewhere to go back to.
    const b = h("button", { id: "opener", onclick: () => { window.__answer = confirmDialog("Delete Sales", "Delete it?", "Delete"); } }, "Open");
    document.body.append(b);
  }, level);
  return app;
}

// Opens the dialog and reports what the first frame has: focus inside, and whether it animates in.
async function openDialog(page) {
  await page.click("#opener");
  return page.evaluate(() => {
    const root = document.getElementById("dialog-root");
    const dialog = root.querySelector(".dialog");
    return { focusInside: root.contains(document.activeElement), marked: root.firstElementChild.classList.contains("mo-in"),
      animating: dialog.getAnimations().length > 0 };
  });
}

const afterClose = (page) => page.evaluate(() => ({
  open: document.getElementById("dialog-root").childElementCount,
  focus: document.activeElement?.id || document.activeElement?.tagName,
  ghost: document.querySelector(".mo-ghost")?.getAttribute("aria-hidden") ?? null,
  ghostText: document.querySelector(".mo-ghost")?.textContent ?? null,
  dialogs: document.querySelectorAll(".dialog, .overlay, [role=dialog]").length,
}));

test("At Refined a dialog animates in with focus already inside; Cancel takes it away at once and a blank card fades", { skip }, async () => {
  const app = await open();
  const { page } = app;
  assert.deepStrictEqual(await openDialog(page), { focusInside: true, marked: true, animating: true });
  await page.getByRole("button", { name: "Cancel" }).click();
  const closed = await afterClose(page);
  assert.deepStrictEqual(closed, { open: 0, focus: "opener", ghost: "true", ghostText: "", dialogs: 0 });
  assert.strictEqual(await page.evaluate(() => window.__answer), false);
  await page.locator(".mo-ghost").waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("Escape closes a dialog at once with nothing fading", { skip }, async () => {
  const app = await open({ level: 3 });
  const { page } = app;
  await openDialog(page);
  await page.keyboard.press("Escape");
  assert.deepStrictEqual(await afterClose(page), { open: 0, focus: "opener", ghost: null, ghostText: null, dialogs: 0 });
  assert.deepStrictEqual(app.errors, []);
});

test("Minimal still has the transitions; Off and reduced motion have none", { skip }, async () => {
  const minimal = await open({ level: 1 });
  assert.deepStrictEqual(await openDialog(minimal.page), { focusInside: true, marked: true, animating: true });
  for (const opts of [{ level: 0 }, { level: 3, reducedMotion: "reduce" }]) {
    const app = await open(opts);
    const { page } = app;
    assert.deepStrictEqual(await openDialog(page), { focusInside: true, marked: false, animating: false }, JSON.stringify(opts));
    await page.getByRole("button", { name: "Cancel" }).click();
    assert.strictEqual((await afterClose(page)).ghost, null, `a card faded with ${JSON.stringify(opts)}`);
    assert.deepStrictEqual(app.errors, []);
  }
});

test("The activity tray slides in, isn't replayed when it redraws, and leaves at once", { skip }, async () => {
  const app = await open();
  const { page } = app;
  await page.evaluate(() => toggleTray(true));
  const tray = page.locator(".tray");
  assert.ok(await tray.evaluate((el) => el.classList.contains("mo-in")));
  await page.waitForFunction(() => document.querySelector(".tray")?.getAnimations().length === 0);
  await page.evaluate(() => renderTray());
  assert.strictEqual(await tray.evaluate((el) => el.classList.contains("mo-in")), false, "a redraw played the tray's way in again");
  await page.evaluate(() => toggleTray(false));
  assert.strictEqual(await page.locator(".tray").count(), 0);
  assert.strictEqual(await page.locator(".mo-ghost .mo-ghost-card.side").count(), 1);
  assert.deepStrictEqual(app.errors, []);
});

test("A side pane opened on the notebook slides in; opening the notebook doesn't animate the Explorer", { skip }, async () => {
  const app = await open();
  const { page } = app;
  await page.evaluate(() => {
    loadIntoState({ name: "Daily sales", cells: [{ type: "code", cell_id: "a", source: "x = 1", meta: {} }] });
    go("notebook");
  });
  await page.locator(".explorer").first().waitFor();
  assert.strictEqual(await page.locator(".explorer.mo-in").count(), 0, "the Explorer animated as the page opened");
  await page.evaluate(() => toggleVariables());
  const pane = page.locator(".varpane");
  await pane.waitFor();
  assert.ok(await pane.evaluate((el) => el.classList.contains("mo-in")), "the Variables pane didn't slide in");
  assert.strictEqual(await page.locator(".explorer.mo-in").count(), 0, "the Explorer replayed when the Variables pane opened");
  assert.deepStrictEqual(app.errors, []);
});
