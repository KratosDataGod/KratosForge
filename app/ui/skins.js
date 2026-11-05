// Platform skins (Preview, docs/skins.md): how the app looks and how the left rail is ordered, from a
// skin manifest the engine has checked (fabric_desktop/skins.py, skins_list). A skin sets the color tokens
// on :root and the rail's order and labels, and nothing else: no page reloads, so open notebooks, editors,
// selections and connections stay as they are, and a skin never calls the engine except to read the list.

const skinState = { list: null, applied: null };

async function loadSkins() {
  skinState.list = await rpc("skins_list").then((r) => r.skins).catch(() => skinState.list || []);
  applySkin();
  return skinState.list;
}

// The skin in force: the one chosen in Settings when the engine knows it, else the KratosForge skin.
function currentSkin() {
  const list = skinState.list || [];
  const id = state.settings?.ui_skin?.value || "kratosforge";
  return list.find((s) => s.id === id) || list.find((s) => s.id === "kratosforge") || null;
}

// Sets the skin's light tokens on <html> (the KratosForge skin's are styles.css's own, so it sets none)
// and marks <body> with its id. Only what the previous skin set is removed.
function applySkin() {
  const skin = currentSkin();
  const root = document.documentElement.style;
  for (const name of skinState.applied?.tokens || []) root.removeProperty(name);
  const tokens = skin && skin.id !== "kratosforge" ? skin.appearance?.tokens?.light || {} : {};
  for (const [name, value] of Object.entries(tokens)) root.setProperty(name, value);
  skinState.applied = { id: skin?.id || "kratosforge", tokens: Object.keys(tokens) };
  document.body.dataset.skin = skinState.applied.id;
}

// The rail's order under the skin: pages it names come first, in its order; the rest keep theirs after.
function skinRailSort(list) {
  const order = currentSkin()?.workflow?.rail || [];
  const at = (f) => (order.includes(f.id) ? order.indexOf(f.id) : order.length + f.rail.order / 1000);
  return order.length ? [...list].sort((a, b) => at(a) - at(b)) : list;
}

// KratosForge's own pages, which no platform has. In a platform skin they sit together under a
// "KratosForge" label, so a developer sees what the app adds to the platform they know (mockup v7).
const KRATOSFORGE_PAGES = ["platform", "optimize", "metadata"];

// The rail's buttons, with the "KratosForge" label before the first of those pages in a platform skin.
function skinRailChildren(list, item) {
  const items = list.map(item);
  if ((currentSkin()?.id || "kratosforge") === "kratosforge") return items;
  const at = list.findIndex((f) => KRATOSFORGE_PAGES.includes(f.id));
  if (at < 0 || list.slice(at).some((f) => !KRATOSFORGE_PAGES.includes(f.id))) return items;
  return [...items.slice(0, at), h("div", { class: "rail-group-label", role: "heading", "aria-level": "2" }, "KratosForge"), ...items.slice(at)];
}

function skinRailLabel(f) {
  return currentSkin()?.workflow?.labels?.[f.id] || f.rail.label;
}

// Settings > Appearance > Skin: one radio per skin, as a card with a small picture of its colors.
function skinChooser(locked) {
  const list = skinState.list || [];
  if (list.length < 2 || !PREVIEW_FEATURES.skins) return null;
  const now = currentSkin()?.id;
  const card = (skin) => {
    const t = { ...SKIN_PREVIEW_BASE, ...(skin.appearance?.tokens?.light || {}) };
    const on = skin.id === now;
    // Colors go in through style.setProperty, one checked value at a time, never as CSS text.
    const paint = (el, props) => { for (const [prop, token] of props) el.style.setProperty(prop, t[token]); return el; };
    return h("label", { class: `skin-card${on ? " selected" : ""}` },
      paint(h("span", { class: "skin-thumb", "aria-hidden": "true" },
        paint(h("span", { class: "skin-thumb-rail" }), [["background-color", "--surface"], ["border-color", "--border"]]),
        h("span", { class: "skin-thumb-body" },
          paint(h("span", { class: "skin-thumb-bar" }), [["background-color", "--accent"]]),
          paint(h("span", { class: "skin-thumb-line" }), [["background-color", "--border"]]),
          paint(h("span", { class: "skin-thumb-line" }), [["background-color", "--border"]]))),
        [["background-color", "--canvas"], ["border-color", "--border"]]),
      h("span", { class: "skin-name" },
        h("input", { type: "radio", name: "ui_skin", value: skin.id, checked: on ? true : null, disabled: locked("ui_skin") ? true : null,
          // Redraw either way: after a failed save the radios go back to the skin still in force.
          onchange: async () => { await saveSettings({ ui_skin: skin.id }); render(); } }),
        h("b", {}, skin.name)),
      skin.description ? h("span", { class: "muted-text small" }, skin.description) : null);
  };
  return h("fieldset", { class: "skin-chooser" }, h("legend", {}, "Skin ", previewTag()),
    h("p", { class: "muted-text small" }, "How KratosForge looks and how its pages are ordered. Your projects, connections, engines and open work stay exactly as they are, and Settings stays at the bottom of every skin and in Ctrl+K."),
    locked("ui_skin") ? h("div", { class: "setting-source" }, SOURCE_TEXT.policy) : null,
    h("div", { class: "skin-cards" }, list.map(card)));
}

// The KratosForge skin's colors, for the pictures of skins that set only some tokens.
const SKIN_PREVIEW_BASE = { "--canvas": "#f3f2f1", "--surface": "#ffffff", "--border": "#e0e0e0", "--accent": "#1b3664" };
