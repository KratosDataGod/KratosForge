// The SQL editor on the SQL query page: CodeMirror 6 (vendor/codemirror, window.CM) with a dialect
// per query tab, completion from a metadata provider, and the page's keys (Ctrl+Enter, Ctrl+S).
//
// A completion provider says which names exist. The SQL page passes one built from the lakehouse
// (lakehouseSqlProvider in app.js); a Warehouse or SQL database provider can be added later with
// the same shape, without touching the editor:
//   { id, label,
//     tables()        -> [{schema, name}]            what is known now (may be empty while loading)
//     columns(table)  -> Promise<[{name, type}]>     table is one of tables(); the provider caches
//     cachedColumns(table) -> [{name, type}] | undefined   without asking anyone }
// Typing never calls the cloud: tables come from what the page already listed, and a table's
// columns are asked for once, the first time a statement names that table.
//
// The top of this file has no CodeMirror or DOM in it, so app/tests can check it with plain Node.

const SQL_DIALECTS = {
  spark: { label: "Spark SQL (Lakehouse)", short: "Spark SQL", runsHere: true, quote: "`" },
  warehouse: { label: "Fabric Warehouse (T-SQL)", short: "Warehouse T-SQL", runsHere: false, quote: "[" },
  endpoint: { label: "Fabric SQL analytics endpoint (T-SQL)", short: "SQL endpoint T-SQL", runsHere: false, quote: "[" },
  sqldb: { label: "Fabric SQL database (T-SQL)", short: "SQL database T-SQL", runsHere: false, quote: "[" },
  duckdb: { label: "DuckDB (this PC)", short: "DuckDB", runsHere: true, quote: '"' },
};

