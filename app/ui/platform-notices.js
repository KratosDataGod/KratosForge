// Platform notices (Preview): governance's request and shortcut events sent to Teams, a Power
// Automate flow or email (platform_notify_service.py, workloads/deliver.py). The bell's Platform filter lists the notices still open as far
// as this PC's notices know; Settings > Notifications sets the routes and the company mail server.
// Route addresses never come back from the engine: a route shows how it sends and a label (its
// host, or how many people). The mail server's password never comes back either.

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

// How a route sends, as its saved address says (deliver.py): a Teams webhook, a flow, or email.
const PN_SEND = { teams: "Teams webhook", flow: "Power Automate flow", email: "Email" };

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
    h("td", {}, r.url_set ? [h("span", {}, PN_SEND[r.kind] || PN_SEND.teams),
      h("div", { class: "muted-text small" }, r.kind === "email" ? r.host : [h("span", { class: "mono" }, r.host), ", address hidden"])]
      : [h("span", { class: "fail-text" }, "Not set"), h("div", { class: "muted-text small" }, "Nothing is sent until one is added")]),
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
    h("p", { class: "muted-text small" }, "Send requests waiting on someone and broken shortcuts to Teams, a Power Automate flow or email, once each. A request's detail never leaves the app: a notice names it, who it waits on, and where to open it."),
    s.routes.length ? h("div", { class: "table-scroll" }, h("table", { class: "list pn-routes" },
      h("thead", {}, h("tr", {}, ["Route", "Sends", "Domains", "Sends by"].map((c) => h("th", {}, c)), h("th", {}, h("span", { class: "sr-only" }, "Actions")))),
      h("tbody", {}, rows)))
      : h("p", { class: "muted-text small" }, "No routes yet, so nothing is sent. Add one, or name a route default to send everything."),
    mailServerCard(s.mail_server || { set: false }, () => drawPlatformNotices(box)),
    h("div", { class: "pn-quiet" },
      h("div", { class: "field" }, h("label", { for: "pn-quiet" }, "Quiet hours"), quiet,
        h("div", { class: "muted-text small" }, "This PC's clock. Notices wait and go out when quiet hours end. Leave empty for none.")),
      h("fieldset", { class: "field" }, h("legend", {}, "Send during quiet hours anyway"),
        exempt("warn", "Warnings (governance marks these)"), exempt("info", "Everything else"))),
    waiting,
    howItWorks("settings.platform-notices", {
      reads: "The request and shortcut events governance hands to this PC. It doesn't poll your tenant.",
      changes: "Your app settings (routes, quiet hours, the mail server) and a notice outbox and record in this app's folder. The mail server's password goes in Windows Credential Manager. Test sends one message to that route.",
      results: "The Teams channels, flows and email addresses you name. Webhook and flow addresses work like passwords: shown only as a host, never logged or backed up. Email goes only through your company mail server, always encrypted. Each notice is sent once; a fixed shortcut or a decided request sends once more to close it.",
      anchor: "platform-notices-preview",
    }));
}

