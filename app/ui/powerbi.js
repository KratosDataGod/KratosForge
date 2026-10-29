// Embed Studio (Preview, PBE-06; docs/architecture/power-bi-embedding.md, docs/embed-studio.md).
// Workspaces > Power BI lists the reports, paginated reports and dashboards you can open, and Embed Studio
// opens one through the embed host (app/src-tauri/embed-host, served by the shell on its pbiembed scheme)
// to show how it behaves embedded in an app: its pages, its events and what the SDK supports for it.
//
// Power BI draws the content inside its own frame, inside the host frame, and runs its queries on the
// workspace's capacity, as it does in Fabric. The app never touches that frame: it sends the host a fixed
// list of commands and reads back events with anything that looks like a token taken out.
//
// The token (pbi_embed_token) is the person's Power BI access token, and it can write: it is kept in a local
// variable only long enough to post it to the host, never in state, storage, events or error text, and a
// fresh one is fetched before it expires rather than kept.

const PBI_TYPES = ["Report", "PaginatedReport", "Dashboard"];
const PBI_CHIPS = [["all", "All"], ["Report", "Reports"], ["PaginatedReport", "Paginated reports"], ["Dashboard", "Dashboards"]];
const PBI_FAVORITES_KEY = "fd.pbi.favorites";  // item ids starred on this PC
const PBI_OPENED_KEY = "fd.pbi.opened";  // item id -> when it was last opened in Embed Studio, on this PC
const PBI_READERS = 4;  // workspaces read at once
const PBI_EVENTS_KEPT = 500;

const pbiPage = { filter: "all", favoritesOnly: false, find: "", workspace: "", reading: 0, failed: new Set() };
// The item open in Embed Studio ({workspace_id, workspace_name, item_id, type, name}); Back and Forward keep it.
const pbiStudio = { item: null, side: "details", session: null };

// The host's origin, as the shell serves it next to the app: http(s)://pbiembed.localhost on Windows,
// pbiembed://localhost elsewhere.
function pbiHostOrigin() {
  const p = location.protocol;
  return p === "http:" || p === "https:" ? `${p}//pbiembed.localhost` : "pbiembed://localhost";
}

function pbiRead(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null") || {}; } catch { return {}; }
}

function pbiWrite(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* this session only */ }
}

function pbiFavorites() {
  const v = pbiRead(PBI_FAVORITES_KEY);
  return Array.isArray(v) ? v : [];
}

function pbiOpenedLabel(at, now = Date.now()) {
  if (!at) return "Not opened";
  const day = (t) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
  const days = Math.round((day(now) - day(at)) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 14) return "Last week";
  return dateText(at);
}

// Reads the items of every workspace not read yet, a few at a time, for the list. A workspace that can't be
// read is counted, not toasted: the list says how many.
async function pbiLoadItems(workspaces, redraw) {
  const wp = workspacesPage;
  const todo = workspaces.filter((w) => !wp.items[w.id]);
  pbiPage.reading = todo.length;
  let next = 0;
  const reader = async () => {
    while (next < todo.length) {
      const w = todo[next++];
      try {
        wp.items[w.id] = (await rpc("list_items", { workspace_id: w.id })) || [];
        pbiPage.failed.delete(w.id);
      } catch {
        pbiPage.failed.add(w.id);
      }
      pbiPage.reading--;
      redraw();
    }
  };
  await Promise.all(Array.from({ length: PBI_READERS }, reader));
}

function pbiRows(workspaces) {
  const rows = [];
  for (const w of workspaces) {
    for (const it of workspacesPage.items[w.id] || []) {
      if (PBI_TYPES.includes(it.type)) {
        rows.push({ workspace_id: w.id, workspace_name: w.displayName, item_id: it.id, type: it.type, name: it.displayName });
      }
    }
  }
  return rows;
}

