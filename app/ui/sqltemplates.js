// SQL from a template on the Warehouse page (WH-04, with WH-05's fill-in parameters): T-SQL for
// Fabric Warehouse, grouped like Fabric's own "New SQL query from template" plus accelerators.
// A template marks what you fill in the way SSMS does, <name, kind, default>. The kinds say how a
// value is checked and written: sysname (an identifier, written inside [ ]), columns (a comma list
// of identifiers, written [a], [b]), string (inside ' ', with ' doubled), int, datatype, and
// datetime (yyyy-mm-ddThh:mm:ss). Opening a template runs nothing. Every template is checked against
// the Fabric Warehouse rules in app/tests/browser/sql-templates.test.cjs.

const SQL_TEMPLATE_GROUPS = ["Tables", "Load data", "Views and functions", "Stored procedures", "Time travel and clone",
  "Security", "Statistics", "Accelerators"];

const SQL_TEMPLATES = [
  { id: "create-table", group: "Tables", title: "Create a table", description: "A new table with typed columns",
    sql: `CREATE TABLE [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
(
    [<key_column, sysname, Id>] int NOT NULL,
    [Name] varchar(100) NULL,
    [CreatedAt] datetime2(6) NOT NULL
);` },
  { id: "ctas", group: "Tables", title: "Table from a query (CTAS)", description: "CREATE TABLE AS SELECT, from this warehouse or a lakehouse in the workspace",
    sql: `CREATE TABLE [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
AS
SELECT *
FROM [<source_database, sysname, MyLakehouse>].[<source_schema, sysname, dbo>].[<source_table, sysname, MySource>];` },
  { id: "add-column", group: "Tables", title: "Add a column", description: "A new column that allows NULL",
    sql: `ALTER TABLE [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
ADD [<column_name, sysname, NewColumn>] <column_type, datatype, varchar(50)> NULL;` },
  { id: "primary-key", group: "Tables", title: "Primary key (not enforced)", description: "Tells the optimizer a column is unique; Fabric doesn't enforce it",
    sql: `ALTER TABLE [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
ADD CONSTRAINT [<constraint_name, sysname, PK_MyTable>] PRIMARY KEY NONCLUSTERED ([<key_column, sysname, Id>]) NOT ENFORCED;` },
  { id: "copy-into", group: "Load data", title: "Load files with COPY INTO", description: "Parquet or CSV files from Azure storage or OneLake",
    sql: `COPY INTO [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
FROM '<file_url, string, https://myaccount.blob.core.windows.net/mycontainer/folder/*.parquet>'
WITH (FILE_TYPE = 'PARQUET');` },
  { id: "insert-select", group: "Load data", title: "Append from a lakehouse table", description: "INSERT ... SELECT across databases in the workspace",
    sql: `INSERT INTO [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
SELECT *
FROM [<source_database, sysname, MyLakehouse>].[<source_schema, sysname, dbo>].[<source_table, sysname, MySource>];` },
  { id: "truncate-reload", group: "Load data", title: "Truncate and reload", description: "Empty a table, then fill it again from a query",
    sql: `TRUNCATE TABLE [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>];

INSERT INTO [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
SELECT *
FROM [<source_schema, sysname, staging>].[<source_table, sysname, MySource>];` },
  { id: "create-view", group: "Views and functions", title: "Create or alter a view", description: "A view over one table", endpoint: true,
    sql: `CREATE OR ALTER VIEW [<schema_name, sysname, dbo>].[<view_name, sysname, vMyView>]
AS
SELECT [<columns, columns, Id, Name>]
FROM [<source_schema, sysname, dbo>].[<source_table, sysname, MyTable>];` },
  { id: "inline-function", group: "Views and functions", title: "Inline table-valued function", description: "A parameterized view", endpoint: true,
    sql: `CREATE OR ALTER FUNCTION [<schema_name, sysname, dbo>].[<function_name, sysname, fn_MyRows>] (@since date)
RETURNS TABLE
AS
RETURN
    SELECT *
    FROM [<source_schema, sysname, dbo>].[<source_table, sysname, MyTable>]
    WHERE [<date_column, sysname, CreatedAt>] >= @since;` },
  { id: "procedure", group: "Stored procedures", title: "Stored procedure with parameters", description: "A CREATE OR ALTER PROCEDURE skeleton", endpoint: true,
    sql: `CREATE OR ALTER PROCEDURE [<schema_name, sysname, dbo>].[<procedure_name, sysname, usp_MyProcedure>]
    @from date,
    @to date
AS
BEGIN
    SELECT COUNT_BIG(*) AS [Rows]
    FROM [<source_schema, sysname, dbo>].[<source_table, sysname, MyTable>]
    WHERE [<date_column, sysname, CreatedAt>] >= @from AND [<date_column, sysname, CreatedAt>] < @to;
END;` },
  { id: "time-travel", group: "Time travel and clone", title: "Query a table as it was", description: "Time travel to a point in the retention period",
    sql: `SELECT *
FROM [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
OPTION (FOR TIMESTAMP AS OF '<as_of, datetime, 2026-01-01T00:00:00>');` },
  { id: "clone", group: "Time travel and clone", title: "Clone a table", description: "A zero-copy clone, now or as it was at a point in time",
    sql: `CREATE TABLE [<schema_name, sysname, dbo>].[<clone_name, sysname, MyTable_clone>]
AS CLONE OF [<source_schema, sysname, dbo>].[<source_table, sysname, MyTable>];` },
  { id: "grant-schema", group: "Security", title: "Grant read on a schema", description: "SELECT on every table and view in a schema", endpoint: true,
    sql: `GRANT SELECT ON SCHEMA::[<schema_name, sysname, dbo>] TO [<principal, sysname, someone@contoso.com>];` },
  { id: "column-security", group: "Security", title: "Column-level security", description: "SELECT on some columns only", endpoint: true,
    sql: `GRANT SELECT ON [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>] ([<columns, columns, Id, Name>]) TO [<principal, sysname, someone@contoso.com>];` },
  { id: "row-security", group: "Security", title: "Row-level security", description: "A predicate function and a security policy that filters rows by user", endpoint: true,
    sql: `CREATE FUNCTION [<schema_name, sysname, dbo>].[<predicate_name, sysname, fn_RowFilter>] (@owner AS varchar(256))
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN SELECT 1 AS [allowed] WHERE @owner = USER_NAME();
GO

CREATE SECURITY POLICY [<schema_name, sysname, dbo>].[<policy_name, sysname, RowFilter>]
ADD FILTER PREDICATE [<schema_name, sysname, dbo>].[<predicate_name, sysname, fn_RowFilter>]([<owner_column, sysname, Owner>])
ON [<table_schema, sysname, dbo>].[<table_name, sysname, MyTable>]
WITH (STATE = ON);` },
  { id: "masking", group: "Security", title: "Dynamic data masking", description: "Mask a column for people without UNMASK",
    sql: `ALTER TABLE [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>]
ALTER COLUMN [<column_name, sysname, Email>] ADD MASKED WITH (FUNCTION = 'email()');` },
  { id: "statistics", group: "Statistics", title: "Create and update statistics", description: "Single-column statistics for better plans",
    sql: `CREATE STATISTICS [<statistics_name, sysname, stats_MyTable_Id>]
ON [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>] ([<column_name, sysname, Id>])
WITH FULLSCAN;

UPDATE STATISTICS [<schema_name, sysname, dbo>].[<table_name, sysname, MyTable>];` },
  { id: "scd2", group: "Accelerators", title: "SCD type 2 merge procedure", description: "Closes changed rows and inserts new versions, validity in UTC",
    sql: `CREATE OR ALTER PROCEDURE [<schema_name, sysname, dbo>].[<procedure_name, sysname, usp_MergeDimCustomer>]
AS
BEGIN
    DECLARE @now datetime2(6) = SYSUTCDATETIME();

    UPDATE d
    SET d.[ValidTo] = @now, d.[IsCurrent] = 0
    FROM [<schema_name, sysname, dbo>].[<target_table, sysname, DimCustomer>] AS d
    JOIN [<source_schema, sysname, staging>].[<source_table, sysname, Customer>] AS s
        ON s.[<business_key, sysname, CustomerId>] = d.[<business_key, sysname, CustomerId>]
    WHERE d.[IsCurrent] = 1
      AND d.[<tracked_column, sysname, CustomerName>] <> s.[<tracked_column, sysname, CustomerName>];

    INSERT INTO [<schema_name, sysname, dbo>].[<target_table, sysname, DimCustomer>]
        ([<business_key, sysname, CustomerId>], [<tracked_column, sysname, CustomerName>], [ValidFrom], [ValidTo], [IsCurrent])
    SELECT s.[<business_key, sysname, CustomerId>], s.[<tracked_column, sysname, CustomerName>], @now, NULL, 1
    FROM [<source_schema, sysname, staging>].[<source_table, sysname, Customer>] AS s
    WHERE NOT EXISTS (
        SELECT 1
        FROM [<schema_name, sysname, dbo>].[<target_table, sysname, DimCustomer>] AS d
        WHERE d.[<business_key, sysname, CustomerId>] = s.[<business_key, sysname, CustomerId>] AND d.[IsCurrent] = 1);
END;` },
  { id: "watermark", group: "Accelerators", title: "Incremental load with a watermark", description: "Loads rows changed since the last run, then moves the watermark",
    sql: `CREATE OR ALTER PROCEDURE [<schema_name, sysname, dbo>].[<procedure_name, sysname, usp_LoadIncremental>]
AS
BEGIN
    DECLARE @last datetime2(6) = (
        SELECT MAX([LoadedUntil]) FROM [<schema_name, sysname, dbo>].[<watermark_table, sysname, Watermark>]
        WHERE [TableName] = '<table_name, string, FactSales>');
    DECLARE @until datetime2(6) = SYSUTCDATETIME();

    INSERT INTO [<schema_name, sysname, dbo>].[<target_table, sysname, FactSales>]
    SELECT *
    FROM [<source_schema, sysname, staging>].[<source_table, sysname, Sales>]
    WHERE [<modified_column, sysname, ModifiedAt>] > COALESCE(@last, '1900-01-01')
      AND [<modified_column, sysname, ModifiedAt>] <= @until;

    INSERT INTO [<schema_name, sysname, dbo>].[<watermark_table, sysname, Watermark>] ([TableName], [LoadedUntil])
    VALUES ('<table_name, string, FactSales>', @until);
END;` },
  { id: "surrogate-key", group: "Accelerators", title: "Dimension with surrogate keys", description: "New members get the next keys after the highest one in the table",
    sql: `INSERT INTO [<schema_name, sysname, dbo>].[<dimension_table, sysname, DimProduct>] ([<surrogate_key, sysname, ProductKey>], [<business_key, sysname, ProductId>])
SELECT
    COALESCE((SELECT MAX([<surrogate_key, sysname, ProductKey>]) FROM [<schema_name, sysname, dbo>].[<dimension_table, sysname, DimProduct>]), 0)
        + ROW_NUMBER() OVER (ORDER BY s.[<business_key, sysname, ProductId>]),
    s.[<business_key, sysname, ProductId>]
FROM (SELECT DISTINCT [<business_key, sysname, ProductId>] FROM [<source_schema, sysname, staging>].[<source_table, sysname, Product>]) AS s
WHERE NOT EXISTS (
    SELECT 1 FROM [<schema_name, sysname, dbo>].[<dimension_table, sysname, DimProduct>] AS d
    WHERE d.[<business_key, sysname, ProductId>] = s.[<business_key, sysname, ProductId>]);` },
  { id: "quality-checks", group: "Accelerators", title: "Data quality checks", description: "Nulls, duplicates and orphan keys, one result row per check", endpoint: true,
    sql: `SELECT 'Null keys' AS [Check], COUNT_BIG(*) AS [Failures]
FROM [<schema_name, sysname, dbo>].[<table_name, sysname, FactSales>]
WHERE [<key_column, sysname, CustomerKey>] IS NULL
UNION ALL
SELECT 'Duplicate rows', COUNT_BIG(*) - COUNT_BIG(DISTINCT [<unique_column, sysname, SalesKey>])
FROM [<schema_name, sysname, dbo>].[<table_name, sysname, FactSales>]
UNION ALL
SELECT 'Orphan keys', COUNT_BIG(*)
FROM [<schema_name, sysname, dbo>].[<table_name, sysname, FactSales>] AS f
WHERE NOT EXISTS (
    SELECT 1 FROM [<dimension_schema, sysname, dbo>].[<dimension_table, sysname, DimCustomer>] AS d
    WHERE d.[<key_column, sysname, CustomerKey>] = f.[<key_column, sysname, CustomerKey>]);` },
];