// Add (route null) or edit a route. Its address box is empty: a route keeps its address unless a new
// one is entered, so switching how it sends needs the new address. A route's name is fixed once
// made, since its address is kept by name.
async function platformRouteDialog(s, route, done) {
  const name = h("input", { id: "pn-name", value: route?.name || "", disabled: route ? true : null, spellcheck: "false" });
  let how = route?.url_set ? route.kind || "teams" : "teams";
  const url = h("input", { id: "pn-url", type: "password", autocomplete: "off", spellcheck: "false" });
  const to = h("input", { id: "pn-to", autocomplete: "off", spellcheck: "false", placeholder: "ana@contoso.com; platform-admins@contoso.com" });
  const urlHelp = h("span", { class: "muted-text small" });
  const toHelp = h("span", { class: "muted-text small" });
  const urlField = h("label", { class: "field" }, h("span", { id: "pn-url-label" }), url, urlHelp);
  const toField = h("label", { class: "field" }, "To", to, toHelp);
  const mail = s.mail_server || { set: false };
  const note = h("p", {});
  const kept = () => route?.url_set && route.kind === how;
  const show = () => {
    urlField.hidden = how === "email";
    toField.hidden = how !== "email";
    urlField.querySelector("#pn-url-label").textContent = how === "flow" ? "Flow address (HTTP POST URL)" : "Teams webhook";
    url.placeholder = kept() ? `Set for ${route.host}. Paste a new one to replace it.` : "https://...";
    urlHelp.textContent = kept() ? "Leave empty to keep the one it has. The address is never shown again once saved."
      : how === "flow" ? "In Power Automate, make a flow that starts \"When an HTTP request is received\" and sends the email, then paste its HTTP POST URL. It gets the title and lines as JSON."
        : "In the Teams channel, add the Workflows template \"Post to a channel when a webhook request is received\", then paste its address.";
    to.placeholder = kept() ? `Set for ${route.host.split(",")[0]}. Type the addresses again to change them.` : "ana@contoso.com; platform-admins@contoso.com";
    toHelp.textContent = `Addresses or distribution lists, separated by semicolons. Up to 20.${kept() ? " Leave empty to keep the ones it has." : ""}`;
    note.textContent = how === "email"
      ? (mail.set ? `Mail goes through ${mail.host} (Company mail server). Saving changes this PC's settings only. Test and save sends one email, "KratosForge platform notices are on", to these addresses and saves only if the server takes it.`
        : "Set the company mail server first (below the routes): email waits until one is set. Saving changes this PC's settings only.")
      : `Saving changes this PC's settings only and sends nothing. Test and save sends one message, "KratosForge platform notices are on", to this ${how === "flow" ? "flow" : "channel"} first and saves only if it takes it.`;
  };
  const sendBy = h("div", { class: "pn-send-by", role: "radiogroup" }, Object.entries({
    teams: "A channel's \"Post to a channel when a webhook request is received\" workflow",
    flow: "A flow that starts \"When an HTTP request is received\". It sends the mail, as your tenant allows",
    email: "Straight to people, through the company mail server",
  }).map(([k, hint]) => {
    const rb = h("input", { type: "radio", name: "pn-send-by", value: k, checked: how === k ? true : null });
    rb.onchange = () => { how = k; show(); };
    return h("label", { class: "pn-send-choice" }, rb, h("span", {}, PN_SEND[k], h("small", {}, hint)));
  }));
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
  const fail = (text) => { err.textContent = text; err.hidden = false; };
  const save = async (test) => {
    const me = { name: name.value.trim(), kinds: kinds.filter(([, cb]) => cb.checked).map(([k]) => k),
      domains: domains?.length ? [...picked] : domainBox.value.split(",").map((x) => x.trim()).filter(Boolean) };
    const typed = (how === "email" ? to.value : url.value).trim();
    if (typed) me.url = how === "email" ? `mailto:${typed}` : how === "flow" ? `flow:${typed}` : typed;
    if (!me.name) return fail("Give the route a name.");
    if (!route && s.routes.some((r) => r.name === me.name)) return fail(`There's already a route called ${me.name}.`);
    if (!typed && route?.url_set && !kept()) return fail(how === "email" ? "Enter the email addresses this route sends to." : `Paste the ${how === "flow" ? "flow's address" : "Teams webhook"} this route sends to.`);
    if (test && !typed && !kept()) return fail(how === "email" ? "Enter the email addresses to send a test." : `Paste the ${how === "flow" ? "flow's address" : "Teams webhook"} to send a test.`);
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
    if (!await confirmDialog(`Remove the route ${route.name}?`, "Notices stop going to it, and its address is forgotten. Notices already waiting for it go to the default route, if there is one.", "Remove", { danger: true })) return;
    const res = await attempt(() => rpc("platform_notify_save", { routes: s.routes.filter((r) => r.name !== route.name).map((r) => ({ name: r.name, kinds: r.kinds, domains: r.domains })),
      quiet: s.quiet, quiet_exempt: s.quiet_exempt }), "Not removed");
    if (res) { toast(`Removed ${route.name}`); done(); }
  };
  show();
  showDialog(route ? `Edit route: ${route.name}` : "Add a route", h("div", { class: "wl-form pn-route" },
    h("label", { class: "field" }, "Name", name,
      route ? null : h("span", { class: "muted-text small" }, "Name it default to send everything no other route takes.")),
    h("fieldset", { class: "field" }, h("legend", {}, "Send by"), sendBy),
    urlField, toField,
    h("fieldset", { class: "field" }, h("legend", {}, "Sends"),
      kinds.map(([, cb, label]) => h("label", { class: "check" }, cb, label)),
      h("span", { class: "muted-text small" }, "None ticked sends everything.")),
    h("fieldset", { class: "field" }, h("legend", {}, "Domains"), domainBox,
      h("span", { class: "muted-text small" }, domains?.length ? "From Data mesh. All domains when none is ticked." : "Domain ids, separated by commas. All domains when empty.")),
    h("div", { class: "note-box" }, h("b", {}, "Before you save"), note),
    err),
  [route ? btn("Remove route", null, remove, "tool outline danger") : null, btn("Cancel", null, closeDialog, "tool outline"),
    btn("Test and save", "alert", () => save(true), "tool outline"), btn("Save", null, () => save(false), "tool primary")].filter(Boolean));
}