// Workspaces > Power BI: the content you can open in Embed Studio.
async function renderPowerBi() {
  if (!previewShown()) { state.view = "workspaces"; renderWorkspaces(); return; }
  const pp = pbiPage;
  const body = h("div", {}, state.user ? loading("Loading workspaces...") : "");
  const filters = h("div", { class: "pbi-filters" });
  setView(h("div", { class: "page" }, h("h1", {}, "Workspaces"), workspacesTabs("powerbi"),
    pageIntro("Reports, paginated reports and dashboards you can open in Power BI. Open one in Embed Studio to see how it "
      + "behaves when embedded in an app. It runs in Power BI and uses its capacity, as it does in Fabric. ", docLink("embed-studio.md", "Learn more")),
    signInCallout(), withHowItWorks([filters, body], howItWorks("workspaces.powerbi", {
      reads: "Your workspaces and their items, from Fabric with your sign-in, to list the reports, paginated reports and dashboards in them.",
      changes: "Nothing. Stars and when you last opened each item are kept on this PC only.",
      results: "Open in Embed Studio shows the item through Power BI's own embedding, as an app would. Viewing it uses the workspace's capacity, as in Fabric.",
      more: [["Q&A", "Q&A isn't offered: Microsoft retires it, embedded included, in December 2026."]],
    }))));
  if (!state.user) return;
  const workspaces = await loadWorkspaces();
  if (state.view !== "powerbi") return;
  const here = () => state.view === "powerbi";

  const find = h("input", { type: "search", class: "catalog-search small pbi-find", placeholder: "Find a report or dashboard", "aria-label": "Find content",
    value: pp.find, oninput: () => { pp.find = find.value; draw(); } });
  const pick = h("select", { "aria-label": "Workspace", onchange: () => { pp.workspace = pick.value; draw(); } },
    h("option", { value: "" }, "All workspaces"),
    workspaces.map((w) => h("option", { value: w.id, selected: pp.workspace === w.id }, w.displayName)));

  function toggleFavorite(row, e) {
    e.stopPropagation();
    const favs = pbiFavorites();
    pbiWrite(PBI_FAVORITES_KEY, favs.includes(row.item_id) ? favs.filter((id) => id !== row.item_id) : [...favs, row.item_id]);
    draw();
  }

  function draw() {
    if (!here()) return;
    const favs = pbiFavorites();
    const opened = pbiRead(PBI_OPENED_KEY);
    const all = pbiRows(workspaces);
    const count = (key) => all.filter((r) => key === "all" || r.type === key).length;
    const chip = (key, label) => h("button", { class: `chip${pp.filter === key ? " on" : ""}`, "aria-pressed": String(pp.filter === key),
      onclick: () => { pp.filter = key; draw(); } }, `${label} ${count(key)}`);
    filters.replaceChildren(find, ...PBI_CHIPS.map(([key, label]) => chip(key, label)),
      h("button", { class: `chip${pp.favoritesOnly ? " on" : ""}`, "aria-pressed": String(pp.favoritesOnly),
        onclick: () => { pp.favoritesOnly = !pp.favoritesOnly; draw(); } }, "★ Favorites only"),
      h("span", { class: "spacer" }), pick);
    const text = pp.find.trim().toLowerCase();
    const shown = all.filter((r) => (pp.filter === "all" || r.type === pp.filter) && (!pp.workspace || r.workspace_id === pp.workspace)
      && (!pp.favoritesOnly || favs.includes(r.item_id)) && (!text || r.name.toLowerCase().includes(text)))
      .sort((a, b) => (opened[b.item_id] || 0) - (opened[a.item_id] || 0) || a.name.localeCompare(b.name));
    const notes = [];
    if (pp.reading) notes.push(h("p", { class: "muted-text small", role: "status" }, `Reading ${pp.reading} more workspace${pp.reading === 1 ? "" : "s"}...`));
    if (pp.failed.size) notes.push(h("p", { class: "muted-text small" }, `${pp.failed.size} workspace${pp.failed.size === 1 ? "" : "s"} couldn't be read and aren't listed.`));
    const table = shown.length ? h("table", { class: "list pbi-list" },
      h("thead", {}, h("tr", {}, h("th", { class: "pbi-star-col" }), h("th", {}, "Name"), h("th", {}, "Type"), h("th", {}, "Workspace"),
        h("th", {}, "Opened"), h("th", {}))),
      h("tbody", {}, shown.map((r) => {
        const fav = favs.includes(r.item_id);
        return h("tr", {},
          h("td", {}, h("button", { class: `star${fav ? " on" : ""}`, "aria-pressed": String(fav), "aria-label": `Favorite ${r.name}`,
            title: fav ? "Remove from favorites" : "Add to favorites", onclick: (e) => toggleFavorite(r, e) }, fav ? "★" : "☆")),
          h("td", {}, h("span", { class: "item" }, itemIcon(r.type), r.name)),
          h("td", {}, typeLabel(r.type)),
          h("td", {}, r.workspace_name),
          h("td", { class: "muted-text" }, pbiOpenedLabel(opened[r.item_id])),
          h("td", { class: "actions" },
            btn("Open in Embed Studio", null, () => openEmbedStudio(r), "tool small primary"),
            btn("Open in Fabric", "open", () => openInFabric(fabricItemLink(r)), "tool small link")));
      }))) : pp.reading ? null : h("p", { class: "muted-text", style: "padding:16px 0" },
      all.length ? (pp.favoritesOnly ? "No favorites here yet. Click the star next to one." : "Nothing matches.")
        : "No reports, paginated reports or dashboards in your workspaces.");
    body.replaceChildren(...notes, table || "");
  }
  draw();
  await pbiLoadItems(workspaces, draw);
  draw();
}

