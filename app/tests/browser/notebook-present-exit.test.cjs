// Notebook Present (nbpresent.js, N49, Preview): the edges a Preview-exit review found untested.
//   - Small windows and zoom: Present in a 480x320 and an 800x600 window, and at 200% zoom. Zoom is a
//     page with deviceScaleFactor 2 and half the CSS viewport (720x470 for a 1440x940 window), which is
//     what Chromium and WebView2 do at 200%; the harness can set both. The page doesn't scroll sideways,
//     Slides, Leave, Previous and Next are on screen and not covered, the slide's text wraps inside it,
//     and its first heading shows whole, with the rest reachable by scrolling the slide.
//   - The engine's export page (notebook_print_page) failing: an error toast says "Couldn't show the
//     notebook's outputs. Open Present again." with the engine's reason in Activity, Present doesn't
//     open half drawn, nothing stays waiting, Esc and the notebook still work, and Present opens once
//     the engine answers again.
//   - Tab and Shift+Tab while presenting, through a slide with several links, a slide with nothing
//     focusable, and with the slide strip open: focus stays in the Present dialog, wraps at each end,
//     and never lands in the notebook behind it.
//   - The UX review's must-fix items (mockup NNYEnNiJkZuTxywzXHPTDT, section 2): S opens a grid of
//     thumbnails below the slide, not a panel beside it, so at 390px the slide keeps the window's width;
//     and the slide's text scales with the slide's width (18px body and 34px # heading on a wide window,
//     smaller on a narrow one) instead of staying at 18px; and the run line in the app's relative words.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const MOCKS = { save_notebook: () => ({ saved: true }), notebook_syntax: () => [], notebook_dependencies: () => ({ cells: [] }) };

const NB = { name: "Quarterly review of the western region sales pipeline", cells: [
  { type: "markdown", cell_id: "m1", source: "# Quarterly review of revenue by region\n\nRevenue by region since 1 October, with a sentence long enough to wrap across the slide rather than run off its right edge.\n\n- First point\n- Second point\n- Third point", meta: {} },
  { type: "code", cell_id: "c1", source: "show(df)", meta: {} },
  { type: "markdown", cell_id: "m2", source: "## Sources\n\nSee [the dashboard](https://example.com/dash), [the model](https://example.com/model) and [the runbook](https://example.com/runbook).", meta: {} },
  { type: "markdown", cell_id: "m3", source: "## Plain\n\nNothing here takes focus.", meta: {} },
] };

let app;      // the real engine's export page, a 1440x940 window
let zoomed;   // the same window at 200%
let failing;  // an engine whose export page fails until told otherwise
let printFails = null;  // the error failing's notebook_print_page throws, or null to answer

async function load(p, nb = NB) {
  await p.evaluate((nb) => {
    closeNotebookPresent();
    state.settings = { ...(state.settings || {}), show_preview: { ...(state.settings?.show_preview || {}), value: true } };
    loadIntoState(structuredClone(nb));
    const c1 = state.notebook.cells.find((c) => c.cell_id === "c1");
    if (c1) {
      c1.outputs = [{ type: "table", columns: ["region", "revenue"], rows: [["West", 1204330.5], ["North", 998120]] }];
      c1.finishedAtMs = Date.UTC(2026, 10, 14, 10, 42);
    }
    state.ribbonTab = "view";
    go("notebook");
  }, nb);
  await p.locator(".cell .cm-content").first().waitFor();
}

const present = (p) => p.getByRole("dialog", { name: /presenting$/ });

async function openPresent(p) {
  // The first cell selected, as a click on its gutter does (in a 480px window the gutter is under the explorer).
  await p.evaluate(() => { state.selected = state.notebook.cells[0].id; document.querySelector(".cell").focus(); });
  await p.keyboard.press("Control+Shift+F5");
  await present(p).waitFor();
}

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_print_page"], mocks: MOCKS });
  zoomed = await openApp({ engine: ["notebook_print_page"], mocks: MOCKS, viewport: { width: 720, height: 470 }, scale: 2 });
  // Present as the overlay that covers the window (the window refused full screen, as notebook-present
  // tests): headless full screen would change the window's size under these measurements.
  for (const a of [app, zoomed]) await a.page.evaluate(() => { Element.prototype.requestFullscreen = () => Promise.reject(new TypeError("refused")); });
  failing = await openApp({ mocks: { ...MOCKS, notebook_print_page: () => {
    if (printFails) throw new Error(printFails);
    return { html: '<div class="cell"><div class="label">In [1]</div><pre class="src">show(df)</pre><pre class="out">loaded 2 rows</pre></div>' };
  } } });
});
after(async () => { await app?.close(); await zoomed?.close(); await failing?.close(); });