const SQL_WORDS = (() => {
  const split = (s) => s.trim().split(/\s+/);
  const common = "select from where group by having order limit offset join inner left right full outer cross on using as " +
    "distinct all union intersect except with recursive case when then else end and or not in is null like between exists " +
    "true false insert into values table create view drop alter add column rename to if update set delete merge matched " +
    "over partition rows range unbounded preceding following current row asc desc nulls first last cast";
  const spark = common + " semi anti rlike ilike overwrite replace temporary temp partitioned cluster clustered distribute " +
    "sort lateral explode window tablesample pivot unpivot qualify describe show use database schema cache uncache refresh " +
    "analyze compute statistics location tblproperties options comment struct array map interval try_cast minus";
  const tsql = common + " top percent ties identity go declare begin transaction tran commit rollback try catch throw raiserror " +
    "print exec execute procedure proc function returns return while break continue goto output inserted deleted source " +
    "target truncate index option openrowset apply pivot unpivot fetch next only collate grant revoke deny schema " +
    "nolock try_cast try_convert convert within";
  const duck = common + " semi anti asof positional ilike similar glob replace temporary temp qualify describe summarize " +
    "pivot unpivot copy attach detach install load pragma exclude columns struct list map interval try_cast window filter " +
    "sample using";
  const sparkFns = "abs acos add_months aggregate any approx_count_distinct approx_percentile array array_contains " +
    "array_distinct array_except array_join array_max array_min array_position array_remove array_sort arrays_zip ascii " +
    "avg base64 bin bool_and bool_or btrim cardinality cbrt ceil ceiling char_length coalesce collect_list collect_set " +
    "concat concat_ws contains conv corr cos count count_if covar_pop current_date current_timestamp date_add date_diff " +
    "date_format date_part date_sub date_trunc datediff day dayofmonth dayofweek dayofyear decode dense_rank element_at " +
    "endswith exp explode explode_outer extract filter first first_value flatten floor format_number format_string " +
    "from_json from_unixtime from_utc_timestamp get_json_object greatest hash hex hour if ifnull initcap inline instr " +
    "isnan isnotnull isnull json_tuple lag last last_day last_value lcase lead least left length levenshtein ln locate " +
    "log log10 log2 lower lpad ltrim make_date map_keys map_values max md5 mean median min minute mod month " +
    "months_between named_struct nanvl next_day now ntile nullif nvl nvl2 percent_rank percentile pi posexplode " +
    "position pow power quarter rand randn rank regexp_extract regexp_extract_all regexp_like regexp_replace repeat " +
    "replace reverse right round row_number rpad rtrim second sequence sha1 sha2 sign sin size slice sort_array soundex " +
    "split split_part sqrt startswith stddev stddev_pop stddev_samp substr substring substring_index sum tan to_date " +
    "to_json to_timestamp to_unix_timestamp to_utc_timestamp transform translate trim trunc try_add try_divide typeof " +
    "ucase unbase64 unhex unix_timestamp upper uuid var_pop var_samp variance weekday weekofyear xxhash64 year";
  const tsqlFns = "abs approx_count_distinct ascii avg cast ceiling charindex checksum choose coalesce concat concat_ws " +
    "convert count count_big cume_dist current_timestamp datalength dateadd datediff datediff_big datefromparts datename " +
    "datepart datetrunc day dense_rank eomonth exp first_value floor format getdate getutcdate greatest grouping " +
    "hashbytes iif isdate isjson isnull isnumeric json_modify json_query json_value lag last_value lead least left len " +
    "log log10 lower ltrim max min month newid ntile nullif object_id parse patindex percent_rank percentile_cont " +
    "percentile_disc power quotename rank replace replicate reverse right round row_number rtrim schema_name sign " +
    "soundex space sqrt square stdev stdevp str string_agg string_split stuff substring sum switchoffset sysdatetime " +
    "sysdatetimeoffset sysutcdatetime timefromparts todatetimeoffset translate trim try_cast try_convert try_parse " +
    "unicode upper var varp year";
  const duckFns = "abs age any_value approx_count_distinct arg_max arg_min array_agg avg bit_count bool_and bool_or ceil " +
    "coalesce concat concat_ws contains count current_date current_timestamp date_add date_diff date_part date_sub " +
    "date_trunc datediff dayname epoch exp extract first floor format generate_series greatest hash histogram if ifnull " +
    "last least left length list list_aggregate list_contains list_distinct list_sort lower lpad ltrim max md5 mean " +
    "median min mode month monthname now nullif percentile_cont quantile random range read_csv read_json read_parquet " +
    "regexp_extract regexp_matches regexp_replace repeat replace reverse right round row_number rpad rtrim sha256 " +
    "split_part sqrt starts_with stddev strftime string_agg string_split strptime struct_pack substr substring sum today " +
    "trim trunc try_cast typeof unnest upper uuid year";
  const sparkTypes = "string varchar char int integer bigint smallint tinyint long short float double decimal numeric " +
    "boolean date timestamp timestamp_ntz binary void";
  const tsqlTypes = "bigint int smallint tinyint bit decimal numeric money smallmoney float real date time datetime " +
    "datetime2 datetimeoffset smalldatetime char varchar nchar nvarchar text ntext binary varbinary uniqueidentifier xml";
  const duckTypes = "varchar text integer int bigint hugeint smallint tinyint double float real decimal numeric boolean " +
    "date time timestamp timestamptz interval blob uuid json";
  const t = { keywords: split(tsql), functions: split(tsqlFns), types: split(tsqlTypes) };
  return {
    spark: { keywords: split(spark), functions: split(sparkFns), types: split(sparkTypes) },
    warehouse: t,
    endpoint: t,
    sqldb: t,
    duckdb: { keywords: split(duck), functions: split(duckFns), types: split(duckTypes) },
  };
})();

// Words that end a table reference: after them comes no alias.
const SQL_CLAUSE_WORDS = new Set(("where on using join inner left right full outer cross group order having limit union " +
  "except intersect window qualify lateral natural semi anti set values select pivot unpivot offset fetch for option into " +
  "when then tablesample with as asof positional").split(" "));

function sqlDialect(id) {
  return SQL_DIALECTS[id] ? id : "spark";
}

const SQL_PLAIN_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

// ---- compatibility findings (SQL-002) as editor diagnostics

// The engine counts characters as Python does (code points); the editor counts UTF-16 units.
function sqlUtf16Offset(text, codePoints) {
  let units = 0;
  for (let n = 0; n < codePoints && units < text.length; n++) {
    const c = text.charCodeAt(units);
    units += c >= 0xd800 && c <= 0xdbff && units + 1 < text.length ? 2 : 1;
  }
  return units;
}

const SQL_LINT_SEVERITY = { error: "error", warning: "warning", information: "info", unverified: "hint" };
const SQL_SEVERITY_LABEL = { error: "Error", warning: "Warning", information: "Information", unverified: "Unverified" };