function openEmbedStudio(row) {
  pbiStudio.item = { workspace_id: row.workspace_id, workspace_name: row.workspace_name, item_id: row.item_id, type: row.type, name: row.name };
  pbiStudio.side = "details";
  go("embedstudio");
}

// What the report area says when Power BI can't open the content: what happened and the one thing to do next.
// Power BI's own error, with its request id and no token, stays in the Events panel.
function pbiProblem(detail, item) {
  const d = detail || {};
  const code = String(d.errorCode || d.statusCode || "");
  const text = `${d.message || ""} ${d.detailedMessage || ""}`;
  const fabric = ["Open in Fabric", () => openInFabric(fabricItemLink(item))];
  if (code === "401" || /TokenExpired|token.*expired/i.test(text)) {
    return { tag: "Sign-in", tone: "info", title: "Your sign-in to Power BI has expired",
      body: "Sign in again and the content reopens. Nothing was lost.", primary: ["Sign in", () => pbiStudio.session?.reopen()] };
  }
  if (/licen[cs]e/i.test(text)) {
    return { tag: "Licence", tone: "warn", title: "This content needs a Power BI licence",
      body: "Viewing it needs a Pro or Premium Per User licence, or the workspace on F64 or larger capacity. Your admin can assign one.", primary: fabric };
  }
  if (/capacity/i.test(text)) {
    return { tag: "Capacity", tone: "warn", title: "The workspace's capacity isn't running",
      body: "Power BI can't run the content's queries until the capacity is resumed. Resume it in Fabric, or ask your capacity admin.", primary: fabric };
  }
  if (code === "403" || /forbidden|unauthori[sz]ed|access/i.test(text)) {
    return { tag: "No access", tone: "stop", title: "Your account can't open this",
      body: "Power BI said no. Ask the workspace's admin for at least Viewer access, then open it again.", primary: fabric };
  }
  return { tag: "Power BI", tone: "stop", title: "Power BI couldn't open it",
    body: String(d.detailedMessage || d.message || "Power BI didn't say why.").slice(0, 400), primary: fabric };
}

// Power BI's TraceType Fatal (6), or an expired sign-in, which stops the content whatever its level.
function pbiFatal(detail) {
  const d = detail || {};
  return d.level === 6 || d.level === "Fatal" || String(d.errorCode || "") === "401" || /TokenExpired/i.test(String(d.message || ""));
}

// An engine error from opening the content (pbi_embed_content or pbi_embed_token), as a problem card.
function pbiOpenProblem(err) {
  const text = String(err?.message || err || "");
  const fabric = ["Open in Fabric", () => openInFabric(fabricItemLink(pbiStudio.item))];
  return /licen[cs]e|access|not allowed|forbidden/i.test(text)
    ? { tag: "No access", tone: "stop", title: "Your account can't open this", body: text.slice(0, 400), primary: fabric }
    : { tag: "Couldn't open", tone: "stop", title: "Embed Studio couldn't open this", body: text.slice(0, 400), primary: fabric };
}

// One line of an event's detail for the Events panel.
function pbiEventText(name, detail) {
  const d = detail || {};
  if (name === "pageChanged") return `${d.newPage?.displayName || d.newPage?.name || ""}`;
  if (name === "error") return `${d.message || "Error"}${d.errorCode ? ` (${d.errorCode})` : ""}${d.technicalDetails?.requestId ? `, request ${d.technicalDetails.requestId}` : ""}`;
  if (name === "dataHyperlinkClicked") return String(d.url || "");
  if (name === "embed") return "Started with your sign-in";
  const text = JSON.stringify(d);
  return text === "{}" || text === "null" ? "" : text.slice(0, 300);
}

