// Pop-out windows (Preview): a section of a page in a window of its own, to keep on another screen
// while you work in the main window. Monitor's From KratosForge list ("runs") and Fabric's Monitoring
// hub ("fabric") pop out. A pop-out window is the app's own page with ?popout=<section> (POP_OUT in
// app.js), opened by the shell (main.rs, pop_out_open) with the same policy as the main window; it
// draws only that section and uses the engine the main window started. While a section is out, the
// main window shows a band for it with Show window and Dock back. Closing the window docks it back.

const POP_OUT_REFRESH_MS = 15000;

function popOutTopKey(section) {
  return `fd.popout.${section}.top`;
}

function popOutOnTop(section) {
  try { return localStorage.getItem(popOutTopKey(section)) === "1"; } catch { return false; }
}

// Opens the section's window, or brings it forward when it is open already.
async function popOut(section) {
  const opened = await attempt(() => invoke("pop_out_open", { section, onTop: popOutOnTop(section) }).then(() => true),
    "Could not open the window");
  if (!opened) return;
  popOuts.add(section);
  render();
}

function dockBack(section) {
  return attempt(() => invoke("pop_out_close", { section }), "Could not close the window");
}

// The Pop out button on a section's band, in the main window only.
function popOutButton(section, label = "Pop out") {
  if (POP_OUT) return null;
  return preview("popOut", btn(label, "open", () => popOut(section), "tool outline",
    { title: "Open this in a window of its own, to keep on another screen" }));
}

// What the main window shows in place of a section that is in its own window.
function poppedOutBand(section, title, cls) {
  return h("section", { class: `card ${cls} popped-out` },
    h("div", { class: "monitor-band" },
      h("h2", {}, title),
      h("span", { class: "muted-text" }, "In a window of its own"),
      h("span", { class: "spacer" }),
      btn("Show window", "open", () => popOut(section), "tool outline"),
      btn("Dock back", null, () => dockBack(section), "tool outline")));
}

// The main window follows its pop-out windows: one opening or closing redraws Monitor, and a
// Fabric link clicked in one opens here, in the Fabric view.
function listenForPopOuts() {
  const listen = window.__TAURI__?.event?.listen;
  if (!listen) return;
  listen("pop-out", ({ payload }) => {
    if (!["runs", "fabric"].includes(payload?.section)) return;
    if (payload.open) popOuts.add(payload.section); else popOuts.delete(payload.section);
    if (state.view === "monitor") render();
  });
  listen("pop-out-open-in-fabric", ({ payload }) => { if (typeof payload === "string") openInFabric(payload); });
  invoke("pop_out_list").then((open) => {
    (Array.isArray(open) ? open : []).forEach((s) => popOuts.add(s));
    if (popOuts.size && state.view === "monitor") render();
  }).catch(() => {});
}

// Keep on top and Dock back, at the right of a pop-out window's top bar.
function popOutControls() {
  const box = h("input", { type: "checkbox", checked: popOutOnTop(POP_OUT) ? true : null, onchange: (e) => {
    const on = e.target.checked;
    try { localStorage.setItem(popOutTopKey(POP_OUT), on ? "1" : "0"); } catch { /* this window only */ }
    attempt(() => invoke("pop_out_on_top", { on }), "Keep on top");
  } });
  return h("div", { class: "pop-out-controls" },
    h("label", { class: "check" }, box, "Keep on top"),
    btn("Dock back", null, () => dockBack(POP_OUT), "tool outline", { title: "Close this window and put it back in the main window" }));
}

// A pop-out window's start: no component check or update (the main window did that and owns the
// engine), no splash, and the page it shows never becomes the one the main window reopens on.
async function startPopOut() {
  document.body.classList.add("pop-out", `pop-out-${POP_OUT}`);
  document.querySelector(".topbar")?.append(popOutControls());
  state.view = "monitor";
  monitorPage.panel = "runs";
  nav.stack = [navEntry("monitor")];
  nav.index = 0;  // nav.restored stays false, so navSave never runs here
  const env = await rpc("environment").catch(() => null);
  if (env) Object.assign(state, { python: env.python, user: env.user, tenant: env.tenant });
  await loadSettings();
  render();
  if (POP_OUT === "runs") setInterval(popOutRefresh, POP_OUT_REFRESH_MS);
}

// The runs list keeps itself current, keeping your place in it; never while you are on another
// panel or page, have a dialog open, or the window is minimized.
function popOutRefresh() {
  if (state.view !== "monitor" || monitorPage.panel !== "runs" || document.hidden) return;
  if (document.getElementById("dialog-root")?.childElementCount) return;
  monitorPage.refreshing = true;
  render();
}

// The Monitoring hub on its own: the band's buttons and the Fabric view, filling the window.
function renderFabricPopOut() {
  const slot = h("div", { class: "fabric-slot" },
    h("p", { class: "muted-text" }, "Opening the Fabric Monitoring hub. Sign in once and the app remembers it."));
  setView(h("div", { class: "page monitor" },
    h("section", { class: "card monitor-fabric" },
      h("div", { class: "monitor-band" },
        h("h2", {}, "In Fabric"),
        h("span", { class: "muted-text" }, "Fabric's own Monitoring hub, for everything else in your workspaces"),
        h("span", { class: "spacer" }),
        monitorHubButtons()),
      slot)));
  dockFabricView(slot, "monitor", "monitor");
}

// A Fabric link in a pop-out window (a Fabric run's Details) opens in the main window's Fabric view.
function popOutOpenInFabric(page) {
  attempt(() => invoke("pop_out_open_in_fabric", { page }), "Could not open it in the main window");
}