// A compatibility report's findings, placed for the editor. `text` is the SQL the report was made from.
function sqlLintDiagnostics(report, text) {
  if (!report || !Array.isArray(report.diagnostics)) return [];
  return report.diagnostics.map((d) => {
    const from = sqlUtf16Offset(text, d.from);
    const to = Math.max(from, sqlUtf16Offset(text, d.to));
    return {
      from, to, severity: SQL_LINT_SEVERITY[d.severity] || "hint", source: d.rule,
      message: `${SQL_SEVERITY_LABEL[d.severity] || d.severity} · ${d.title}\n${d.message}\n${d.action}\nConfidence: ${d.confidence}`,
      lines: [`${SQL_SEVERITY_LABEL[d.severity] || d.severity} · ${d.title}`, d.message, d.action,
        `${d.rule} · confidence ${d.confidence}`],
    };
  });
}

// A name as the dialect writes it: plain when it can be, quoted otherwise.
function sqlQuoteIdent(name, dialect = "spark") {
  if (SQL_PLAIN_NAME.test(name)) return name;
  const q = SQL_DIALECTS[sqlDialect(dialect)].quote;
  if (q === "[") return `[${name.replace(/]/g, "]]")}]`;
  return `${q}${name.split(q).join(q + q)}${q}`;
}

// "dbo.sales" reads as "sales", the way Fabric's query tab names it.
function sqlTableName(schema, table, dialect = "spark") {
  return schema === "dbo" ? sqlQuoteIdent(table, dialect) : `${sqlQuoteIdent(schema, dialect)}.${sqlQuoteIdent(table, dialect)}`;
}

function sqlUnquote(word) {
  if (/^`.*`$/s.test(word)) return word.slice(1, -1).replace(/``/g, "`");
  if (/^\[.*\]$/s.test(word)) return word.slice(1, -1).replace(/]]/g, "]");
  if (/^".*"$/s.test(word)) return word.slice(1, -1).replace(/""/g, '"');
  return word;
}

// `text` with string literals and comments blanked out (same length), and whether `pos` is inside one.
function sqlMask(text, pos = -1) {
  const out = text.split("");
  let inert = false;
  let i = 0;
  const blank = (from, to) => { for (let k = from; k < to; k++) if (out[k] !== "\n") out[k] = " "; };
  while (i < text.length) {
    const start = i;
    let end = -1;
    if (text[i] === "'") {
      end = i + 1;
      while (end < text.length && !(text[end] === "'" && text[end + 1] !== "'")) end += text[end] === "'" ? 2 : 1;
      end = Math.min(end + 1, text.length);
      blank(start + 1, Math.max(start + 1, end - 1));
    } else if (text.startsWith("--", i)) {
      end = text.indexOf("\n", i);
      if (end === -1) end = text.length;
      blank(start, end);
    } else if (text.startsWith("/*", i)) {
      end = text.indexOf("*/", i + 2);
      end = end === -1 ? text.length : end + 2;
      blank(start, end);
    }
    if (end === -1) { i++; continue; }
    // Inside: after the opening, and before the end or at it when the literal or comment never closes.
    const closed = text[start] === "'" ? end > start + 1 && text[end - 1] === "'" && end - 1 > start
      : text.startsWith("--", start) ? false : text.slice(start + 2, end).endsWith("*/");
    if (pos > start && (pos < end || (pos === end && !closed))) inert = true;
    i = end;
  }
  return { text: out.join(""), inert };
}

// The statement around `pos`: from the `;` before it to the `;` after it, outside strings and comments.
function sqlStatementAt(text, pos) {
  const masked = sqlMask(text).text;
  let from = masked.lastIndexOf(";", pos - 1) + 1;
  let to = masked.indexOf(";", pos);
  if (to === -1) to = text.length;
  return { from, to, text: text.slice(from, to), masked: masked.slice(from, to) };
}