// <name, kind, default> placeholders. A '<' or '>' inside one isn't allowed, so a <> b never matches.
const SQL_PARAM_RE = /<([A-Za-z_]\w*)\s*,\s*(\w+)\s*,\s*([^<>\n]*?)\s*>/g;

// The template's parameters in the order they first appear: [{name, kind, value}].
function sqlTemplateParams(sql) {
  const seen = new Map();
  for (const [, name, kind, value] of sql.matchAll(SQL_PARAM_RE)) if (!seen.has(name)) seen.set(name, { name, kind, value });
  return [...seen.values()];
}

// Why a value can't be used for its kind, or "".
function sqlTemplateProblem(kind, value) {
  const v = String(value ?? "").trim();
  if (!v) return "Fill this in.";
  if (kind === "sysname") return /[\]\n]/.test(v) || v.length > 128 ? "Use a name of up to 128 characters without ] or a line break." : "";
  if (kind === "columns") return v.split(",").some((c) => !c.trim() || /[\]\n]/.test(c) || c.trim().length > 128) ? "List names separated by commas, without ]." : "";
  if (kind === "int") return /^-?\d+$/.test(v) ? "" : "Use a whole number.";
  if (kind === "datatype") return /^[A-Za-z0-9_]+(\s*\(\s*(\d+|max)(\s*,\s*\d+)?\s*\))?$/i.test(v) ? "" : "Use a type such as int, varchar(50) or decimal(18,2).";
  if (kind === "datetime") return /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?)?$/.test(v) ? "" : "Use yyyy-mm-dd or yyyy-mm-ddThh:mm:ss.";
  if (kind === "string") return /\n/.test(v) ? "Use one line." : "";
  return "";
}

