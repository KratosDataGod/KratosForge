// The action-first baseline (docs/ux/action-first.md): facts about where each screen's main action
// sits, measured the same way on main and on every pull request, so an improvement is a change in
// these numbers rather than an impression. Not a test: it prints JSON.
//
//   FABRIC_DESKTOP_PYTHON=<python with the engine installed> NODE_PATH="$(npm root -g)" node app/tests/browser/action-baseline.cjs
//
// Signed out, no tenant, a 1440x940 window, the real engine behind every call except sign-in.
const { openApp } = require("./harness.cjs");

// The action each screen exists for, and the command bar text that should find it.
const SCREENS = [
  { view: "home", main: ["New notebook"] },
  { view: "notebook", main: ["Run all"], open: () => newNotebook() },
  { view: "optimize", main: [] },
  { view: "monitor", main: [] },
  { view: "platform", main: [] },
];
const COMMANDS = ["Run all", "Run the selected cell", "Validate", "Publish", "Run in Fabric", "Schedule"];

(async () => {
  const app = await openApp({ engine: { length: 1, includes: (m) => m !== "sign_in" } });
  const p = app.page;
  await p.waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 60000 });
  const out = { window: "1440x940", signedIn: false, screens: {}, commandBar: {} };
  for (const s of SCREENS) {
    if (s.open) await p.evaluate(s.open.toString().replace(/^\(\) => /, ""));
    else await p.evaluate((v) => go(v), s.view);
    await p.waitForFunction((v) => state.view === v, s.view, { timeout: 30000 });
    await p.waitForTimeout(2500);
    out.screens[s.view] = await p.evaluate((main) => {
      const shown = (b) => b.offsetParent !== null;
      const solid = [...document.querySelectorAll("#view button.primary")].filter(shown);
      const all = [...document.querySelectorAll("#view button, #view a")].filter(shown);
      const top = (el) => Math.round(el.getBoundingClientRect().top + (document.getElementById("view").scrollTop || 0));
      return {
        solidButtons: solid.map((b) => b.textContent.trim()),
        controls: all.length,
        signInPrompts: all.filter((b) => /^Sign in/.test(b.textContent.trim())).length,
        main: Object.fromEntries(main.map((label) => {
          const el = all.find((b) => b.textContent.trim().startsWith(label));
          return [label, el ? { y: top(el), inFirstView: top(el) < innerHeight, solid: el.classList.contains("primary") } : null];
        })),
      };
    }, s.main);
    // Asked from the notebook, where those actions belong.
    if (s.view === "notebook") out.commandBar = await p.evaluate((labels) => Object.fromEntries(labels.map((l) =>
      [l, paletteItems(l).some((i) => i.group === "Actions" && i.label.toLowerCase().startsWith(l.toLowerCase()))])), COMMANDS);
  }
  console.log(JSON.stringify(out, null, 2));
  await app.close();
  process.exit(0);
})();
