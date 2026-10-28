// Changes to a warehouse from the Warehouse page (WH-10b). A page reads until Allow changes is ticked
// for that warehouse (this session only). Then Run first asks the engine what the script does (a
// dry run, nothing sent): a script that only reads runs as before; one that changes the warehouse
// shows what it changes and asks, and in Prod, or a workspace with no environment, asks for the
// warehouse's name to be typed. The engine checks that name against Fabric's (sql_exec.py).

const sqlChangesOn = new Set();  // sqlRefKey of each warehouse whose page allows changes this session

const whChangesAllowed = () => state.settings?.sql_warehouse_writes?.value !== false;
const whChangesTicked = (ref) => whChangesAllowed() && sqlChangesOn.has(sqlRefKey(ref));

// "dev" | "test" | "prod" | "none", from the project's environments only (#807). A workspace that
// isn't Dev, Test or Prod there is "none", treated like Prod, whatever its name says: a name is a
// guess, and an unmapped "kf_tenant_test" read as Test skipped the typed name.
// A workspace two environments share, such as the Keys workspace, is the strictest of them.
let whEnvWs = {};
function whEnv(ref) {
  return whEnvWs[ref.workspace_id] || "none";
}

// Reads the project's environments again, so a change or deploy sees the workspaces as Metadata has
// them now. If they can't be read, what was read before stays (at first nothing: all like Prod).
async function whEnvRead() {
  const p = await rpc("meta_project").catch(() => null);
  if (p) whEnvWs = envWorkspaces(p.environments, true);
}

// A Prod workspace, or one whose environment isn't known, asks for the name to be typed.
const whNeedsName = (ref) => ["prod", "none"].includes(whEnv(ref));

// The Allow changes switch in the page's header.
function whChangesSwitch(ref, redraw) {
  const allowed = whChangesAllowed();
  const box = h("input", { type: "checkbox", checked: whChangesTicked(ref) || null, disabled: allowed ? null : true,
    onchange: (e) => {
      if (e.target.checked) sqlChangesOn.add(sqlRefKey(ref)); else sqlChangesOn.delete(sqlRefKey(ref));
      redraw();
    } });
  return h("label", { class: `wh-changes${whChangesTicked(ref) ? " on" : ""}`,
    title: allowed ? `Run may change ${ref.name} after it shows what it changes and you confirm. Untick to only read.`
      : "Warehouse changes are turned off in Settings > Advanced > SQL queries." }, box, "Allow changes");
}

// The confirm for a change. plan is the engine's dry run. Resolves true to run it.
async function whChangeConfirm(ref, plan) {
  await whEnvRead();
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const env = whEnv(ref);
    const named = whNeedsName(ref);
    const where = ENV_NAMES[env] || env;
    const changes = plan.statements.filter((s) => s.permission === "warehouse_write");
    const reads = plan.statements.length - changes.length;
    const typed = h("input", { "aria-label": "Warehouse name", spellcheck: "false", autocomplete: "off",
      oninput: () => { go.disabled = typed.value !== ref.name; } });
    const go = btn(env === "prod" ? "Run in Prod" : "Run the change", null, () => { finish(true); closeDialog(); }, "tool danger-solid");
    go.disabled = named;
    const cancel = btn("Cancel", null, () => { finish(false); closeDialog(); }, "tool outline");
    showDialog(`Change ${ref.name}${env === "prod" ? " in Prod" : ""}?`, h("div", { class: "wh-change" },
      h("p", {}, `This runs in Fabric as you and uses capacity.`),
      h("dl", { class: "wh-change-where" },
        h("dt", {}, "Workspace"), h("dd", {}, ref.workspace_name || ref.workspace_id),
        h("dt", {}, ref.kind === "endpoint" ? "SQL analytics endpoint" : "Warehouse"), h("dd", {}, ref.name),
        h("dt", {}, "Environment"), h("dd", { class: env === "prod" ? "error-text" : null },
          env === "none" ? "Not set, so it's treated like Prod" : where)),
      h("div", { class: "wh-change-what" }, h("div", { class: "wh-change-head" }, "What it changes"),
        h("ul", {}, ...changes.map((s) => h("li", {}, `Statement ${s.statement}: ${s.reason}`)),
          reads ? h("li", { class: "muted-text" }, `and ${reads} statement${reads === 1 ? "" : "s"} that read${reads === 1 ? "s" : ""} or set${reads === 1 ? "s" : ""} a variable`) : null)),
      h("p", { class: "muted-text small" }, "It all runs in one transaction. If a statement fails, or you press Stop, nothing is kept. " +
        "A procedure's body isn't checked here, since what it does depends on the procedure. The audit log records who changed which warehouse and how many rows, never the SQL or the data."),
      named ? h("label", { class: "field" }, h("span", {}, "Type ", h("code", {}, ref.name), ` to change ${env === "prod" ? "Prod" : "it"}`), typed) : null),
    [cancel, go], () => finish(false));
    (named ? typed : cancel).focus();  // as confirmDialog does: Enter never runs the change by itself
  });
}