// ---------- small windows and zoom ----------

// What the window shows of Present: page scroll, the controls' boxes and what is on top at their middles,
// and the slide's box and its text.
function layout(p) {
  return p.evaluate(() => {
    const box = (el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom }; };
    const root = document.querySelector(".nb-present");
    const controls = {};
    for (const [name, el] of [["Slides", root.querySelector("[data-nbp-strip]")], ["Leave", [...root.querySelectorAll(".nbp-top button")].find((b) => /Leave/.test(b.textContent))],
      ["Previous", root.querySelector("[data-nbp-prev]")], ["Next", root.querySelector("[data-nbp-next]")]]) {
      const b = box(el);
      const top = document.elementFromPoint(b.x + b.w / 2, b.y + b.h / 2);
      controls[name] = { ...b, onTop: el === top || el.contains(top) };
    }
    const slide = root.querySelector(".nbp-slide");
    const cs = getComputedStyle(slide);
    const inner = { x: slide.getBoundingClientRect().x + slide.clientLeft + parseFloat(cs.paddingLeft),
      r: slide.getBoundingClientRect().x + slide.clientLeft + slide.clientWidth - parseFloat(cs.paddingRight) };
    const head = slide.querySelector("h1, h2");
    const text = [...slide.querySelectorAll(".nbp-md h1, .nbp-md h2, .nbp-md p, .nbp-md li")];
    return {
      w: innerWidth, h: innerHeight,
      pageScrollX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      rootScrollX: root.scrollWidth - root.clientWidth,
      controls,
      slide: { ...box(slide), clientTop: slide.getBoundingClientRect().y + slide.clientTop, clientBottom: slide.getBoundingClientRect().y + slide.clientTop + slide.clientHeight,
        overflowY: cs.overflowY, scrollable: slide.scrollHeight > slide.clientHeight },
      head: head && box(head),
      // Text that runs past the slide's sides, or wider than its own line box (a word that doesn't wrap).
      wide: text.filter((el) => { const b = el.getBoundingClientRect(); return b.left < inner.x - 1 || b.right > inner.r + 1 || el.scrollWidth > el.clientWidth + 1; })
        .map((el) => el.textContent.slice(0, 30)),
    };
  });
}

function checkFits(l, label) {
  assert.ok(l.pageScrollX <= 0, `${label}: the page scrolls sideways by ${l.pageScrollX}px`);
  assert.ok(l.rootScrollX <= 0, `${label}: Present scrolls sideways by ${l.rootScrollX}px`);
  for (const [name, c] of Object.entries(l.controls)) {
    assert.ok(c.x >= 0 && c.y >= 0 && c.r <= l.w && c.b <= l.h, `${label}: ${name} is off screen ${JSON.stringify(c)}`);
    assert.ok(c.onTop, `${label}: ${name} is covered`);
  }
  assert.ok(l.slide.x >= 0 && l.slide.r <= l.w, `${label}: the slide runs off the side ${JSON.stringify(l.slide)}`);
  assert.deepStrictEqual(l.wide, [], `${label}: text runs past the slide`);
  // The first heading shows whole, not cut through by the slide's edge.
  assert.ok(l.head, `${label}: a heading`);
  assert.ok(l.head.y >= l.slide.clientTop - 1 && l.head.b <= l.slide.clientBottom + 1,
    `${label}: the heading is cut: heading ${JSON.stringify(l.head)}, slide shows ${l.slide.clientTop}-${l.slide.clientBottom}`);
  // What doesn't fit scrolls inside the slide.
  if (l.slide.scrollable) assert.match(l.slide.overflowY, /auto|scroll/, `${label}: the slide's overflow is cut off`);
}

