// A choice set by policy (a segmented control whose buttons are disabled) still shows which option is in
// force: the chosen button keeps --on-accent text on its --accent fill, not the grey of a disabled option.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;

before(async () => { if (!skip) app = await openApp({ mocks: { readiness_check: () => [] } }); });
after(async () => { if (app) await app.close(); });

test("a policy-locked segmented choice keeps its chosen option readable", { skip }, async () => {
  const drawn = await app.page.evaluate(() => {
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Density" },
      h("button", { class: "on", disabled: true }, "Comfortable"), h("button", { disabled: true }, "Compact"));
    document.body.append(seg);
    const root = getComputedStyle(document.documentElement);
    const [on, off] = [...seg.querySelectorAll("button")].map((b) => getComputedStyle(b).color);
    const probe = (token) => { const s = document.createElement("span"); s.style.color = `var(${token})`; document.body.append(s);
      const c = getComputedStyle(s).color; s.remove(); return c; };
    seg.remove();
    return { on, off, onAccent: probe("--on-accent"), muted: probe("--muted"), accent: root.getPropertyValue("--accent").trim() };
  });
  assert.strictEqual(drawn.on, drawn.onAccent, "the chosen option keeps its on-accent text");
  assert.strictEqual(drawn.off, drawn.muted, "the other options still look unavailable");
});