// The company mail server every email route sends through, set once on this PC. Its password goes
// to Windows Credential Manager and never comes back.
function mailServerCard(m, done) {
  const what = m.set
    ? [h("span", { class: "mono" }, `${m.host}:${m.port}`), `, ${m.security === "tls" ? "TLS" : "STARTTLS required"}, from `,
      h("span", { class: "mono" }, m.sender),
      m.username ? [", signs in as ", h("span", { class: "mono" }, m.username), m.password_saved ? ". Password in Windows Credential Manager." : ". No password saved: enter it again."] : ", no sign-in."]
    : ["Not set. Email routes wait until one is."];
  return h("div", { class: "card pad pn-mail" },
    h("div", { class: "row" }, h("h3", { class: "grow" }, "Company mail server"),
      btn(m.set ? "Edit" : "Set up", null, () => mailServerDialog(m, done), "tool outline small")),
    h("p", { class: "small" }, what),
    h("p", { class: "muted-text small" }, "Set once on this PC. Every email route sends through it."));
}

function mailServerDialog(m, done) {
  const host = h("input", { id: "ms-host", value: m.host || "", placeholder: "smtp.contoso.com", spellcheck: "false" });
  const port = h("input", { id: "ms-port", value: String(m.port || 587), inputmode: "numeric" });
  let security = m.security || "starttls";
  const sec = [["starttls", "STARTTLS, required (port 587)", 587], ["tls", "TLS from the start (port 465)", 465]].map(([k, label, p]) => {
    const rb = h("input", { type: "radio", name: "ms-security", value: k, checked: security === k ? true : null });
    rb.onchange = () => { security = k; if (port.value === "587" || port.value === "465") port.value = String(p); };
    return h("label", { class: "check" }, rb, label);
  });
  const sender = h("input", { id: "ms-sender", value: m.sender || "", placeholder: "kratosforge@contoso.com", spellcheck: "false" });
  const user = h("input", { id: "ms-user", value: m.username || "", autocomplete: "off", spellcheck: "false" });
  const pass = h("input", { id: "ms-pass", type: "password", autocomplete: "new-password",
    placeholder: m.password_saved ? "Saved. Type to replace" : "" });
  const testTo = h("input", { id: "ms-test", placeholder: "you@contoso.com", spellcheck: "false" });
  const err = h("p", { class: "fail-text small", hidden: true });
  const save = async (test) => {
    if (test && !testTo.value.trim()) { err.textContent = "Enter an address to send the test to."; err.hidden = false; return; }
    const res = await attempt(() => rpc("mail_server_save", { host: host.value.trim(), port: Number(port.value) || 0, security,
      sender: sender.value.trim(), username: user.value.trim(), password: pass.value, test_to: test ? testTo.value.trim() : "" }),
    test ? "Test email not sent" : "Not saved");
    if (!res) return;
    closeDialog();
    toast(test ? `Sent a test email to ${testTo.value.trim()} and saved` : "Saved on this PC");
    done();
  };
  const forget = async () => {
    closeDialog();
    if (!await confirmDialog("Forget the company mail server?", "Email routes stop sending and wait until a server is set again. Its password is removed from Windows Credential Manager.", "Forget", { danger: true })) return;
    if (await attempt(() => rpc("mail_server_forget", {}), "Not removed")) { toast("Forgot the mail server"); done(); }
  };
  showDialog("Company mail server", h("div", { class: "wl-form pn-route" },
    h("div", { class: "pn-two" }, h("label", { class: "field" }, "Server", host), h("label", { class: "field" }, "Port", port)),
    h("fieldset", { class: "field" }, h("legend", {}, "Encryption"), sec,
      h("span", { class: "muted-text small" }, "Always encrypted, and the server's certificate is checked. A server that can't do either is refused.")),
    h("label", { class: "field" }, "From", sender, h("span", { class: "muted-text small" }, "The address mail comes from. Your relay may only allow its own.")),
    h("div", { class: "pn-two" }, h("label", { class: "field" }, "User name (optional)", user), h("label", { class: "field" }, "Password", pass)),
    h("span", { class: "muted-text small" }, "Leave both empty for a relay that takes mail from this PC without signing in. The password is kept in Windows Credential Manager for your Windows account, never in the app's settings, logs or backups."),
    h("label", { class: "field" }, "Send a test to", testTo),
    h("div", { class: "note-box" }, h("b", {}, "Before you save"),
      h("p", {}, "Saving changes this PC's settings only. Test and save sends one email to the test address and saves only if the server takes it. Every email route uses this server.")),
    err),
  [m.set ? btn("Forget server", null, forget, "tool outline danger") : null, btn("Cancel", null, closeDialog, "tool outline"),
    btn("Test and save", "alert", () => save(true), "tool outline"), btn("Save", null, () => save(false), "tool primary")].filter(Boolean));
}