// The app's side of the host conversation. Messages are taken only from this session's host frame, on the
// host's origin, and commands go only to that origin.
function pbiConnect(frame, onEvent) {
  const origin = pbiHostOrigin();
  const waiting = new Map();
  let seq = 0;
  let ready = null;
  const isReady = new Promise((resolve) => { ready = resolve; });
  function listener(e) {
    if (e.source !== frame.contentWindow || e.origin !== origin) return;
    const body = e.data && e.data.kfEmbedHost;
    if (!body || typeof body !== "object") return;
    if (body.event === "ready") { ready(); return; }
    if (body.reply !== undefined) {
      const done = waiting.get(body.reply);
      waiting.delete(body.reply);
      if (done) done(body);
      return;
    }
    if (typeof body.event === "string") onEvent(body.event, body.detail);
  }
  window.addEventListener("message", listener);
  return {
    ready: isReady,
    // A command and its answer. The token, when there is one, is only in `message` and the host's memory.
    send(message) {
      const id = ++seq;
      return new Promise((resolve, reject) => {
        waiting.set(id, (body) => (body.ok ? resolve(body.result) : reject(new Error(body.error || "The embed host refused it."))));
        frame.contentWindow.postMessage({ kfEmbed: { ...message, seq: id } }, origin);
      });
    },
    close() { window.removeEventListener("message", listener); waiting.clear(); },
  };
}