async function checkWindow(p, label) {
  await load(p);
  await openPresent(p);
  try {
    for (let i = 0; i < 3; i += 1) {  // the title slide with the table, the links, the plain one
      checkFits(await layout(p), `${label}, slide ${i + 1}`);
      // The last line of the slide can be scrolled into view, inside the slide.
      const last = await p.evaluate(() => {
        const slide = document.querySelector(".nbp-slide");
        slide.scrollTop = slide.scrollHeight;
        const items = slide.querySelectorAll(".nbp-md p, .nbp-md li, .nbp-out tr");
        const b = items[items.length - 1].getBoundingClientRect();
        const s = slide.getBoundingClientRect();
        slide.scrollTop = 0;
        return { b: b.bottom, sb: s.top + slide.clientTop + slide.clientHeight };
      });
      assert.ok(last.b <= last.sb + 1, `${label}, slide ${i + 1}: the last line can't be scrolled into view ${JSON.stringify(last)}`);
      if (i < 2) await p.locator("[data-nbp-next]").click();
      if (i < 2) assert.strictEqual(await p.locator(".nbp-count").innerText(), `${i + 2} / 3`);
    }
    // The controls work by pointer at this size.
    await p.locator("[data-nbp-prev]").click();
    assert.strictEqual(await p.locator(".nbp-count").innerText(), "2 / 3");
    await p.locator("[data-nbp-strip]").click();
    await p.getByRole("navigation", { name: "Slides" }).waitFor();
    assert.ok((await layout(p)).pageScrollX <= 0, `${label}: the strip makes the page scroll sideways`);
    await p.locator("[data-nbp-strip]").click();
    await p.locator(".nbp-top button", { hasText: "Leave" }).click();
    await present(p).waitFor({ state: "detached" });
  } finally {
    await p.evaluate(() => closeNotebookPresent());
  }
}

test("Present in a 480x320 window fits, its controls are reachable and its text isn't cut", { skip }, async () => {
  const p = app.page;
  await p.setViewportSize({ width: 480, height: 320 });
  try {
    await checkWindow(p, "480x320");
  } finally {
    await p.setViewportSize({ width: 1440, height: 940 });
  }
});

test("Present in an 800x600 window fits, its controls are reachable and its text isn't cut", { skip }, async () => {
  const p = app.page;
  await p.setViewportSize({ width: 800, height: 600 });
  try {
    await checkWindow(p, "800x600");
  } finally {
    await p.setViewportSize({ width: 1440, height: 940 });
  }
});

test("Present at 200% zoom fits, its controls are reachable and its text isn't cut", { skip }, async () => {
  const p = zoomed.page;
  assert.deepStrictEqual(await p.evaluate(() => [devicePixelRatio, innerWidth, innerHeight]), [2, 720, 470]);
  await checkWindow(p, "200%");
  assert.deepStrictEqual(zoomed.errors, []);
});

// ---------- the engine's export page failing ----------