// The template with each placeholder replaced by its value, written for its kind. A value with a
// problem leaves its placeholder in place, so nothing half-checked reaches the editor.
function sqlTemplateFill(sql, values) {
  return sql.replace(SQL_PARAM_RE, (whole, name, kind, def) => {
    const v = String(values[name] ?? def).trim();
    if (sqlTemplateProblem(kind, v)) return whole;
    if (kind === "columns") return v.split(",").map((c) => c.trim()).join("], [");
    if (kind === "string") return v.replace(/'/g, "''");
    return v;
  });
}

// The gallery. open(title, sql) puts the filled template in a new tab; ref says which warehouse,
// and an endpoint (it only reads) is offered only the templates that make sense there.
function openSqlTemplates(ref, open) {
  const list = SQL_TEMPLATES.filter((t) => ref.kind !== "endpoint" || t.endpoint);
  const sel = { group: list[0].group, id: null, term: "", values: {} };
  const groups = h("nav", { class: "sqlt-groups", "aria-label": "Template groups" });
  const cards = h("div", { class: "sqlt-list" });
  const detail = h("div", { class: "sqlt-detail" });
  const search = h("input", { type: "search", class: "sqlt-search", placeholder: "Search templates", "aria-label": "Search templates",
    oninput: (e) => { sel.term = e.target.value.trim().toLowerCase(); drawCards(); } });
  const shown = () => list.filter((t) => (sel.term ? `${t.title} ${t.description} ${t.group}`.toLowerCase().includes(sel.term) : t.group === sel.group));
  function drawGroups() {
    groups.replaceChildren(...SQL_TEMPLATE_GROUPS.filter((g) => list.some((t) => t.group === g)).map((g) =>
      h("button", { class: `sqlt-group${!sel.term && g === sel.group ? " on" : ""}`, "aria-current": String(!sel.term && g === sel.group),
        onclick: () => { sel.group = g; sel.term = ""; search.value = ""; drawGroups(); drawCards(); } }, g)));
  }
  function drawCards() {
    const ts = shown();
    if (!ts.some((t) => t.id === sel.id)) pick(ts[0]?.id ?? null, false);
    cards.replaceChildren(...(ts.length ? ts.map((t) => h("button", { class: `sqlt-card${t.id === sel.id ? " on" : ""}`, onclick: () => pick(t.id) },
      h("b", {}, t.title), h("span", {}, t.description))) : [h("p", { class: "muted-text" }, "No template matches.")]));
    drawGroups();
  }
  function pick(id, redraw = true) {
    sel.id = id;
    sel.values = {};
    drawDetail();
    if (redraw) drawCards();
  }
  function drawDetail() {
    const t = list.find((x) => x.id === sel.id);
    if (!t) { detail.replaceChildren(); return; }
    const params = sqlTemplateParams(t.sql);
    const script = h("pre", { class: "sqlt-preview", "aria-label": "Script" });
    const problems = () => params.filter((p) => sqlTemplateProblem(p.kind, sel.values[p.name] ?? p.value));
    const openBtn = btn("Open in a new tab", null, () => { closeDialog(); open(t.title, `${sqlTemplateFill(t.sql, sel.values)}\n`); }, "tool primary");
    const refresh = () => {
      script.textContent = sqlTemplateFill(t.sql, sel.values);
      openBtn.disabled = problems().length > 0;
    };
    const fields = params.map((p) => {
      const why = h("span", { class: "error-text sqlt-why", role: "alert" });
      const input = h("input", { value: sel.values[p.name] ?? p.value, "aria-label": p.name.replace(/_/g, " "),
        oninput: (e) => { sel.values[p.name] = e.target.value; why.textContent = sqlTemplateProblem(p.kind, e.target.value); refresh(); } });
      return h("label", { class: "field sqlt-field" }, h("span", {}, p.name.replace(/_/g, " "), h("span", { class: "hint" }, ` ${p.kind}`)), input, why);
    });
    refresh();
    detail.replaceChildren(h("h3", {}, t.title), h("p", { class: "muted-text" }, t.description),
      fields.length ? h("div", { class: "sqlt-fields" }, fields) : null, script,
      h("div", { class: "sqlt-actions" }, h("span", { class: "muted-text" }, "Opening a template runs nothing. Only a query that reads runs on this page; for now, run the rest in Fabric."), h("span", { class: "spacer" }), openBtn));
  }
  drawCards();
  const overlay = showDialog("SQL from a template", h("div", { class: "sqlt" },
    h("div", { class: "sqlt-top" }, h("span", { class: "muted-text" }, `For ${ref.name} · T-SQL for Fabric ${ref.kind === "endpoint" ? "SQL analytics endpoint" : "Warehouse"}`), search),
    h("div", { class: "sqlt-body" }, groups, cards, detail)));
  overlay.querySelector(".dialog").classList.add("wide", "sqlt-dialog");
  search.focus();
}
