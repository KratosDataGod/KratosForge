// Notebook environments (environments.py): packages a notebook adds on top of the engine's, like
// a Fabric Environment item or Databricks notebook-scoped libraries. "Environment" in the notebook
// status bar picks one, makes one, and installs or removes its packages with pip. Needs notebooks
// in their own process (Settings).

function environmentButton(nb) {
  return preview("environments", h("button", { class: "status-link", title: "Extra Python packages for this notebook",
    onclick: () => environmentDialog(nb) }, nb.environment ? `Environment: ${nb.environment}` : "Environment"));
}

async function environmentDialog(nb, job = null) {
  const data = await attempt(() => rpc("notebook_environments", { notebook: nb.name }), "Could not list environments");
  if (!data) return;
  nb.environment = data.current;
  const env = data.environments.find((e) => e.name === data.current);
  const reopen = (j = null) => environmentDialog(nb, j);
  if (!data.process) {
    showDialog("Notebook environment", h("div", { class: "scroll env-body" },
      h("p", { class: "dialog-text" }, "Environments add Python packages to a notebook. They need Run notebooks in their own process, so each notebook's packages stay in its own process."),
      h("p", { class: "dialog-text" }, "Until then, notebooks use the packages the app ships: Polars, DuckDB, pandas, pyarrow, deltalake, matplotlib and more.")),
    [btn("Settings", "settings", () => { closeDialog(); go("settings"); }, "tool outline"), btn("Close", null, closeDialog, "tool primary")]);
    return;
  }
  const pick = h("select", { "aria-label": "Environment", onchange: async (e) => {
    const out = await attempt(() => rpc("environment_attach", { notebook: nb.name, name: e.target.value || null }), "Could not change the environment");
    if (out?.restart_needed) toast("Restart the kernel to use it. Variables are cleared.");
    reopen();
  } }, h("option", { value: "" }, "None: the app's packages only"),
  data.environments.map((e) => h("option", { value: e.name, selected: e.name === data.current ? true : null }, e.name)));
  const specs = h("input", { placeholder: "scikit-learn==1.5.2 great_expectations", "aria-label": "Packages to install",
    disabled: data.allowed ? null : true, onkeydown: (e) => { if (e.key === "Enter") install(); } });
  async function install() {
    const packages = specs.value.split(/[\s,]+/).filter(Boolean);
    if (!packages.length) return;
    closeDialog();
    const ok = await confirmDialog(`Install into ${env.name}?`,
      `pip downloads ${packages.join(", ")} from PyPI, or from the package index this PC's pip is set up for, and installs them on this PC for the notebooks using ${env.name}. Packages run with your permissions, so install only ones you trust. The app's own packages stay as they are.`, "Install");
    if (!ok) return reopen();
    const started = await attempt(() => rpc("environment_install", { name: env.name, packages }), "Could not install");
    reopen(started);
  }
  async function remove(name) {
    closeDialog();
    const ok = await confirmDialog(`Remove ${name} from ${env.name}?`,
      `Its files are deleted from this PC, so ${env.notebooks.length ? env.notebooks.join(", ") : "notebooks using it"} can't import it any more. Install it again to bring it back.`,
      "Remove", { danger: true });
    if (!ok) return reopen();
    const j = await attempt(() => rpc("environment_uninstall", { name: env.name, packages: [name] }), "Could not remove");
    reopen(j);
  }
  async function del() {
    closeDialog();
    const ok = await confirmDialog(`Delete ${env.name}?`, `Its packages are removed from this PC${env.notebooks.length ? `, and ${env.notebooks.join(", ")} go back to the app's packages` : ""}.`, "Delete", { danger: true });
    if (ok) await attempt(() => rpc("environment_delete", { name: env.name }), "Could not delete");
    reopen();
  }
  async function create() {
    closeDialog();
    const name = await askText("New environment", "Name", "", "Create");
    if (name && await attempt(() => rpc("environment_create", { name }), "Could not create the environment")) {
      await attempt(() => rpc("environment_attach", { notebook: nb.name, name }), "Could not use the environment");
    }
    reopen();
  }
  const log = job ? h("div", { class: "env-job" }, h("p", {}, job.state === "running" ? (job.remove ? "Removing..." : "Installing...") : job.state === "done" ? "Done." : "pip failed:"),
    job.log ? h("pre", { class: "env-log" }, job.log) : null) : null;
  showDialog("Notebook environment", h("div", { class: "scroll env-body" },
    h("p", { class: "dialog-text" }, "Packages this notebook adds to the app's own. They come after the app's packages, so they can't replace Polars, DuckDB or the others it ships."),
    h("label", { class: "field" }, "Environment", h("div", { class: "env-row" }, pick, btn("New", "add", create, "tool outline small"))),
    env ? [
      env.installed.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Package", "Version", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, env.installed.map((p) => h("tr", {}, h("td", {}, p.name), h("td", {}, p.version),
          h("td", {}, btn("Remove", "delete", () => remove(p.name), "tool small")))))) : h("p", { class: "muted" }, "No packages yet."),
      env.notebooks.length > 1 ? h("p", { class: "muted" }, `Shared with ${env.notebooks.filter((n) => n !== nb.name).join(", ")}.`) : null,
      data.allowed ? h("label", { class: "field" }, "Add packages", h("div", { class: "env-row" }, specs,
        btn("Install", "add", install, "tool primary small", { disabled: job?.state === "running" ? true : null })))
        : h("p", { class: "muted" }, "Your organization doesn't allow installing packages on this PC."),
      log,
    ] : null),
  [env ? btn("Delete environment", "delete", del, "tool outline") : null, btn("Close", null, closeDialog, "tool primary")].filter(Boolean));
  if (job?.state === "running") {
    setTimeout(async () => {
      if (!document.querySelector(".env-body")) return;  // closed meanwhile
      const next = await attempt(() => rpc("environment_job", { job_id: job.id }), "Could not check the install");
      if (next && document.querySelector(".env-body")) { closeDialog(); reopen(next); }
    }, 1000);
  }
}
