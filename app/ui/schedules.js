// Schedules: on this PC (Windows Task Scheduler starts them, even with the app closed) or in
// Fabric (its job scheduler, on capacity). One dialog adds them for a notebook or an environment;
// Monitor > KratosForge > Schedules and Monitor > Fabric > Schedules list every one.

// What a run of a schedule on this PC did, as a pill.
function scheduleRunPill(last) {
  if (!last) return h("span", { class: "pill-s neu" }, "Not run yet");
  return statusPill(last.status === "ok" ? "ok" : last.status === "skipped" ? "skipped" : "failed");
}

function fabricRunPill(last) {
  if (!last) return h("span", { class: "pill-s neu" }, "Not run yet");
  const s = fabricStatus(last.status);
  return s === "running" ? h("span", { class: "pill-s info" }, last.status === "NotStarted" ? "Queued" : "Running") : statusPill(s);
}

function onOffButton(enabled, toggle) {
  return h("button", { class: `pill-s ${enabled ? "pass" : "neu"} pill-btn`, title: enabled ? "On. Click to pause." : "Paused. Click to turn on.",
    onclick: (e) => { e.stopPropagation(); toggle(!enabled); } }, enabled ? "On" : "Paused");
}

function nextRunText(iso) {
  const t = readDate(iso);
  if (!t) return "";
  const day = t.toDateString() === new Date().toDateString() ? "Today"
    : t.toDateString() === new Date(Date.now() + 86400000).toDateString() ? "Tomorrow"
    : dateText(t, { weekday: true });  // weekly schedules read by their day
  return `${day} ${timeText(t)}`;
}

// UX-018: removing a schedule can't be undone, so say what stops and point at Off.
// offHere: false where the list has no Off control (the Fabric tab of the schedule dialog).
// Asked from the Schedule dialog, the confirm takes its place; that dialog comes back afterwards so the
// list it redraws is seen. It has no onClose, so putting it back starts nothing again.
async function confirmRemoveSchedule(what, text, fabric, offHere = true) {
  const how = fabric ? "Fabric deletes the schedule and stops starting runs from it."
    : "Its Windows task is deleted and it stops running on this PC.";
  const root = document.getElementById("dialog-root");
  const under = root.firstElementChild;
  const ok = await confirmDialog(`Remove the schedule for ${what}?`,
    `${text}. ${how} Past runs stay where they are. To stop it for a while instead, turn it Off${offHere ? "" : " in Monitor > Fabric > Schedules"}.`, "Remove", { danger: true });
  if (under && !under.isConnected && !root.firstElementChild) root.replaceChildren(under);
  return ok;
}

// The schedule form's values for a stored schedule: Fabric's configuration, which this PC's schedules keep too.
function scheduleValues(config) {
  const c = config || {};
  return { kind: c.type === "Cron" ? "cron" : c.type === "Weekly" ? "weekly" : "daily", times: c.times || [],
    every_minutes: c.interval || 60, weekdays: c.weekdays || [], time_zone: c.localTimeZoneId === "Local" ? "" : c.localTimeZoneId || "" };
}

// What the form says, in words, before it is saved.
function scheduleSummary(v, fabric) {
  const times = v.times.map((t) => t.trim()).filter(Boolean).join(" and ");
  const when = v.kind === "cron" ? `every ${v.every_minutes} minutes`
    : v.kind === "weekly" ? `${v.weekdays.map((d) => d.slice(0, 3)).join(", ") || "no days"} at ${times || "no time"}`
    : `every day at ${times || "no time"}`;
  return fabric && v.kind !== "cron" && v.time_zone ? `${when}, ${v.time_zone}` : when;
}