test("when the engine can't make the export page, the person is told, nothing waits, and Esc and the app still work", { skip }, async () => {
  const p = failing.page;
  for (const reason of ["ValueError: the export page couldn't be built", "The engine stopped answering (notebook_print_page)."]) {
    printFails = reason;
    await load(p);
    await p.evaluate(() => { state.selected = state.notebook.cells[0].id; document.querySelector(".cell").focus(); });
    const asked = failing.calls.length;
    await p.keyboard.press("Control+Shift+F5");
    const toast = p.locator("#toast").filter({ hasText: "Couldn't show the notebook's outputs. Open Present again." });
    await toast.waitFor();
    assert.strictEqual(await p.locator("#toast").getAttribute("role"), "alert");
    const logged = await p.evaluate(() => activity.items[0]);
    assert.strictEqual(logged.title, "Couldn't show the notebook's outputs. Open Present again.");
    assert.ok(logged.details.length > 0, "the engine's reason is in Activity");
    assert.ok(failing.calls.slice(asked).some((c) => c.method === "notebook_print_page"));
    assert.strictEqual(await present(p).count(), 0, "Present doesn't open half drawn");
    assert.strictEqual(await p.locator(".nb-present").count(), 0);
    assert.deepStrictEqual(await p.evaluate(() => ({ opening: nbPresent.opening, root: !!nbPresent.root, fs: !!document.fullscreenElement })),
      { opening: false, root: false, fs: false }, "nothing is left waiting");
    // Esc and the notebook's keys work: Esc does what it does in the notebook, B adds a cell below.
    await p.keyboard.press("Escape");
    await p.locator(".cell").first().locator(".gutter").click();
    const n = await p.locator(".cell").count();
    await p.keyboard.press("b");
    await p.waitForFunction((n) => document.querySelectorAll(".cell").length === n + 1, n);
    assert.strictEqual(await p.evaluate(() => state.view), "notebook");
  }
  // The engine answers again: the same shortcut opens Present, and Esc leaves it.
  printFails = null;
  await load(p);
  await openPresent(p);
  assert.match(await p.locator(".nbp-slide").innerText(), /Quarterly review/);
  await p.keyboard.press("ArrowRight");
  assert.match(await p.locator(".nbp-slide").innerText(), /loaded 2 rows|Sources/);
  await p.keyboard.press("Escape");
  await present(p).waitFor({ state: "detached" });
  assert.deepStrictEqual(failing.errors, [], "no uncaught errors");
  assert.strictEqual(await p.locator("#toast").filter({ hasText: "Something didn't work" }).count(), 0);
});

// ---------- Tab stays in the dialog ----------

// Tab or Shift+Tab `times` times, noting what has focus after each and anything focused outside Present.
async function tabs(p, key, times) {
  await p.evaluate(() => {
    window.__outside = [];
    window.__onFocus = (e) => { if (!document.querySelector(".nb-present")?.contains(e.target)) window.__outside.push(e.target.outerHTML.slice(0, 80)); };
    document.addEventListener("focusin", window.__onFocus, true);
  });
  const seen = [];
  for (let i = 0; i < times; i += 1) {
    await p.keyboard.press(key);
    seen.push(await p.evaluate(() => {
      const el = document.activeElement;
      const root = document.querySelector(".nb-present");
      return root.contains(el) ? (el.matches(".md-link") ? el.title : el.textContent.trim() || el.className) : `OUTSIDE ${el.tagName}.${el.className}`;
    }));
  }
  const outside = await p.evaluate(() => { document.removeEventListener("focusin", window.__onFocus, true); return window.__outside; });
  return { seen, outside };
}

// The Present dialog's Tab stops, in order, as the app's own FOCUSABLE list finds them.
const stops = (p) => p.evaluate(() => [...document.querySelector(".nb-present").querySelectorAll(FOCUSABLE)]
  .filter((el) => el.offsetParent !== null).map((el) => (el.matches(".md-link") ? el.title : el.textContent.trim() || el.className)));

async function checkCycle(p, label) {
  const want = await stops(p);
  assert.ok(want.length >= 2, `${label}: ${want}`);
  // From the slide itself (where Present puts focus), Tab goes on through the stops after it.
  await p.locator(".nbp-slide").focus();
  const fwd = await tabs(p, "Tab", want.length * 2 + 1);
  assert.deepStrictEqual(fwd.outside, [], `${label}: Tab reached the notebook`);
  assert.ok(!fwd.seen.some((s) => s.startsWith("OUTSIDE")), `${label}: ${fwd.seen}`);
  // Once on the first stop, each Tab is the next stop, wrapping from the last to the first.
  const at = fwd.seen.indexOf(want[0]);
  assert.ok(at >= 0, `${label}: Tab never reached ${want[0]}: ${fwd.seen}`);
  assert.deepStrictEqual(fwd.seen.slice(at, at + want.length), want, `${label}: Tab order`);
  assert.strictEqual(fwd.seen[at + want.length], want[0], `${label}: Tab wraps from the last stop to the first`);
  // Shift+Tab goes back the same way, wrapping from the first to the last.
  await p.evaluate(() => document.querySelector(".nb-present").querySelectorAll(FOCUSABLE)[0].focus());
  const back = await tabs(p, "Shift+Tab", want.length + 1);
  assert.deepStrictEqual(back.outside, [], `${label}: Shift+Tab reached the notebook`);
  assert.deepStrictEqual(back.seen, [...want].reverse().concat(want.at(-1)), `${label}: Shift+Tab order`);
  assert.strictEqual(await present(p).count(), 1, `${label}: still presenting`);
}

