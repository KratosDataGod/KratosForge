// Table detail on the Warehouse page (WH-16): a table's columns with a profile (empty values,
// distinct values, smallest and largest), counted in Fabric by one query that reads, plus a query
// tab that reads the table as it was at a past time and a script that clones it. Nothing here
// changes the warehouse: the clone script opens in a tab, and runs only through Allow changes.

const WH_PROFILE_MAX_COLUMNS = 50;
// Types whose smallest and largest values mean something; the rest show only empty and distinct.
const WH_ORDERED = new Set(["tinyint", "smallint", "int", "bigint", "decimal", "numeric", "float", "real", "money", "smallmoney",
  "date", "time", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "char", "varchar", "nchar", "nvarchar"]);
const WH_NO_DISTINCT = new Set(["varbinary", "binary", "image", "text", "ntext", "sql_variant", "xml"]);

const whBaseType = (label) => String(label || "").toLowerCase().replace(/\(.*$/, "").trim();
const whIsMax = (label) => /\(max\)$/i.test(String(label || ""));

// The profile query for a table: one row, with n (rows) and for each profiled column i
// c{i}_null, c{i}_distinct (or NULL), c{i}_min and c{i}_max (or NULL, as text).
function whProfileSql(o, cols) {
  const parts = ["COUNT_BIG(*) AS n"];
  cols.slice(0, WH_PROFILE_MAX_COLUMNS).forEach((c, i) => {
    const col = whQuote(c.name);
    const base = whBaseType(c.type);
    const max = whIsMax(c.type);
    parts.push(`SUM(CASE WHEN ${col} IS NULL THEN 1 ELSE 0 END) AS c${i}_null`);
    parts.push(WH_NO_DISTINCT.has(base) || max ? `NULL AS c${i}_distinct` : `APPROX_COUNT_DISTINCT(${col}) AS c${i}_distinct`);
    const ordered = WH_ORDERED.has(base) && !max;
    parts.push(ordered ? `CAST(MIN(${col}) AS nvarchar(100)) AS c${i}_min` : `NULL AS c${i}_min`);
    parts.push(ordered ? `CAST(MAX(${col}) AS nvarchar(100)) AS c${i}_max` : `NULL AS c${i}_max`);
  });
  return `SELECT ${parts.join(",\n  ")}\nFROM ${whName(o.schema, o.name)};`;
}

// A past time for Fabric's FOR TIMESTAMP AS OF: the UTC time of a local "YYYY-MM-DDTHH:MM" value,
// as yyyy-MM-ddTHH:mm:ss.fff, or "" when it isn't a time.
function whAsOfUtc(local) {
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString().replace("Z", "");
}

function whTimeTravelSql(o, utc) {
  return `-- ${o.schema}.${o.name} as it was at ${utc} UTC. It reads only.\n` +
    `SELECT TOP (100) *\nFROM ${whName(o.schema, o.name)}\nOPTION (FOR TIMESTAMP AS OF '${utc}');\n`;
}

function whCloneSql(o, name, utc) {
  return `-- A clone of ${o.schema}.${o.name}${utc ? ` as it was at ${utc} UTC` : ""}: a new table that shares its files.\n` +
    "-- Tick Allow changes, then Run: it shows what it changes and asks first.\n" +
    `CREATE TABLE ${whName(o.schema, name)} AS CLONE OF ${whName(o.schema, o.name)}${utc ? ` AT '${utc}'` : ""};\n`;
}

const whCount = (v) => (v === null || v === undefined || v === "" ? "" : Number(v).toLocaleString());

// The Table detail dialog. actions.open(title, text) opens a query tab.
function whTableDetail(ref, o, cols, actions) {
  cols = cols || [];
  const profiled = cols.slice(0, WH_PROFILE_MAX_COLUMNS);
  const summary = h("p", { class: "muted-text" }, `${cols.length} column${cols.length === 1 ? "" : "s"} in ${ref.name}. Profile counts every row in Fabric with one query that reads; it uses capacity.`);
  const rows = h("tbody");
  let profile = null;
  const draw = () => {
    const total = profile ? Number(profile.n) : null;
    rows.replaceChildren(...cols.map((c, i) => {
      const got = profile && i < profiled.length;
      const nulls = got ? Number(profile[`c${i}_null`]) : null;
      const share = got && total ? Math.round((nulls / total) * 1000) / 10 : null;
      const min = got ? profile[`c${i}_min`] : null;
      const max = got ? profile[`c${i}_max`] : null;
      return h("tr", {},
        h("td", {}, c.name),
        h("td", { class: "muted-text" }, `${c.type}${c.nullable === false ? " NOT NULL" : ""}`),
        h("td", { class: `num${share ? " wh-detail-empty" : ""}` }, got ? `${share ?? 0}%` : ""),
        h("td", { class: "num" }, got ? whCount(profile[`c${i}_distinct`]) || "–" : ""),
        h("td", {}, got && (min !== null || max !== null) ? `${min ?? ""} … ${max ?? ""}` : got ? "–" : ""));
    }));
  };
  const profileBtn = btn("Profile", "dataBarVertical", async () => {
    if (!(await warehouseRunOk(ref))) return;
    profileBtn.disabled = true;
    summary.textContent = `Counting ${o.schema}.${o.name} in Fabric...`;
    try {
      const out = await whCatalogQuery(ref, whProfileSql(o, profiled));
      profile = Object.fromEntries(out.columns.map((c, i) => [c.name, out.rows[0]?.[i] ?? null]));
      const left = cols.length - profiled.length;
      summary.textContent = `${whCount(profile.n)} rows, counted in Fabric at ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. ` +
        "Distinct values are Fabric's estimate (APPROX_COUNT_DISTINCT)." + (left > 0 ? ` The last ${left} columns weren't profiled.` : "");
      draw();
    } catch (err) {
      summary.textContent = `Could not profile ${o.schema}.${o.name}: ${String(err).replace(/^Error: /, "")}`;
    }
    profileBtn.disabled = false;
  }, "tool outline small", { title: "One query that reads, in Fabric (uses capacity)" });

  const when = h("input", { type: "datetime-local", "aria-label": "Past time (your time)", step: "1" });
  const asOf = btn("Open as it was", null, () => {
    const utc = whAsOfUtc(when.value);
    if (!utc) { toast("Pick a past time first.", true); return; }
    closeDialog();
    actions.open(`${o.name} (as of)`, whTimeTravelSql(o, utc));
  }, "tool outline small");
  const cloneName = h("input", { "aria-label": "Clone's name", value: `${o.name}_clone`, spellcheck: "false", autocomplete: "off" });
  const cloneWhen = h("input", { type: "datetime-local", "aria-label": "Clone as it was at (your time, optional)", step: "1" });
  const clone = btn("Script the clone", "code", () => {
    const name = cloneName.value.trim();
    if (!name) { toast("Name the new table first.", true); return; }
    const utc = cloneWhen.value ? whAsOfUtc(cloneWhen.value) : "";
    closeDialog();
    actions.open(`${name} (CLONE)`, whCloneSql(o, name, utc));
  }, "tool outline small");

  draw();
  showDialog(`${o.schema}.${o.name}`, h("div", { class: "wh-detail" },
    h("div", { class: "wh-detail-head" }, summary, profileBtn),
    h("div", { class: "wh-detail-cols" }, h("table", { class: "wh-detail-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Column"), h("th", {}, "Type"), h("th", { class: "num" }, "Empty"),
        h("th", { class: "num" }, "Distinct"), h("th", {}, "Smallest … largest"))),
      rows)),
    h("div", { class: "wh-detail-tools" },
      h("section", { "aria-label": "As it was" }, h("b", {}, "See it as it was"),
        h("p", { class: "muted-text small" }, "A query tab that reads the table at a past time, within Fabric's retention. It reads only."),
        h("div", { class: "wh-detail-row" }, when, asOf)),
      h("section", { "aria-label": "Clone" }, h("b", {}, "Clone"),
        h("p", { class: "muted-text small" }, "A new table that shares this one's files, so it takes seconds and no extra storage until either changes. The script opens in a tab; it runs only with Allow changes, after a preview and confirm."),
        h("div", { class: "wh-detail-row" }, cloneName, cloneWhen, clone)))),
  [btn("Close", null, closeDialog, "tool outline")]);
}