const SQL_TOKEN = /`[^`]*`|\[[^\]]*\]|"[^"]*"|[A-Za-z_@#][\w$#@]*|\d+(?:\.\d+)?|\S/g;

// The tables a statement reads or writes, with their aliases, and the CTEs it defines.
function sqlTableRefs(maskedStatement) {
  const toks = maskedStatement.match(SQL_TOKEN) || [];
  const low = (t) => (t || "").toLowerCase();
  const isName = (t) => !!t && (/^[`["]/.test(t) || /^[A-Za-z_#][\w$#@]*$/.test(t));
  const refs = [];
  const ctes = [];
  for (let i = 0; i + 2 < toks.length; i++) {
    if (isName(toks[i]) && low(toks[i + 1]) === "as" && toks[i + 2] === "(" && ["with", ",", "recursive"].includes(low(toks[i - 1]))) {
      ctes.push(sqlUnquote(toks[i]));
    }
  }
  const skipParens = (i) => {
    let depth = 0;
    for (; i < toks.length; i++) {
      if (toks[i] === "(") depth++;
      else if (toks[i] === ")" && --depth === 0) return i + 1;
    }
    return i;
  };
  const readAlias = (i) => {
    if (low(toks[i]) === "as") i++;
    if (isName(toks[i]) && !SQL_CLAUSE_WORDS.has(low(toks[i]))) return [sqlUnquote(toks[i]), i + 1];
    return [null, i];
  };
  for (let i = 0; i < toks.length; i++) {
    const word = low(toks[i]);
    if (!["from", "join", "update", "into"].includes(word)) continue;
    let j = i + 1;
    for (;;) {
      if (toks[j] === "(") {  // a derived table: only its alias is known
        j = skipParens(j);
        const [alias, next] = readAlias(j);
        if (alias) refs.push({ parts: [], name: null, alias });
        j = next;
      } else if (isName(toks[j]) && !SQL_CLAUSE_WORDS.has(low(toks[j]))) {
        const parts = [sqlUnquote(toks[j])];
        j++;
        while (toks[j] === "." && isName(toks[j + 1])) { parts.push(sqlUnquote(toks[j + 1])); j += 2; }
        if (toks[j] === "(") { j = skipParens(j); const [, next] = readAlias(j); j = next; }  // a table function
        else {
          const [alias, next] = readAlias(j);
          refs.push({ parts, name: parts[parts.length - 1], alias });
          j = next;
        }
      }
      if (word === "from" && toks[j] === ",") { j++; continue; }
      break;
    }
  }
  return { refs, ctes };
}