// PB-048: change when a schedule runs, in place. o = { title, text, fabric, where, config, save(values), done() }
function editScheduleDialog(o) {
  const form = scheduleForm();
  form.set(scheduleValues(o.config));
  const zoneField = form.fields[4];
  const hideZone = () => { if (!o.fabric) zoneField.hidden = true; };  // this PC runs in its own time zone
  hideZone();
  const after = h("b");
  const show = () => { after.textContent = scheduleSummary(form.value(), o.fabric); };
  const box = h("div", { class: "scroll", oninput: show, onchange: () => { hideZone(); show(); } },
    h("p", { class: "text" }, `Now: ${o.text}, ${o.fabric ? `in Fabric${o.where ? ` (${o.where})` : ""}. Fabric changes the schedule in place; runs already started aren't changed.`
      : "on this PC. The change applies from the next run; a run going on now finishes as it is."}`),
    form.fields, h("div", { class: "pass-box small" }, "After saving: ", after));
  show();
  showDialog(o.title, box, [btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save changes", "save", async () => {
      if (await attempt(() => o.save(form.value()), "Not saved")) { closeDialog(); toast("Schedule changed."); o.done?.(); }
    }, "tool primary")]);
}

// Rows for this PC's schedules; actions redraw through `changed`. onEdit: the Schedule dialog edits in its own form.
function pcScheduleRows(rows, changed, { showWhat = false, onEdit = null } = {}) {
  return rows.map((s) => h("tr", {},
    h("td", {}, onOffButton(s.enabled, async (on) => {
      if (await attempt(() => rpc("set_pc_schedule_enabled", { schedule_id: s.id, enabled: on }), on ? "Not turned on" : "Not paused")) changed();
    })),
    showWhat ? h("td", {}, h("b", {}, s.label), h("div", { class: "muted-text small" }, ({ notebook: "Notebook", pipeline: "Pipeline" })[s.target.kind] || "Metadata run")) : null,
    h("td", {}, s.text),
    h("td", {}, s.enabled ? nextRunText(s.next_run) : h("span", { class: "muted-text" }, "Paused")),
    h("td", {}, scheduleRunPill(s.last), s.last ? h("span", { class: "muted-text small" }, " ", when(s.last.started)) : null,
      s.last && s.last.status !== "ok" && s.last.message ? h("div", { class: "reason" }, s.last.message) : null,
      s.last?.alert_error ? h("div", { class: "reason" }, s.last.alert_error) : null),
    h("td", { class: "n nowrap" },
      btn("Run now", "play", async () => {
        if (await attempt(() => rpc("run_pc_schedule_now", { schedule_id: s.id }), "Not started")) {
          toast(`${s.label} started on this PC. Its result shows here when it finishes.`);
          setTimeout(changed, 4000);
        }
      }, "tool link small"),
      btn("Edit", "create", () => (onEdit ? onEdit(s) : editScheduleDialog({ title: `Edit the schedule for ${s.label}`, text: s.text,
        config: s.config, save: (schedule) => rpc("update_pc_schedule", { schedule_id: s.id, schedule }), done: changed })), "tool link small"),
      btn("Remove", "delete", async () => {
        if (!(await confirmRemoveSchedule(s.label, s.text, false))) return;
        if (await attempt(() => rpc("delete_pc_schedule", { schedule_id: s.id }), "Not removed")) changed();
      }, "tool link small"))));
}

const PC_SCHEDULE_NOTE = "Windows starts these runs on this PC, even with KratosForge closed, while you are signed in to Windows. A run missed while the PC was off starts when it is back.";

// One dialog for a notebook's or an environment's schedules, on this PC or in Fabric.
//   o.what names it in confirms.   o.pc = { target, query, intro }   o.fabric = { intro, list(), add(schedule), remove(id), unavailable }
async function openScheduleDialog(o) {
  let mode = o.mode === "fabric" && !o.fabric?.unavailable ? "fabric" : "pc";
  const list = h("div", { class: "scroll schedule-list" });
  const intro = h("p", { class: "text" });
  const form = scheduleForm();
  const kind = form.fields[0].querySelector("select");
  const zoneField = form.fields[4];
  const hideZone = () => { if (mode === "pc") zoneField.hidden = true; };
  kind.addEventListener("change", hideZone);
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Runs on" });
  // Editing one of the listed schedules (PB-048): the form below holds it and Add becomes Save changes.
  let editing = null;
  const heading = h("b", {}, "Add a schedule");
  const cancelEdit = btn("Cancel edit", null, () => stopEditing(), "tool link small", { hidden: true });
  const addBtn = btn("Add schedule", "add", () => add(), "tool primary");
  const startEditing = (row, config, text) => {
    editing = { ...row, mode };
    form.set(scheduleValues(config));
    hideZone();
    heading.textContent = `Edit the schedule: ${text}`;
    addBtn.lastChild.textContent = "Save changes";
    cancelEdit.hidden = false;
    form.fields[0].querySelector("select").focus();
  };
  function stopEditing() {
    editing = null;
    heading.textContent = "Add a schedule";
    addBtn.lastChild.textContent = "Add schedule";
    cancelEdit.hidden = true;
  }

  const showPc = (rows) => {
    list.replaceChildren(rows?.length ? h("table", { class: "mini" }, h("tbody", {}, pcScheduleRows(rows, draw,
      { onEdit: (s) => startEditing({ id: s.id }, s.config, s.text) })))
      : h("p", { class: "muted-text" }, "No schedules on this PC yet."));
  };
  const showFabric = (res) => {
    if (!res) { list.replaceChildren(); return; }
    list.replaceChildren(res.schedules.length ? h("table", { class: "mini" }, h("tbody", {}, res.schedules.map((s) => h("tr", {},
      h("td", {}, s.enabled ? h("span", { class: "pill-s pass" }, "On") : h("span", { class: "pill-s neu" }, "Paused")),
      h("td", {}, s.text),
      h("td", { class: "n nowrap" }, res.workspace_id && res.item_id ? btn("Edit", "create", () => startEditing({ id: s.id, enabled: s.enabled,
        workspace_id: res.workspace_id, item_id: res.item_id }, s.configuration, s.text), "tool link small") : null,
      btn("Remove", "delete", async () => {
        if (!(await confirmRemoveSchedule(o.what, s.text, true, false))) return;
        const next = await attempt(() => o.fabric.remove(s.id), "Not removed");
        if (next) showFabric(next);
      }, "tool link small"))))))
      : h("p", { class: "muted-text" }, res.published === false ? "Not in Fabric yet. Adding a schedule publishes it." : "No schedules in Fabric yet."));
  };
  async function draw() {
    if (editing && editing.mode !== mode) stopEditing();
    seg.replaceChildren(...[["pc", "This PC"], ["fabric", "Fabric"]].map(([v, label]) => h("button", {
      class: mode === v ? "on" : null, disabled: v === "fabric" && o.fabric?.unavailable ? true : null,
      title: v === "fabric" ? o.fabric?.unavailable || "Fabric runs it on capacity, even when this PC is off" : "Runs on this PC, with its data and tools",
      onclick: () => { if (mode !== v) { mode = v; draw(); } } }, label)));
    intro.textContent = mode === "pc" ? `${o.pc.intro} ${PC_SCHEDULE_NOTE}` : o.fabric.intro;
    form.sync();
    hideZone();
    list.replaceChildren(loading("Loading schedules..."));
    if (mode === "pc") {
      showPc(await attempt(() => rpc("pc_schedules_for", o.pc.query), "Could not read schedules"));
    } else {
      if (!(await ensureSignedIn())) return;
      showFabric(await attempt(() => o.fabric.list(), "Could not read schedules"));
    }
  }
  const add = async () => {
    const schedule = form.value();
    if (editing) {
      const e = editing;
      const ok = await attempt(() => (e.mode === "pc" ? rpc("update_pc_schedule", { schedule_id: e.id, schedule })
        : rpc("update_fabric_schedule", { workspace_id: e.workspace_id, item_id: e.item_id, job_type: o.fabric.jobType || "RunNotebook",
          schedule_id: e.id, schedule, enabled: e.enabled })), "Not saved");
      if (!ok) return;
      toast("Schedule changed.");
      stopEditing();
      draw();
      return;
    }
    if (mode === "pc") {
      const res = await attempt(() => rpc("add_pc_schedule", { target: o.pc.target, schedule }), "Schedule not added");
      if (!res) return;
      toast(res.windows ? "Scheduled on this PC. Windows starts it at those times, even with the app closed."
        : "Saved, but only Windows can start schedules on this PC.", !res.windows);
    } else {
      toast(o.fabric.adding || "Adding the schedule in Fabric...");
      const res = await attempt(() => o.fabric.add(schedule), "Schedule not added");
      if (!res) return;
      toast(o.fabric.added || "Scheduled in Fabric.");
    }
    draw();
  };
  showDialog(o.title, h("div", { class: "scroll" },
    h("div", { class: "compute-row" }, h("span", { class: "muted-text small" }, "Runs on"), seg),
    intro, list, h("div", { class: "row wrap" }, heading, cancelEdit), form.fields,
    h("p", { class: "muted-text small" }, "Every schedule, on this PC and in Fabric, is listed in Monitor > KratosForge > Schedules and Monitor > Fabric > Schedules.")),
  [btn("Close", null, closeDialog, "tool outline"), addBtn]);
  draw();
}

// ---------- Monitor > KratosForge > Schedules and Monitor > Fabric > Schedules ----------

// Monitor has two schedule pages: KratosForge > Schedules (panel "schedules", those Windows starts on this
// PC) and Fabric > Schedules (panel "fabric_schedules", Fabric's job scheduler). Both read all_schedules.
// refresh: Monitor's Refresh asks Fabric again rather than using the last read.
const schedulesPage = { allWorkspaces: false, data: null, timer: null, refresh: false };
const onSchedules = () => state.view === "monitor" && ["schedules", "fabric_schedules"].includes(monitorPage.panel);

function schedulesPanel() {
  const box = h("div", { class: "monitor-scroll schedules-panel" }, loading("Loading schedules...", "monitor-empty"));
  const refresh = schedulesPage.refresh;
  schedulesPage.refresh = false;
  loadSchedules(box, refresh);
  return box;
}

async function loadSchedules(box, refresh) {
  clearTimeout(schedulesPage.timer);
  const res = await attempt(() => rpc("all_schedules", { refresh, all_workspaces: schedulesPage.allWorkspaces }), "Could not read schedules");
  if (!res || !onSchedules() || !box.isConnected) return;
  schedulesPage.data = res;
  drawSchedules(box);
  if (res.fabric.pending) schedulesPage.timer = setTimeout(() => loadSchedules(box, false), 1500);
}

// This PC's schedules file couldn't be read and was moved aside (QA-011): its schedules are no longer
// listed, but Task Scheduler may still start tasks made for them.
function setAsideNotice(files) {
  if (!files?.length) return null;
  const [latest] = files;
  return h("div", { class: "card pad warn-box set-aside" },
    h("div", {}, h("b", {}, "Some schedules on this PC may be missing. "),
      "The schedules file couldn't be read ", when(latest.at), " and was moved aside to ",
      h("span", { class: "mono" }, latest.file), ". Schedules saved before then aren't listed here."),
    h("div", { class: "small" }, "Windows may still run tasks made for them: look in Task Scheduler under ",
      h("span", { class: "mono" }, "FabricDesktop"), ". Their runs fail and don't show here. The file has the old schedules if you need to add them again. This warning goes away once that file is moved out of the folder or deleted."),
    h("div", { class: "muted-text small mono" }, latest.path),
    files.length > 1 ? h("div", { class: "muted-text small" }, `${files.length - 1} older ${files.length === 2 ? "copy is" : "copies are"} kept next to it.`) : null);
}

function drawSchedules(box) {
  const res = schedulesPage.data;
  const redraw = () => loadSchedules(box, false);
  const fabric = res.fabric || {};
  const pc = res.pc.filter((s) => matches(`${s.label} ${s.text}`));
  const fab = (fabric.items || []).filter((s) => matches(`${s.name} ${s.workspace_name} ${s.type} ${s.text}`));
  const scope = (all, label) => h("button", { class: schedulesPage.allWorkspaces === all ? "on" : null, "aria-pressed": String(schedulesPage.allWorkspaces === all),
    onclick: () => { schedulesPage.allWorkspaces = all; loadSchedules(box, false); } }, label);
  const failed = (n) => (n ? h("span", { class: "bad" }, h("b", {}, n), " failed last time") : null);

  if (monitorPage.panel === "schedules") {
    fill(box,
      h("div", { class: "schedule-summary" },
        h("span", {}, h("b", {}, res.pc.filter((s) => s.enabled).length), ` of ${res.pc.length} on`),
        failed(pc.filter((s) => s.last && s.last.status === "failed").length)),
      setAsideNotice(res.pc_set_aside),
      res.windows ? null : h("p", { class: "muted-text small" }, "Only Windows starts these. On this system they are saved but nothing runs them."),
      pc.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["", "What", "When", "Next run", "Last run", ""].map((c) => h("th", {}, c)))),
        h("tbody", {}, pcScheduleRows(pc, redraw, { showWhat: true })))
        : h("p", { class: "muted-text" }, res.pc.length ? "No schedule on this PC matches the search."
          : "None yet. Use Schedule on a notebook, or on Data mesh, and pick This PC."));
    return;
  }
  // Which workspaces are read is this tab's view; how far the read got sits by Refresh.
  monitorSlots.views?.replaceChildren(h("div", { class: "seg", role: "group", "aria-label": "Workspaces" },
    scope(false, "Workspaces I work in"), scope(true, "All workspaces")));
  monitorSlots.tools?.replaceChildren(fabric.pending ? h("span", { class: "muted-text small" }, "Reading schedules from Fabric...")
    : fabric.scanned ? h("span", { class: "muted-text small" }, `${fabric.workspaces} workspace${fabric.workspaces === 1 ? "" : "s"} read `, when(fabric.scanned)) : "");
  fill(box,
    h("div", { class: "schedule-summary" },
      h("span", {}, h("b", {}, (fabric.items || []).filter((s) => s.enabled).length), ` of ${(fabric.items || []).length} on`),
      failed(fab.filter((s) => s.last && fabricStatus(s.last.status) === "failed").length)),
    fabric.error ? h("p", { class: "muted-text" }, `Couldn't read Fabric's schedules: ${friendlyError(fabric.error).text}`)
      : !fabric.pending && !schedulesPage.allWorkspaces ? h("p", { class: "muted-text small" },
        "Pinned workspaces, your environments' workspaces, and the ones your notebooks publish to.") : null,
    fab.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["", "What", "Workspace", "When", "Last run", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, fab.map((s) => h("tr", {},
        h("td", {}, onOffButton(s.enabled, async (on) => {
          if (await attempt(() => rpc("set_fabric_schedule_enabled", { workspace_id: s.workspace_id, item_id: s.item_id,
            job_type: s.job_type, schedule_id: s.id, enabled: on, configuration: s.configuration }), on ? "Not turned on" : "Not paused")) {
            s.enabled = on; drawSchedules(box);
          }
        })),
        h("td", {}, h("div", { class: "row tight" }, itemIcon(s.item_type, 16), h("b", {}, s.name)), h("div", { class: "muted-text small" }, s.type)),
        h("td", {}, s.workspace_name),
        h("td", {}, s.text),
        h("td", {}, fabricRunPill(s.last), s.last ? h("span", { class: "muted-text small" }, " ", when(s.last.started)) : null,
          s.last?.message ? h("div", { class: "reason" }, s.last.message) : null),
        h("td", { class: "n nowrap" },
          btn("Run now", "play", async () => {
            // It starts a job on the workspace's capacity: the risk check, then a confirm, as Run in Fabric does.
            // The risk check knows only the workloads in the workspaces this app works in; for any other
            // item it would fail with a reason that reads as if the item were gone, so it's skipped and the
            // confirm says so (as dfRunInFabric reads the same code). A failed lookup still runs the check.
            const id = `fabric:${s.workspace_id}:${s.item_id}`;
            const pre = await rpc("workload_preflight", { id, action: "run_item", env: null, assess: false }).catch(() => null);
            const listed = pre?.code !== "not_in_inventory";
            if (listed && !(await riskGate({ id, action: "run_item", title: `Run ${s.name} now?`, runLabel: "Run now" }))) return;
            if (!(await confirmDialog(`Run ${s.name} now?`, `Starts one run of it in Fabric, in ${s.workspace_name}, which uses that workspace's capacity. `
              + "Its schedule isn't changed." + (listed ? "" : " The risk check wasn't run: it covers only workloads in the workspaces this app works in."),
              "Run now"))) return;
            const r = await attempt(() => rpc("run_fabric_item_now", { workspace_id: s.workspace_id, item_id: s.item_id,
              job_type: s.job_type, name: s.name, workspace_name: s.workspace_name }), "Not started");
            if (r) toast(`${s.name} started in Fabric. Follow it under Runs.`);
          }, "tool link small"),
          btn("Open", "publish", () => openInFabric(fabricItemLink({ type: s.item_type, item_id: s.item_id, workspace_id: s.workspace_id })), "tool link small"),
          btn("Edit", "create", () => editScheduleDialog({ title: `Edit the schedule for ${s.name}`, text: s.text, fabric: true,
            where: s.workspace_name, config: s.configuration, done: redraw,
            save: (schedule) => rpc("update_fabric_schedule", { workspace_id: s.workspace_id, item_id: s.item_id, job_type: s.job_type,
              schedule_id: s.id, schedule, enabled: s.enabled }) }), "tool link small"),
          btn("Remove", "delete", async () => {
            if (!(await confirmRemoveSchedule(s.name, s.text, true))) return;
            if (await attempt(() => rpc("delete_fabric_schedule", { workspace_id: s.workspace_id, item_id: s.item_id,
              job_type: s.job_type, schedule_id: s.id }), "Not removed")) redraw();
          }, "tool link small"))))))
      : fabric.pending ? null : h("p", { class: "muted-text" }, (fabric.items || []).length ? "No Fabric schedule matches the search."
        : "No schedules in these workspaces."));
}