test("Tab and Shift+Tab stay in Present and wrap, through links, an empty slide and the strip", { skip }, async () => {
  const p = app.page;
  await load(p);
  await openPresent(p);
  try {
    // Slide 2 holds the three links.
    await p.keyboard.press("ArrowRight");
    assert.match(await p.locator(".nbp-slide").innerText(), /^Sources/);
    assert.strictEqual(await p.locator(".nbp-slide a.md-link").count(), 3);
    const linked = await stops(p);
    for (const href of ["https://example.com/dash", "https://example.com/model", "https://example.com/runbook"]) assert.ok(linked.includes(href), `${href} in ${linked}`);
    await checkCycle(p, "slide with links");
    // A link reached by Tab opens with Enter, as it does in the notebook, and Present stays open.
    await p.locator(".nbp-slide").focus();
    await p.keyboard.press("Tab");
    assert.strictEqual(await p.evaluate(() => document.activeElement.title), "https://example.com/dash");
    const opened = () => p.evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url));
    const before = (await opened()).length;
    await p.keyboard.press("Enter");
    await p.waitForFunction((n) => window.__fdShellCalls.filter((c) => c.cmd === "open_link").length > n, before, { timeout: 5000 });
    assert.deepStrictEqual((await opened()).slice(before), ["https://example.com/dash"]);
    assert.strictEqual(await present(p).count(), 1);
    // Slide 3 has nothing to focus, and Next is disabled on it.
    await p.locator(".nbp-slide").focus();
    await p.keyboard.press("End");
    assert.match(await p.locator(".nbp-slide").innerText(), /^Plain/);
    assert.strictEqual(await p.locator(".nbp-slide").evaluate((s) => s.querySelectorAll(FOCUSABLE).length), 0);
    await checkCycle(p, "slide with nothing to focus");
    // The first slide, with Previous disabled and the strip open.
    await p.locator(".nbp-slide").focus();
    await p.keyboard.press("Home");
    await p.keyboard.press("s");
    await p.getByRole("navigation", { name: "Slides" }).waitFor();
    await checkCycle(p, "with the strip open");
    // Focus moved out from the page (a click on the notebook through the overlay can't happen; a script
    // focusing it can) comes back to the slide.
    await p.evaluate(() => document.querySelector(".cell")?.focus());
    assert.ok(await p.evaluate(() => document.querySelector(".nb-present").contains(document.activeElement)));
  } finally {
    await p.keyboard.press("Escape");
  }
  await present(p).waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

// ---------- the strip below the slide, and text that scales (UX review) ----------

const measure = (p) => p.evaluate(() => {
  const r = (el) => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height, b: b.bottom }; };
  const slide = document.querySelector(".nbp-slide");
  const strip = document.querySelector(".nbp-strip");
  const thumbs = [...strip.querySelectorAll(".nbp-thumb")].map(r);
  const px = (sel) => parseFloat(getComputedStyle(slide.querySelector(sel)).fontSize);
  const lh = parseFloat(getComputedStyle(slide.querySelector(".nbp-md p")).lineHeight);
  return { slide: r(slide), strip: strip.hidden ? null : r(strip), thumbs, body: px(".nbp-md p"), h1: px(".nbp-md h1"), out: px(".nbp-out table"),
    lines: Math.floor(slide.clientHeight / lh), pageScrollX: document.documentElement.scrollWidth - document.documentElement.clientWidth };
});

