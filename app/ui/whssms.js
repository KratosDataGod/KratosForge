// Open in SSMS on the Warehouse page (docs/warehouse.md, "Open in SSMS"): SQL Server Management Studio
// on this warehouse, signed in as you with Microsoft Entra. The engine gives the connection Fabric
// reports (wh_connection: server, database, your sign-in name); the shell finds SSMS 22 or 21, or the
// folder you chose, and starts it (ssms_link.rs). KratosForge passes SSMS no password or token. The
// menu also copies the server name, opens the query in SSMS, or opens VS Code's MSSQL extension.

const SSMS_FOLDER_KEY = "fd.ssms.folder";
const SSMS_GET = "https://learn.microsoft.com/ssms/install/install";
const whSsmsConnections = new Map();  // sqlRefKey -> {server, database, user}, for this session

function whSsmsFolder() {
  try { return localStorage.getItem(SSMS_FOLDER_KEY) || null; } catch { return null; }
}
function whSsmsRememberFolder(folder) {
  try { localStorage.setItem(SSMS_FOLDER_KEY, folder); } catch { /* asked again next time */ }
}

async function whSsmsConnection(ref) {
  const key = sqlRefKey(ref);
  if (!whSsmsConnections.has(key)) {
    whSsmsConnections.set(key, await rpc("wh_connection", { workspace_id: ref.workspace_id, item_id: ref.item_id, kind: ref.kind }));
  }
  return whSsmsConnections.get(key);
}

// The toolbar's split button: Open in SSMS, and its menu.
function whSsmsButton(ref, queryText) {
  const more = h("button", { class: "tool split-more", "aria-label": "More SSMS options", title: "More SSMS options", "aria-haspopup": "menu",
    onclick: async (e) => {
      const anchor = e.currentTarget;
      const conn = await attempt(() => whSsmsConnection(ref), "Couldn't read the warehouse's connection");
      if (!conn) return;
      showMenu(anchor, [
        { info: `Opens SQL Server Management Studio on ${ref.name} and signs in as you with Microsoft Entra.` },
        { info: conn.server },
        { label: "Open in SSMS", icon: "open", action: () => whOpenSsms(ref) },
        { label: "Open in SSMS with this query", icon: "document", action: () => whOpenSsms(ref, queryText()) },
        { label: "Copy server name", icon: "copyArrowRight", action: () => whSsmsCopy(conn.server) },
        { label: "Open in VS Code (MSSQL extension)", icon: "code", action: () => whOpenMssql(ref) },
      ]);
    } }, icon("chevronDown", 14));
  return h("div", { class: "split" },
    btn("Open in SSMS", "open", () => whOpenSsms(ref), "tool", { title: `Open SQL Server Management Studio on ${ref.name}, signed in as you with Microsoft Entra` }),
    more);
}

function whSsmsCopy(server) {
  return navigator.clipboard.writeText(server).then(() => toast("Copied the server name."), () => toast("Couldn't copy the server name.", true));
}

async function whOpenSsms(ref, query = null) {
  const conn = await attempt(() => whSsmsConnection(ref), "Couldn't read the warehouse's connection");
  if (!conn) return;
  const exe = await attempt(() => invoke("ssms_find", { chosen: whSsmsFolder() }), "Couldn't look for SSMS");
  if (exe === undefined) return;
  if (!exe) { ssmsNotFound(ref, conn, query); return; }
  await whSsmsLaunch(ref, conn, query);
}

// The shell finds SSMS again itself (in the chosen folder or where it installs): the page never names the program.
async function whSsmsLaunch(ref, conn, query) {
  const ok = await attempt(async () => {
    await invoke("open_in_ssms", { chosen: whSsmsFolder(), server: conn.server, database: conn.database, user: conn.user || "", query: query || null });
    return true;
  }, "Couldn't open SSMS");
  if (ok) toast(`Opened SSMS on ${ref.name}. Sign in there if it asks; KratosForge doesn't pass it a password or token.`);
}

async function whOpenMssql(ref) {
  const conn = await attempt(() => whSsmsConnection(ref), "Couldn't read the warehouse's connection");
  if (!conn) return;
  const ok = await attempt(async () => {
    await invoke("open_in_mssql", { server: conn.server, database: conn.database, user: conn.user || "" });
    return true;
  }, "Couldn't open VS Code");
  if (ok) toast(`Opened VS Code's MSSQL extension on ${ref.name}. Sign in there if it asks.`);
}

// Neither SSMS 22 nor 21 where they install, and no folder chosen that holds it: say how to connect by hand.
function ssmsNotFound(ref, conn, query = null) {
  const server = h("input", { value: conn.server, readonly: true, class: "mono", "aria-label": "Server name" });
  const problem = h("p", { class: "error-text", style: "font-size:13px", hidden: true });
  const choose = btn("Choose SSMS's folder…", "folderOpen", async () => {
    choose.disabled = true;
    try {
      const folder = await attempt(() => invoke("pick_folder", { start: whSsmsFolder() }), "Couldn't open the folder dialog");
      if (!folder) return;
      const exe = await attempt(() => invoke("ssms_find", { chosen: folder }), "Couldn't look for SSMS");
      if (!exe) { problem.textContent = `There's no SSMS.exe signed by Microsoft in ${folder}. Choose the folder of SSMS 21 or later that holds it (…\\Common7\\IDE).`; problem.hidden = false; return; }
      whSsmsRememberFolder(folder);
      closeDialog();
      await whSsmsLaunch(ref, conn, query);
    } finally { choose.disabled = false; }
  }, "tool outline");
  showDialog("SSMS isn't on this PC", h("div", { style: "display:flex;flex-direction:column;gap:10px;max-width:520px" },
    h("p", {}, "KratosForge looked for SQL Server Management Studio 22 and 21 and didn't find either. Install it, or connect by hand with the server name below."),
    h("div", { class: "field" }, h("span", {}, "Server name"),
      h("div", { class: "folder-field" }, server, btn("Copy", null, () => whSsmsCopy(conn.server), "tool outline", { "aria-label": "Copy server name" }))),
    h("p", { class: "muted" }, `Database ${conn.database} · Authentication: Microsoft Entra MFA`),
    h("p", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); openLink(SSMS_GET); } }, "Get SSMS")),
    problem), [
    choose,
    btn("Close", null, closeDialog, "tool primary"),
  ]);
}
