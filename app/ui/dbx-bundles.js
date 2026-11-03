// Source control > Bundles (Preview): a Databricks Asset Bundle in this repository, validated,
// planned and deployed with your own Databricks CLI as a connection's CLI profile (engine
// databricks_bundle_*). Validate and plan change nothing. Deploy plans again and stops if the plan
// changed since you looked; a plan that deletes or rebuilds anything, or a production target,
// needs the target's name typed. Deploying runs no job or pipeline.
// Mockup: https://claude.ai/artifact/XgpknxChxChHfHTKHKHugD (Bundles)

const dbxBundle = { folder: "", root: "", info: null, target: "", connection: "", plan: null, validated: null, busy: "", load: 0 };
const DBX_BUNDLE_ACTION = { create: ["Create", "info"], update: ["Update", "neu"], delete: ["Delete", "fail"], recreate: ["Recreate", "wrn"] };

function gitBundlesTab(st) {
  if (dbxBundle.root !== st.root) Object.assign(dbxBundle, { root: st.root, folder: st.root, info: null, plan: null, validated: null });
  const box = h("div", { class: "dbx-bundles" }, loading("Reading databricks.yml…"));
  dbxBundleLoad(box);
  return box;
}

async function dbxBundleLoad(box) {
  // Loads can overlap, and the page can draw the tab again meanwhile: only the newest load draws.
  const mine = ++dbxBundle.load;
  const [info, conns] = await Promise.all([
    rpc("databricks_bundle_targets", { folder: dbxBundle.folder }).catch((err) => ({ error: friendlyError(err).text })),
    rpc("engine_connections").catch(() => ({ connections: [] }))]);
  if (!box.isConnected || mine !== dbxBundle.load) return;
  dbxBundle.info = info;
  const cli = (conns.connections || []).filter((c) => c.host && c.source === "cli");
  if (!cli.some((c) => c.key === dbxBundle.connection)) dbxBundle.connection = cli[0]?.key || "";
  const targets = info.targets || [];
  if (!targets.some((t) => t.name === dbxBundle.target)) dbxBundle.target = (targets.find((t) => t.default) || targets[0])?.name || "";
  box.replaceChildren(dbxBundleForm(box, cli), dbxBundleBody(box, cli));
}

function dbxBundleForm(box, cli) {
  const folder = h("input", { "aria-label": "Bundle folder", value: dbxBundle.folder, spellcheck: "false" });
  const reset = () => Object.assign(dbxBundle, { plan: null, validated: null });
  return h("form", { class: "row wrap dbx-bundle-form", onsubmit: (e) => {
    e.preventDefault();
    dbxBundle.folder = folder.value.trim();
    reset();
    dbxBundleLoad(box);
  } },
    h("label", { class: "field grow" }, "Bundle folder (holds databricks.yml)", folderField(folder, "the bundle folder")),
    btn("Read", null, null, "tool outline", { type: "submit" }),
    dbxBundle.info?.targets?.length ? h("label", { class: "field" }, "Target", h("select", { "aria-label": "Target",
      onchange: (e) => { dbxBundle.target = e.target.value; reset(); dbxBundleLoad(box); } },
      dbxBundle.info.targets.map((t) => h("option", { value: t.name, selected: t.name === dbxBundle.target }, `${t.name}${t.mode ? ` (${t.mode})` : ""}`)))) : null,
    cli.length ? h("label", { class: "field" }, "Runs as", h("select", { "aria-label": "Databricks CLI profile",
      onchange: (e) => { dbxBundle.connection = e.target.value; reset(); dbxBundleLoad(box); } },
      cli.map((c) => h("option", { value: c.key, selected: c.key === dbxBundle.connection }, c.label)))) : null);
}

function dbxBundleBody(box, cli) {
  const info = dbxBundle.info || {};
  if (info.error) return h("p", { class: "muted-text" }, info.error);
  if (!info.targets?.length) return h("p", { class: "muted-text" }, "databricks.yml has no targets to deploy to.");
  if (!cli.length) {
    return h("div", { class: "pad" }, h("p", {}, "Bundles run with the Databricks CLI as one of its profiles, and there's none on this PC yet."),
      h("p", { class: "muted-text small" }, "Sign in with databricks auth login, then come back here."));
  }
  const busy = !!dbxBundle.busy;
  const p = dbxBundle.plan;
  const v = dbxBundle.validated;
  return h("div", {},
    h("p", { class: "muted-text small" }, `${info.name || "This bundle"} · ${info.file}. Validate and plan change nothing in Databricks; deploy creates, updates or deletes the jobs and pipelines the plan lists, and runs none of them.`),
    h("div", { class: "row" },
      btn(dbxBundle.busy === "validate" ? "Validating…" : "Validate", null, () => dbxBundleStep(box, "validate"), "tool outline", { disabled: busy || null }),
      btn(dbxBundle.busy === "plan" ? "Planning…" : "Plan", null, () => dbxBundleStep(box, "plan"), "tool outline", { disabled: busy || null }),
      btn(dbxBundle.busy === "deploy" ? "Deploying…" : `Deploy to ${dbxBundle.target}…`, null, () => dbxBundleDeploy(box),
        p?.ok && p.needs_typed_confirm ? "tool danger-solid" : "tool primary", { disabled: busy || !(p?.ok && p.rows.length) || null })),
    v ? (v.ok ? h("p", { class: "small dbx-valid" }, v.warnings?.length ? `Valid, with ${v.warnings.length} warning${v.warnings.length === 1 ? "" : "s"}:` : "Valid: no problems found.",
      v.warnings?.length ? h("ul", { class: "plain small" }, v.warnings.map((w) => h("li", {}, w))) : null)
      : h("pre", { class: "dbx-error small", role: "alert" }, v.error)) : null,
    p ? dbxBundlePlan(p) : h("p", { class: "muted-text small" }, "Plan to see what a deploy would change. Deploy is on once there is a plan."));
}