// What the person is typing at `pos`: the partial word, what comes before its dot, and the statement's tables.
function sqlCompletionContext(text, pos) {
  if (sqlMask(text, pos).inert) return null;
  const line = text.slice(text.lastIndexOf("\n", pos - 1) + 1, pos);
  const m = line.match(/((?:(?:`[^`]*`|\[[^\]]*\]|"[^"]*"|[\w$#@]+)\.)*)(`[^`]*|\[[^\]]*|"[^"]*|[\w$#@]*)$/);
  const qualifierText = m ? m[1] : "";
  const partial = m ? m[2] : "";
  const qualifier = qualifierText ? (qualifierText.slice(0, -1).match(/`[^`]*`|\[[^\]]*\]|"[^"]*"|[\w$#@]+/g) || []).map(sqlUnquote) : null;
  const stmt = sqlStatementAt(text, pos);
  const before = stmt.masked.slice(0, pos - stmt.from).match(SQL_TOKEN) || [];
  const words = before.filter((t) => /^[A-Za-z_]/.test(t)).map((t) => t.toLowerCase());
  if (partial && words.length && words[words.length - 1] === partial.toLowerCase()) words.pop();
  return { from: pos - partial.length, partial, qualifier, previous: words[words.length - 1] || "", ...sqlTableRefs(stmt.masked) };
}

// The provider's table a reference names (["sales"], ["dbo", "sales"], ["Lakehouse", "dbo", "sales"]).
function sqlResolveTable(parts, tables) {
  if (!parts || !parts.length) return null;
  const name = parts[parts.length - 1].toLowerCase();
  const schema = parts.length > 1 ? parts[parts.length - 2].toLowerCase() : null;
  const hits = tables.filter((t) => t.name.toLowerCase() === name && (!schema || t.schema.toLowerCase() === schema));
  return hits.find((t) => t.schema.toLowerCase() === "dbo") || hits[0] || null;
}

// The tables whose columns the completion at `ctx` needs: the qualifier's, or every table the statement names.
function sqlTablesNeeded(ctx, tables) {
  if (ctx.qualifier) {
    const q = ctx.qualifier;
    const alias = q.length === 1 && ctx.refs.find((r) => r.alias && r.alias.toLowerCase() === q[0].toLowerCase());
    const table = alias ? sqlResolveTable(alias.parts, tables) : sqlResolveTable(q, tables);
    return table ? [table] : [];
  }
  const seen = new Set();
  return ctx.refs.map((r) => sqlResolveTable(r.parts, tables)).filter((t) => t && !seen.has(t) && seen.add(t));
}

const SQL_TABLE_SLOTS = new Set(["from", "join", "update", "into", "table"]);

// Completion options at `ctx`. `columnsOf(table)` gives a needed table's columns (already fetched).
function sqlCompletionOptions(ctx, dialect, tables, columnsOf) {
  const d = sqlDialect(dialect);
  const words = SQL_WORDS[d];
  const options = [];
  const col = (c, table) => ({ label: c.name, apply: sqlQuoteIdent(c.name, d), type: "property",
    detail: `${c.type || "column"} · ${table.name}` });
  if (ctx.qualifier) {
    const needed = sqlTablesNeeded(ctx, tables);
    if (needed.length) return (columnsOf(needed[0]) || []).map((c) => col(c, needed[0]));
    const schema = ctx.qualifier[ctx.qualifier.length - 1].toLowerCase();
    return tables.filter((t) => t.schema.toLowerCase() === schema)
      .map((t) => ({ label: t.name, apply: sqlQuoteIdent(t.name, d), type: "class", detail: "table" }));
  }
  const wantsTable = SQL_TABLE_SLOTS.has(ctx.previous);
  for (const t of tables) {
    options.push({ label: sqlTableName(t.schema, t.name, d), type: "class", detail: t.schema === "dbo" ? "table" : `table · ${t.schema}`,
      boost: wantsTable ? 20 : 0 });
  }
  const schemas = [...new Set(tables.map((t) => t.schema))];
  if (schemas.some((s) => s !== "dbo")) {
    for (const s of schemas) options.push({ label: sqlQuoteIdent(s, d), type: "namespace", detail: "schema", boost: wantsTable ? 10 : 0 });
  }
  for (const c of ctx.ctes) options.push({ label: sqlQuoteIdent(c, d), type: "class", detail: "CTE", boost: wantsTable ? 20 : 0 });
  if (!wantsTable) {
    for (const r of ctx.refs) if (r.alias) options.push({ label: r.alias, type: "variable", detail: r.name ? `alias of ${r.name}` : "alias", boost: 5 });
    const seen = new Set();
    for (const t of sqlTablesNeeded(ctx, tables)) {
      for (const c of columnsOf(t) || []) {
        if (seen.has(c.name.toLowerCase())) continue;
        seen.add(c.name.toLowerCase());
        options.push({ ...col(c, t), boost: 10 });
      }
    }
  }
  for (const k of words.keywords) options.push({ label: k.toUpperCase(), type: "keyword", boost: -10 });
  for (const f of words.functions) options.push({ label: f.toUpperCase(), type: "function", detail: "function", boost: -5 });
  for (const t of words.types) options.push({ label: t.toUpperCase(), type: "type", detail: "type", boost: -20 });
  return options;
}

// The SQL a run should use: the selection when there is one, otherwise everything.
function sqlChosenText(text, from, to) {
  return to > from ? text.slice(from, to) : text;
}

// ---------- the editor ----------

const sqlCmDialects = {};

function sqlCmDialect(id) {
  const d = sqlDialect(id);
  if (!sqlCmDialects[d]) {
    const w = SQL_WORDS[d];
    if (SQL_DIALECTS[d].quote === "[") {
      sqlCmDialects[d] = CM.SQLDialect.define({ ...CM.MSSQL.spec, builtin: `${CM.MSSQL.spec.builtin} ${w.functions.join(" ")}` });
    } else {
      sqlCmDialects[d] = CM.SQLDialect.define({
        keywords: w.keywords.join(" "), types: w.types.join(" "), builtin: w.functions.join(" "),
        identifierQuotes: d === "spark" ? "`" : '"', backslashEscapes: d === "spark", doubleQuotedStrings: d === "spark",
        specialVar: d === "spark" ? "$" : "$?", operatorChars: "*+\\-%<>!=&|~^/:",
      });
    }
  }
  return sqlCmDialects[d];
}

const sqlHighlight = CM.HighlightStyle.define([
  { tag: CM.tags.keyword, class: "kw" },
  { tag: [CM.tags.string, CM.tags.special(CM.tags.string)], class: "str" },
  { tag: [CM.tags.number, CM.tags.bool, CM.tags.null], class: "num" },
  { tag: [CM.tags.comment, CM.tags.lineComment, CM.tags.blockComment], class: "cm" },
  { tag: CM.tags.standard(CM.tags.name), class: "fn" },
  { tag: CM.tags.typeName, class: "bi" },
  { tag: CM.tags.special(CM.tags.name), class: "var" },
  { tag: CM.tags.operator, class: "op" },
]);

const sqlTheme = CM.EditorView.theme({
  "&": { height: "100%", fontSize: "13.5px" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "22px", overflow: "auto" },
  ".cm-content": { padding: "8px 0" },
  ".cm-gutters": { background: "#fafafa", borderRight: "1px solid var(--border-soft)", color: "#a19f9d" },
  ".cm-activeLine": { background: "rgba(27, 54, 100, 0.04)" },
  ".cm-activeLineGutter": { background: "rgba(27, 54, 100, 0.06)" },
  ".cm-tooltip-autocomplete": { fontFamily: "var(--mono)", fontSize: "12.5px" },
});

// Known tables and columns underlined as references, so a typo stands out before running. In T-SQL,
// [bracketed names] are names: CodeMirror's T-SQL reads the words inside as keywords, so they are marked plain.
function sqlReferenceMarks(provider, getDialect) {
  const tableMark = CM.Decoration.mark({ class: "sql-ref-table" });
  const colMark = CM.Decoration.mark({ class: "sql-ref-col" });
  const quotedMark = CM.Decoration.mark({ class: "sql-quoted-name" });
  const quotedTableMark = CM.Decoration.mark({ class: "sql-quoted-name sql-ref-table" });
  const quotedColMark = CM.Decoration.mark({ class: "sql-quoted-name sql-ref-col" });
  const build = (view) => {
    const tables = new Set(provider.tables().map((t) => t.name.toLowerCase()));
    const cols = new Set();
    for (const t of provider.tables()) for (const c of provider.cachedColumns(t) || []) cols.add(c.name.toLowerCase());
    const tsql = SQL_DIALECTS[getDialect()].quote === "[";
    const marks = [];
    for (const { from, to } of view.visibleRanges) {
      const tree = CM.syntaxTree(view.state);
      tree.iterate({ from, to, enter: (node) => {
        if (node.name !== "Identifier" && node.name !== "QuotedIdentifier") return;
        const word = sqlUnquote(view.state.doc.sliceString(node.from, node.to)).toLowerCase();
        if (tables.has(word)) marks.push([node.from, node.to, tableMark]);
        else if (cols.has(word)) marks.push([node.from, node.to, colMark]);
      } });
      if (!tsql) continue;
      const text = view.state.doc.sliceString(from, to);
      for (const m of text.matchAll(/\[[^\]\n]*\]/g)) {
        const at = from + m.index;
        const inside = tree.resolveInner(at, 1).name;
        if (["String", "LineComment", "BlockComment"].includes(inside)) continue;
        const word = m[0].slice(1, -1).toLowerCase();
        marks.push([at, at + m[0].length, tables.has(word) ? quotedTableMark : cols.has(word) ? quotedColMark : quotedMark]);
      }
    }
    marks.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const builder = new CM.RangeSetBuilder();
    let last = -1;
    for (const [a, b, mark] of marks) if (a >= last) { builder.add(a, b, mark); last = b; }
    return builder.finish();
  };
  return CM.ViewPlugin.fromClass(class {
    constructor(view) { this.decorations = build(view); }
    update(u) { this.decorations = build(u.view); }
  }, { decorations: (v) => v.decorations });
}

function sqlCompletionSource(provider, getDialect) {
  return async (cmCtx) => {
    const text = cmCtx.state.doc.toString();
    const ctx = sqlCompletionContext(text, cmCtx.pos);
    if (!ctx) return null;
    if (!ctx.partial && !ctx.qualifier && !cmCtx.explicit) return null;
    const tables = provider.tables();
    await Promise.all(sqlTablesNeeded(ctx, tables).map((t) => provider.columns(t).catch(() => [])));
    if (cmCtx.aborted) return null;
    const options = sqlCompletionOptions(ctx, getDialect(), tables, (t) => provider.cachedColumns(t));
    return options.length ? { from: ctx.from, options, validFor: /^[\w$#@]*$/ } : null;
  };
}

// The SQL page's editor. `on` holds the page's actions: change(text), run(), save(), format().
// `check(text, dialect)`, when given, returns a compatibility report (or null), shown as diagnostics.
function createSqlEditor({ doc, dialect, provider, on, check = null, extensions = [] }) {
  let current = sqlDialect(dialect);
  const recheckNow = CM.StateEffect.define();
  const recheck = () => {
    if (!check) return;
    view.dispatch({ effects: recheckNow.of(null) });  // marks the check due (needsRefresh) ...
    CM.forceLinting(view);  // ... and runs it now instead of after the typing delay
  };
  const langComp = new CM.Compartment();
  const language = () => [CM.sql({ dialect: sqlCmDialect(current), upperCaseKeywords: true }),
    CM.autocompletion({ override: [sqlCompletionSource(provider, () => current)], activateOnTypingDelay: 120 })];
  const keys = [
    { key: "Mod-Enter", run: () => { on.run(); return true; } },
    { key: "Mod-s", run: () => { on.save(); return true; }, preventDefault: true },
    { key: "Shift-Alt-f", run: () => { on.format(); return true; } },
    { key: "Mod-/", run: CM.toggleComment },
  ];
  const view = new CM.EditorView({
    state: CM.EditorState.create({
      doc,
      extensions: [
        CM.lineNumbers(), CM.foldGutter(), CM.highlightActiveLineGutter(),
        CM.highlightSpecialChars(), CM.history(), CM.drawSelection(), CM.dropCursor(),
        CM.EditorState.allowMultipleSelections.of(true), CM.rectangularSelection(), CM.crosshairCursor(),
        CM.indentOnInput(), CM.bracketMatching(), CM.closeBrackets(), CM.highlightActiveLine(), CM.highlightSelectionMatches(),
        CM.syntaxHighlighting(sqlHighlight),
        langComp.of(language()),
        sqlReferenceMarks(provider, () => current),
        CM.Prec.highest(CM.keymap.of(keys)),
        CM.keymap.of([...CM.closeBracketsKeymap, ...CM.defaultKeymap, ...CM.searchKeymap, ...CM.historyKeymap,
          ...CM.foldKeymap, ...CM.completionKeymap, CM.indentWithTab]),
        CM.placeholder("Write SQL. Ctrl+Enter runs it, Ctrl+Space suggests names."),
        CM.EditorView.updateListener.of((u) => { if (u.docChanged) on.change(u.state.doc.toString()); }),
        CM.EditorView.contentAttributes.of({ "aria-label": "SQL query" }),
        sqlTheme,
        check ? [CM.linter(async (v) => {
          const text = v.state.doc.toString();
          const report = await check(text, current);
          if (v.state.doc.toString() !== text) return [];
          return sqlLintDiagnostics(report, text).map((d) => ({ ...d, renderMessage: () => {
            const box = document.createElement("div");
            box.className = "sql-lint-msg";
            d.lines.forEach((line, i) => {
              const el = document.createElement(i === 0 ? "b" : "div");
              el.textContent = line;
              if (i === d.lines.length - 1) el.className = "muted";
              box.append(el);
            });
            return box;
          } }));
        }, { delay: 750, needsRefresh: (u) => u.transactions.some((tr) => tr.effects.some((e) => e.is(recheckNow))) }),
        CM.lintGutter()] : [],
        ...extensions,
      ],
    }),
  });
  return {
    view,
    text: () => view.state.doc.toString(),
    chosen: () => {
      const { from, to } = view.state.selection.main;
      return sqlChosenText(view.state.doc.toString(), from, to);
    },
    selection: () => view.state.selection.main,
    // Replace the selection with `text` and leave the cursor after it.
    insert(text) {
      const { from, to } = view.state.selection.main;
      view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length }, scrollIntoView: true });
      view.focus();
    },
    // Replace from..to (everything by default) as its own undo step.
    replace(text, from = 0, to = view.state.doc.length) {
      view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length }, scrollIntoView: true,
        annotations: CM.isolateHistory.of("full") });
    },
    setDialect(id) {
      current = sqlDialect(id);
      view.dispatch({ effects: langComp.reconfigure(language()) });
      recheck();
    },
    recheck,
    // Select from..to and show it, for a finding picked in the Compatibility panel.
    reveal(from, to) {
      view.dispatch({ selection: { anchor: from, head: to }, scrollIntoView: true });
      view.focus();
    },
    refresh() { view.dispatch({}); },  // redraw the reference marks after columns arrive
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}