// Embed Studio: one item, embedded through the host.
async function renderEmbedStudio() {
  const item = pbiStudio.item;
  if (!item || !previewShown()) { state.view = item ? "workspaces" : "powerbi"; return (item ? renderWorkspaces : renderPowerBi)(); }
  pbiStudio.session?.stop();
  const opened = pbiRead(PBI_OPENED_KEY);
  opened[item.item_id] = Date.now();
  pbiWrite(PBI_OPENED_KEY, opened);

  const s = { content: null, pages: [], active: null, events: [], problem: null, timer: null, link: null, stopped: false, started: 0, loaded: false };
  pbiStudio.session = s;
  const live = () => !s.stopped && pbiStudio.session === s && state.view === "embedstudio";
  const frame = h("iframe", { class: "pbi-frame", title: `${item.name}, drawn by Power BI`,
    src: `${pbiHostOrigin()}/host.html`,
    // No pop-ups and no top navigation: links come back as events and open in the default browser.
    sandbox: "allow-scripts allow-same-origin allow-forms allow-downloads", allow: "fullscreen", referrerpolicy: "no-referrer" });
  const pagesBar = h("div", { class: "pbi-pages", role: "tablist", "aria-label": "Report pages" });
  const problemBox = h("div", { class: "pbi-problem-slot" });
  const side = h("aside", { class: "card pbi-side", "aria-label": "Details" });
  const eventList = h("ol", { class: "pbi-events", "aria-label": "Events" });
  const reloadBtn = btn("Reload", "restart", () => s.link?.send({ command: "reload" }).catch((e) => toast(e.message, true)), "tool outline");
  const fullBtn = btn("Full screen", null, () => s.link?.send({ command: "fullscreen" }).catch((e) => toast(e.message, true)), "tool outline");

  setView(h("div", { class: "page embed-studio" },
    h("div", { class: "page-head" },
      h("div", {},
        h("button", { class: "crumb learn-more", onclick: () => go("powerbi") }, "Workspaces › Power BI"),
        h("h1", {}, item.name, previewTag()),
        h("p", { class: "sub" }, `${typeLabel(item.type)} · ${item.workspace_name || "Workspace"} · Runs in Power BI and uses its capacity`)),
      h("div", { class: "head-actions" }, reloadBtn, fullBtn,
        btn("Open in Fabric", "open", () => openInFabric(fabricItemLink(item)), "tool outline"))),
    withHowItWorks([
      h("div", { class: "pbi-studio" },
        h("section", { class: "card pbi-stage", "aria-label": "Embedded content" },
          h("div", { class: "pbi-stage-bar" }, pagesBar, h("span", { class: "spacer" }), h("span", { class: "muted-text small" }, "Drawn by Power BI")),
          h("div", { class: "pbi-frame-wrap" }, frame, problemBox)),
        side),
      h("section", { class: "card pbi-events-card", "aria-label": "Events" },
        h("div", { class: "pbi-events-head" }, h("h2", {}, "Events"),
          h("span", { class: "muted-text small" }, "Timings measured in this window, not capacity units"), h("span", { class: "spacer" }),
          btn("Copy events", null, () => copyText(JSON.stringify(s.events, null, 2), "Events"), "tool outline small")),
        eventList)],
    howItWorks("workspaces.embedstudio", {
      reads: "The item's embed address, pages or tiles from Power BI, and a Power BI token from your sign-in. Power BI then reads the content's data, as it does in Fabric.",
      changes: "Nothing by itself. Viewing runs the content's queries on the workspace's capacity, as viewing it in Fabric does.",
      results: "The events below stay in this window until you copy them. Tokens are taken out of every event.",
      more: [
        ["Your token", "Power BI's frame gets your own Power BI token: it can do what you can do in Power BI. It stays in memory, goes only to app.powerbi.com, and is renewed before it expires."],
        ["Q&A", "Q&A isn't offered: Microsoft retires it, embedded included, in December 2026."],
      ],
    }))));

  function drawPages() {
    const show = s.content?.capabilities?.pages && s.pages.length;
    pagesBar.hidden = !show;
    if (!show) { pagesBar.replaceChildren(); return; }
    pagesBar.replaceChildren(...s.pages.map((p) => h("button", { class: `tab${p.name === s.active ? " active" : ""}`, role: "tab",
      "aria-selected": String(p.name === s.active), onclick: () => setPage(p.name) }, p.display_name || p.name)));
  }

  async function setPage(name) {
    if (name === s.active) return;
    try { await s.link.send({ command: "setPage", pageName: name }); } catch (e) { toast(e.message, true); }
  }

  function drawProblem() {
    frame.classList.toggle("pbi-hidden", !!s.problem);
    if (!s.problem) { problemBox.replaceChildren(); return; }
    const p = s.problem;
    const lastError = [...s.events].reverse().find((e) => e.name === "error");
    problemBox.replaceChildren(h("div", { class: `card pad pbi-problem ${p.tone}`, role: "alert" },
      h("span", { class: "pill" }, p.tag), h("h2", {}, p.title), h("p", {}, p.body),
      h("div", { class: "row wrap" }, btn(p.primary[0], null, p.primary[1], "tool primary"),
        lastError ? btn("Copy the details", null, () => copyText(JSON.stringify(lastError, null, 2), "Details"), "tool outline") : null)));
  }

  function drawSide() {
    const c = s.content;
    const tab = (key, label) => h("button", { class: `tab${pbiStudio.side === key ? " active" : ""}`, role: "tab",
      "aria-selected": String(pbiStudio.side === key), onclick: () => { pbiStudio.side = key; drawSide(); } }, label);
    const fact = (k, v) => h("div", { class: "pbi-fact" }, h("span", { class: "muted-text small" }, k), h("span", {}, v));
    const caps = c?.capabilities || {};
    const yes = (on) => (on ? "Yes" : "No");
    const details = c ? [
      fact("Workspace", item.workspace_name || item.workspace_id),
      c.dataset_id ? fact("Semantic model", btn("Open in Fabric", "open",
        () => openInFabric(fabricItemLink({ workspace_id: item.workspace_id, item_id: c.dataset_id, type: "SemanticModel" })), "tool small link")) : null,
      fact("Signed in as", `${state.user || "you"}, with your Power BI access`),
      fact("Embed type", `${typeLabel(item.type)}, user owns data (Entra token)`),
      fact("Licence", "Pro or Premium Per User, or F64 or larger capacity for viewers without one"),
      fact("Token", "Renews by itself before it expires"),
      c.tiles?.length ? fact("Tiles", c.tiles.map((t) => t.title || t.id).join(", ")) : null,
    ] : [h("p", { class: "muted-text" }, "Reading it from Power BI...")];
    const supports = c ? [
      ...[["Pages", caps.pages], ["Filters", caps.filters], ["Bookmarks", caps.bookmarks], ["Slicers", caps.slicers], ["Visuals", caps.visuals],
        ["Tiles", caps.tiles], ["Parameters", caps.parameters]].filter(([, v]) => v !== undefined).map(([k, v]) => fact(k, yes(v))),
      fact("Events", (caps.events || []).join(", ")),
      ...(caps.limits || []).map((l) => fact("Limit", l)),
    ] : [];
    side.replaceChildren(h("div", { class: "subtabs", role: "tablist", "aria-label": "Details" }, tab("details", "Details"), tab("supports", "Supports")),
      h("div", { class: "pbi-facts" }, pbiStudio.side === "details" ? details : supports));
  }

  // Timed by this window's clock when the event arrives: the host's own clock starts elsewhere.
  function addEvent(name, detail) {
    if (!live()) return;
    const t = Math.max(0, (performance.now() - s.started) / 1000);
    s.events.push({ name, t: Number(t.toFixed(2)), detail: detail ?? null });
    if (s.events.length > PBI_EVENTS_KEPT) s.events.shift();
    const tone = name === "error" ? "bad" : ["loaded", "rendered", "tileLoaded"].includes(name) ? "good" : "";
    eventList.append(h("li", {}, h("span", { class: "muted-text" }, `+${t.toFixed(2)} s`), h("b", { class: tone }, name),
      h("span", { class: "pbi-event-detail" }, pbiEventText(name, detail))));
    while (eventList.childElementCount > PBI_EVENTS_KEPT) eventList.firstElementChild.remove();
    eventList.scrollTop = eventList.scrollHeight;
    if (name === "pageChanged" && detail?.newPage?.name) { s.active = detail.newPage.name; drawPages(); }
    // Once the content has loaded, Power BI also raises errors it carries on from (a visual that failed, a
    // refused page change): those stay in the Events list. The card covers the content only when it couldn't
    // load, when Power BI calls the error fatal, or when the sign-in ran out.
    if (name === "error" && (!s.loaded || pbiFatal(detail))) { s.problem = pbiProblem(detail, item); drawProblem(); }
    if (name === "loaded" || name === "rendered" || name === "tileLoaded") {
      s.loaded = true;
      if (s.problem) { s.problem = null; drawProblem(); }
    }
    if (name === "dataHyperlinkClicked" && /^https:\/\//i.test(String(detail?.url || ""))) openLink(String(detail.url));
  }

  // A fresh token before this one runs out: the engine hands one with at least 10 minutes left.
  function renewIn(secondsLeft) {
    clearTimeout(s.timer);
    const wait = Math.max(60, (Number(secondsLeft) || 600) - 300);
    s.timer = setTimeout(async () => {
      if (!live()) return;
      try {
        const fresh = await rpc("pbi_embed_token");
        if (!live()) return;
        await s.link.send({ command: "setToken", accessToken: fresh.token });
        renewIn(fresh.seconds_left);
      } catch {
        if (live()) { s.problem = pbiProblem({ errorCode: "401" }, item); drawProblem(); }
      }
    }, wait * 1000);
  }

  async function embed() {
    s.problem = null;
    s.loaded = false;
    drawProblem();
    try {
      const [content, token] = await Promise.all([
        s.content ? s.content : rpc("pbi_embed_content", { workspace_id: item.workspace_id, item_id: item.item_id, item_type: item.type }),
        rpc("pbi_embed_token")]);
      if (!live()) return;
      s.content = content;
      s.pages = [...(content.pages || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
      s.active = s.active || s.pages[0]?.name || null;
      drawPages();
      drawSide();
      await s.link.ready;
      if (!live()) return;
      s.started = performance.now();
      await s.link.send({ command: "embed", kind: content.capabilities?.embed_type || "report", id: content.id,
        embedUrl: content.embed_url, accessToken: token.token, pageName: content.capabilities?.pages ? s.active : undefined });
      renewIn(token.seconds_left);
    } catch (err) {
      if (!live()) return;
      s.problem = pbiOpenProblem(err);
      drawProblem();
      drawSide();
    }
  }

  s.reopen = () => embed();
  s.stop = () => { s.stopped = true; clearTimeout(s.timer); s.link?.close(); };
  s.link = pbiConnect(frame, addEvent);
  // Leaving the page stops the session: its timer and listener go with it.
  const watch = setInterval(() => { if (!live()) { clearInterval(watch); s.stop(); } }, 1000);
  drawPages();
  drawSide();
  embed();
}

registerFeature({
  id: "embedStudio", preview: true, views: { powerbi: renderPowerBi, embedstudio: renderEmbedStudio },
  commands: [{ label: "Power BI in Embed Studio", icon: "dataBarVertical", keywords: "power bi embed embedded report dashboard paginated studio",
    run: () => go("powerbi") }],
});
