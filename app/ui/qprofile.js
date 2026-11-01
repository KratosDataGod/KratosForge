// The notebook Query profile view (Preview): the query behind a cell's spark or %%sql result, run
// again under DuckDB's profiler (spark_local.query_profile, over notebook_frame_query_profile), step
// by step with rows, time and share, the local counterpart of a Spark UI's query details. It runs
// only when asked, since a lakehouse table is read from OneLake again.

const QPROFILE_NOTE = "Runs the query again on this PC to time each step. It only reads, but a lakehouse table is read from OneLake again, which uses capacity.";

function qprofileSeconds(s) {
  return s >= 10 ? `${s.toFixed(1)} s` : s >= 0.01 ? `${+s.toFixed(2)} s` : `${+s.toFixed(4)} s`;
}

// DuckDB's step names in words; one it doesn't know keeps its own name.
const QPROFILE_NAMES = {
  TABLE_SCAN: "Read table", SEQ_SCAN: "Read table", READ_PARQUET: "Read Parquet", READ_CSV_AUTO: "Read CSV", READ_CSV: "Read CSV", ARROW_SCAN: "Read result",
  FILTER: "Filter", PROJECTION: "Select columns", HASH_GROUP_BY: "Group by", PERFECT_HASH_GROUP_BY: "Group by",
  UNGROUPED_AGGREGATE: "Aggregate", ORDER_BY: "Sort", TOP_N: "Top rows", LIMIT: "Limit", STREAMING_LIMIT: "Limit",
  HASH_JOIN: "Join", NESTED_LOOP_JOIN: "Join", PIECEWISE_MERGE_JOIN: "Join", CROSS_PRODUCT: "Cross join",
  UNION: "Union", WINDOW: "Window", STREAMING_WINDOW: "Window", DISTINCT: "Distinct", UNNEST: "Unnest",
};
const qprofileName = (n) => QPROFILE_NAMES[String(n).trim().toUpperCase().replace(/\s+/g, "_")] || String(n).trim();

// What a step worked on, in a few words: its table, filters or keys.
function qprofileDetail(step) {
  const d = step.details || {};
  const pick = d.Table || d.Function || d.Filters || d.Expression || d.Groups || d["Order By"] || d.Conditions || d.Projections || "";
  const text = String(pick).replace(/\s+/g, " ").trim();
  return text.length > 60 ? `${text.slice(0, 59)}…` : text;
}

// The profile as plain text for a ticket or a chat: one indented line per step.
function qprofileText(p) {
  return [`Query profile: ${qprofileSeconds(p.seconds)}, ${p.rows_scanned.toLocaleString()} rows read, ${p.rows.toLocaleString()} rows returned, peak memory ${sizeText(p.peak_memory)}`,
    ...p.steps.map((s) => `${"  ".repeat(s.depth)}${qprofileName(s.name)}${qprofileDetail(s) ? ` (${qprofileDetail(s)})` : ""}: ${s.rows.toLocaleString()} rows, ${qprofileSeconds(s.seconds)}, ${Math.round(s.share * 100)}%`),
    ...(p.more_steps ? [`${p.more_steps} more steps not listed`] : [])].join("\n");
}

// Learn from execution (GD-04): what this profile measured that a best-practice rule explains, from
// the engine (learn/evidence.py). A note appears only when the numbers show it; most profiles have none.
// Professional and Guided add the rule's principle, Guided its lesson; Expert keeps the measured line.
function qprofileNotes(p) {
  if (!p.notes?.length || !previewShown()) return null;
  const mode = learnMode("notebooks");
  return h("div", { class: "qprofile-notes" },
    h("b", {}, "What this profile shows"),
    ...p.notes.map((n) => h("div", { class: "qprofile-note", "data-rule": n.rule_id },
      h("p", {}, h("span", { class: "badge" }, "Measured"), " ", n.text),
      mode !== "expert" && n.principle ? h("p", { class: "muted-text small" }, `The principle: ${n.principle}`) : null,
      h("div", { class: "row" }, docLink(n.url, `About ${n.rule_id}`), mode === "guided" ? learnFindingLink(n.rule_id, "notebooks") : null))),
    h("span", { class: "muted-text small" }, "Measured in this run on this PC. Fabric's Spark may plan the query differently."));
}

