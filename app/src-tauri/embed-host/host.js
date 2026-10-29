// The Power BI embed host (PBE-03, docs/architecture/power-bi-embedding.md).
//
// The shell serves this page from its pbiembed scheme, to the main window only, with a policy that lets it
// frame app.powerbi.com and nothing else. The main window frames it and talks to it by postMessage. It runs
// Microsoft's powerbi-client SDK (vendor/powerbi.min.js) and does only what a fixed list of commands asks:
// embed one item, give it a new token, change page, reload, full screen, reset. Messages are taken only
// from the parent window on one of the app's own origins, and answered only to that origin. Events go back
// with anything that looks like a token or secret removed, so the screen can show and copy them.
//
// The token arrives with the embed command and lives only in this frame's memory and in Power BI's frame.
// The SDK posts it to its frame with target origin "*"; the policy's frame-src is what keeps that frame on
// app.powerbi.com, and embedUrlOk refuses any other embed address before the SDK sees it.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else api.start(root);
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const EMBED_HOST = "app.powerbi.com";
  // The main window's origin on Windows (http://tauri.localhost, or https with useHttpsScheme) and elsewhere.
  const APP_ORIGINS = ["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"];
  const KINDS = ["report", "visual", "dashboard", "tile"];
  // What the SDK raises for each kind (powerbi-client 2.25.0). Q&A isn't here: Microsoft retires it in December 2026.
  const EVENTS = {
    report: ["loaded", "rendered", "error", "pageChanged", "dataSelected", "buttonClicked", "saved"],
    visual: ["loaded", "rendered", "error", "dataSelected"],
    dashboard: ["loaded", "error", "tileClicked"],
    tile: ["tileLoaded", "tileClicked", "error"],
  };
  const SECRET = /token|authorization|secret|password|credential/i;
  const MAX_TEXT = 2000;

  // An embed address the frame may load: https on app.powerbi.com, no port and no user name.
  function embedUrlOk(url) {
    if (typeof url !== "string" || !url) return false;
    let u;
    try { u = new URL(url); } catch (e) { return false; }
    return u.protocol === "https:" && u.hostname === EMBED_HOST && !u.port && !u.username && !u.password;
  }

  // A copy of an event's detail that is plain data: no functions, no DOM, no cycles, nothing past a few
  // levels, long text cut, and every key that looks like a token or secret left out.
  function clean(value, depth) {
    depth = depth || 0;
    if (value === null || value === undefined) return value === undefined ? null : value;
    if (typeof value === "string") return value.length > MAX_TEXT ? value.slice(0, MAX_TEXT) + "…" : value;
    if (typeof value === "number" || typeof value === "boolean") return value;
    if (typeof value !== "object" || depth >= 6) return null;
    if (typeof Node !== "undefined" && value instanceof Node) return null;
    if (typeof Window !== "undefined" && value instanceof Window) return null;
    if (Array.isArray(value)) return value.slice(0, 200).map((v) => clean(v, depth + 1));
    const out = {};
    for (const key of Object.keys(value).slice(0, 100)) {
      if (SECRET.test(key)) continue;
      const v = value[key];
      if (typeof v === "function") continue;
      out[key] = clean(v, depth + 1);
    }
    return out;
  }

  // The SDK's pageChanged detail holds a Page object that points back at the whole report: keep its names.
  function eventDetail(name, detail) {
    if (name === "pageChanged" && detail && detail.newPage) {
      return { newPage: { name: String(detail.newPage.name || ""), displayName: String(detail.newPage.displayName || "") } };
    }
    return clean(detail);
  }

  // The SDK configuration for an embed command, from the fields this host knows and nothing else.
  function embedConfig(msg, models) {
    const kind = msg && msg.kind;
    if (!KINDS.includes(kind)) throw new Error("Embed Studio embeds reports, visuals, dashboards and tiles.");
    if (!embedUrlOk(msg.embedUrl)) throw new Error(`Embed Studio only opens content on ${EMBED_HOST}.`);
    if (typeof msg.accessToken !== "string" || !msg.accessToken) throw new Error("There's no sign-in token to open it with.");
    if (typeof msg.id !== "string" || !msg.id) throw new Error("There's no item to open.");
    const config = {
      type: kind, id: msg.id, embedUrl: msg.embedUrl, accessToken: msg.accessToken,
      tokenType: models.TokenType.Aad,
      settings: { panes: { pageNavigation: { visible: false } } },
    };
    if (kind === "report" && typeof msg.pageName === "string" && msg.pageName) config.pageName = msg.pageName;
    if (kind === "visual") {
      if (typeof msg.pageName !== "string" || !msg.pageName || typeof msg.visualName !== "string" || !msg.visualName) {
        throw new Error("Pick the page and the visual to open.");
      }
      config.pageName = msg.pageName;
      config.visualName = msg.visualName;
    }
    if (kind === "tile") {
      if (typeof msg.dashboardId !== "string" || !msg.dashboardId) throw new Error("Pick the dashboard the tile is on.");
      config.dashboardId = msg.dashboardId;
    }
    if (kind === "dashboard" || kind === "tile") delete config.settings;
    return config;
  }

  function start(win) {
    const doc = win.document;
    const el = doc.getElementById("content");
    const service = win.powerbi;
    const models = win["powerbi-client"] && win["powerbi-client"].models;
    let parentOrigin = null;
    let current = null;
    let kind = null;

    const send = (body, origin) => {
      try { win.parent.postMessage({ kfEmbedHost: body }, origin || parentOrigin); } catch (e) { /* the window is going */ }
    };
    const emit = (name, detail) => send({ event: name, detail: eventDetail(name, detail), at: win.performance.now() });

    const commands = {
      embed(msg) {
        const config = embedConfig(msg, models);
        service.reset(el);
        kind = config.type;
        current = service.embed(el, config);
        for (const name of EVENTS[kind]) current.on(name, (e) => emit(name, e && e.detail));
        emit("embed", { kind });
        return { kind };
      },
      setToken(msg) {
        if (!current) throw new Error("Nothing is open.");
        if (typeof msg.accessToken !== "string" || !msg.accessToken) throw new Error("There's no sign-in token.");
        return current.setAccessToken(msg.accessToken).then(() => ({}));
      },
      getPages() {
        if (!current || kind !== "report") throw new Error("Only a report has pages.");
        return current.getPages().then((pages) => pages.map((p) => ({
          name: p.name, displayName: p.displayName, isActive: !!p.isActive, visibility: p.visibility,
        })));
      },
      setPage(msg) {
        if (!current || kind !== "report") throw new Error("Only a report has pages.");
        if (typeof msg.pageName !== "string" || !msg.pageName) throw new Error("Pick a page.");
        return current.setPage(msg.pageName).then(() => ({}));
      },
      reload() {
        if (!current) throw new Error("Nothing is open.");
        return current.reload().then(() => ({}));
      },
      fullscreen() {
        if (!current) throw new Error("Nothing is open.");
        current.fullscreen();
        return {};
      },
      reset() {
        service.reset(el);
        current = null;
        kind = null;
        return {};
      },
    };

    win.addEventListener("message", (e) => {
      if (e.source !== win.parent || !APP_ORIGINS.includes(e.origin)) return;
      const msg = e.data && e.data.kfEmbed;
      if (!msg || typeof msg !== "object" || !Object.prototype.hasOwnProperty.call(commands, msg.command)) return;
      parentOrigin = e.origin;
      const seq = msg.seq;
      Promise.resolve()
        .then(() => commands[msg.command](msg))
        .then((result) => send({ reply: seq, ok: true, result: clean(result) }))
        .catch((err) => send({ reply: seq, ok: false, error: String((err && err.message) || err).slice(0, MAX_TEXT) }));
    });
    // Ready: the parent can send commands now. It carries nothing, so it goes to each app origin it may be.
    for (const origin of APP_ORIGINS) send({ event: "ready" }, origin);
    return { commands };
  }

  return { EMBED_HOST, APP_ORIGINS, EVENTS, embedUrlOk, clean, eventDetail, embedConfig, start };
});
