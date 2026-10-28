// Platform notices (Preview): governance's request and shortcut events posted to Teams
// (platform_notify_service.py). The bell's Platform filter lists the notices still open as far
// as this PC's notices know; Settings > Notifications sets the routes. Webhook addresses never
// come back from the engine: a route shows only whether one is set and its host.

const PN_KINDS = [["request.waiting", "Request waiting on someone"], ["request.decided", "Request decided"],
  ["request.withdrawn", "Request withdrawn"], ["shortcut.broken", "Shortcut broken"], ["shortcut.fixed", "Shortcut fixed"],
  ["incident.impact", "Incident reaching data products"], ["product.changed", "Data product changed (to its followers)"],
  ["exception.ending", "Policy exception ending within 14 days"]];
const PN_WHERE = { requests: "Data mesh > Requests", health: "Shortcuts > Health", detail: "Monitor > KratosForge > Operations > Incidents", products: "Data mesh > Products",
  exceptions: "Platform > Govern > Governance" };

// ---------- the bell ----------

// The open notices, the ones Teams hasn't taken, and whether any route is set. Kept on activity
// so renderTray can draw them; a failed read leaves the filter out rather than showing nothing.
async function loadPlatformNotices() {
  if (!previewShown()) { activity.platform = null; return; }
  activity.platform = await rpc("platform_notify_open", {}).catch(() => null);
}

function platformNoticesShown() {
  const p = activity.platform;
  return !!p && (p.routes || p.open.length > 0 || p.waiting > 0);
}

function platformTrayList() {
  const p = activity.platform;
  const items = p.open.map((n) => {
    const request = n.kind === "request.waiting";
    const scope = [n.domain ? `${n.domain} domain` : "", n.env ? n.env[0].toUpperCase() + n.env.slice(1) : ""].filter(Boolean).join(", ");
    const text = request ? `A request waits on ${n.whom || "someone"}. Its detail is in Platform, not here.`
      : [scope, n.detail].filter(Boolean).join(". ");
    return h("div", { class: "tray-item" },
      h("div", { class: "row" }, h("span", { class: `pill-s ${request ? "warn" : "fail"}` }, request ? "Waiting" : "Broken"),
        h("b", { class: "grow" }, n.title || (request ? "A request" : "A shortcut")), h("span", { class: "muted-text small" }, when(n.at))),
      text ? h("div", { class: "tray-text" }, text) : null,
      h("div", { class: "row wrap" }, btn(`Open in ${PN_WHERE[n.open.tab] || "Platform"}`, null, () => { toggleTray(false); openPlatformNotice(n); }, "tool link small")));
  });
  if (p.waiting) {
    const one = p.waiting === 1;
    const why = p.held === p.waiting ? `${one ? "It's" : "They're"} held for quiet hours and ${one ? "goes" : "go"} out when quiet hours end.`
      : `Teams didn't answer. ${one ? "It waits" : "They wait"} on this PC and ${one ? "is" : "are"} sent, marked Delayed, the next time a notice goes out.`;
    items.push(h("div", { class: "tray-item pn-waiting" },
      h("div", { class: "row" }, h("span", { class: "pill-s neu" }, p.held === p.waiting ? "Held" : "Delayed"),
        h("b", { class: "grow" }, `${p.waiting} notice${p.waiting === 1 ? " hasn't" : "s haven't"} reached Teams`)),
      h("div", { class: "tray-text" }, why),
      p.held !== p.waiting && p.error ? h("details", {}, h("summary", {}, "Details"), h("pre", {}, p.error)) : null,
      h("div", { class: "row wrap" }, btn("Open Settings > Notifications", null, () => { toggleTray(false); go("settings"); }, "tool link small"))));
  }
  return [
    h("p", { class: "muted-text small pn-note" }, "Requests waiting and shortcuts broken, as far as this PC's notices know. Another PC or Fabric itself may have settled some already."),
    ...(items.length ? items : [h("p", { class: "muted-text tray-empty" }, "Nothing open.")]),
    h("p", { class: "muted-text small pn-note" }, "Only platform notices sent from this PC show here. Teams has every one."),
  ];
}

function openPlatformNotice(n) {
  if (n.open.view === "shortcuts") {
    catalogPage.tab = "shortcuts";
    scPage.view = "health";
    go("onelake");
  } else if (n.open.view === "onelake") {
    openProducts();
  } else if (n.open.view === "incident") {
    incPage.filter = "active";
    backToIncidents();
  } else if (n.open.view === "governance") {
    openPolicyExceptions(n.open.target);
  } else {
    goMeta("requests");
  }
}

// ---------- Settings > Notifications ----------

async function platformNoticesSection() {
  if (!previewShown()) return null;
  const box = h("div", { class: "pn-settings" }, h("p", { class: "muted-text small" }, "Loading platform notices..."));
  drawPlatformNotices(box);
  return box;
}