function renderQueryProfile(o) {
  const q = o.qprofile ||= { data: null, busy: false, error: null, gone: false };
  const box = h("div", { class: "qprofile" });
  const redraw = () => box.replaceChildren(...draw().filter(Boolean));  // replaceChildren would write a null as "null"
  const run = () => {
    q.busy = true; q.error = null; redraw();
    rpc("notebook_frame_query_profile", { frame: o.frame }).then((p) => {
      q.busy = false;
      if (p.gone) q.gone = true; else q.data = p;
      redraw();
    }, (err) => { q.busy = false; q.error = friendlyError(err).text; redraw(); });
  };
  const draw = () => {
    if (q.gone) return [h("div", { class: "more" }, "The kernel restarted since this result. Run the cell again to profile its query.")];
    const head = h("div", { class: "qprofile-head" }, h("b", {}, "Query profile"), h("span", { class: "muted-text grow" }, QPROFILE_NOTE));
    if (!q.data) {
      head.append(btn(q.busy ? "Profiling..." : "Profile the query", "play", run, "tool primary small", { disabled: q.busy }));
      return [head, q.error ? h("div", { class: "more", role: "alert" }, q.error) : null];
    }
    const p = q.data;
    head.append(btn(q.busy ? "Profiling..." : "Run again", null, run, "tool outline small", { disabled: q.busy }),
      btn("Copy as text", "document", () => attempt(() => navigator.clipboard.writeText(qprofileText(p)).then(() => toast("Copied the query profile.")), "Could not copy"), "tool outline small"));
    const stat = (label, value) => h("div", { class: "qprofile-stat" }, h("span", {}, label), h("b", {}, value));
    const slowest = p.steps.reduce((m, s, i) => (s.seconds > (p.steps[m]?.seconds ?? -1) ? i : m), -1);
    const rows = p.steps.map((s, i) => {
      const detail = qprofileDetail(s);
      const full = Object.entries(s.details || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
      return h("tr", { class: i === slowest && p.steps.length > 1 ? "slowest" : null },
        h("td", { style: `padding-left:${8 + s.depth * 16}px`, title: full || null }, qprofileName(s.name),
          detail ? h("span", { class: "muted-text" }, ` · ${detail}`) : null,
          i === slowest && p.steps.length > 1 ? h("span", { class: "qprofile-flag" }, "Slowest step") : null),
        h("td", { class: "n" }, s.rows.toLocaleString()),
        h("td", { class: "n" }, qprofileSeconds(s.seconds)),
        h("td", {}, h("span", { class: "qprofile-bar", style: `width:${Math.max(2, Math.round(s.share * 140))}px` }),
          h("span", { class: "muted-text" }, ` ${Math.round(s.share * 100)}%`)));
    });
    return [head,
      q.error ? h("div", { class: "more", role: "alert" }, q.error) : null,
      h("div", { class: "qprofile-stats" }, stat("Time", qprofileSeconds(p.seconds)), stat("Rows read", p.rows_scanned.toLocaleString()),
        stat("Rows returned", p.rows.toLocaleString()), stat("Peak memory", sizeText(p.peak_memory))),
      h("div", { class: "out-table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Step"), h("th", { class: "n" }, "Rows"), h("th", { class: "n" }, "Time"), h("th", {}, "Share of time"))),
        h("tbody", {}, rows))),
      qprofileNotes(p),
      h("div", { class: "more" }, (p.more_steps ? `${p.more_steps} more steps aren't listed. ` : "") +
        "Steps are DuckDB's, the engine that runs spark on this PC, so they won't match Fabric's Spark UI one for one. Hover a step to see its details.")];
  };
  redraw();
  return box;
}
