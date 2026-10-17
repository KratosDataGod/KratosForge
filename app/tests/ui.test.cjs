// UI checks that run without a browser: `node --test app/tests`.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ui = path.join(__dirname, "..", "ui");

test("every UI script parses", () => {
  for (const name of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    assert.doesNotThrow(() => new vm.Script(fs.readFileSync(path.join(ui, name), "utf8"), { filename: name }), name);
  }
});

// esc and inlineMd from app.js, without the rest of the page.
function markdownHelpers() {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const code = src.match(/function esc\([\s\S]*?\n}\n/)[0] + src.match(/function inlineMd\([\s\S]*?\n}\n/)[0];
  const ctx = {};
  vm.runInNewContext(`${code}; this.esc = esc; this.inlineMd = inlineMd;`, ctx);
  return ctx;
}

test("a Markdown link can't add attributes or handlers", () => {
  const { inlineMd } = markdownHelpers();
  const html = inlineMd('[x](a" style="position:fixed" onmouseover="alert(1)) and [y](b\' onclick=\'z)');
  assert.ok(!/"\s+on\w+=|'\s+on\w+=|" style=/.test(html), html);
  assert.match(html, /title="a&quot; style=&quot;/);
});

test("Markdown still formats", () => {
  const { inlineMd } = markdownHelpers();
  assert.strictEqual(inlineMd("**b** *i* `c` [ok](https://x.y) <b>"),
    '<strong>b</strong> <em>i</em> <code>c</code> <u title="https://x.y">ok</u> &lt;b&gt;');
});

test("a run says where it ran: Fabric, an agent or this PC", () => {
  const src = fs.readFileSync(path.join(ui, "activity.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function ranOn\([\s\S]*?\n}\n/)[0]}; this.ranOn = ranOn;`, ctx);
  assert.strictEqual(ctx.ranOn({ ran_on: "Agent etl-01 on srv1" }), "Agent etl-01 on srv1");
  assert.strictEqual(ctx.ranOn({ compute: "fabric" }), "Fabric");  // an engine from before ran_on
  assert.strictEqual(ctx.ranOn({}), "This PC");
});

test("an engine ended for not answering says so, and that variables were cleared", () => {
  const src = fs.readFileSync(path.join(ui, "activity.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function friendlyError\([\s\S]*?\n}\n/)[0]}; this.friendlyError = friendlyError;`, ctx);
  const raw = "The engine stopped answering, so Fabric Desktop by KratosBI restarted it. poll_cell had no answer after 60 seconds.";
  const out = ctx.friendlyError(raw);
  assert.match(out.text, /restarted it\. Notebook variables were cleared/);
  assert.strictEqual(out.details, raw);
});

test("a failing workload's row shows the failed run, not a newer good one elsewhere", () => {
  const src = fs.readFileSync(path.join(ui, "workloads.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function rowRun\([\s\S]*?\n}\n/)[0]}; this.rowRun = rowRun;`, ctx);
  const failed = { state: "failed", started_at: "2026-10-16T09:00:00Z" };
  const ok = { state: "ok", started_at: "2026-10-16T10:00:00Z" };
  const row = (health) => ({ health, last_run: ok, deployments: [{ latest_run: ok }, { latest_run: failed }] });
  assert.strictEqual(ctx.rowRun(row("failing")), failed);
  assert.strictEqual(ctx.rowRun(row("healthy")), ok);
});

test("Workloads is a Preview feature", () => {
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const workloads = fs.readFileSync(path.join(ui, "workloads.js"), "utf8");
  assert.match(features, /workloads: "Workloads"/);
  assert.match(workloads, /id: "workloads", preview: true/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="workloads.js"><\/script>/);
});

// The pure helpers at the top of prove.js, without the rest of the page.
function proveHelpers() {
  const src = fs.readFileSync(path.join(ui, "prove.js"), "utf8");
  const code = src.slice(0, src.indexOf("// ---------- state ----------"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { proveNames, proveRulesFrom, proveKind, proveVerdict, proveDiffRows, proveDifference });`, ctx);
  return ctx;
}

test("proof rules from the form keep only what was filled in", () => {
  const { proveRulesFrom } = proveHelpers();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(proveRulesFrom({ keys: " id, region ,", ignore: "", abs: "", rel: "1e-6", timestamps: "us" }))),
    { keys: ["id", "region"], rel: 1e-6 });
  assert.ok(Number.isNaN(proveRulesFrom({ abs: "abc" }).abs), "a bad number goes to the engine, which refuses it");
});

test("a scorecard never shows an estimate or a missing number as measured", () => {
  const { proveKind } = proveHelpers();
  assert.strictEqual(proveKind("estimated")[0], "Estimate");
  assert.strictEqual(proveKind("not available")[0], "Not available");
  assert.strictEqual(proveKind(undefined)[0], "Not available");
  assert.strictEqual(proveKind("measured")[0], "Measured");
});

test("the difference grid lists each changed column, and whole rows by side", () => {
  const { proveDiffRows, proveDifference, proveVerdict } = proveHelpers();
  const keyed = { examples: [
    { key: { id: 2 }, status: "changed", columns: { value: { pc: 20.5, fabric: 0 }, name: { pc: "a", fabric: "b" } } },
    { key: { id: 9 }, status: "only_pc" }] };
  assert.strictEqual(proveDiffRows(keyed, "changed").length, 2);
  assert.strictEqual(proveDiffRows(keyed, "only_pc")[0].key.id, 9);
  assert.strictEqual(proveDiffRows(keyed, "only_fabric").length, 0);
  const whole = { examples: [{ row: { a: 1 }, status: "only_fabric", times: 2 }] };
  assert.strictEqual(proveDiffRows(whole, "only_fabric")[0].times, 2);
  assert.strictEqual(proveDifference(20.5, 0), "20.5");
  assert.strictEqual(proveDifference("a", "b"), "");
  assert.strictEqual(proveVerdict({ state: "done", verdict: { verdict: "inconclusive" } })[1], "Inconclusive");
  assert.strictEqual(proveVerdict({ state: "cancelled" })[1], "Cancelled");
});

// The pure helpers at the top of deps.js.
function depsHelpers() {
  const src = fs.readFileSync(path.join(ui, "deps.js"), "utf8");
  const code = src.slice(0, src.indexOf("// ---------- state ----------"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { depsStale, depsPlan, depsList, depsLines });`, ctx);
  return ctx;
}

// Cells 1 (imports), 2 (reads orders), 3 (writes a table from 2's variable), 4 (reads 3's table).
const depsGraph = { cells: [
  { cell_id: "a", n: 1, upstream: [], effects: [], dynamic: [], defines: ["pl"] },
  { cell_id: "b", n: 2, upstream: [{ cell_id: "a", n: 1, kind: "variable", via: ["pl"], source: "detected" }], effects: [], dynamic: [] },
  { cell_id: "c", n: 3, upstream: [{ cell_id: "b", n: 2, kind: "variable", via: ["orders"], source: "detected" }],
    effects: [{ line: 1, text: "writes the table silver.daily" }], dynamic: [], writes: [{ kind: "table", name: "silver.daily" }] },
  { cell_id: "d", n: 4, upstream: [{ cell_id: "c", n: 3, kind: "table", via: ["silver.daily"], source: "detected" }], effects: [], dynamic: [] },
] };
const ranCells = (over = {}) => ["a", "b", "c", "d"].map((id, i) => ({ cell_id: id, source: `s${id}`, ran: { source: `s${id}`, seq: i + 1, epoch: 0 }, ...over[id] }));

test("an edited cell and everything that depends on it are stale; nothing else is", () => {
  const { depsStale } = depsHelpers();
  assert.deepStrictEqual(Object.keys(depsStale(ranCells(), depsGraph, 0)), []);
  const stale = depsStale(ranCells({ b: { source: "edited" } }), depsGraph, 0);
  assert.deepStrictEqual(Object.keys(stale), ["b", "c", "d"]);
  assert.strictEqual(stale.b.text, "Stale: code changed");
  assert.strictEqual(stale.d.text, "Stale: cell 3 is out of date");
});

test("a cell is stale when a cell it depends on ran again after it, or the kernel restarted", () => {
  const { depsStale } = depsHelpers();
  const cells = ranCells({ a: { ran: { source: "sa", seq: 9, epoch: 0 } } });
  assert.strictEqual(depsStale(cells, depsGraph, 0).b.text, "Stale: cell 1 ran again");
  assert.deepStrictEqual(Object.keys(depsStale(ranCells(), depsGraph, 1)), ["a", "b", "c", "d"]);
  assert.deepStrictEqual(Object.keys(depsStale(ranCells({ a: { ran: null } }), depsGraph, 0)), [], "a cell that hasn't run isn't stale");
});

test("a cell reading can't follow depends on every cell above it", () => {
  const { depsStale } = depsHelpers();
  const graph = { cells: depsGraph.cells.map((g) => (g.cell_id === "d" ? { ...g, upstream: [], dynamic: [{ line: 1, text: "exec" }] } : g)) };
  const cells = ranCells({ a: { source: "edited" } });
  assert.ok(depsStale(cells, graph, 0).d, "exec cell is stale after a change anywhere above");
});

test("run stale cells asks before a side effect and offers the cells before it", () => {
  const { depsStale, depsPlan, depsList } = depsHelpers();
  const plan = depsPlan(depsStale(ranCells({ b: { source: "edited" } }), depsGraph, 0), depsGraph);
  assert.deepStrictEqual([...plan.run], ["b", "c", "d"]);
  assert.deepStrictEqual(plan.effects.map((e) => e.n), [3]);
  assert.deepStrictEqual([...plan.before], ["b"]);
  assert.strictEqual(depsList([2, 3, 4]), "2, 3 and 4");
  assert.strictEqual(depsList([2]), "2");
});

test("Integrations is a Preview tab in Settings, not a rail page, with no AI of its own", () => {
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const src = fs.readFileSync(path.join(ui, "integrations.js"), "utf8");
  assert.match(features, /integrations: "External AI tools"/);
  assert.match(src, /id: "integrations",\s*preview: true/);
  assert.ok(!/rail:/.test(src), "Integrations must not add a rail item");
  assert.ok(!/api[_ ]?key|model picker|chat\(/i.test(src.replace(/no AI keys|no model picker|no chat/gi, "")), "no AI keys, models or chat");
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="integrations.js"') < html.indexOf('src="settings.js"'));
});

test("every AI tool is marked untested until someone tries it", () => {
  const src = fs.readFileSync(path.join(ui, "integrations.js"), "utf8");
  assert.match(src, /const TESTED_TOOLS = new Set\(\);/);
});

test("an approval request shows the code and decides only through the engine", () => {
  const src = fs.readFileSync(path.join(ui, "integrations.js"), "utf8");
  const body = src.slice(src.indexOf("function approvalBody"), src.indexOf("async function decide"));
  assert.match(body, /s\.content \? h\("pre"/);
  assert.match(src, /rpc\("integrations_decide", \{ approval_id: a\.id, approve \}\)/);
});

test("extensions are switched only through the engine, marked Preview and locked by policy", () => {
  const src = fs.readFileSync(path.join(ui, "integrations.js"), "utf8");
  const card = src.slice(src.indexOf("function extensionsCard"), src.indexOf("async function revokeTool"));
  assert.match(card, /rpc\("integrations_set_extension", \{ extension_id: e\.id, enabled: ev\.target\.checked \}\)/);
  assert.match(card, /previewTag\(\)/);
  assert.match(card, /disabled: e\.allowed \? null : true/);
});

test("Report a problem takes out secrets, emails, user names and ids (same cases as the engine)", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function redactReport\([\s\S]*?\n}\n/)[0]}; this.redactReport = redactReport;`, ctx);
  const { cases } = JSON.parse(fs.readFileSync(path.join(__dirname, "redact-cases.json"), "utf8"));
  for (const c of cases) assert.strictEqual(ctx.redactReport(c.in, c.level), c.out, c.in);
});

// Accessibility (FD-139): checks that need no browser.
test("every icon-only button has a name a screen reader can say", () => {
  const missing = [];
  for (const name of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    const src = fs.readFileSync(path.join(ui, name), "utf8");
    const line = (i) => `${name}:${src.slice(0, i).split("\n").length}`;
    for (const m of src.matchAll(/h\("button",\s*\{([^{}]*)\},\s*icon\([^()]*\)\s*\)/g)) {
      if (!/aria-label|title/.test(m[1])) missing.push(line(m.index));
    }
    for (const m of src.matchAll(/\bbtn\((?:null|""|''),\s*"[^"]+",[^;]*/g)) {
      if (!/aria-label|title/.test(m[0])) missing.push(line(m.index));
    }
  }
  assert.deepStrictEqual(missing, []);
});

test("the page has a skip link and labelled controls", () => {
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.match(html, /<a class="skip-link" href="#view">/);
  assert.match(html, /<main id="view"[^>]*tabindex="-1"/);
  for (const m of html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)) {
    const text = m[2].replace(/<[^>]+>/g, "").trim();
    assert.ok(/aria-label="[^"]+"/.test(m[1]) || /\w{2,}/.test(text), `unnamed button: ${m[0]}`);
  }
  for (const m of html.matchAll(/<input\b[^>]*>/g)) {
    const before = html.slice(0, m.index);
    const inLabel = before.lastIndexOf("<label") > before.lastIndexOf("</label>");
    assert.ok(inLabel || /aria-label=/.test(m[0]), `unlabelled input: ${m[0]}`);
  }
});

test("dialogs are modal for keyboard users and focus shows", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  for (const m of app.matchAll(/role: "dialog"[^)]*/g)) assert.match(m[0], /"aria-modal": "true"/);
  assert.match(app, /function trapFocus\(/);
  assert.match(app, /dialogFocus\.opener/);
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  assert.match(css, /^:focus-visible \{ outline: 2px/m);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\n {2}\*, \*::before, \*::after/);
});

test("a lakehouse name Fabric would refuse is caught before it's sent", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function lakehouseNameProblem\([\s\S]*?\n}\n/)[0]}; this.check = lakehouseNameProblem;`, ctx);
  assert.strictEqual(ctx.check("TPCH_Benchmark"), "");
  assert.match(ctx.check("TPC-H_Benchmark"), /For example TPC_H_Benchmark\./);
  assert.match(ctx.check("1st lake"), /For example LH_1st_lake\./);
  assert.match(ctx.check("x".repeat(257)), /256/);
});

test("Fabric refusing a name reads as a name problem, not an app crash", () => {
  const src = fs.readFileSync(path.join(ui, "activity.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function friendlyError\([\s\S]*?\n}\n/)[0]}; this.friendlyError = friendlyError;`, ctx);
  const raw = 'FabricError: POST https://api.fabric.microsoft.com/v1/workspaces/w/lakehouses failed with 400: {"errorCode":"InvalidInput","moreDetails":[{"errorCode":"InvalidParameter","message":"Artifact request failed due to invalid input. Error: DisplayName is Invalid for ArtifactType. DisplayName: <pi>TPC-H_Benchmark</pi>"}]}';
  assert.match(ctx.friendlyError(raw).text, /doesn't accept that name/);
});

test("Report a problem takes out secrets, emails, user names and ids (same cases as the engine)", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function redactReport\([\s\S]*?\n}\n/)[0]}; this.redactReport = redactReport;`, ctx);
  const { cases } = JSON.parse(fs.readFileSync(path.join(__dirname, "redact-cases.json"), "utf8"));
  for (const c of cases) assert.strictEqual(ctx.redactReport(c.in, c.level), c.out, c.in);
});

function gitHelpers() {
  const src = fs.readFileSync(path.join(ui, "git.js"), "utf8");
  const code = src.slice(0, src.indexOf("// ---------- state ----------"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { gitSplitPath, gitPillText, gitSideBySide, gitCommitLabel });`, ctx);
  return ctx;
}

const gitStatus = (over = {}) => ({ branch: "main", commit: "abc1234def", ahead: 0, behind: 0, merging: false,
  staged: [], unstaged: [], untracked: [], conflicts: [], ...over });

test("the branch pill shows the branch and what's ahead or behind", () => {
  const { gitPillText } = gitHelpers();
  assert.strictEqual(gitPillText(gitStatus()), "main");
  assert.strictEqual(gitPillText(gitStatus({ ahead: 2, behind: 1 })), "main ↑2 ↓1");
  assert.strictEqual(gitPillText(gitStatus({ branch: null })), "abc1234", "a detached head shows the commit");
  assert.strictEqual(gitPillText(gitStatus({ branch: null, commit: null })), "No commits");
  assert.strictEqual(gitPillText(null), "");
});

test("the commit button says what it will commit, and refuses with conflicts", () => {
  const { gitCommitLabel } = gitHelpers();
  assert.strictEqual(gitCommitLabel(gitStatus({ staged: [{}] })), "Commit 1 staged file to main");
  assert.strictEqual(gitCommitLabel(gitStatus({ staged: [{}, {}] })), "Commit 2 staged files to main");
  assert.strictEqual(gitCommitLabel(gitStatus({ unstaged: [{}], untracked: [{}] })), "Stage all and commit 2 files to main");
  assert.strictEqual(gitCommitLabel(gitStatus()), "Nothing to commit");
  assert.strictEqual(gitCommitLabel(gitStatus({ merging: true, conflicts: [{}] })), "Fix the conflicts to commit");
  assert.strictEqual(gitCommitLabel(gitStatus({ merging: true })), "Commit the merge to main");
});

test("side by side pairs removed and added lines and keeps context on both sides", () => {
  const { gitSideBySide, gitSplitPath } = gitHelpers();
  const rows = gitSideBySide([{ header: "@@ -1,3 +1,3 @@", lines: [
    { kind: " ", text: "a" }, { kind: "-", text: "b" }, { kind: "-", text: "c" }, { kind: "+", text: "B" }, { kind: " ", text: "d" }] }]);
  assert.strictEqual(rows[0].header, "@@ -1,3 +1,3 @@");
  assert.strictEqual(rows[1].left.text, "a"); assert.strictEqual(rows[1].right.text, "a");
  assert.strictEqual(rows[2].left.text, "b"); assert.strictEqual(rows[2].right.text, "B");
  assert.strictEqual(rows[3].left.text, "c"); assert.strictEqual(rows[3].right, null);
  assert.strictEqual(rows[4].left.text, "d");
  assert.deepStrictEqual({ ...gitSplitPath("notebooks/load.ipynb") }, { name: "load.ipynb", dir: "notebooks" });
  assert.deepStrictEqual({ ...gitSplitPath("README.md") }, { name: "README.md", dir: "" });
});

// The pure helpers at the top of sqleditor.js (SQL-001), without CodeMirror.
function sqlEditorHelpers() {
  const src = fs.readFileSync(path.join(ui, "sqleditor.js"), "utf8");
  const code = src.slice(0, src.indexOf("// ---------- the editor ----------"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { SQL_DIALECTS, SQL_WORDS, sqlDialect, sqlQuoteIdent, sqlTableName,
    sqlMask, sqlStatementAt, sqlTableRefs, sqlCompletionContext, sqlResolveTable, sqlTablesNeeded, sqlCompletionOptions,
    sqlChosenText, sqlUtf16Offset, sqlLintDiagnostics });`, ctx);
  return ctx;
}
const plain = (v) => JSON.parse(JSON.stringify(v));
const SQL_TABLES = [{ schema: "dbo", name: "sales" }, { schema: "dbo", name: "customers" }, { schema: "finance", name: "budget" },
  { schema: "finance", name: "sales" }];

test("the SQL editor knows five dialects and only Spark SQL and DuckDB run on this PC", () => {
  const { SQL_DIALECTS, SQL_WORDS, sqlDialect } = sqlEditorHelpers();
  assert.deepStrictEqual(Object.keys(SQL_DIALECTS), ["spark", "warehouse", "endpoint", "sqldb", "duckdb"]);
  assert.strictEqual(SQL_WORDS.endpoint, SQL_WORDS.warehouse);
  assert.deepStrictEqual(Object.keys(SQL_DIALECTS).filter((d) => SQL_DIALECTS[d].runsHere), ["spark", "duckdb"]);
  assert.strictEqual(sqlDialect(undefined), "spark");  // a tab saved before dialects existed
  assert.strictEqual(sqlDialect("oracle"), "spark");
  assert.ok(SQL_WORDS.warehouse.keywords.includes("top") && !SQL_WORDS.spark.keywords.includes("top"));
  assert.ok(SQL_WORDS.spark.functions.includes("date_format") && SQL_WORDS.warehouse.functions.includes("dateadd"));
});

test("names are quoted the way each dialect quotes them, only when needed", () => {
  const { sqlQuoteIdent, sqlTableName } = sqlEditorHelpers();
  assert.strictEqual(sqlQuoteIdent("amount"), "amount");
  assert.strictEqual(sqlQuoteIdent("Region Name"), "`Region Name`");
  assert.strictEqual(sqlQuoteIdent("we`ird"), "`we``ird`");
  assert.strictEqual(sqlQuoteIdent("Region Name", "warehouse"), "[Region Name]");
  assert.strictEqual(sqlQuoteIdent("a]b", "sqldb"), "[a]]b]");
  assert.strictEqual(sqlQuoteIdent("Region Name", "duckdb"), '"Region Name"');
  assert.strictEqual(sqlTableName("dbo", "sales"), "sales");
  assert.strictEqual(sqlTableName("finance", "Budget 2026", "warehouse"), "finance.[Budget 2026]");
});

test("strings and comments are not SQL to complete in", () => {
  const { sqlMask, sqlStatementAt } = sqlEditorHelpers();
  const text = "SELECT 'a;b' -- c;d\nFROM t; SELECT 2 /* x; */";
  assert.strictEqual(sqlMask(text).text.length, text.length);
  assert.ok(!sqlMask(text).text.includes("a;b") && !sqlMask(text).text.includes("c;d"));
  assert.ok(sqlMask(text, 9).inert, "inside the string");
  assert.ok(sqlMask(text, 17).inert, "inside the line comment");
  assert.ok(!sqlMask(text, 24).inert, "in FROM t");
  assert.ok(sqlMask("SELECT 'abc", 11).inert, "an unclosed string");
  assert.strictEqual(sqlStatementAt(text, 24).text, "SELECT 'a;b' -- c;d\nFROM t");
  assert.strictEqual(sqlStatementAt(text, 30).text, " SELECT 2 /* x; */");
});

test("table references are found with their aliases, across joins, comma lists, subqueries and CTEs", () => {
  const { sqlTableRefs } = sqlEditorHelpers();
  const refs = (sql) => plain(sqlTableRefs(sql));
  assert.deepStrictEqual(refs("SELECT * FROM sales s JOIN dbo.customers AS c ON c.id = s.id LEFT JOIN `finance`.`sales` fs USING (id)").refs, [
    { parts: ["sales"], name: "sales", alias: "s" },
    { parts: ["dbo", "customers"], name: "customers", alias: "c" },
    { parts: ["finance", "sales"], name: "sales", alias: "fs" }]);
  assert.deepStrictEqual(refs("SELECT * FROM sales, customers c WHERE 1 = 1").refs.map((r) => [r.name, r.alias]),
    [["sales", null], ["customers", "c"]]);
  assert.deepStrictEqual(refs("SELECT * FROM (SELECT 1 x) AS d JOIN range(10) r ON 1=1").refs, [{ parts: [], name: null, alias: "d" }]);
  const cte = refs("WITH m AS (SELECT * FROM sales), n AS (SELECT 1) SELECT * FROM m");
  assert.deepStrictEqual(cte.ctes, ["m", "n"]);
  assert.deepStrictEqual(refs("SELECT * FROM [Sales Data] WHERE x = 1").refs[0].name, "Sales Data");
  assert.deepStrictEqual(refs("INSERT INTO target SELECT * FROM src").refs.map((r) => r.name), ["target", "src"]);
});

test("completion knows when it follows a dot, and which tables the statement names", () => {
  const { sqlCompletionContext } = sqlEditorHelpers();
  const ctx = plain(sqlCompletionContext("SELECT s.am FROM sales s", 11));
  assert.deepStrictEqual([ctx.from, ctx.partial, ctx.qualifier], [9, "am", ["s"]]);
  assert.strictEqual(ctx.refs[0].alias, "s");
  const fromCtx = sqlCompletionContext("SELECT * FROM sa", 16);
  assert.deepStrictEqual([fromCtx.partial, fromCtx.qualifier, fromCtx.previous], ["sa", null, "from"]);
  assert.strictEqual(sqlCompletionContext("SELECT 'sa", 10), null, "inside a string");
  assert.deepStrictEqual(plain(sqlCompletionContext("SELECT * FROM `finance`.bu", 26).qualifier), ["finance"]);
  // Only the statement at the cursor counts.
  assert.deepStrictEqual(plain(sqlCompletionContext("SELECT * FROM a x; SELECT x. FROM b x", 28).refs.map((r) => r.name)), ["b"]);
});

test("a reference resolves to the lakehouse table, preferring dbo for a bare name", () => {
  const { sqlResolveTable } = sqlEditorHelpers();
  assert.deepStrictEqual(plain(sqlResolveTable(["SALES"], SQL_TABLES)), { schema: "dbo", name: "sales" });
  assert.deepStrictEqual(plain(sqlResolveTable(["finance", "sales"], SQL_TABLES)), { schema: "finance", name: "sales" });
  assert.deepStrictEqual(plain(sqlResolveTable(["Lakehouse", "finance", "budget"], SQL_TABLES)), { schema: "finance", name: "budget" });
  assert.strictEqual(sqlResolveTable(["nope"], SQL_TABLES), null);
});

test("completion options: columns after an alias, tables after FROM, everything otherwise", () => {
  const { sqlCompletionContext, sqlCompletionOptions, sqlTablesNeeded } = sqlEditorHelpers();
  const cols = { sales: [{ name: "amount", type: "double" }, { name: "Region Name", type: "string" }], customers: [{ name: "region", type: "string" }] };
  const columnsOf = (t) => cols[t.name];
  const at = (sql, pos = sql.length, dialect = "spark") => {
    const ctx = sqlCompletionContext(sql, pos);
    return { ctx, options: plain(sqlCompletionOptions(ctx, dialect, SQL_TABLES, columnsOf)) };
  };
  const alias = at("SELECT c. FROM sales s JOIN customers c ON 1=1", 9);
  assert.deepStrictEqual(alias.options.map((o) => o.label), ["region"]);
  assert.deepStrictEqual(plain(sqlTablesNeeded(alias.ctx, SQL_TABLES)), [{ schema: "dbo", name: "customers" }]);
  const quoted = at("SELECT s. FROM sales s", 9, "warehouse").options.find((o) => o.label === "Region Name");
  assert.strictEqual(quoted.apply, "[Region Name]");
  assert.deepStrictEqual(at("SELECT * FROM finance.").options.map((o) => o.label), ["budget", "sales"]);
  const from = at("SELECT * FROM s").options;
  assert.ok(from.find((o) => o.label === "sales").boost > from.find((o) => o.label === "SELECT").boost);
  assert.ok(!from.some((o) => o.type === "property"), "no columns where a table goes");
  const general = at("SELECT a FROM sales s WHERE r").options;
  for (const label of ["amount", "Region Name", "s", "sales", "finance", "WHERE", "COUNT"]) {
    assert.ok(general.some((o) => o.label === label), label);
  }
  assert.ok(!general.some((o) => o.label === "region"), "customers isn't in this statement");
  assert.ok(at("SELECT * FROM t", 15, "warehouse").options.some((o) => o.label === "TOP"));
});

test("Run uses the selection when there is one, otherwise the whole query", () => {
  const { sqlChosenText } = sqlEditorHelpers();
  assert.strictEqual(sqlChosenText("SELECT 1; SELECT 2", 10, 18), "SELECT 2");
  assert.strictEqual(sqlChosenText("SELECT 1; SELECT 2", 4, 4), "SELECT 1; SELECT 2");
});

test("compatibility findings land where the engine says, even after characters outside the basic plane", () => {
  const { sqlUtf16Offset, sqlLintDiagnostics } = sqlEditorHelpers();
  const text = "SELECT '🙂' AS f LIMIT 3";  // the emoji is one character to Python, two to JavaScript
  assert.strictEqual(sqlUtf16Offset(text, 0), 0);
  assert.strictEqual(sqlUtf16Offset(text, 8), 8);
  assert.strictEqual(sqlUtf16Offset(text, 9), 10);
  assert.strictEqual(sqlUtf16Offset(text, 999), text.length);
  const report = { diagnostics: [
    { severity: "error", rule: "SYN-001", title: "LIMIT isn't T-SQL", message: "No LIMIT.", action: "Use TOP.", confidence: "high", from: 16, to: 23 },
    { severity: "information", rule: "SEM-002", title: "t", message: "m", action: "a", confidence: "high", from: 0, to: 0 },
    { severity: "unverified", rule: "SYN-007", title: "t", message: "m", action: "a", confidence: "medium", from: 0, to: 6 },
  ] };
  const diags = sqlLintDiagnostics(report, text);
  assert.strictEqual(text.slice(diags[0].from, diags[0].to), "LIMIT 3");
  assert.deepStrictEqual(diags.map((d) => d.severity), ["error", "info", "hint"]);
  assert.strictEqual(diags[0].source, "SYN-001");
  assert.deepStrictEqual(plain(diags[0].lines), ["Error · LIMIT isn't T-SQL", "No LIMIT.", "Use TOP.", "SYN-001 · confidence high"]);
  assert.deepStrictEqual(plain(sqlLintDiagnostics(null, text)), []);
  assert.deepStrictEqual(plain(sqlLintDiagnostics({ failed: "x" }, text)), []);
});

test("the SQL page uses the CodeMirror editor, and saved tabs keep working", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const page = app.slice(app.indexOf("// ---------- SQL query page ----------"), app.indexOf("// Fabric's rule for lakehouse names"));
  assert.ok(!page.includes('h("textarea"'), "no textarea editor");
  assert.match(page, /createSqlEditor\(/);
  assert.match(page, /dialect: sqlDialect\(t\.dialect\)/, "a tab saved without a dialect opens as Spark SQL");
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf("codemirror.js") < html.indexOf("sqleditor.js") && html.indexOf("sqleditor.js") < html.indexOf("app.js"));
  const cm = fs.readFileSync(path.join(ui, "vendor", "codemirror", "codemirror.js"), "utf8");
  for (const name of ["SQLDialect", "MSSQL", "rectangularSelection", "crosshairCursor", "isolateHistory", "RangeSetBuilder",
    "linter", "lintGutter", "forceLinting", "StateEffect"]) {
    assert.match(cm, new RegExp(`${name}:\\(\\)=>`), `codemirror.js exports ${name}; rebuild it with npm run build`);
  }
});
