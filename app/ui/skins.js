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
  // The skin's look (appearance.style), one attribute per part, e.g. data-skin-rail="inline". The engine
  // checked each value against its fixed choices; styles.css draws them. A part the skin leaves out is
  // KratosForge's own. The look is Preview: with Preview features hidden, every part is the default.
  const style = PREVIEW_FEATURES.skins && previewShown() ? skin?.appearance?.style || {} : {};
  for (const [part, own] of Object.entries(SKIN_STYLE_DEFAULTS)) {
    document.body.dataset[`skin${part[0].toUpperCase()}${part.slice(1)}`] = style[part] || own;
  }
}

// KratosForge's own look, part by part: the first choice of each in the engine's STYLE (test_skins.py
// keeps the two equal).
const SKIN_STYLE_DEFAULTS = { font: "segoe-variable", header: "light", rail: "stacked", corners: "soft",
  tabs: "underline", cells: "bordered", icons: "regular" };

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

// The commands a skin places on a view's command group (workflow.commands, checked by the engine), or
// null when it places none: then the view keeps its own buttons (the KratosForge skin sets none).
function skinCommands(view) {
  return currentSkin()?.workflow?.commands?.[view] || null;
}

// The ids a skin puts on the group, primary first; the command bar lists these first.
function skinPlacedCommands(view) {
  const c = skinCommands(view);
  return c ? [c.primary, ...(c.bar || [])] : [];
}

// A command's name under the skin, or null to keep its own. The engine allows renaming only low and
// moderate commands that don't use capacity, so Run in Fabric always keeps its name.
function skinCommandLabel(id) {
  for (const c of Object.values(currentSkin()?.workflow?.commands || {})) if (c?.labels?.[id]) return c.labels[id];
  return null;
}

function skinRailLabel(f) {
  return currentSkin()?.workflow?.labels?.[f.id] || f.rail.label;
}

// Settings > Appearance > Skin: one radio per skin, as a card with a small picture of its look: its
// colors, and its header, rail, corners, tabs, cells and typeface (appearance.style, the same fixed
// choices styles.css draws on the page).
function skinChooser(locked) {
  const list = skinState.list || [];
  if (list.length < 2 || !PREVIEW_FEATURES.skins) return null;
  const now = currentSkin()?.id;
  const card = (skin) => {
    const t = { ...SKIN_PREVIEW_BASE, ...(skin.appearance?.tokens?.light || {}) };
    const look = { ...SKIN_STYLE_DEFAULTS, ...(skin.appearance?.style || {}) };
    const on = skin.id === now;
    // Colors go in through style.setProperty, one checked value at a time, never as CSS text.
    const thumb = h("span", { class: "skin-thumb", "aria-hidden": "true" },
      h("span", { class: "skin-thumb-head" }, h("span", { class: "skin-thumb-type" }, "Aa")),
      h("span", { class: "skin-thumb-main" },
        h("span", { class: "skin-thumb-rail" }, h("i", {}), h("i", { class: "on" }), h("i", {})),
        h("span", { class: "skin-thumb-body" },
          h("span", { class: "skin-thumb-tabs" }, h("i", { class: "on" }), h("i", {}), h("i", {})),
          h("span", { class: "skin-thumb-cell" }, h("i", {}), h("i", {})))));
    for (const name of ["--canvas", "--surface", "--border", "--accent", "--on-accent"]) thumb.style.setProperty(`--thumb${name.slice(1)}`, t[name]);
    for (const part of ["font", "header", "rail", "corners", "tabs", "cells"]) thumb.dataset[part] = look[part];
    return h("label", { class: `skin-card${on ? " selected" : ""}` },
      thumb,
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
const SKIN_PREVIEW_BASE = { "--canvas": "#f3f2f1", "--surface": "#ffffff", "--border": "#e0e0e0", "--accent": "#1b3664",
  "--on-accent": "#ffffff" };