async function drawPlatformNotices(box) {
  const s = await rpc("platform_notify_settings", {}).catch((e) => ({ error: e }));
  if (s.error) {
    box.replaceChildren(h("p", { class: "fail-text small" }, `Couldn't read the platform notice settings: ${friendlyError(s.error).text}`));
    return;
  }
  const save = (changes = {}) => attempt(() => rpc("platform_notify_save", {
    routes: s.routes.map((r) => ({ name: r.name, kinds: r.kinds, domains: r.domains })), quiet: s.quiet, quiet_exempt: s.quiet_exempt, ...changes,
  }), "Not saved");
  const kindsText = (kinds) => {
    if (!kinds.length) return "Everything";
    const req = kinds.filter((k) => k.startsWith("request.")).map((k) => k.split(".")[1]);
    const sc = kinds.filter((k) => k.startsWith("shortcut.")).map((k) => k.split(".")[1]);
    return [req.length ? `Requests: ${req.join(", ")}` : "", sc.length ? `Shortcuts: ${sc.join(", ")}` : ""].filter(Boolean).join("; ");
  };
  const rows = s.routes.map((r) => h("tr", {},
    h("td", {}, h("b", {}, r.name === "default" ? "default (anything no other route posts)" : r.name)),
    h("td", {}, kindsText(r.kinds)),
    h("td", {}, r.domains.length ? r.domains.join(", ") : "All"),
    h("td", {}, r.url_set ? [h("span", { class: "mono" }, r.host), h("div", { class: "muted-text small" }, "Set, address hidden")]
      : [h("span", { class: "fail-text" }, "Not set"), h("div", { class: "muted-text small" }, "Nothing posts until one is added")]),
    h("td", { class: "nowrap" },
      r.url_set ? btn("Test", null, async () => { if (await save({ test: r.name })) toast(`Sent a test message to ${r.name}`); }, "tool link small") : null,
      btn("Edit", null, () => platformRouteDialog(s, r, () => drawPlatformNotices(box)), "tool link small"))));
  const quiet = h("input", { id: "pn-quiet", value: s.quiet, placeholder: "22:00-07:00", spellcheck: "false" });
  quiet.onchange = async () => { if (await save({ quiet: quiet.value.trim() })) drawPlatformNotices(box); else quiet.value = s.quiet; };
  const exempt = (sev, label) => {
    const cb = h("input", { type: "checkbox", checked: s.quiet_exempt.includes(sev) ? true : null });
    cb.onchange = async () => {
      const next = cb.checked ? [...new Set([...s.quiet_exempt, sev])] : s.quiet_exempt.filter((x) => x !== sev);
      if (await save({ quiet_exempt: next })) drawPlatformNotices(box); else cb.checked = !cb.checked;
    };
    return h("label", { class: "check" }, cb, label);
  };
  const waiting = s.waiting ? h("div", { class: "pn-waiting-line" }, h("span", { class: "pill-s neu" }, `${s.waiting} waiting`),
    h("span", {}, `${s.waiting === 1 ? "A notice hasn't" : `${s.waiting} notices haven't`} reached Teams yet, held for quiet hours or because Teams didn't answer. They wait on this PC for up to three days and go out, marked Delayed when Teams didn't answer, the next time a notice goes out.`)) : null;
  box.replaceChildren(
    h("div", { class: "row" }, h("h3", { class: "grow" }, "Platform notices ", previewTag()),
      btn("Add a route", "add", () => platformRouteDialog(s, null, () => drawPlatformNotices(box)), "tool primary")),
    h("p", { class: "muted-text small" }, "Post requests waiting on someone and broken shortcuts to Teams channels, once each. A request's detail never goes to Teams: the post names it and who it waits on, and says where to open it."),
    s.routes.length ? h("div", { class: "table-scroll" }, h("table", { class: "list pn-routes" },
      h("thead", {}, h("tr", {}, ["Route", "Posts", "Domains", "Teams webhook"].map((c) => h("th", {}, c)), h("th", {}, h("span", { class: "sr-only" }, "Actions")))),
      h("tbody", {}, rows)))
      : h("p", { class: "muted-text small" }, "No routes yet, so nothing is posted. Add one, or name a route default to post everything."),
    h("div", { class: "pn-quiet" },
      h("div", { class: "field" }, h("label", { for: "pn-quiet" }, "Quiet hours"), quiet,
        h("div", { class: "muted-text small" }, "This PC's clock. Notices wait and go out when quiet hours end. Leave empty for none.")),
      h("fieldset", { class: "field" }, h("legend", {}, "Send during quiet hours anyway"),
        exempt("warn", "Warnings (governance marks these)"), exempt("info", "Everything else"))),
    waiting,
    howItWorks("settings.platform-notices", {
      reads: "The request and shortcut events governance hands to this PC. It doesn't poll your tenant.",
      changes: "Your app settings (routes, quiet hours) and a notice outbox and record in this app's folder. Test posts one message to that channel.",
      results: "Teams channels you name. Webhook addresses work like passwords: shown only as a host, never logged or backed up. Each notice is posted once; a fixed shortcut or a decided request posts once more to close it.",
      anchor: "platform-notices-preview",
    }));
}

// Add (route null) or edit a route. The webhook box is empty: a route keeps its address unless
// a new one is pasted. A route's name is fixed once made, since its address is kept by name.
async function platformRouteDialog(s, route, done) {
  const name = h("input", { id: "pn-name", value: route?.name || "", disabled: route ? true : null, spellcheck: "false" });
  const url = h("input", { id: "pn-url", type: "password", autocomplete: "off", spellcheck: "false",
    placeholder: route?.url_set ? `Set for ${route.host}. Paste a new one to replace it.` : "https://..." });
  const kinds = PN_KINDS.map(([k, label]) => [k, h("input", { type: "checkbox", checked: route?.kinds.includes(k) ? true : null }), label]);
  const domains = await rpc("platform_domains", {}).catch(() => null);
  const picked = new Set(route?.domains || []);
  const domainBox = domains?.length
    ? h("div", { class: "pn-domains" }, domains.map((d) => {
      const cb = h("input", { type: "checkbox", checked: picked.has(d.id) || picked.has(d.name) ? true : null });
      cb.onchange = () => (cb.checked ? picked.add(d.id) : (picked.delete(d.id), picked.delete(d.name)));
      return h("label", { class: "check" }, cb, d.name);
    }))
    : h("input", { id: "pn-domains", value: [...picked].join(", "), placeholder: "sales, finance", spellcheck: "false" });
  const err = h("p", { class: "fail-text small", hidden: true });
  const save = async (test) => {
    const me = { name: name.value.trim(), kinds: kinds.filter(([, cb]) => cb.checked).map(([k]) => k),
      domains: domains?.length ? [...picked] : domainBox.value.split(",").map((x) => x.trim()).filter(Boolean) };
    if (url.value.trim()) me.url = url.value.trim();
    if (!me.name) { err.textContent = "Give the route a name."; err.hidden = false; return; }
    if (!route && s.routes.some((r) => r.name === me.name)) { err.textContent = `There's already a route called ${me.name}.`; err.hidden = false; return; }
    if (test && !me.url && !route?.url_set) { err.textContent = "Paste the Teams webhook to send a test."; err.hidden = false; return; }
    const routes = s.routes.map((r) => (r.name === route?.name ? me : { name: r.name, kinds: r.kinds, domains: r.domains }));
    if (!route) routes.push(me);
    const res = await attempt(() => rpc("platform_notify_save", { routes, quiet: s.quiet, quiet_exempt: s.quiet_exempt, test: test ? me.name : "" }),
      test ? "Test message not sent" : "Not saved");
    if (!res) return;
    closeDialog();
    toast(test ? `Sent a test message to ${me.name} and saved` : "Saved on this PC");
    done();
  };
  const remove = async () => {
    closeDialog();
    if (!await confirmDialog(`Remove the route ${route.name}?`, "Notices stop going to its channel, and its webhook address is forgotten. Notices already waiting for it go to the default route, if there is one.", "Remove", { danger: true })) return;
    const res = await attempt(() => rpc("platform_notify_save", { routes: s.routes.filter((r) => r.name !== route.name).map((r) => ({ name: r.name, kinds: r.kinds, domains: r.domains })),
      quiet: s.quiet, quiet_exempt: s.quiet_exempt }), "Not removed");
    if (res) { toast(`Removed ${route.name}`); done(); }
  };
  showDialog(route ? `Edit route: ${route.name}` : "Add a route", h("div", { class: "wl-form pn-route" },
    h("label", { class: "field" }, "Name", name,
      route ? null : h("span", { class: "muted-text small" }, "Name it default to post everything no other route takes.")),
    h("label", { class: "field" }, "Teams webhook", url,
      h("span", { class: "muted-text small" }, route?.url_set ? "Leave empty to keep the one it has. The address is never shown again once saved."
        : "In the Teams channel, add the Workflows template \"Post to a channel when a webhook request is received\", then paste its address.")),
    h("fieldset", { class: "field" }, h("legend", {}, "Posts"),
      kinds.map(([, cb, label]) => h("label", { class: "check" }, cb, label)),
      h("span", { class: "muted-text small" }, "None ticked posts everything.")),
    h("fieldset", { class: "field" }, h("legend", {}, "Domains"), domainBox,
      h("span", { class: "muted-text small" }, domains?.length ? "From Data mesh. All domains when none is ticked." : "Domain ids, separated by commas. All domains when empty.")),
    h("div", { class: "note-box" }, h("b", {}, "Before you save"),
      h("p", {}, "Saving changes this PC's app settings only and posts nothing. Test and save sends one message, \"KratosForge platform notices are on\", to this channel first and saves only if Teams takes it.")),
    err),
  [route ? btn("Remove route", null, remove, "tool outline danger") : null, btn("Cancel", null, closeDialog, "tool outline"),
    btn("Test and save", "alert", () => save(true), "tool outline"), btn("Save", null, () => save(false), "tool primary")].filter(Boolean));
}