function dbxBundlePlan(p) {
  if (!p.ok) return h("pre", { class: "dbx-error small", role: "alert" }, p.error);
  if (!p.rows.length) return h("p", { class: "small" }, `Nothing to change on ${p.target}: it already matches these files.`);
  return h("div", {},
    p.production ? h("p", { class: "callout git-warn small" }, `${p.target} is a production target: deploying asks you to type its name.`) : null,
    p.destructive ? h("p", { class: "callout git-warn small" }, `This plan deletes or rebuilds ${p.destructive} resource${p.destructive === 1 ? "" : "s"}. A rebuilt job or pipeline loses its run history.`) : null,
    h("div", { class: "table-wrap" }, h("table", { class: "list dbx-plan" },
      h("thead", {}, h("tr", {}, ["Change", "Kind", "Name"].map((t) => h("th", {}, t)))),
      h("tbody", {}, p.rows.map((r) => {
        const [label, cls] = DBX_BUNDLE_ACTION[r.action] || [r.action, "neu"];
        return h("tr", {}, h("td", {}, h("span", { class: `pill-s ${cls}` }, label)), h("td", {}, r.kind), h("td", { class: "mono" }, r.name));
      })))));
}

function dbxBundleParams() {
  return { folder: dbxBundle.folder, target: dbxBundle.target, connection: dbxBundle.connection };
}

async function dbxBundleStep(box, step) {
  dbxBundle.busy = step;
  dbxBundleRedraw(box);
  const out = await attempt(() => (step === "plan" ? rpc("databricks_bundle_plan", dbxBundleParams()) : rpc("databricks_bundle_validate", dbxBundleParams())),
    step === "plan" ? "Couldn't plan" : "Couldn't validate");
  dbxBundle.busy = "";
  if (out) dbxBundle[step === "plan" ? "plan" : "validated"] = out;
  dbxBundleRedraw(box);
}

async function dbxBundleDeploy(box) {
  const p = dbxBundle.plan;
  const t = dbxBundle.target;
  const counts = ["create", "update", "delete", "recreate"].map((a) => [a, p.rows.filter((r) => r.action === a).length]).filter(([, n]) => n)
    .map(([a, n]) => `${n} to ${a}`).join(", ");
  const who = dbxBundle.connection.replace(/^cli:/, "");
  const typed = p.needs_typed_confirm ? t : "";
  const ok = p.needs_typed_confirm
    ? await confirmDialog(`Deploy to ${t}?`, `This changes ${counts} in Databricks, as ${who}.${p.destructive ? " Deleted or rebuilt jobs and pipelines lose their run history." : ""}${p.production ? ` ${t} is production.` : ""} No job or pipeline runs.`,
      `Deploy to ${t}`, { danger: true, typeToConfirm: t })
    : await confirmDialog(`Deploy to ${t}?`, `This changes ${counts} in Databricks, as ${who}. No job or pipeline runs.`, "Deploy");
  if (!ok) return;
  dbxBundle.busy = "deploy";
  dbxBundleRedraw(box);
  const out = await attempt(() => rpc("databricks_bundle_deploy", { ...dbxBundleParams(), digest: p.digest, typed }), "Couldn't deploy");
  dbxBundle.busy = "";
  if (out?.ok) {
    toast(`Deployed to ${t}: ${out.changed} change${out.changed === 1 ? "" : "s"}.`);
    dbxBundle.plan = null;
  } else if (out?.replanned) {
    toast(out.error, true);
    dbxBundle.plan = out.replanned;
  } else if (out) {
    toast("The deploy didn't finish: see the plan area.", true);
    dbxBundle.plan = { ...p, ok: false, error: out.error };
  }
  dbxBundleRedraw(box);
}

function dbxBundleRedraw(box) {
  const live = box.isConnected ? box : document.querySelector(".dbx-bundles");
  if (live) dbxBundleLoad(live);
}