for (const [w, h] of [[1440, 940], [700, 900], [390, 844]]) {
  test(`at ${w}x${h}, S opens a grid of thumbnails below the slide, and the slide keeps its width`, { skip }, async () => {
    const p = app.page;
    await p.setViewportSize({ width: w, height: h });
    try {
      await load(p);
      await openPresent(p);
      const closed = await measure(p);
      assert.strictEqual(closed.strip, null);
      await p.keyboard.press("s");
      await p.getByRole("navigation", { name: "Slides" }).waitFor();
      const open = await measure(p);
      assert.ok(open.strip.y >= open.slide.b - 1, `the strip is below the slide: ${JSON.stringify(open)}`);
      assert.ok(open.strip.w >= w - 2, `the strip spans the window: ${open.strip.w}`);
      // The slide doesn't give its width to the strip (at 390px a side panel left it about 100px wide).
      if (w <= 700) assert.ok(open.slide.w >= w - 56, `the slide is ${open.slide.w}px of ${w}`);
      else assert.ok(open.slide.w >= closed.slide.w * 0.6, `the slide went from ${closed.slide.w} to ${open.slide.w}px`);
      // Thumbnails in a grid: side by side in a row, each 16:9.
      assert.strictEqual(open.thumbs.length, 3);
      assert.ok(Math.abs(open.thumbs[0].y - open.thumbs[1].y) < 1, `two thumbnails share a row: ${JSON.stringify(open.thumbs)}`);
      for (const t of open.thumbs) assert.ok(Math.abs(t.w / t.h - 16 / 9) < 0.1, `a thumbnail is ${t.w}x${t.h}`);
      if (w >= 700) assert.ok(Math.abs(open.thumbs[0].y - open.thumbs[2].y) < 1, "three in a row on a wider window");
      assert.ok(open.pageScrollX <= 0);
      // A thumbnail still goes to its slide, and S closes the strip again.
      await p.locator(".nbp-thumb").nth(2).click();
      assert.strictEqual(await p.locator(".nbp-count").innerText(), "3 / 3");
      await p.keyboard.press("s");
      assert.strictEqual(await p.getByRole("navigation", { name: "Slides" }).isVisible(), false);
    } finally {
      await p.evaluate(() => closeNotebookPresent());
      await p.waitForFunction(() => !document.fullscreenElement);
      await p.setViewportSize({ width: 1440, height: 940 });
    }
  });
}

test("the slide's text scales with the slide's width, from 18px on a wide window to smaller on a narrow one", { skip }, async () => {
  const p = app.page;
  const sizes = {};
  try {
    for (const [w, h] of [[1440, 940], [1024, 768], [700, 900], [390, 844]]) {
      await p.setViewportSize({ width: w, height: h });
      await load(p);
      await openPresent(p);
      sizes[w] = await measure(p);
      await p.evaluate(() => closeNotebookPresent());
      await p.waitForFunction(() => !document.fullscreenElement);  // the window can't be resized while full screen
    }
  } finally {
    await p.evaluate(() => closeNotebookPresent());
    await p.waitForFunction(() => !document.fullscreenElement);
    await p.setViewportSize({ width: 1440, height: 940 });
  }
  const at = (w) => sizes[w];
  assert.deepStrictEqual([at(1440).body, at(1440).h1, at(1440).out], [18, 34, 15], "a wide window keeps the sizes it had");
  for (const key of ["body", "h1", "out"]) {
    assert.ok(at(1024)[key] < at(1440)[key] && at(700)[key] <= at(1024)[key] && at(390)[key] <= at(700)[key], `${key} scales down: ${[1440, 1024, 700, 390].map((w) => at(w)[key])}`);
  }
  assert.ok(at(390).body >= 13 && at(390).h1 >= 20, "and stays readable");
  // At 390px the slide shows more than a few lines.
  assert.ok(at(390).lines >= 10, `${at(390).lines} lines show at 390px`);
});

test("the run line says today or yesterday at the time, and the date before that (UX review, minor)", { skip }, async () => {
  const lines = await app.page.evaluate(() => {
    const at = (daysBack, h, m) => { const d = new Date(); d.setDate(d.getDate() - daysBack); d.setHours(h, m, 0, 0); return d.getTime(); };
    return [at(0, 0, 5), at(1, 14, 2), at(3, 14, 2)].map((ms) => nbPresentRanLine([{ finishedAtMs: ms }]));
  });
  assert.match(lines[0], /^Outputs from the last run, today at 12:05\sAM$/);
  assert.match(lines[1], /^Outputs from the last run, yesterday at 0?2:02\sPM$/);
  assert.match(lines[2], /^Outputs from the last run, [A-Z][a-z]{2} \d{1,2}, \d{4}, 0?2:02\sPM$/);
});
