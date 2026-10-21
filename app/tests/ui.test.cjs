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

// The names a script declares at its top level (function, class, var, let, const, however indented
// and including destructured names), found by V8's own parser rather than a regex: appending
// `let x;` to a script is a SyntaxError exactly when the script already declares x at the top
// level. Nothing is run; the scripts are only compiled.
const bindable = new Map();
function canBind(word) {
  if (!bindable.has(word)) {
    try { new vm.Script(`let ${word};`); bindable.set(word, true); } catch { bindable.set(word, false); }
  }
  return bindable.get(word);
}
function topLevelNames(src) {
  const left = new Set([...new Set(src.match(/[A-Za-z_$][\w$]*/g) || [])].filter(canBind));
  const found = [];
  while (left.size) {
    try {
      new vm.Script(`${src}\n;let ${[...left].join(", ")};`);
      break;
    } catch (e) {
      const m = /^Identifier '([^']+)' has already been declared$/.exec(e.message);
      if (!m || !left.has(m[1])) throw e;
      found.push(m[1]);
      left.delete(m[1]);
    }
  }
  return found;
}

test("topLevelNames finds indented and destructured declarations, not nested ones", () => {
  const src = "function a() { var inner; }\n  const b = 1, { c, d: e } = {}, [f] = [];\n\tclass G {}\n" +
    "if (true) { let blockScoped; }\n    async function* h() {}\n// var commented;\nconst s = 'var quoted';";
  assert.deepStrictEqual(topLevelNames(src).sort(), ["G", "a", "b", "c", "e", "f", "h", "s"]);
});

// The page's scripts share one global scope, so a top-level name declared in two of them makes
// the later file silently replace the earlier one (integrations.js once replaced activity.js's when()).
test("no two page scripts declare the same top-level name", () => {
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map((m) => m[1]).filter((s) => !s.startsWith("vendor/"));
  assert.ok(scripts.length > 10, "index.html scripts not found");
  const owner = new Map();
  const clashes = [];
  for (const name of scripts) {
    for (const id of topLevelNames(fs.readFileSync(path.join(ui, name), "utf8"))) {
      if (owner.has(id)) clashes.push(`${id}: ${owner.get(id)} and ${name}`);
      else owner.set(id, name);
    }
  }
  assert.deepStrictEqual(clashes, []);
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
  const raw = "The engine stopped answering, so KratosForge restarted it. poll_cell had no answer after 60 seconds.";
  const out = ctx.friendlyError(raw);
  assert.match(out.text, /restarted it\. Notebook variables were cleared/);
  assert.strictEqual(out.details, raw);
});

test("a cell stopped by a limit from Settings is recognised, and other errors aren't", () => {
  const src = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function limitStop\([\s\S]*?\n}\n/)[0]}; this.limitStop = limitStop;`, ctx);
  assert.strictEqual(ctx.limitStop({ ename: "TimeLimit", evalue: "Stopped: this cell ran longer than the 1 hour limit set in Settings." }), "time");
  assert.strictEqual(ctx.limitStop({ ename: "KernelStopped", evalue: "Stopped: the notebook used more than 8 GB, the memory limit set in Settings. Its process was ended." }), "memory");
  assert.strictEqual(ctx.limitStop({ ename: "KernelStopped", evalue: "Stopped: this cell ran longer than the 15 minute limit set in Settings and didn't stop when interrupted" }), "time");
  assert.strictEqual(ctx.limitStop({ ename: "KernelStopped", evalue: "The notebook's Python process stopped (exit code 3)" }), null);
  assert.strictEqual(ctx.limitStop({ ename: "ValueError", evalue: "the memory limit set in Settings" }), null);
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
  vm.runInNewContext(`${code}; Object.assign(this, { proveNames, proveRulesFrom, proveKind, proveVerdict, proveDiffRows, proveDifference, pollRun });`, ctx);
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

function gitHelpers() {
  const src = fs.readFileSync(path.join(ui, "git.js"), "utf8");
  const code = src.slice(0, src.indexOf("// ---------- state ----------"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { gitSplitPath, gitPillText, gitSideBySide, gitCommitLabel, gitGroupRepos, nbData, nbDataText,
    platformChangeLines, platformStepText, PLATFORM_STATES });`, ctx);
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

test("a cost figure shows its classification, and an unknown one never reads as 0", () => {
  const src = fs.readFileSync(path.join(ui, "estate.js"), "utf8");
  const code = src.match(/const MEASURE_KIND = [\s\S]*?\n};\n/)[0] + src.match(/function measureValue\([\s\S]*?\n}\n/)[0]
    + src.match(/function money\([\s\S]*?\n}\n/)[0] + src.match(/function cuHours\([\s\S]*?\n}\n/)[0];
  const ctx = {};
  vm.runInNewContext(`${code}; this.MEASURE_KIND = MEASURE_KIND; this.measureValue = measureValue;`, ctx);
  assert.deepStrictEqual(Object.keys(ctx.MEASURE_KIND), ["measured", "estimated", "scenario", "unknown"]);
  assert.strictEqual(ctx.measureValue({ classification: "unknown", unit: "currency", value: null }, "USD"), "Not known");
  assert.strictEqual(ctx.measureValue(undefined, "USD"), "Not known");
  assert.strictEqual(ctx.measureValue({ classification: "estimated", unit: "cu_hours", value: 3 }), "3.00 CU h");
  assert.strictEqual(ctx.measureValue({ classification: "estimated", unit: "percent", value: 0.05 }), "<0.1%");
  assert.match(ctx.measureValue({ classification: "estimated", unit: "currency", value: 0.001 }, "USD"), /^under /);
  // Every figure in the Cost panel goes through measureFigure, which adds the badge.
  const body = src.match(/function costBody\([\s\S]*?\n}\n/)[0];
  assert.ok(!/money\(t\.(cost|saved)/.test(body) && !/cuHours\(t\./.test(body), "a raw total is shown without its classification");
  // FINOPS-005: savings come from Fabric runs of the same table, and runs with none are said to be left out.
  assert.ok(/never from how long it took here/.test(body) && !/Scenario: what runs on this PC/.test(body));
  assert.ok(/t\.uncalibrated_runs/.test(body) && !/scenario_cu/.test(body), "the rough scenario isn't shown as the saving");
});

test("a capacity reads as running only when Fabric said Active, and a read failure says why", () => {
  const src = fs.readFileSync(path.join(ui, "estate.js"), "utf8");
  const ctx = { h: (tag, attrs, ...kids) => ({ tag, attrs, text: kids.flat().join("") }) };
  vm.runInNewContext(`${src.match(/function capacityStatus\([\s\S]*?\n}\n/)[0]}; this.status = capacityStatus;`, ctx);
  assert.strictEqual(ctx.status({ access: "ok", status: "running" }).text, "Running");
  assert.strictEqual(ctx.status({ access: "ok", status: "not_running" }).text, "Not running");
  assert.strictEqual(ctx.status({ access: "ok", status: "unknown" }).text, "Unknown");
  const hidden = ctx.status({ access: "capacity_not_visible", access_text: "The capacity isn't visible to you", status: "running", action: "Ask" });
  assert.strictEqual(hidden.text, "The capacity isn't visible to you");
});

test("a usage file that reads two ways asks before importing, and a ready one says what it is", () => {
  const src = fs.readFileSync(path.join(ui, "estate.js"), "utf8");
  const flat = (kids) => kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false);
  const text = (n) => (typeof n === "object" && n ? flat(n.kids || []).map(text).join("") : String(n ?? ""));
  const ctx = {
    h: (tag, attrs, ...kids) => ({ tag, attrs, kids }),
    btn: (label, _icon, onclick) => ({ tag: "button", label, onclick, kids: [label] }),
    measureFigure: (title, m) => ({ tag: "fig", kids: [`${m.value} ${m.classification}`] }),
  };
  vm.runInNewContext(`${src.match(/function usagePreview\([\s\S]*?\n}\n/)[0]}; this.usagePreview = usagePreview;`, ctx);
  const chosen = [];
  const ask = ctx.usagePreview({ ready: false, problem: "The dates can be read two ways", provider: { label: "Capacity Metrics app export" },
    layout: { label: "Operations (Timepoint page)" }, needs: { option: "date_order", choices: { mdy: "Month first", dmy: "Day first" } } },
  (opt, v) => chosen.push([opt, v]));
  const buttons = flat(ask).flatMap(function walk(n) { return n && typeof n === "object" ? [n].concat(flat(n.kids || []).flatMap(walk)) : []; })
    .filter((n) => n.tag === "button");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(buttons.map((b) => b.label))), ["Month first", "Day first"]);
  buttons[1].onclick();
  assert.deepStrictEqual(chosen, [["date_order", "dmy"]]);
  const ready = flat(ctx.usagePreview({ ready: true, provider: { label: "Capacity Metrics app export" }, layout: { label: "Operations (Timepoint page)" },
    columns: { used: { cu: "Total CU (s)" }, not_stored: ["User"], ignored: ["User", "Timepoint CU (s)"] },
    rows_read: 4, to_import: 3, already_imported: 0, duplicates_in_file: 0, total: { value: 0.6, classification: "measured" },
    skipped: [{ reason: "No CU value", count: 1, lines: [5] }], notes: [] }, () => {})).map(text).join(" ");
  assert.match(ready, /Capacity Metrics app export, Operations \(Timepoint page\)/);
  assert.match(ready, /Not storedUser/);
  assert.match(ready, /Not usedTimepoint CU \(s\)/);
  assert.match(ready, /4 read · 3 to import/);
  assert.match(ready, /0.6 measured/);
  assert.match(ready, /No CU value: 1 \(line 5\)/);
});

test("billed capacity totals show each currency on its own and say which environments use the capacity", () => {
  const src = fs.readFileSync(path.join(ui, "estate.js"), "utf8");
  const flat = (kids) => kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false);
  const text = (n) => (typeof n === "object" && n ? flat(n.kids || []).map(text).join(" ") : String(n ?? ""));
  const ctx = {
    h: (tag, attrs, ...kids) => ({ tag, attrs, kids }),
    measureFigure: (title, m) => ({ tag: "fig", kids: [`${m.value} ${m.currency || m.unit} ${m.classification}`] }),
  };
  vm.runInNewContext(`${src.match(/function usageBilled\([\s\S]*?\n}\n/)[0]}; this.usageBilled = usageBilled;`, ctx);
  assert.strictEqual(ctx.usageBilled([], "USD"), null);
  const out = text(ctx.usageBilled([
    { capacity: "fabcapprod", environments: ["Prod"], days: 2, first_day: "2026-10-01", last_day: "2026-10-02",
      cu: { value: 96, unit: "cu_hours", classification: "measured" },
      cost: [{ value: 17.28, currency: "EUR", classification: "measured" }, { value: 20, currency: "USD", classification: "measured" }] },
    { capacity: "other", environments: [], days: 1, first_day: "2026-10-01", last_day: "2026-10-01", cu: null, cost: [] },
  ], "USD"));
  assert.match(out, /Used by Prod/);
  assert.match(out, /96 cu_hours measured/);
  assert.match(out, /17.28 EUR measured/);
  assert.match(out, /20 USD measured/);
  assert.match(out, /Not one of your environments' capacities/);
  assert.match(out, /None/);
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
  const page = fs.readFileSync(path.join(ui, "query.js"), "utf8");
  assert.ok(!page.includes('h("textarea"'), "no textarea editor");
  assert.match(page, /createSqlEditor\(/);
  assert.match(page, /dialect: sqlDialect\(t\.dialect\)/, "a tab saved without a dialect opens as Spark SQL");
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf("codemirror.js") < html.indexOf("sqleditor.js") && html.indexOf("sqleditor.js") < html.indexOf("query.js"));
  // app.js registers renderQuery when it loads, so query.js must come first (HD-056).
  assert.ok(html.indexOf('src="query.js"') < html.indexOf('src="app.js"'));
  const cm = fs.readFileSync(path.join(ui, "vendor", "codemirror", "codemirror.js"), "utf8");
  for (const name of ["SQLDialect", "MSSQL", "rectangularSelection", "crosshairCursor", "isolateHistory", "RangeSetBuilder",
    "linter", "lintGutter", "forceLinting", "StateEffect"]) {
    assert.match(cm, new RegExp(`${name}:\\(\\)=>`), `codemirror.js exports ${name}; rebuild it with npm run build`);
  }
});

test("Incidents is a Preview feature on Monitor", () => {
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const incidents = fs.readFileSync(path.join(ui, "incidents.js"), "utf8");
  assert.match(features, /incidents: "Incidents"/);
  assert.match(incidents, /id: "incidents", preview: true/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="incidents.js"><\/script>/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /\{ panel: "incidents", feature: "incidents"/);
});

test("Agents is a Preview feature on Monitor and only reads", () => {
  const agents = fs.readFileSync(path.join(ui, "agents.js"), "utf8");
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /agents: "Agents"/);
  assert.match(agents, /id: "agents", preview: true/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="incidents.js"><\/script>\s*<script src="agents.js"><\/script>/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /\{ panel: "agents", feature: "agents"/);
  assert.deepStrictEqual([...agents.matchAll(/rpc\("([a-z_]+)"/g)].map((m) => m[1]).filter((m) => m !== "agents_list"), []);
});

test("an agent past its promised report never shows as running", () => {
  const src = fs.readFileSync(path.join(ui, "agents.js"), "utf8");
  const ctx = { h: (tag, attrs, ...kids) => ({ tag, attrs, text: kids.flat().join("") }) };
  vm.runInNewContext(`${src.match(/const AG_STATE = \{[\s\S]*?\n};\n/)[0]}${src.match(/function agStatePill\([\s\S]*?\n}\n/)[0]}; this.pill = agStatePill;`, ctx);
  assert.strictEqual(ctx.pill({ state: "offline" }).text, "Stopped reporting");
  assert.strictEqual(ctx.pill({ state: "something new" }).text, "Stopped reporting");
  assert.strictEqual(ctx.pill({ state: "live" }).text, "Running");
});

test("Scheduled in two places names both places and never reads a silent older agent as every table", () => {
  const src = fs.readFileSync(path.join(ui, "agents.js"), "utf8");
  const text = (n) => (n == null ? "" : typeof n === "string" ? n : (n.kids || []).map(text).join(""));
  const ctx = { h: (tag, attrs, ...kids) => ({ tag, attrs, kids: kids.flat().filter((k) => k != null && k !== false) }),
    btn: (label) => label, envName: (e) => e.charAt(0).toUpperCase() + e.slice(1), monitorPage: {}, go: () => {} };
  vm.runInNewContext(`${src.match(/function agPlace\([\s\S]*?\n}\n/)[0]}${src.match(/function agOverlaps\([\s\S]*?\n}\n/)[0]}; this.f = agOverlaps;`, ctx);
  assert.strictEqual(ctx.f({ overlaps: [], unknown: [], pc_schedules_read: true }), null);
  const one = text(ctx.f({ overlaps: [{ env: "prod", entities: ["silver_orders"], schedules: [
    { place: "agent", agent: "etl-01", state: "live", schedule: "hourly", text: "Every 60 minutes" },
    { place: "pc", schedule: "p1", text: "Daily at 06:00" }] }], unknown: [] }));
  assert.match(one, /Scheduled in two places/);
  assert.match(one, /silver_orders in Prod runs on agent etl-01 \(hourly, every 60 minutes\) and on a schedule on this PC \(daily at 06:00\)\. Both write the same table\./);
  const old = text(ctx.f({ overlaps: [], unknown: [{ agent: "etl-old", schedule: "nightly" }] }));
  assert.doesNotMatch(old, /Scheduled in two places/);
  assert.match(old, /Can't tell what etl-old schedules/);
  assert.match(text(ctx.f({ overlaps: [], unknown: [], pc_schedules_read: false })), /couldn't be read/);
});

test("Queue is a Preview feature on Monitor that only reads and labels who owns each row", () => {
  const src = fs.readFileSync(path.join(ui, "queue.js"), "utf8");
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /queue: "Queue"/);
  assert.match(src, /id: "queue", preview: true/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="agents.js"><\/script>\s*<script src="queue.js"><\/script>/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /\{ panel: "queue", feature: "queue"/);
  assert.deepStrictEqual([...src.matchAll(/rpc\("([a-z_]+)"/g)].map((m) => m[1]), ["queue_list"]);
  const ctx = { h: (tag, attrs, ...kids) => ({ tag, attrs, text: kids.flat().join("") }) };
  vm.runInNewContext(`${src.match(/const QU_STATE = \{[\s\S]*?\n};\n/)[0]}${src.match(/function quStatePill\([\s\S]*?\n}\n/)[0]}; this.pill = quStatePill;`, ctx);
  assert.strictEqual(ctx.pill({ state: "something new" }).text, "Unknown");
  assert.strictEqual(ctx.pill({ state: "running" }).text, "Running");
  assert.match(src, /w\.kind === "agents"/);  // a shared schedule's row names every agent sharing it
  assert.match(fs.readFileSync(path.join(ui, "agents.js"), "utf8"), /s\.shared \? h\("span", \{ class: "pill-s neu"/);
});

test("Run as is a Preview Monitor segment that only reads who runs what", () => {
  const src = fs.readFileSync(path.join(ui, "runas.js"), "utf8");
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /runAs: "Run as"/);
  assert.match(src, /id: "runAs", preview: true/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="queue.js"><\/script>\s*<script src="runas.js"><\/script>/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /\{ panel: "runas", feature: "runAs"/);
  assert.deepStrictEqual([...src.matchAll(/rpc\("([a-z_]+)"/g)].map((m) => m[1]), ["run_as_list"]);
  const ctx = { h: (tag, attrs, ...kids) => ({ tag, attrs, text: kids.flat().join("") }) };
  vm.runInNewContext(`${src.match(/const RA_STATE = \{[\s\S]*?\n};\n/)[0]}${src.match(/function raDaysLeft\([\s\S]*?\n}\n/)[0]}${src.match(/function raPill\([\s\S]*?\n}\n/)[0]}; this.pill = raPill;`, ctx);
  assert.strictEqual(ctx.pill({ state: "something new" }).text, "Unknown");
  const soon = new Date(Date.now() + 25.5 * 86400000).toISOString();
  assert.strictEqual(ctx.pill({ state: "expiring", expires_at: soon }).text, "Expires in 25 days");
  assert.strictEqual(ctx.pill({ state: "failed" }).attrs.class, "pill-s fail");
});

test("an incident goes to the routes that match it, else the default, never the escalation route", () => {
  const src = fs.readFileSync(path.join(ui, "incidents.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function incRoutes\([\s\S]*?\n}\n/)[0]}; this.incRoutes = incRoutes;`, ctx);
  const cfg = { escalate_to: "on-call", routes: [{ name: "sales", teams: ["Sales"] }, { name: "on-call", criticality: ["critical"] }] };
  assert.strictEqual(ctx.incRoutes({ team: "sales", env: "prod" }, cfg), "sales");
  assert.match(ctx.incRoutes({ team: "finance", criticality: "critical" }, cfg), /default/);
});

test("a release record says where its files stood in Git in one line", () => {
  const src = fs.readFileSync(path.join(ui, "dataops.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function releaseCommitText\([\s\S]*?\n}\n/)[0]}; this.f = releaseCommitText;`, ctx);
  const sha = "8c1d0e7aa1b2c3d4e5f60718293a4b5c6d7e8f90";
  assert.strictEqual(ctx.f({ receipt_id: "x", commit: sha, branch: "main", dirty: false, pushed: true }), "commit 8c1d0e7 on main, pushed");
  assert.strictEqual(ctx.f({ receipt_id: "x", commit: sha, branch: null, dirty: false, pushed: false }), "commit 8c1d0e7, not pushed");
  assert.strictEqual(ctx.f({ receipt_id: "x", commit: sha, branch: "main", dirty: true, pushed: true }), "commit 8c1d0e7 on main, with uncommitted changes");
  assert.strictEqual(ctx.f({ receipt_id: "x", commit: null, dirty: null }), "not in Git");
  assert.strictEqual(ctx.f({ receipt_id: "x", commit: null, dirty: false }), "nothing committed yet");
  assert.strictEqual(ctx.f({ receipt_id: null }), "not recorded");
});

test("What's new lists the month's history and drops notes that only repeat its newest item", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function whatsNewItems\([\s\S]*?\n}\n/)[0]}; this.items = whatsNewItems;`, ctx);
  const history = [{ title: "SQL editor", pr: 118 }, { title: "Policies", pr: 119 }, { title: " " }, null];
  const preview = ctx.items("SQL editor", history);
  const plain = (v) => JSON.parse(JSON.stringify(v));
  assert.deepStrictEqual(plain([preview.lines, preview.items.map((i) => i.title)]), [[], ["SQL editor", "Policies"]]);
  const stable = ctx.items("- New SQL page\n- Policies for admins", history);
  assert.deepStrictEqual(plain(stable.lines), ["New SQL page", "Policies for admins"]);
  assert.deepStrictEqual(plain(ctx.items("Old build", undefined)), { lines: ["Old build"], items: [] });
  const dialog = src.slice(src.indexOf("function showWhatsNew"), src.indexOf("function whatsNewItems"));
  assert.match(dialog, /items\.slice\(0, 10\)/);
  assert.match(dialog, /https:\/\/github\.com\/KratosDataGod\/KratosForge/);
});

// The pure helpers at the top of debugging.js.
function dbgHelpers() {
  const src = fs.readFileSync(path.join(ui, "debugging.js"), "utf8");
  const code = src.slice(0, src.indexOf("// ---------- state ----------"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { dbgDiff, dbgHidden, dbgTrail });`, ctx);
  return ctx;
}

test("variable history says what a cell added, changed and removed, without keeping values", () => {
  const { dbgDiff } = dbgHelpers();
  const before = [{ name: "df", type: "DataFrame", size: "10 × 3", value: "a, b, c" }, { name: "n", type: "int", size: "", value: "1" },
    { name: "tmp", type: "list", size: "2", value: "[1, 2]" }, { name: "same", type: "str", size: "1", value: "'x'" }];
  const after = [{ name: "df", type: "DataFrame", size: "8 × 3", value: "a, b, c" }, { name: "n", type: "int", size: "", value: "2" },
    { name: "same", type: "str", size: "1", value: "'x'" }, { name: "secret", type: "str", size: "9", value: "hidden" }];
  const d = dbgDiff(before, after);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(d)), {
    added: [{ name: "secret", type: "str", size: "9" }],
    changed: [{ name: "df", type: "DataFrame", size: "8 × 3", was: "10 × 3" }, { name: "n", type: "int", size: "", was: "" }],
    removed: [{ name: "tmp" }],
  });
  assert.ok(!JSON.stringify(d).includes("value"), "values are not kept");
});

test("hidden state: a name only the kernel has is reported, with the deleted cell's code", () => {
  const { dbgHidden } = dbgHelpers();
  const g = { unresolved: [
    { name: "total", text: "total isn't defined by any cell (an import, a typo, or something defined dynamically)" },
    { name: "later", text: "later is defined only in a later cell (7)" },
    { name: "pl", text: "pl isn't defined by any cell" },
    { name: "quiet", text: "quiet isn't defined by any cell" }] };
  const setBy = { total: { cell_id: "gone", source: "total = len(events)" }, later: { cell_id: "c7", source: "later = 1" } };
  const cells = [{ cell_id: "c6", source: "print(total)" }, { cell_id: "c7", source: "later = 1" }];
  const out = JSON.parse(JSON.stringify(dbgHidden(g, ["total", "later", "quiet"], setBy, cells, ["quiet"])));
  assert.deepStrictEqual(out, [
    { name: "total", text: "total isn't defined by any cell in this notebook", source: "total = len(events)" },
    { name: "later", text: "later comes from a cell further down, which ran before this one", source: null },
  ]);
  // An edited cell that no longer has the code counts as gone.
  const edited = dbgHidden(g, ["total"], { total: { cell_id: "c6", source: "total = 3" } }, cells, []);
  assert.strictEqual(edited[0].source, "total = 3");
});

test("a watch keeps its last few values and marks a change", () => {
  const { dbgTrail } = dbgHelpers();
  let t = [];
  t = dbgTrail(t, 1, { value: "5" });
  t = dbgTrail(t, 2, { value: "5" });
  t = dbgTrail(t, 3, { error: "NameError: x" });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(t)), [
    { n: 1, value: "5", changed: false }, { n: 2, value: "5", changed: false }, { n: 3, value: "error: NameError: x", changed: true }]);
  for (let i = 4; i < 12; i += 1) t = dbgTrail(t, i, { value: String(i) });
  assert.strictEqual(t.length, 5);
  assert.strictEqual(t[t.length - 1].n, 11);
});

test("only Monitor > Runs shares the page with Fabric's hub, so Schedules and Operations get the whole page (#190)", () => {
});

test("Settings says plainly when sign-in isn't Microsoft's own, and how to go back", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function clientIdNote\([\s\S]*?\n}\n/)[0]}; this.f = clientIdNote;`, ctx);
  assert.strictEqual(ctx.f("default"), null);
  for (const source of ["policy", "environment", "user", "built_in"]) assert.match(ctx.f(source), /isn't using Microsoft's Fabric sign-in/);
  assert.match(ctx.f("environment"), /setx FABRIC_DESKTOP_CLIENT_ID ""/);
  assert.match(ctx.f("policy"), /only they can change it/);
  assert.match(src, /btn\("Use Microsoft's sign-in"/);
});

// FINOPS-004: what the risk panel shows up front, and when "Run on this PC" is offered.
test("the risk panel shows what matters, folds the rest and never offers this PC for a Fabric-only notebook", () => {
  const src = fs.readFileSync(path.join(ui, "risk.js"), "utf8");
  const flat = (k) => (Array.isArray(k) ? k.flatMap(flat) : [k]);
  const text = (n) => (typeof n === "object" && n ? flat(n.kids || []).map(text).join(" ") : String(n ?? ""));
  const ctx = { h: (tag, attrs, ...kids) => ({ tag, attrs, kids }), icon: () => null,
    measureBadge: (m) => ({ tag: "badge", kids: [m.classification] }),
    previewMarked: (id, node) => { node.kids.push(`Preview:${id}`); return node; } };
  vm.runInNewContext(`${src}; Object.assign(this, { riskPanel, riskLocalAlternative });`, ctx);
  const f = (check, outcome, title, measurement = null) => ({ check, outcome, title, detail: "", evidence: [], remedy: "", measurement });
  const blocked = { result: "blocked", label: "Blocked", summary: "This run can't start: paused", findings: [
    f("capacity", "blocked", "fabcapdev is paused"), f("local_alternative", "pass", "This PC could run it too"),
    f("history", "pass", "Usually takes about 4 min")] };
  const out = text(ctx.riskPanel(blocked));
  assert.match(out, /fabcapdev is paused/);
  assert.match(out, /This PC could run it too/);
  assert.match(out, /1 other check passed or isn't known/);
  assert.match(out, /Preview:risk/);
  assert.strictEqual(ctx.riskLocalAlternative(blocked), true);
  assert.strictEqual(ctx.riskLocalAlternative({ findings: [f("local_alternative", "pass", "Needs Fabric")] }), false);
  const pass = ctx.riskPanel({ result: "pass", label: "Pass", summary: "Nothing found", findings: [
    f("consumption", "pass", "The last run used about 0.42 CU hours", { classification: "measured" })] });
  assert.strictEqual(pass.tag, "details");
  assert.match(text(pass), /0.42 CU hours\s+measured/);
  assert.match(text(pass), /Preview:risk/);
  assert.strictEqual(ctx.riskPanel(null), null);
});

// FINOPS-004: the run gate stays quiet on a pass, offers no run on a block, and waits for the tick.
test("the risk gate passes quietly, never runs a blocked item and needs the tick when asked", async () => {
  const src = fs.readFileSync(path.join(ui, "risk.js"), "utf8");
  const node = (tag, attrs, ...kids) => ({ tag, attrs, kids, querySelector: () => node.box });
  let dialog = null;
  const acks = [];
  const toasts = [];
  const ctx = { h: node, icon: () => null, measureBadge: () => null, previewMarked: (_, n) => n,
    btn: (label, _i, onClick) => ({ label, onClick }), closeDialog: () => { dialog = null; },
    showDialog: (title, body, actions) => { dialog = { title, actions }; }, toast: (t) => toasts.push(t),
    attempt: (fn) => fn(), rpc: async (m, p) => (m === "risk_acknowledge" ? (acks.push(p), { ok: true }) : ctx.result) };
  vm.runInNewContext(`${src}; Object.assign(this, { riskGate });`, ctx);
  const target = { id: "notebook:nb", action: "run_in_fabric", title: "Run?", alt: { label: "Run on this PC", run: () => ctx.ran.push("pc") } };
  ctx.ran = [];
  const settle = () => new Promise((r) => setImmediate(r));
  const press = (label) => dialog.actions.find((a) => a.label === label).onClick();

  for (const result of ["pass", "unknown"]) {
    ctx.result = { result, findings: [] };
    assert.strictEqual(await ctx.riskGate(target), true);
    assert.strictEqual(dialog, null, `${result} shows no dialog`);
  }

  ctx.result = { id: "r1", result: "blocked", summary: "paused", findings: [{ check: "local_alternative", outcome: "pass", title: "This PC could run it too" }] };
  let gate = ctx.riskGate(target);
  await settle();
  assert.deepStrictEqual(Array.from(dialog.actions, (a) => a.label), ["Check again", "Run on this PC", "Close"]);
  press("Run on this PC");
  assert.strictEqual(await gate, false);
  assert.deepStrictEqual(ctx.ran, ["pc"]);

  ctx.result = { id: "r2", result: "warning", summary: "check", needs_acknowledgement: true, findings: [] };
  node.box = { checked: false };
  gate = ctx.riskGate(target);
  await settle();
  assert.deepStrictEqual(Array.from(dialog.actions, (a) => a.label), ["Cancel", "Run anyway"]);
  await press("Run anyway");
  assert.ok(dialog, "ran without the tick");
  assert.strictEqual(acks.length, 0);
  assert.match(toasts[0], /Tick/);
  node.box.checked = true;
  await press("Run anyway");
  assert.strictEqual(await gate, true);
  assert.deepStrictEqual(acks.map((a) => a.risk_id), ["r2"]);
});

test("only Monitor > Runs shares the page with Fabric's hub, so Schedules and Cost get the whole page (#190)", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(src, /const wide = monitorPage\.panel !== "runs";/);
});

// The pure helpers at the top of replay.js.
function replayHelpers() {
  const src = fs.readFileSync(path.join(ui, "replay.js"), "utf8");
  const code = src.slice(0, src.indexOf("// ---------- the button and the dialog ----------"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { replayCells, replayInputLines, replayEnvironmentLine });`, ctx);
  return ctx;
}

test("a replay bundle sends the cells up to the failed one, with only that cell's output", () => {
  const { replayCells } = replayHelpers();
  const cells = [{ type: "code", source: "a = 1", meta: {}, count: 1, outputs: [{ type: "text", text: "data" }], id: "x" },
    { type: "markdown", source: "# m", meta: {}, outputs: [] },
    { type: "code", source: "1/0", meta: {}, count: 2, outputs: [{ type: "error", ename: "ZeroDivisionError" }] },
    { type: "code", source: "later", meta: {}, count: 3, outputs: [] }];
  const out = JSON.parse(JSON.stringify(replayCells(cells, 2)));
  assert.strictEqual(out.length, 3);
  assert.deepStrictEqual(out[0], { type: "code", source: "a = 1", meta: {}, count: 1 });
  assert.strictEqual(out[1].count, null);
  assert.deepStrictEqual(out[2].outputs, [{ type: "error", ename: "ZeroDivisionError" }]);
});

test("the replay dialog lists inputs and the environment plainly", () => {
  const { replayInputLines, replayEnvironmentLine } = replayHelpers();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(replayInputLines({
    tables: [{ table: "bronze.events", version: 41 }, { table: "dbo.out", version: null, created: true }],
    files: [{ path: "Files/raw/fx.csv", sha256: "9f2c0a1b2c3d" }, { path: "Files/x.csv", sha256: null }] }))), [
    "bronze.events at Delta version 41", "dbo.out: created by the notebook, so a replay starts without it",
    "Files/raw/fx.csv (SHA-256 9f2c0a1b…)", "Files/x.csv (changed after it was read, so no SHA-256)"]);
  assert.strictEqual(replayEnvironmentLine({ python: "3.11.9", packages: { polars: "1.9.0" },
    kernel: { process: true, limits: { cell_seconds: 3600, memory_bytes: null } } }),
  "Python 3.11.9, polars 1.9.0, kernel in its own process, 60 min cell limit");
});

test("Save replay bundle is a Preview feature, offered only under a cell that failed on this PC", () => {
  const src = fs.readFileSync(path.join(ui, "replay.js"), "utf8");
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /replay: "Failure replay bundles"/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="debugging.js"><\/script>\s*<script src="replay.js"><\/script>/);
  assert.match(src, /cell\.status !== "error" \|\| nbWhere\(nb\)\.fabric/);
  assert.match(fs.readFileSync(path.join(ui, "notebook.js"), "utf8"), /replayOffer\(cell\)/);
});

test("a replay notebook shows its banner, and Create can open a bundle", () => {
  const src = fs.readFileSync(path.join(ui, "replay.js"), "utf8");
  assert.match(fs.readFileSync(path.join(ui, "notebook.js"), "utf8"), /replayBanner\(nb\), sbsPanel\(nb\)/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /preview\("replay", tile\("history", "Open replay bundle"/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /replay: nb\.replay \|\| null/);
  assert.match(fs.readFileSync(path.join(ui, "deps.js"), "utf8"), /replayRestarted\(\);/);
  // A table whose version is gone is replaced only by the person's choice.
  assert.match(src, /disabled: busy \|\| !!c\.blockers\.length \|\| unresolved/);
  const { replayBase64 } = (() => { const ctx = { btoa: (s) => Buffer.from(s, "binary").toString("base64") };
    vm.runInNewContext(`${src.slice(src.indexOf("function replayBase64"), src.indexOf("async function replayOpenDialog"))}; this.replayBase64 = replayBase64;`, ctx);
    return ctx; })();
  const bytes = new Uint8Array(70000).map((_, i) => i % 251);
  assert.strictEqual(replayBase64(bytes.buffer), Buffer.from(bytes).toString("base64"));
});

test("sign-in errors say what happened, and a cancelled sign-in says nothing (UX-003)", () => {
  const src = fs.readFileSync(path.join(ui, "activity.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function friendlyError\([\s\S]*?\n}\n/)[0]}; this.friendlyError = friendlyError;`, ctx);
  const say = (raw) => ctx.friendlyError(raw).text;
  assert.match(say("SignInError: AADSTS50076: Due to a configuration change made by your administrator"), /multi-factor/);
  assert.match(say("SignInError: AADSTS50079: The user is required to use multi-factor authentication"), /multi-factor/);
  assert.match(say("SignInError: AADSTS50020: User account from identity provider does not exist in tenant"), /work account/);
  assert.match(say("SignInError: AADSTS53003: Access has been blocked by Conditional Access policies"), /Conditional Access/);
  assert.match(say("SignInError: AADSTS50173: The provided grant has expired"), /expired/);
  assert.match(say("SignInError: AADSTS9999999: Something new"), /quote the code/);
  assert.match(say("SignInError: invalid_client: bad"), /didn't finish/);
  assert.doesNotMatch(say("SignInError: AADSTS50076: x"), /expired/);
  assert.deepStrictEqual({ ...ctx.friendlyError("SignInCancelled: cancelled: The sign-in window was closed before signing in.") }, { text: "", details: "", cancelled: true });
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(app, /!f\.text\.startsWith\(label\)/);  // never "Sign-in failed: Sign-in failed:"
});

// UX-001: the command bar's list held a null for every result that didn't start a group, and
// replaceChildren prints a null as the text "null".
test("the command bar lists group headers and results, never the text null", () => {
  const src = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const node = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(), addEventListener() {}, focus() {},
    setSelectionRange() {}, querySelector() { return null; }, value: attrs.value || "",
    replaceChildren(...k) { this.kids = k; } });
  const ctx = {
    h: node, icon: () => node("svg"), closeMenu() {}, state: {}, rpc: () => Promise.resolve([]),
    document: { getElementById: () => null, body: { append() {} } }, console,
  };
  vm.runInNewContext(src, ctx);
  ctx.paletteItems = () => [
    { group: "Pages", label: "Home", icon: "home", run() {} },
    { group: "Pages", label: "Create", icon: "create", run() {} },
    { group: "Actions", label: "New notebook", icon: "notebook", run() {} },
  ];
  let list = null;
  ctx.document.body.append = (box) => { list = box.kids[0].kids[1]; };
  ctx.openPalette();
  assert.ok(list, "the command bar's list was not drawn");
  assert.ok(list.kids.every((k) => k && typeof k === "object"), `list holds ${JSON.stringify(list.kids.map((k) => k && k.tag))}`);
  assert.deepStrictEqual(list.kids.map((k) => k.attrs.class.split(" ")[0]),
    ["palette-group", "palette-item", "palette-item", "palette-group", "palette-item"]);
});

// UX-002: every help link the app opens lands on a page and heading that exist.
test("every in-app help link opens an existing docs page and heading", () => {
  const docs = path.join(__dirname, "..", "..", "docs");
  // GitHub's heading anchors: lower case, punctuation dropped, spaces to hyphens, repeats numbered.
  const anchors = (page) => {
    const seen = new Map();
    const out = new Set();
    let fence = false;
    for (const line of fs.readFileSync(path.join(docs, page), "utf8").split("\n")) {
      if (/^\s*(```|~~~)/.test(line)) { fence = !fence; continue; }
      const m = !fence && /^#{1,6}\s+(.*?)\s*#*\s*$/.exec(line);
      if (!m) continue;
      const base = m[1].replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[`*]/g, "").trim().toLowerCase()
        .replace(/[^\p{L}\p{N}_\- ]/gu, "").replace(/ /g, "-");
      const n = seen.get(base) || 0;
      seen.set(base, n + 1);
      out.add(n ? `${base}-${n}` : base);
    }
    return out;
  };
  const links = [];
  for (const name of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    const src = fs.readFileSync(path.join(ui, name), "utf8");
    for (const [, a] of src.matchAll(/learnMore\("([^"]*)"/g)) links.push([name, `user-guide.md${a ? `#${a}` : ""}`]);
    for (const [, p] of src.matchAll(/open_doc", \{ page: "([^"]+)"/g)) links.push([name, p]);
  }
  const xray = fs.readFileSync(path.join(ui, "xray.js"), "utf8").match(/const XRAY_DOCS = \{([\s\S]*?)\};/)[1];
  for (const [, a] of xray.matchAll(/:\s*"([^"]+)"/g)) links.push(["xray.js", `xray-fixes.md#${a}`]);
  assert.ok(links.length > 20, `only ${links.length} help links found`);
  const broken = links.filter(([, link]) => {
    const [page, anchor] = link.split("#");
    if (!fs.existsSync(path.join(docs, page))) return true;
    return anchor && !anchors(page).has(anchor);
  }).map(([file, link]) => `${file}: ${link}`);
  assert.deepStrictEqual(broken, []);
});

// A notebook that can't be saved is never reported as saved, closed or replaced (QA-002).
test("a failed save says so and keeps the notebook open and dirty", async () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const fn = (name) => src.match(new RegExp(`(?:async )?function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  const toasts = [];
  const calls = [];
  const ctx = {
    toast: (text, error) => toasts.push([text, !!error]), friendlyError: (e) => ({ text: String(e.message) }),
    rpc: async (method) => { calls.push(method); if (method === "save_notebook") throw new Error("disk full"); return { name: "other", cells: [] }; },
    clearTimeout: () => {}, renderChrome: () => {}, setComputeCheck: () => {}, loadIntoState: () => { calls.push("load"); },
    go: () => {}, closeDialog: () => {}, markDirty: () => {}, saveTimer: null,
  };
  vm.runInNewContext(["attempt", "notebookPayload", "saveNotebook", "openNotebook", "newNotebook"].map(fn).join("\n") +
    "; this.saveNotebook = saveNotebook; this.openNotebook = openNotebook; this.newNotebook = newNotebook;", ctx);
  const nb = { name: "nb", cells: [], dirty: true, lakehouse: null };
  ctx.state = { notebook: nb };
  assert.strictEqual(await ctx.saveNotebook(), false);
  await ctx.openNotebook("other");
  await ctx.newNotebook();
  assert.strictEqual(ctx.state.notebook, nb);
  assert.strictEqual(nb.dirty, true);
  assert.deepStrictEqual(calls, ["save_notebook", "save_notebook", "save_notebook"]);
  assert.ok(toasts.length === 3 && toasts.every(([t, err]) => err && t.includes("disk full")));
  // Ctrl+S and closing the notebook use the result.
  assert.match(src, /saveNotebook\(\)\.then\(\(ok\) => \{ if \(ok\) toast\("Saved"\); \}\)/);
  assert.match(src, /state\.notebook\?\.dirty && !\(await saveNotebook\(\)\)\) return false;/);
  assert.match(src, /if \(await onClose\(\) === false\) return;/);
  // A save that works still clears dirty.
  ctx.rpc = async () => ({});
  assert.strictEqual(await ctx.saveNotebook(), true);
  assert.strictEqual(nb.dirty, false);
});

// UX-009: domains, the glossary and platform setup each have one home, on the Platform page.
test("domains, the glossary and platform setup are edited only on the Platform page", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const groups = meta.match(/const META_TAB_GROUPS = \[[\s\S]*?\n\];/)[0];
  assert.ok(!/"glossary"/.test(groups), "Data mesh still has a Glossary tab");
  const mesh = fs.readFileSync(path.join(ui, "mesh.js"), "utf8");
  const domainsTab = mesh.match(/async function renderMeshDomainsTab\([\s\S]*?\n}\n/)[0];
  for (const edit of ["domainDialog(", "importFabricDomains(", "platform_delete_domain"]) {
    assert.ok(!domainsTab.includes(edit), `Data mesh > Domains still calls ${edit}`);
  }
  assert.match(domainsTab, /goPlatform\("domains"\)/);
  assert.match(domainsTab, /goPlatform\("glossary"\)/);
  const platform = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  assert.match(platform, /importFabricDomains\(rerender\)/);
  assert.match(platform, /platformSection\(\)/);
  const settings = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  assert.ok(!settings.includes("platformSection("), "Settings still sets up the platform");
  assert.match(settings, /goPlatform\("settings"\)/);
});

// UX-009: a project's source systems come from the platform's one list when Preview is on.
test("a project reads source systems from the platform's list and can move its own there", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const tab = meta.match(/async function renderSourcesTab\([\s\S]*?\n}\n/)[0];
  assert.match(tab, /meta_keys", \{ env: m\.env, platform: previewShown\(\) \}/);
  assert.match(tab, /"Add a source this project reads"/);
  assert.match(tab, /"Move sources to the platform"/);
  assert.match(tab, /"New source system"/, "without the platform the project keeps its own list");
  assert.match(meta, /metaCall\("meta_source_read"/);
  assert.match(meta, /metaCall\("meta_sources_move"/);
  const server = fs.readFileSync(path.join(__dirname, "..", "..", "sidecar", "fabric_desktop", "server.py"), "utf8");
  for (const m of ["meta_source_read", "meta_sources_move"]) assert.ok(server.includes(`"${m}"`), `${m} isn't allowed by the engine`);
});

// The template gallery filters by group and search, and the review step names every table's contract.
test("the template gallery filters templates and the review states each contract", () => {
  const src = fs.readFileSync(path.join(ui, "wizard.js"), "utf8");
  const fn = (name) => src.match(new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  const ctx = {};
  vm.runInNewContext(`${fn("wizardShown")}${fn("wizardContract")}; this.shown = wizardShown; this.contract = wizardContract;`, ctx);
  const all = [{ id: "a", group: "load", title: "Incremental load", text: "With a watermark" },
    { id: "b", group: "model", title: "Date dimension", text: "A calendar table" }];
  assert.deepStrictEqual(ctx.shown(all, "all", "").map((t) => t.id), ["a", "b"]);
  assert.deepStrictEqual(ctx.shown(all, "model", "").map((t) => t.id), ["b"]);
  assert.deepStrictEqual(ctx.shown(all, "all", "CALENDAR  table").map((t) => t.id), ["b"]);
  assert.deepStrictEqual(ctx.shown(all, "load", "calendar"), []);
  assert.strictEqual(ctx.contract({}), "This notebook writes no tables, so there is no proof contract.");
  assert.strictEqual(ctx.contract({ "s.good": { keys: ["id"], ignore: ["loaded_at"] }, "s.bad": { keys: [], ignore: [] } }),
    "Proof contract saved in the notebook: rows of s.good are matched by id, ignoring loaded_at; rows of s.bad are compared whole.");
});

// PR-OPT-001b: Optimize takes the Fabric page's rail slot; Fabric's portal is a Workspaces tab.
test("Optimize is a rail page in Fabric's old slot, and the rail stays within its limit", () => {
  const sources = fs.readdirSync(ui).filter((f) => f.endsWith(".js")).map((f) => fs.readFileSync(path.join(ui, f), "utf8")).join("\n");
  const rails = [...sources.matchAll(/rail: \{ label: "([^"]+)"[^}]*\}/g)].filter((m) => !/bottom: true/.test(m[0])).map((m) => m[1]);
  assert.ok(rails.includes("Optimize"), rails.join(", "));
  assert.ok(!rails.includes("Fabric"), "Fabric is a Workspaces tab, not a rail page");
  assert.ok(rails.length <= 8, rails.join(", "));
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(app, /also: \["workspace", "lakehouse", "query", "fabric"\]/);
  assert.match(app, /label: "Fabric portal"/);  // still found with Ctrl+K
  const opt = fs.readFileSync(path.join(ui, "optimize.js"), "utf8");
  assert.match(opt, /id: "optimize", preview: true/);
  // Cost and savings is the Cost panel without Fabric's Monitoring hub underneath.
  assert.match(opt, /costPanel\(\)/);
  assert.ok(!/dockFabricView|fabric_view_show/.test(opt));
});

test("Optimize offers only the recommendation moves the engine allows", () => {
  const src = fs.readFileSync(path.join(ui, "optimize.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/const REC_MOVES = \{[\s\S]*?\n\};\n/)[0]}; this.REC_MOVES = REC_MOVES;`, ctx);
  const py = fs.readFileSync(path.join(__dirname, "..", "..", "sidecar", "fabric_desktop", "optimize", "shared.py"), "utf8");
  const block = py.match(/TRANSITIONS[^=]*= \{([\s\S]*?)\n\}/)[1];
  const allowed = {};
  for (const [, from, to] of block.matchAll(/\n\s+(\w+): (\{[^}]*\}|set\(\)),/g)) {
    allowed[from.toLowerCase()] = to === "set()" ? [] : to.slice(1, -1).split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  }
  for (const [from, moves] of Object.entries(ctx.REC_MOVES)) {
    for (const [to] of moves) assert.ok((allowed[from] || []).includes(to), `${from} -> ${to}`);
  }
  // Disconnecting and a new installation id both ask first.
  assert.match(src, /confirmDialog\(`Disconnect/);
  assert.match(src, /confirmDialog\("Use a new installation ID\?"/);
});

// The Schedule dialog opens on Fabric when the current environment runs on Fabric (it once read
// state.env, which is never set, so it always opened on This PC).
test("Schedule follows the current environment's Runs on", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const code = src.match(/async function scheduleNotebook\([\s\S]*?\n}\n/)[0];
  const opened = [];
  const ctx = { openScheduleDialog: (o) => opened.push(o.mode), publishWorkspaceName: () => "Sales Prod", nbLakehouse: (nb) => nb.lakehouse };
  vm.runInNewContext(`${code}; this.scheduleNotebook = scheduleNotebook;`, ctx);
  const environments = { dev: { compute: "local" }, prod: { compute: "fabric" } };
  for (const env of ["dev", "prod"]) {
    ctx.state = { notebook: { name: "nb", lakehouse: { lakehouse_id: "l" } }, meta: { env, project: { environments } } };
    ctx.scheduleNotebook();
  }
  ctx.state = { notebook: { name: "nb", compute: "local", lakehouse: { lakehouse_id: "l" } }, meta: { env: "prod", project: { environments } } };
  ctx.scheduleNotebook();
  assert.deepStrictEqual(opened, ["pc", "fabric", "pc"]);
});

test("with platform keys, no Keys button makes a project key table", () => {
  const src = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  assert.strictEqual([...src.matchAll(/newKeyTableDialog\(/g)].length, 2, "newKeyTableDialog is reached only through addKeyTableButton");
  const opened = [];
  const ctx = { btn: (label, _icon, onClick) => ({ label, onClick }),
    useKeyTableDialog: () => opened.push("use"), newKeyTableDialog: () => opened.push("new") };
  vm.runInNewContext(`${src.match(/function addKeyTableButton\([\s\S]*?\n}\n/)[0]}; this.addKeyTableButton = addKeyTableButton;`, ctx);
  const platform = ctx.addKeyTableButton({ central_key_tables: [{ name: "customer" }] }, () => {}, "x");
  const local = ctx.addKeyTableButton({ central_key_tables: null }, () => {}, "x");
  platform.onClick(); local.onClick();
  assert.deepStrictEqual([platform.label, local.label, ...opened], ["Use a key table", "New key table", "use", "new"]);
});

test("promote, sync keys, the variable library and quarantine replay carry the Preview tag", () => {
  const read = (f) => fs.readFileSync(path.join(ui, f), "utf8");
  const features = read("features.js");
  for (const [id, file] of [["promote", "meta.js"], ["syncKeys", "meta.js"], ["varlib", "estate.js"], ["quarantineReplay", "dataops.js"]]) {
    assert.match(features, new RegExp(`\\n  ${id}: "`), `${id} is in PREVIEW_FEATURES`);
    assert.ok(read(file).includes(`previewMarked("${id}", `), `${file} tags ${id}`);
  }
  // A marked step stays, and keeps its tag, when someone hides preview features.
  const ctx = { PREVIEW_FEATURES: { promote: "Promote" }, shown: true, previewTag: () => "tag" };
  vm.runInNewContext(`const previewShown = () => this.shown; ${features.match(/function previewMarked\([\s\S]*?\n}\n/)[0]}; this.previewMarked = previewMarked;`, ctx);
  const node = () => { const n = { kids: [], append: (x) => n.kids.push(x) }; return n; };
  assert.deepStrictEqual(ctx.previewMarked("promote", node()).kids, ["tag"]);
  ctx.shown = false;
  assert.deepStrictEqual(ctx.previewMarked("promote", node()).kids, ["tag"]);
  // and Settings names them as staying
  const always = JSON.parse(features.match(/const PREVIEW_ALWAYS_SHOWN = (\[[^\]]*\])/)[1].replace(/'/g, '"'));
  assert.deepStrictEqual(always, ["promote", "syncKeys", "varlib", "quarantineReplay", "risk"]);
  assert.ok(read("settings.js").includes("PREVIEW_ALWAYS_SHOWN"), "Settings explains the steps that stay");
});

// QA review of #173: only a cancelled sign-in is silent; an error with no message still says something.
test("attempt() stays quiet only for a cancelled sign-in, never for an empty error", async () => {
  const activity = fs.readFileSync(path.join(ui, "activity.js"), "utf8");
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const toasts = [];
  const ctx = { toast: (text, isError) => toasts.push([text, isError]) };
  vm.runInNewContext(`${activity.match(/function friendlyError\([\s\S]*?\n}\n/)[0]}
    ${app.match(/async function attempt\([\s\S]*?\n}\n/)[0]}; this.attempt = attempt;`, ctx);
  assert.strictEqual(await ctx.attempt(() => Promise.reject(new Error("")), "Save"), undefined);
  assert.strictEqual(await ctx.attempt(() => Promise.reject(""), "Save"), undefined);
  assert.strictEqual(toasts.length, 2);
  assert.match(toasts[0][0], /^Save: Something went wrong and no reason was given/);
  assert.strictEqual(toasts[0][1], true);
  await ctx.attempt(() => Promise.reject(new Error("SignInCancelled: cancelled: closed")), "Sign in");
  assert.strictEqual(toasts.length, 2);
  assert.strictEqual(await ctx.attempt(() => Promise.resolve(7), "Save"), 7);
});

test("a passed write test counts for a week, for the same account and tenant (#191)", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`const WRITE_PASS_DAYS = 7; ${src.match(/function savedWriteRow\([\s\S]*?\n}\n/)[0]}; this.f = savedWriteRow;`, ctx);
  const now = Date.parse("2026-10-20T12:00:00Z");
  const saved = { user: "ana@contoso.com", tenant: "t1", workspace_id: "w", lakehouse_id: "l", workspace: "Sales Dev", at: "2026-10-18T12:00:00Z" };
  assert.match(ctx.f(saved, "ana@contoso.com", "t1", now).text, /Sales Dev/);
  assert.strictEqual(ctx.f(saved, "bob@contoso.com", "t1", now), null);
  assert.strictEqual(ctx.f(saved, "ana@contoso.com", "t2", now), null);  // another tenant
  assert.strictEqual(ctx.f({ ...saved, lakehouse_id: "" }, "ana@contoso.com", "t1", now), null);
  assert.strictEqual(ctx.f(saved, "ana@contoso.com", "t1", Date.parse("2026-10-25T12:00:01Z")), null);  // expired
  assert.strictEqual(ctx.f({ ...saved, at: "nonsense" }, "ana@contoso.com", "t1", now), null);
  assert.strictEqual(ctx.f(null, "ana@contoso.com", "t1", now), null);
});

test("a failed or refused write test, and signing out, forget the saved pass (#191)", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const store = new Map();
  const localStorage = { setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k), getItem: (k) => store.get(k) ?? null };
  const ctx = { localStorage, state: { user: "ana@contoso.com", tenant: "t1" } };
  vm.runInNewContext(`${src.match(/function rememberWrite\([\s\S]*?\n}\n/)[0]}; this.f = rememberWrite;`, ctx);
  const target = { workspace_id: "w", lakehouse_id: "l", workspace: "Sales Dev" };
  ctx.f({ status: "ok" }, target);
  const saved = JSON.parse(store.get("fd.ready.write"));
  assert.deepStrictEqual([saved.user, saved.tenant, saved.lakehouse_id], ["ana@contoso.com", "t1", "l"]);
  ctx.f(undefined, target);  // refused or threw: attempt() returned nothing
  assert.strictEqual(store.has("fd.ready.write"), false);
  ctx.f({ status: "ok" }, target);
  ctx.f({ status: "fail" }, target);
  assert.strictEqual(store.has("fd.ready.write"), false);
  // The dialog reports every outcome, and the card keeps a way to test again.
  assert.match(src, /done\(result, \{ workspace_id: ws\.value, lakehouse_id: lhSelect\.value/);
  assert.doesNotMatch(src, /if \(result\) done\(result\)/);
  assert.match(src, /"Test again"/);
  const signOut = src.slice(src.indexOf("async function signOut"), src.indexOf("// ---------- account menu"));
  assert.match(signOut, /localStorage\.removeItem\("fd\.ready\.write"\)/);
});

test("Home doesn't say nothing needs attention when run history or Fabric jobs couldn't be read", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(src, /rpc\("meta_runs_all", \{ limit: 300 \}\)\.catch\(\(\) => \(\{ runs: \[\], unavailable: \{\}, failed: true \}\)\)/);
  assert.match(src, /rpc\("fabric_runs", \{ limit: 50, refresh: false \}\)\.catch\(\(\) => null\)/);
  assert.match(src, /unread\.length \? "Nothing found that needs your attention" : "Nothing needs your attention"/);
  assert.match(src, /couldn't check \$\{unread\.join\(", "\)\}/);
});

test("Monitor has three tabs, and Operations holds the workload views in the mockup's order (UX-010)", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const literal = src.match(/const MONITOR_OPS = (\[[\s\S]*?\n\]);/)[1];
  const ops = JSON.parse(JSON.stringify(vm.runInNewContext(literal)));
  assert.deepStrictEqual(ops.map((o) => o.label), ["Workloads", "Queue", "Agents", "Run as", "Incidents"]);
  // Every view keeps its panel id, so links and commands that open it still land there.
  assert.deepStrictEqual(ops.map((o) => o.panel), ["workloads", "queue", "agents", "runas", "incidents"]);
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  for (const o of ops) assert.ok(features.includes(`${o.feature}:`), `${o.feature} is a registered preview feature`);
  const seg = src.slice(src.indexOf('"aria-label": "Show" },'), src.indexOf('h("span", { class: "spacer" })', src.indexOf('"aria-label": "Show" },')));
  assert.deepStrictEqual([...seg.matchAll(/"(Runs|Schedules|Operations|Cost|Workloads|Queue|Agents|Run as|Incidents)"/g)].map((m) => m[1]),
    ["Runs", "Schedules", "Operations"]);
});

test("Monitor > Agents explains agents beside the list, with links to sections of agent.md (testing item 34)", () => {
  const src = fs.readFileSync(path.join(ui, "agents.js"), "utf8");
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "agent.md"), "utf8");
  const slug = (t) => t.toLowerCase().replace(/[^a-z0-9 -]/g, "").replace(/ /g, "-");
  const anchors = new Set([...doc.matchAll(/^#+ (.+)$/gm)].map((m) => slug(m[1])));
  const linked = [...src.matchAll(/agDoc\("([^"]+)"/g)].map((m) => m[1]);
  assert.ok(linked.length >= 6);
  for (const a of linked) assert.ok(anchors.has(a), `agent.md has a section #${a}`);
  assert.match(src, /return h\("div", \{ class: "mon-how" \}, withHowItWorks\(box, agGuide\(\)\)\);/);
});

test("deleting an entity, key table or source system and discarding quarantine rows use the danger confirm (UX-018)", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const dataops = fs.readFileSync(path.join(ui, "dataops.js"), "utf8");
  for (const [src, verb] of [[meta, "Delete"], [dataops, "Discard"]]) {
    const calls = src.match(new RegExp(`confirmDialog\\(\`${verb} [^\\n]*`, "g"));
    assert.ok(calls && calls.length, verb);
    for (const c of calls) assert.match(c, new RegExp(`"${verb}", \\{ danger: true \\}\\)`), c);
  }
});

test("Runs > Env says why a row has no environment, for this PC and for Fabric (testing item 32)", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const ctx = { ENV_LABEL: { dev: "Dev", prod: "Prod" }, h: (tag, attrs, text) => ({ tag, attrs, text }) };
  vm.runInNewContext(`${src.match(/function runEnvCell\([\s\S]*?\n}\n/)[0]}; this.runEnvCell = runEnvCell;`, ctx);
  assert.strictEqual(ctx.runEnvCell({ env: "prod", where: "fabric" }), "Prod");
  const pc = ctx.runEnvCell({ env: "", where: "local" });
  assert.strictEqual(pc.text, "None");
  assert.match(pc.attrs.title, /this PC's schedules/);
  const fabric = ctx.runEnvCell({ env: "", where: "fabric" });
  assert.strictEqual(fabric.text, "None");
  assert.match(fabric.attrs.title, /Fabric workspace/);
  assert.doesNotMatch(fabric.attrs.title, /this PC/);
});

test("Cost lives on Optimize only: Monitor has no Cost panel and old links to it open Optimize (testing item 36)", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const monitor = app.slice(app.indexOf("async function renderMonitor()"), app.indexOf("\n}\n", app.indexOf("async function renderMonitor()")));
  assert.ok(!monitor.includes("costPanel"), "Monitor doesn't draw the Cost panel");
  assert.ok(!monitor.includes("what running on this PC saved"), "no savings claim on Monitor (QA-008)");
  assert.match(monitor, /if \(monitorPage\.panel === "cost"\) \{ monitorPage\.panel = "runs"; optimizePage\.tab = "cost"; go\("optimize"\); return; \}/);
  for (const file of ["app.js", "xray.js", "optimize.js", "features.js"]) {
    const src = fs.readFileSync(path.join(ui, file), "utf8");
    assert.ok(!/monitorPage\.panel = "cost"/.test(src), `${file} sends nobody to Monitor > Cost`);
  }
  assert.match(fs.readFileSync(path.join(ui, "xray.js"), "utf8"), /btn\("See cost", null, \(\) => \{ optimizePage\.tab = "cost"; go\("optimize"\); \}/);
});

// #193: messages from the engine that send people to a screen name one that exists.
test("engine messages point to Data mesh and Platform tabs that exist", () => {
  const read = (f) => fs.readFileSync(path.join(ui, f), "utf8");
  const groups = vm.runInNewContext(`${/const META_TAB_GROUPS = \[[\s\S]*?\n\];/.exec(read("meta.js"))[0].replace("const ", "")}; META_TAB_GROUPS`);
  const platform = vm.runInNewContext(`${/const PLATFORM_TABS = \[[\s\S]*?\n\];/.exec(read("platform.js"))[0].replace("const ", "")}; PLATFORM_TABS`);
  const mesh = new Set();
  for (const [, group, tabs] of groups) for (const [, tab] of tabs) mesh.add(`${group} > ${tab}`);
  const plat = new Set(platform.map(([, label]) => label));
  const engine = path.join(__dirname, "..", "..", "sidecar", "fabric_desktop");
  const files = [];
  const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (e.isDirectory()) walk(path.join(d, e.name)); else if (e.name.endsWith(".py")) files.push(path.join(d, e.name)); } };
  walk(engine);
  const bad = [];
  for (const f of files) {
    const src = fs.readFileSync(f, "utf8");
    if (/\bMetadata > /.test(src)) bad.push(`${path.basename(f)}: "Metadata >" is now Data mesh`);
    for (const m of src.matchAll(/Data mesh > ([A-Z][\w ]*?) > ([A-Z][\w ]*?)(?=[).,"'\n]| first| and promote\b)/g)) {
      const where = `${m[1]} > ${m[2]}`;
      if (![...mesh].some((t) => t.startsWith(where))) bad.push(`${path.basename(f)}: Data mesh > ${where}`);
    }
    for (const m of src.matchAll(/Platform > ([A-Z][\w ]*?)(?=[).,"'\n])/g)) if (!plat.has(m[1])) bad.push(`${path.basename(f)}: Platform > ${m[1]}`);
  }
  assert.deepStrictEqual(bad, []);
});

// UX-018: a confirm that deletes, discards, replaces or removes something draws its button red
// and focuses Cancel, so Enter doesn't lose data.
test("confirms that delete or overwrite something pass danger", () => {
  const missing = [];
  for (const name of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    const lines = fs.readFileSync(path.join(ui, name), "utf8").split("\n");
    lines.forEach((line, i) => {
      const m = line.match(/confirmDialog\(\s*[`"](Delete|Discard|Replace|Remove|Revoke|Undo the pull|Repoint)\b/);
      if (!m) return;
      let end = i;
      while (end < lines.length - 1 && !lines[end].trimEnd().endsWith(";")) end++;
      if (!lines.slice(i, end + 1).join("\n").includes("danger: true")) missing.push(`${name}:${i + 1}`);
    });
  }
  assert.deepStrictEqual(missing, []);
});

// QA-032: X-ray's Optimize asks first, says it runs in Fabric and uses capacity, and shows the risk check.
test("Estate X-ray's Optimize confirms with the risk check before starting a Fabric job", () => {
  const xray = fs.readFileSync(path.join(ui, "xray.js"), "utf8");
  const dialog = /async function optimizeDialog\([\s\S]*?\n\}/.exec(xray)[0];
  assert.match(dialog, /showDialog\(`Optimize \$\{label\}\?`/);
  assert.match(dialog, /runs in Fabric[\s\S]*uses capacity/);
  assert.match(dialog, /rpc\("xray_optimize_check"[\s\S]*riskPanel\(p\.risk/);
  assert.match(dialog, /result === "blocked"/);
  assert.ok(dialog.indexOf('rpc("xray_optimize_check"') > -1 && !/optimize: \(\) => btn\("Optimize", "sync", async/.test(xray),
    "the line's button opens the dialog instead of starting the job");
});

test("Monitor > Workloads and Agents carry the shared How this works panel, each linked to its guide section (testing item 18)", () => {
  const guide = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "user-guide.md"), "utf8");
  const slug = (t) => t.toLowerCase().replace(/[^a-z0-9 -]/g, "").replace(/ /g, "-");
  const anchors = new Set([...guide.matchAll(/^#+ (.+)$/gm)].map((m) => slug(m[1])));
  const seen = {};
  const ctx = {
    h: (tag, attrs, ...children) => ({ tag, attrs, children }),
    howItWorks: (id, parts) => { seen[id] = parts; return { panel: id }; },
    withHowItWorks: (main, panel) => ({ main, panel }),
    agDoc: (anchor) => ({ doc: anchor }),
    loadWorkloads: () => {}, loadAgents: () => {},
  };
  const grab = (file, name) => {
    const src = fs.readFileSync(path.join(ui, file), "utf8");
    return src.match(new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  };
  vm.runInNewContext(`${grab("workloads.js", "workloadsPanel")}${grab("agents.js", "agentsPanel")}${grab("agents.js", "agGuide")}
    this.out = [workloadsPanel(), agentsPanel()];`, ctx);
  for (const [i, id] of [[0, "monitor.workloads"], [1, "monitor.agents"]]) {
    assert.deepStrictEqual(ctx.out[i].children[0].panel, { panel: id }, `${id} sits beside its list`);
    const p = seen[id];
    for (const k of ["reads", "changes", "results"]) assert.ok(String(p[k]).length > 20, `${id} says ${k}`);
    assert.ok(anchors.has(p.anchor), `user-guide.md has #${p.anchor}`);
  }
  assert.match(seen["monitor.agents"].changes, /^Nothing\./);
  assert.match(seen["monitor.workloads"].reads, /Never your whole tenant/);
});

// Testing item 18: Estate X-ray and Workspaces > Fabric portal say how they work, naming every write.
test("Estate X-ray and the Fabric portal tab have How this works panels that name their writes", () => {
  const xray = fs.readFileSync(path.join(ui, "xray.js"), "utf8");
  const how = /function xrayHow\(\) \{[\s\S]*?\n\}/.exec(xray)[0];
  for (const write of ["Optimize", "Vacuum", "Adopt"]) assert.match(how, new RegExp(`${write} `));
  assert.match(how, /only when you confirm/);
  assert.match(how, /each asks first/);
  assert.ok((xray.match(/xrayHow\(\)/g) || []).length >= 3, "the panel shows before and after a scan");
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const fabric = app.slice(app.indexOf("function renderFabric()"), app.indexOf("function openWorkspace("));
  assert.match(fabric, /withHowItWorks\(/);
  assert.match(fabric, /howItWorks\("workspaces\.fabric"[\s\S]*changes: "Nothing by itself/);
});

// Testing item 18: Workspaces, the OneLake catalog and Data products each say how they work, naming every write.
test("Workspaces, the catalog and data products have a How this works panel that names their writes", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const platform = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  const panel = (src, id) => src.match(new RegExp(`howItWorks\\("${id.replace(".", "\\.")}", \\{[\\s\\S]*?\\n    \\}\\)`))?.[0] || "";
  const ws = panel(app, "workspaces");
  assert.match(ws, /changes: "Nothing in Fabric or OneLake. Open in notebook[^"]*makes a new notebook on this PC/);
  const catalog = panel(app, "onelake.catalog");
  assert.match(catalog, /changes: "Nothing in Fabric or OneLake/);
  assert.match(catalog, /Open in notebook makes a new notebook on this PC/);
  const products = panel(platform, "onelake.products");
  assert.match(products, /records your project as a reader/);
  assert.match(products, /adds a request/);
  for (const p of [ws, panel(app, "onelake.catalog"), products]) assert.match(p, /anchor: "/);
});

test("repositories are grouped by what they hold, unsorted ones last", () => {
  const { gitGroupRepos } = gitHelpers();
  const kinds = { notebooks: { label: "Notebooks" }, project: { label: "Data mesh project" }, platform: { label: "Platform" } };
  const groups = gitGroupRepos([{ name: "a", kind: "platform" }, { name: "b", kind: null }, { name: "c", kind: "notebooks" },
    { name: "d", kind: "platform" }], kinds);
  assert.strictEqual(JSON.stringify(groups.map((g) => [g.label, g.repos.map((r) => r.name).join()])),
    JSON.stringify([["Notebooks", "c"], ["Platform", "a,d"], ["Not yet sorted", "b"]]));
  assert.strictEqual(gitGroupRepos([], kinds).length, 0);
});

// QA-005: a shortcut rules check that fails blocks instead of passing, and a partial reader list says so.
test("a failed shortcut rules check isn't swallowed and an incomplete reader list says so", () => {
  const shortcuts = fs.readFileSync(path.join(ui, "shortcuts.js"), "utf8");
  assert.ok(!/shortcut_preflight[\s\S]{0,200}\.catch\(\(\) => \(\{ blocked: \[\]/.test(shortcuts), "a failed preflight reads as no rules");
  assert.match(shortcuts, /unchecked: true \}\)\)/);
  assert.match(shortcuts, /primary\.disabled = !!verdict\.unchecked/);
  const estate = fs.readFileSync(path.join(ui, "estate.js"), "utf8");
  assert.match(estate, /res\.partial \?/);
});

// The Wolf Forge emblem is the app's logo: header, favicon and the Windows icon set.
test("the header and favicon use the Wolf Forge emblem and the icon set covers small sizes", () => {
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.match(html, /<div class="brand">\s*<img src="wolf-forge-emblem\.png"/);
  assert.match(html, /rel="icon" href="wolf-forge-emblem-32\.png"/);
  assert.ok(!html.includes("kratosbi-mark.svg"));
  for (const f of ["wolf-forge-emblem.png", "wolf-forge-emblem-32.png"]) assert.ok(fs.existsSync(path.join(ui, f)), f);
  const ico = fs.readFileSync(path.join(__dirname, "..", "src-tauri", "icons", "icon.ico"));
  const sizes = Array.from({ length: ico.readUInt16LE(4) }, (_, i) => ico[6 + i * 16] || 256);
  for (const s of [16, 24, 32, 48, 256]) assert.ok(sizes.includes(s), `icon.ico has ${s}px`);
});

// Item 37, stage 2: a notebook in a notebooks repository follows the environment's location.
test("a notebook's data: its own lakehouse first, then its repository's location for the environment", () => {
  const { nbData, nbDataText } = gitHelpers();
  const own = { lakehouse_id: "own", name: "LH_Mine", workspace_id: "w" };
  const testRef = { lakehouse_id: "lt", name: "LH_Silver", workspace_id: "wt", workspace_name: "Sales Test" };
  const nb = { place: { root: "/r", label: "sales-notebooks" },
    locations: { envs: { dev: { this_pc: true, lakehouse: null }, test: { lakehouse: testRef }, prod: null } } };
  const pick = (env, n = nb) => { const d = nbData(n, env); return [d.from, d.ref?.lakehouse_id || null, nbDataText(d)]; };
  assert.strictEqual(JSON.stringify(pick("test")), JSON.stringify(["locations", "lt", "Data: LH_Silver"]));
  assert.strictEqual(JSON.stringify(pick("dev")), JSON.stringify(["this_pc", null, "Data: nothing attached in Dev"]));
  assert.strictEqual(JSON.stringify(pick("prod")), JSON.stringify(["unset", null, "No location for Prod"]));
  assert.strictEqual(JSON.stringify(pick("test", { ...nb, lakehouse: own })), JSON.stringify(["notebook", "own", "Data: LH_Mine (this notebook)"]));
  assert.strictEqual(nbData({ name: "home" }, "test").from, null, "notebooks in the app's own folder are unchanged");
});

test("the location's lakehouse is used to run and publish but never saved into the notebook", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const nbjs = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nbjs, /rpc\("start_cell", \{ code: cell\.source, lakehouse_ref: nbLakehouse\(state\.notebook\)/);
  assert.match(app.match(/function notebookPayload\(\)[\s\S]*?\n}\n/)[0], /lakehouse_ref: state\.notebook\.lakehouse,/);
  for (const call of ["publish_notebook", "run_notebook_in_fabric", "add_notebook_schedule"]) {
    assert.match(app, new RegExp(`rpc\\("${call}", (\\{ \\.\\.\\.)?publishPayload\\(\\)`));
  }
});

// Fabric Desktop is now KratosForge: every name a person sees changed; identifiers that installed
// copies, notebooks and workspaces depend on did not. The announcement shows once, before What's new.
test("the app is called KratosForge everywhere people read it, and identifiers stay", () => {
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.match(html, /<title>KratosForge<\/title>/);
  assert.match(html, /<img class="brand-name" src="kratosforge-lettering\.png"[^>]*alt="KratosForge">/);
  assert.ok(!/by <b>KratosBI<\/b>/.test(html));
  assert.ok(fs.existsSync(path.join(ui, "kratosforge-lettering.png")));
  for (const f of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    assert.ok(!fs.readFileSync(path.join(ui, f), "utf8").includes("Fabric Desktop by KratosBI"), f);
  }
  const conf = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "src-tauri", "tauri.conf.json"), "utf8"));
  assert.strictEqual(conf.app.windows[0].title, "KratosForge");
  assert.strictEqual(conf.identifier, "com.kratosbi.fabricdesktop", "a new identifier would stop installed copies updating");
  assert.strictEqual(conf.productName, "Fabric Desktop", "the product name sets the program's file and folder");
  const settings = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  assert.match(settings, /const RENAME_SEEN = "fd\.rename\.kratosforge\.seen"/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /showRenameSplash\(\(\) => showWhatsNew\(\)\) \|\| showUpdateSplash\(\);/);
  assert.match(settings, /"aria-labelledby": "splash-title", "aria-describedby": "splash-text"/);
  assert.match(settings, /label: "About the new name: KratosForge"/);
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\n  \.overlay\.splash/);
});

test("the Platform tab says what differs between Git and the platform, and what a deploy will do", () => {
  const { platformChangeLines, platformStepText, PLATFORM_STATES } = gitHelpers();
  assert.strictEqual(PLATFORM_STATES.only_deployed, "removed in Git");
  assert.strictEqual(JSON.stringify(platformChangeLines({ kind: "domains", changes: [
    { field: "description", git: "Selling things", deployed: "Selling" }, { field: "owners", git: ["a", "b"], deployed: [] }] })),
  JSON.stringify(['description: Git "Selling things", deployed "Selling"', "owners: Git a, b, deployed none"]));
  assert.strictEqual(JSON.stringify(platformChangeLines({ kind: "admins", changes: [{ field: "admins", git: ["ana"], deployed: [] }] })),
    JSON.stringify(["Git adds ana"]));
  const labels = { domains: "Domain", policies: "Policy" };
  assert.strictEqual(platformStepText({ kind: "domains", id: "sales", action: "create" }, labels), "Domain sales: create");
  assert.strictEqual(platformStepText({ kind: "policies", id: "names", action: "update", what: "publish it as a new version" }, labels),
    "Policy names: publish it as a new version");
});

// FINOPS-004 follow-up: Data mesh Run all reads the risk in one batch and starts only what can.
test("Run all checks the risk once and starts only entities that aren't blocked or waiting on one", async () => {
  const src = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const calls = [];
  let dialog = null;
  const ctx = { ENV_LABEL: { dev: "Dev" }, h: (tag, attrs, ...kids) => ({ tag, attrs, kids }),
    btn: (label, icon, onclick) => ({ label, onclick }), riskAckBox: () => null, riskPanel: (r) => r.entity,
    riskProceed: async () => true, closeDialog: () => {}, showDialog: (title, body, buttons) => { dialog = { title, body, buttons }; } };
  vm.runInNewContext(`${src.match(/async function metaRiskGate\([\s\S]*?\n}\n/)[0]}; this.gate = metaRiskGate;`, ctx);
  const p = { project: { name: "proj" } };
  const check = (entity, result) => ({ entity, result, note: "Reports only" });
  ctx.rpc = async (method, args) => { calls.push([method, args]); return ctx.answer; };
  ctx.answer = { checks: [check("a", "pass"), check("b", "pass")], blocked: [], held: {}, run: ["a", "b"] };
  assert.strictEqual(await ctx.gate(p, null, "dev", "all 2 entities"), true);
  assert.strictEqual(JSON.stringify(calls), JSON.stringify([["meta_risk_gate", { names: null, env: "dev" }]]));
  assert.strictEqual(dialog, null);
  ctx.answer = { checks: [check("a", "blocked"), check("b", "pass"), check("c", "warning")], blocked: ["a"],
    held: { b: ["a"] }, run: ["c"] };
  const asked = ctx.gate(p, null, "dev", "all 3 entities");
  await new Promise((r) => setImmediate(r));
  const flat = (k) => (Array.isArray(k) ? k.flatMap(flat) : [k]);
  const text = JSON.stringify(flat(dialog.body.kids));
  assert.match(text, /b \(reads a\)/);
  assert.match(text, /2 of 3 won't start; the other 1 run/);
  assert.strictEqual(dialog.buttons.map((b) => b.label).join("|"), "Cancel|Run the other 1");
  await dialog.buttons[1].onclick();
  assert.strictEqual(JSON.stringify(await asked), '["c"]');
  ctx.answer = { checks: [check("a", "blocked")], blocked: ["a"], held: {}, run: [] };
  const none = ctx.gate(p, ["a"], "dev", "a");
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(dialog.buttons.map((b) => b.label).join("|"), "Close");
  dialog.buttons[0].onclick();
  assert.strictEqual(await none, false);
  ctx.rpc = async () => { throw new Error("engine busy"); };
  assert.strictEqual(await ctx.gate(p, null, "dev", "all"), true);  // the check is advice
  assert.match(src, /meta_run", \{ names: only, env/);
  assert.match(src, /if \(only !== names\) label = only\.join\(", "\);/);  // the toast and tray name only those
});

// Item 25: one keys database for Dev, Test and Prod. Moving keys in always shows what would move
// first; the write is a second call with approve, and cancelling one that stopped is a danger confirm.
test("moving keys into one database previews first, and cancelling it is a danger confirm", () => {
  const mesh = fs.readFileSync(path.join(ui, "mesh.js"), "utf8");
  const move = mesh.match(/async function moveKeysIn\(\)[\s\S]*?\n}\n/)[0];
  assert.match(move, /^async function moveKeysIn\(\) \{\n  const plan = await attempt\(\(\) => rpc\("platform_share_keys", \{\}\)/);
  assert.strictEqual((move.match(/approve: true/g) || []).length, 1);
  assert.ok(move.indexOf('btn("Move keys in"') < move.indexOf("approve: true"), "the write happens only from the dialog's button");
  assert.match(move, /Re-run Dev and Test/);
  const cancel = mesh.match(/async function cancelKeysMove\(\)[\s\S]*?\n}\n/)[0];
  assert.match(cancel, /confirmDialog\("Cancel the move\?", [^)]*\), "Cancel the move", \{ danger: true \}\)/);
  assert.ok(cancel.indexOf("danger: true") < cancel.indexOf("approve: true"));
  assert.match(mesh, /\.\.\.MESH_ENVS\.map\(row\), keysRow\(s, policy\)\)/);
  const platform = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  assert.match(platform, /!\(p\.tab === "keys" && p\.keysShared\) \? envSeg : null/);
  assert.match(platform, /k\.shared \? "Key tables in SQL_Keys"/);
  const how = platform.match(/howItWorks\("platform\.keys", \{[\s\S]*?\n  \}\)/)?.[0] || "";
  for (const write of [/SQL_Keys and UDF_Keys, which uses Fabric capacity/, /copies every key table/, /new numbers/, /closes each environment's old key tables/, /removes the copied tables/]) {
    assert.match(how, write);
  }
  assert.doesNotMatch(mesh, /Moved \$\{k\.moved_at \? when\(k\.moved_at\) : ""\}/, "no bare 'Moved .' when the time is missing");
});

// Validate notebook (validate.js): the headline, the count chips, what changed since a result,
// and which tidy-ups Apply makes.
function validateHelpers() {
  const src = fs.readFileSync(path.join(ui, "validate.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src}; Object.assign(this, { validateHeadline, validateShown, validateSnapshot, validateChanged, cleanupChosen, cleanupState, cleanupUndoable });`, ctx);
  return ctx;
}

test("Validate says how ready the notebook is, and the chips filter by severity", () => {
  const { validateHeadline, validateShown } = validateHelpers();
  assert.deepStrictEqual([...validateHeadline({ verdict: "not_ready", counts: { error: 1, warning: 2 } })], ["fail", "Not ready: 1 error, 2 warnings"]);
  assert.deepStrictEqual([...validateHeadline({ verdict: "ready", counts: {} })], ["pass", "Ready: no errors or warnings"]);
  assert.deepStrictEqual([...validateHeadline({ verdict: "checked", counts: { warning: 1 } })], ["neu", "Code checks: 1 warning"]);
  const f = [{ severity: "error" }, { severity: "info" }, { severity: "error" }];
  assert.strictEqual(validateShown(f, "error").length, 2);
  assert.strictEqual(validateShown(f, "all").length, 3);
});

test("Validate marks a result out of date when a cell changes, is added or is deleted", () => {
  const { validateSnapshot, validateChanged } = validateHelpers();
  const cells = [{ cell_id: "a", type: "code", source: "x = 1" }, { cell_id: "b", type: "markdown", source: "# hi" }];
  const snap = validateSnapshot(cells);
  assert.deepStrictEqual([...validateChanged(snap, cells)], []);
  assert.deepStrictEqual([...validateChanged(snap, [{ ...cells[0], source: "x = 2" }, { ...cells[1], source: "# edited" }])], [1]);
  assert.deepStrictEqual([...validateChanged(snap, [...cells, { cell_id: "c", type: "code", source: "" }])], [3]);
  assert.deepStrictEqual([...validateChanged(snap, [cells[1]])], [0]);
});

test("Clean up applies the ticked tidy-ups, starting from the ones ticked at first", () => {
  const { cleanupChosen } = validateHelpers();
  const plan = { tidy: [{ id: "unused_imports", on: true }, { id: "print_lines", on: false }] };
  assert.deepStrictEqual([...cleanupChosen(plan, {})], ["unused_imports"]);
  assert.deepStrictEqual([...cleanupChosen(plan, { unused_imports: false, print_lines: true })], ["print_lines"]);
  const v = fs.readFileSync(path.join(ui, "validate.js"), "utf8");
  assert.match(v, /preview\("validate", btn\("Validate"/);
  assert.match(v, /v\.undo = \{ cells: before, after: cleanupState\(nb\.cells\) \};/);
  assert.match(v, /if \(cell\.meta\?\.frozen\) continue;/);
  assert.match(v, /changes: "Code checks and suggestions change nothing\. A clean run writes tables only to Files\/_fabric_desktop\/proofs\/validate-<id>\/pc/);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.strictEqual(nb.match(/validateToggle\(nb\), proveToggle\(nb\)/g).length, 2);
  assert.match(nb, /provePanel\(nb\), validatePanel\(nb\)\)/);
});

test("Clean up's Undo is offered only while the cells are exactly what Apply made", () => {
  const { cleanupState, cleanupUndoable } = validateHelpers();
  const cells = [{ cell_id: "a", type: "code", source: "x = 1", meta: {} }, { cell_id: "b", type: "code", source: "y = 2", meta: {} }];
  const undo = { cells: [], after: cleanupState(cells) };
  assert.strictEqual(cleanupUndoable(undo, cells), true);
  assert.strictEqual(cleanupUndoable(undo, [cells[0], { ...cells[1], source: "y = 3" }]), false);  // an edit
  assert.strictEqual(cleanupUndoable(undo, [cells[1], cells[0]]), false);  // a move
  assert.strictEqual(cleanupUndoable(undo, [cells[0], { ...cells[1], meta: { frozen: true } }]), false);
  assert.strictEqual(cleanupUndoable(null, cells), false);
});

test("Schedules warns when this PC's schedules file was moved aside (QA-011)", () => {
  const s = fs.readFileSync(path.join(ui, "schedules.js"), "utf8");
  assert.match(s, /h\("h3", \{ class: "schedule-head" \}, "On this PC"\),\n    setAsideNotice\(res\.pc_set_aside\),/);
  assert.match(s, /look in Task Scheduler under /);
});

test("A metadata project that can't be read is said, not shown as no runs or nothing late (QA-027)", () => {
  const a = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(a, /if \(meta\.project_error\) unread\.unshift\("the metadata project"\);/);
  assert.match(a, /meta\.project_error \? `Metadata runs not shown: the metadata project couldn't be read \(\$\{meta\.project_error\}\)` : ""/);
  const d = fs.readFileSync(path.join(ui, "dataops.js"), "utf8");
  assert.match(d, /if \(res\.project_error\) logActivity\(\{ id: "fresh:project-error", status: "error", title: "Freshness not checked"/);
});

// HD-035: Prove and side-by-side status polling stops on finish, an unknown run and errors.
function pollHarness(answers, { limit } = {}) {
  const { pollRun } = proveHelpers();
  const waits = [];
  let calls = 0;
  const run = pollRun(async () => {
    const a = answers[calls++];
    if (a instanceof Error || typeof a === "string") throw a;  // Tauri rejects with the engine's text
    return a;
  }, { limit, onStatus: (now) => now.state === "running", sleep: async (ms) => { waits.push(ms); } });
  return run.then((end) => ({ end, waits, calls: () => calls }));
}

test("status polling stops when the run finishes", async () => {
  const { end, calls } = await pollHarness([{ state: "running" }, { state: "running" }, { state: "done" }, { state: "running" }]);
  assert.strictEqual(end.stopped, "finished");
  assert.strictEqual(calls(), 3);
});

test("status polling stops at once when the engine doesn't know the run", async () => {
  // the engine replies "LookupError: No proof p-1" (server.py)
  const { end, calls } = await pollHarness([{ state: "running" }, "LookupError: No proof p-1"]);
  assert.deepStrictEqual({ ...end }, { stopped: "lost", message: "LookupError: No proof p-1" });
  assert.strictEqual(calls(), 2);
  const sbs = await pollHarness(["LookupError: No side-by-side run s-1"]);
  assert.strictEqual(sbs.end.stopped, "lost");
});

test("status polling backs off on errors and gives up after the limit", async () => {
  const down = new Error("The engine isn't answering");
  const { end, waits, calls } = await pollHarness([down, { state: "running" }, down, down, down, down, down, { state: "done" }], { limit: 5 });
  assert.deepStrictEqual({ ...end }, { stopped: "lost", message: "The engine isn't answering" });
  assert.strictEqual(calls(), 7, "an answer in between resets the count");
  assert.deepStrictEqual(waits, [1500, 3000, 1500, 3000, 6000, 12000, 12000]);
});

// followProof and followSideBySide with the engine, the bell and the page stubbed out.
function followHarness(statuses) {
  const ctx = { console, Promise, Map, Set, Object, String, Math, RegExp, Number, Error, JSON };
  const calls = { status: 0, bell: [], toasts: [] };
  Object.assign(ctx, {
    state: { notebook: null, view: "notebook" },
    setTimeout: (fn) => Promise.resolve().then(fn),
    rpc: async (method) => {
      if (method.endsWith("_status")) {
        const s = statuses[Math.min(calls.status++, statuses.length - 1)];
        return typeof s === "function" ? s() : s;
      }
      return [];
    },
    setKernel() {}, renderNotebook() {}, updateResults() {}, registerFeature() {}, previewShown: () => true,
    logActivity: (item) => calls.bell.push(item),
    toast: (text) => calls.toasts.push(text),
    document: { querySelector: () => null, querySelectorAll: () => [] },
  });
  vm.createContext(ctx);
  for (const file of ["prove.js", "sidebyside.js"]) vm.runInContext(fs.readFileSync(path.join(ui, file), "utf8"), ctx, { filename: file });
  return { ctx, calls };
}

for (const [kind, follow, slot, prefix] of [["Prove", "followProof", "prove", "prove:"], ["Side by side", "followSideBySide", "sbs", "sbs:"]]) {
  test(`${kind}: closing the notebook mid-run still settles the bell entry`, async () => {
    let closed = false;
    const done = { state: "done", verdict: { text: "Same.", verdict: "same", same: true } };
    const { ctx, calls } = followHarness([{ state: kind === "Prove" ? "pc" : "local" }, () => { ctx.state.notebook = null; closed = true; return { state: kind === "Prove" ? "fabric" : "fabric" }; }, done]);
    const nb = { name: "nb", prove: {}, cells: [] };
    ctx.state.notebook = nb;
    await ctx[follow](nb, "r-1");
    assert.ok(closed, "the notebook was closed during the run");
    const last = calls.bell.at(-1);
    assert.strictEqual(last.id, `${prefix}r-1`);
    assert.strictEqual(last.status, "ok");
    assert.match(calls.toasts.at(-1), /^nb: Same\./);
  });

  test(`${kind}: reopening the notebook mid-run doesn't start a second loop`, async () => {
    const running = { state: kind === "Prove" ? "pc" : "local" };
    let release;
    const gate = new Promise((r) => { release = r; });
    const { ctx, calls } = followHarness([running, () => gate, { state: "cancelled" }]);
    const first = { name: "nb", prove: {}, cells: [] };
    const second = { name: "nb", prove: {}, cells: [] };
    ctx.state.notebook = first;
    const loop = ctx[follow](first, "r-2");
    await new Promise((r) => setImmediate(r));
    ctx.state.notebook = second;
    await ctx[follow](second, "r-2");  // returns at once: the first loop takes the new notebook over
    release(running);
    await loop;
    assert.strictEqual(calls.status, 3);
    assert.strictEqual(slot === "prove" ? second.prove.record.state : second.sbs.state, "cancelled");
    assert.deepStrictEqual(calls.toasts, ["nb: Cancelled."]);
  });
}

test("Prove and side by side follow runs only through pollRun", () => {
  for (const [file, fn] of [["prove.js", "followProof"], ["sidebyside.js", "followSideBySide"]]) {
    const src = fs.readFileSync(path.join(ui, file), "utf8");
    const body = src.match(new RegExp(`async function ${fn}\\([\\s\\S]*?\\n}\\n`))[0];
    assert.match(body, /pollRun\(/, fn);
    assert.doesNotMatch(body, /for \(;;\)|while \(true\)/, fn);
    assert.match(body, /if \(following\) return;/, `${fn} guards against two loops for one run`);
  }
});

// UI-4 (HD-042): closing a prompt by its overlay, its X or Escape settled nothing, so the code
// awaiting it hung. Runs the dialog code from app.js and meta.js on a small fake DOM.
function dialogHelpers() {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const section = app.slice(app.indexOf("// ---------- dialogs and menus ----------"), app.indexOf("function showMenu("));
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const keys = features.slice(features.indexOf("// ---------- keyboard shortcuts"), features.indexOf("function shortcutsDialog("));
  const code = keys + section + app.match(/function pickWorkspace\([\s\S]*?\n}\n/)[0] + meta.match(/function confirmDialog\([\s\S]*?\n}\n/)[0];
  const node = (tag, attrs = {}, ...kids) => {
    const n = { tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k != null && k !== false), on: {}, value: attrs?.value || "",
      addEventListener(type, fn) { (this.on[type] ||= []).push(fn); },
      fire(type, e = {}) { for (const fn of this.on[type] || []) fn({ target: this, preventDefault() {}, stopImmediatePropagation() {}, ...e }); },
      focus() {}, select() {}, contains: () => false, querySelector: () => null, querySelectorAll: () => [] };
    for (const [k, v] of Object.entries(n.attrs)) if (k.startsWith("on") && v) n.addEventListener(k.slice(2), v);
    return n;
  };
  const find = (n, pred) => !n || typeof n !== "object" ? null : pred(n) ? n : n.kids.reduce((hit, k) => hit || find(k, pred), null);
  const root = node("div");
  Object.defineProperty(root, "childElementCount", { get: () => root.kids.length });
  Object.assign(root, {
    replaceChildren(...k) { this.kids = k; },
    querySelector(sel) { return sel === ".overlay[data-dismiss]" ? find(this.kids[0], (n) => "data-dismiss" in n.attrs) : null; },
  });
  const docOn = {};
  const ctx = {
    h: node, icon: () => node("svg"), btn: (label, _i, onClick, cls) => node("button", { class: cls, onclick: onClick }, label),
    MutationObserver: class { observe() {} disconnect() {} },
    document: { getElementById: () => root, addEventListener: (t, fn) => { (docOn[t] ||= []).push(fn); } },
  };
  vm.runInNewContext(`${code}; Object.assign(this, { showDialog, closeDialog, askText, pickWorkspace, confirmDialog });`, ctx);
  const overlay = () => root.kids[0];
  return {
    ...ctx, root,
    clickOverlay: () => overlay().fire("mousedown", { target: overlay() }),
    clickX: () => find(overlay(), (n) => n.attrs["aria-label"] === "Close").fire("click"),
    pressEscape: () => docOn.keydown.forEach((fn) => fn({ key: "Escape", preventDefault() {}, stopImmediatePropagation() {} })),
    clickButton: (label) => find(overlay(), (n) => n.tag === "button" && n.kids[0] === label).fire("click"),
  };
}

const settledOrHung = (p) => Promise.race([p.then((v) => ({ v })), new Promise((r) => setTimeout(() => r("hung"), 20))]);

test("closing a text prompt by its overlay, its X or Escape answers null", async () => {
  for (const close of ["clickOverlay", "clickX", "pressEscape"]) {
    const d = dialogHelpers();
    const answer = d.askText("Rename", "Name", "orders");
    d[close]();
    assert.deepStrictEqual(await settledOrHung(answer), { v: null }, close);
    assert.strictEqual(d.root.childElementCount, 0, close);
  }
  const d = dialogHelpers();
  const answer = d.askText("Rename", "Name", "orders");
  d.clickButton("Cancel");
  assert.deepStrictEqual(await settledOrHung(answer), { v: null });
});

test("a workspace picker and a confirmation settle however they close", async () => {
  for (const close of ["clickOverlay", "clickX", "pressEscape"]) {
    const d = dialogHelpers();
    const pick = d.pickWorkspace([{ id: "w1", displayName: "Sales" }]);
    d[close]();
    assert.deepStrictEqual(await settledOrHung(pick), { v: null }, close);
    const e = dialogHelpers();
    const ok = e.confirmDialog("Delete Orders?", "x", "Delete", { danger: true });
    e[close]();
    assert.deepStrictEqual(await settledOrHung(ok), { v: false }, close);
  }
});

test("a dialog's onClose runs exactly once, also when another dialog replaces it", () => {
  const d = dialogHelpers();
  let closed = 0;
  d.showDialog("One", d.h("p"), [], () => { closed++; });
  d.clickX();
  d.closeDialog();
  d.pressEscape();
  assert.strictEqual(closed, 1);
  let replaced = 0;
  d.showDialog("Two", d.h("p"), [], () => { replaced++; });
  d.showDialog("Three", d.h("p"));
  d.closeDialog();
  assert.strictEqual(replaced, 1);
  assert.strictEqual(d.root.childElementCount, 0);
});

// HD-044 (UI-6): the rail marks the page you're on, and the command bar is a modal dialog.
function paletteHarness() {
  const src = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const node = (tag, attrs = {}, ...kids) => {
    const n = { tag, attrs, kids: kids.flat(), on: {}, isConnected: true, value: attrs.value || "",
      addEventListener(type, fn) { this.on[type] = fn; }, focus() { ctx.document.activeElement = this; },
      setSelectionRange() {}, querySelector() { return null; }, replaceChildren(...k) { this.kids = k; },
      remove() { this.isConnected = false; if (ctx.mounted === this) ctx.mounted = null; } };
    for (const [k, v] of Object.entries(attrs)) if (k.startsWith("on") && typeof v === "function") n.on[k.slice(2)] = v;
    return n;
  };
  const dialogRoot = { childElementCount: 0 };
  const ctx = {
    h: node, icon: () => node("svg"), closeMenu() {}, state: { view: "home" }, rpc: () => new Promise(() => {}),
    previewShown: () => true, go() {}, console, mounted: null, trapped: 0,
    trapFocus() { ctx.trapped++; },
    document: { activeElement: null, body: { append(b) { ctx.mounted = b; } },
      getElementById: (id) => (id === "palette" ? ctx.mounted : id === "dialog-root" ? dialogRoot : null) },
  };
  vm.runInNewContext(src, ctx);
  ctx.paletteItems = () => [{ group: "Pages", label: "Home", icon: "home", run() {} }];
  const key = (k) => { const e = { key: k, stopped: false, preventDefault() {}, stopPropagation() { e.stopped = true; } }; return e; };
  return { ctx, node, dialogRoot, key };
}

test("the command bar is a modal dialog: focus goes in, Tab stays in, Escape closes it and focus returns (HD-044)", () => {
  const { ctx, node, key } = paletteHarness();
  const opener = node("button", { id: "opener" });
  ctx.document.activeElement = opener;
  ctx.openPalette();
  const box = ctx.mounted;
  assert.ok(box, "the command bar did not open");
  const dialog = box.kids[0];
  assert.strictEqual(dialog.attrs.role, "dialog");
  assert.strictEqual(dialog.attrs["aria-modal"], "true");
  assert.strictEqual(ctx.document.activeElement.tag, "input", "focus did not move into the command bar");
  ctx.openPalette();  // Ctrl+K again keeps where focus came from
  const reopened = ctx.mounted;
  assert.ok(reopened.on.keydown, "the command bar has no keydown handler for Tab and Escape");
  reopened.on.keydown(key("Tab"));
  assert.strictEqual(ctx.trapped, 1, "Tab is not kept inside the command bar");
  const esc = key("Escape");
  reopened.on.keydown(esc);
  assert.strictEqual(ctx.mounted, null, "Escape did not close the command bar");
  assert.ok(esc.stopped, "Escape also reaches the activity tray behind the command bar");
  assert.strictEqual(ctx.document.activeElement, opener, "focus did not go back to where it was");
});

test("Ctrl+K does nothing while another dialog is open (HD-044)", () => {
  const { ctx, dialogRoot } = paletteHarness();
  dialogRoot.childElementCount = 1;
  ctx.openPalette();
  assert.strictEqual(ctx.mounted, null, "the command bar opened over a dialog");
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(app, /isShortcut\(e, "palette"\)\) \{ e\.preventDefault\(\); openPalette\(\); \}/);
});

test("the rail marks the page you're on with aria-current, and only that one (HD-044)", () => {
  const { ctx } = paletteHarness();
  const f = { id: "x", rail: { label: "Home", icon: "home", order: 1, also: ["homeDetail"] }, views: { home() {} } };
  ctx.state.view = "home";
  assert.strictEqual(ctx.railItem(f).attrs["aria-current"], "page");
  ctx.state.view = "homeDetail";
  assert.strictEqual(ctx.railItem(f).attrs["aria-current"], "page");
  ctx.state.view = "settings";
  assert.strictEqual(ctx.railItem(f).attrs["aria-current"], null);
  assert.doesNotMatch(ctx.railItem(f).attrs.class, /active/);
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(app, /class: `rail-item\$\{state\.view === view \? " active" : ""\}`, "aria-current": state\.view === view \? "page" : null/);
});

test("A scheduled run whose Teams alert didn't go out says so in Runs and Schedules (QA-013)", () => {
  const a = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(a, /reason: r\.status === "ok" \? "" : \[r\.message\?\.replace\(\/\\\.\$\/, ""\), r\.alert_error\]\.filter\(Boolean\)\.join\("\. "\)/);
  const s = fs.readFileSync(path.join(ui, "schedules.js"), "utf8");
  assert.match(s, /s\.last\?\.alert_error \? h\("div", \{ class: "reason" \}, s\.last\.alert_error\) : null/);
});

// motion.js with a small stand-in DOM and clock: what Forge animations make, and when they don't.
function motionHarness({ setting = true, reduced = false, hidden = false } = {}) {
  let clock = 0;
  let timers = [];
  const node = (cls = "") => {
    const n = {
      cls: new Set(String(cls).split(" ").filter(Boolean)), kids: [], attrs: {}, style: {}, parent: null, textContent: "",
      get isConnected() { return !!n.parent; },
      classList: { add: (c) => n.cls.add(c), contains: (c) => n.cls.has(c) },
      append(...cs) { for (const c of cs) if (c && typeof c === "object") { c.remove(); c.parent = n; n.kids.push(c); } },
      remove() { if (n.parent) n.parent.kids = n.parent.kids.filter((k) => k !== n); n.parent = null; },
      setAttribute(k, v) { n.attrs[k] = v; },
      all() { return n.kids.flatMap((k) => [k, ...k.all()]); },
      querySelector(sel) {
        if (sel === ":scope > .fx") return n.kids.find((k) => k.cls.has("fx")) || null;
        return n.all().find((k) => k.cls.has(sel.slice(1))) || null;
      },
    };
    return n;
  };
  const live = node();
  const ctx = {
    state: { settings: { forge_animations: { value: setting } } },
    document: { hidden, documentElement: { dataset: {} }, createElementNS: () => node(), getElementById: (id) => (id === "announce" ? live : null) },
    matchMedia: () => ({ matches: reduced, addEventListener() {} }),
    setTimeout: (fn, ms) => { const t = { at: clock + ms, fn }; timers.push(t); return t; },
    clearTimeout: (t) => { timers = timers.filter((x) => x !== t); },
    Date: { now: () => clock },
    h: (tag, attrs = {}, ...children) => {
      const n = node(attrs.class || "");
      for (const [k, v] of Object.entries(attrs)) if (k !== "class") n.attrs[k] = v;
      n.append(...children.flat());
      return n;
    },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(ui, "motion.js"), "utf8"), ctx);
  ctx.advance = (ms) => {
    clock += ms;
    for (let due; (due = timers.filter((t) => t.at <= clock).sort((a, b) => a.at - b.at)[0]);) { timers = timers.filter((t) => t !== due); due.fn(); }
  };
  ctx.node = node;
  ctx.live = live;
  return ctx;
}

test("Forge animations: fx plays one hidden effect, removes it, and doesn't stack repeats", () => {
  const ctx = motionHarness();
  const label = ctx.node();
  const el = ctx.fx(label, "spark");
  assert.ok(el, "no spark was made");
  assert.strictEqual(el.attrs["aria-hidden"], "true");
  assert.ok(el.cls.has("fx") && el.cls.has("fx-spark") && label.cls.has("fx-anchor"));
  assert.strictEqual(ctx.fx(label, "fail"), null, "a second effect stacked on the same element");
  assert.strictEqual(ctx.fx(ctx.node(), "spark"), null, "a second spark played within its gap");
  ctx.advance(600);
  assert.strictEqual(label.kids.length, 0, "the spark was not removed");
  assert.ok(ctx.fx(ctx.node(), "spark"), "a spark after the gap did not play");
  assert.strictEqual(ctx.fx(ctx.node(), "confetti"), null, "an unknown kind played");
  assert.strictEqual(ctx.fx(null, "fail"), null);
});

test("Forge animations: nothing is made with the setting off, reduced motion or a hidden window", () => {
  for (const opts of [{ setting: false }, { reduced: true }, { hidden: true }]) {
    const ctx = motionHarness(opts);
    const label = ctx.node();
    assert.strictEqual(ctx.fx(label, "spark"), null, JSON.stringify(opts));
    assert.strictEqual(label.kids.length, 0);
    const track = ctx.wolfTrack(() => ctx.bar ||= ctx.node(), "Notebook run", "Cell");
    track.set(1, 3);
    ctx.advance(700);
    assert.strictEqual(ctx.bar.querySelector(".wolf"), null, `a wolf was drawn: ${JSON.stringify(opts)}`);
    if (!opts.hidden) assert.ok(ctx.bar.querySelector(".wolf-bar"), "the progress bar itself went with the animations");
  }
  const off = motionHarness({ setting: false });
  off.applyMotion();
  assert.strictEqual(off.document.documentElement.dataset.motion, "off");
  const reduced = motionHarness({ reduced: true });
  reduced.applyMotion();
  assert.strictEqual(reduced.document.documentElement.dataset.motion, "off");
  const on = motionHarness();
  on.applyMotion();
  assert.strictEqual(on.document.documentElement.dataset.motion, "on");
  on.state.settings = null;  // settings not loaded yet: on, as for a new install
  assert.strictEqual(on.motionOn(), true);
});

test("Forge animations: the wolf track waits 600 ms, follows real progress and goes when the run ends", () => {
  const ctx = motionHarness();
  const bar = ctx.node();
  const quick = ctx.wolfTrack(() => bar, "Notebook run", "Cell");
  quick.set(0, 2);
  ctx.advance(500);
  quick.stop();
  ctx.advance(500);
  assert.strictEqual(bar.kids.length, 0, "a run shorter than 600 ms flashed the track");

  const track = ctx.wolfTrack(() => bar, "Notebook run", "Cell");
  track.set(0, 4);
  ctx.advance(599);
  assert.strictEqual(track.shown, false);
  ctx.advance(1);
  assert.strictEqual(track.shown, true);
  track.set(1, 4);
  const p = bar.querySelector(".wolf-bar");
  assert.strictEqual(bar.querySelector(".wolf-text").textContent, "Cell 2 of 4");
  assert.deepStrictEqual([p.attrs.role, p.attrs["aria-valuenow"], p.attrs["aria-valuemax"]], ["progressbar", "1", "4"]);
  assert.strictEqual(bar.querySelector(".wolf").style.left, "25%");
  assert.strictEqual(bar.querySelector(".wolf").attrs["aria-hidden"], "true");
  assert.strictEqual(bar.querySelector(".wolf-embers").kids.length, 3, "the wolf has no embers behind it");
  assert.strictEqual(bar.querySelector(".wolf-fill").style.width, "25%");
  track.set(4, 4);
  track.arrive();
  assert.ok(bar.querySelector(".fx-arrive"), "the wolf didn't glow at the end");
  assert.ok(bar.querySelector(".wolf").cls.has("arriving"));
  ctx.advance(700);
  assert.strictEqual(bar.kids.length, 0, "the track stayed after the run");

  const failed = ctx.wolfTrack(() => bar, "Notebook run", "Cell");
  failed.set(1, 3);
  ctx.advance(700);
  failed.stop();
  assert.strictEqual(bar.kids.length, 0, "a failed or cancelled run left its track");
  assert.strictEqual(bar.querySelector(".fx-arrive"), null);
});

test("Forge animations: announce speaks once through the polite region", () => {
  const ctx = motionHarness({ setting: false });
  ctx.announce("Run finished: 3 cells succeeded");
  ctx.advance(60);
  assert.strictEqual(ctx.live.textContent, "Run finished: 3 cells succeeded", "turning animations off silenced screen readers");
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.match(html, /<div id="announce" class="sr-only" role="status" aria-live="polite"><\/div>/);
  assert.ok(html.indexOf('src="motion.js"') < html.indexOf('src="notebook.js"'), "motion.js loads after notebook.js");
});

// cellFx from notebook.js, with fx and announce recorded.
function cellFxHarness(runningAll) {
  const src = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const code = src.match(/function cellFx\([\s\S]*?\n}\n/)[0];
  const calls = [];
  const cells = [{ id: "a" }, { id: "b" }, { id: "c" }];
  const ctx = {
    runningAll, state: { notebook: { cells } }, calls, cells,
    document: { querySelector: (sel) => ({ sel }) },
    fx: (anchor, kind) => calls.push(["fx", kind, anchor.sel]),
    announce: (text) => calls.push(["say", text]),
  };
  vm.runInNewContext(`${code}; this.cellFx = cellFx;`, ctx);
  return ctx;
}

test("Forge animations in the notebook: spark on success, ember on failure, nothing for Stop or Cancel", () => {
  const one = cellFxHarness(false);
  one.cells[1].status = "ok";
  one.cellFx(one.cells[1]);
  assert.deepStrictEqual(one.calls, [["say", "Cell 2 succeeded"], ["fx", "spark", '[data-cell="b"] .cell-status > span']]);
  one.calls.length = 0;
  one.cells[2].status = "error";
  one.cellFx(one.cells[2]);
  assert.deepStrictEqual(one.calls, [["say", "Cell 3 failed"], ["fx", "fail", '[data-cell="c"] .cell-status > span']]);
  for (const status of ["stopped", "cancelled", "running", null]) {
    one.calls.length = 0;
    one.cells[0].status = status;
    one.cellFx(one.cells[0]);
    assert.deepStrictEqual(one.calls, [], `${status} played or said something`);
  }
  const all = cellFxHarness(true);
  all.cells[0].status = "ok";
  all.cellFx(all.cells[0]);
  assert.deepStrictEqual(all.calls, [], "Run all sparked or spoke for each cell");
  all.cells[1].status = "error";
  all.cellFx(all.cells[1]);
  assert.deepStrictEqual(all.calls, [["fx", "fail", '[data-cell="b"] .cell-status > span']]);
});

test("Forge animations in the notebook: runs of several cells use the wolf track and one announcement", () => {
  const src = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const run = src.match(/async function runCells\([\s\S]*?\n}\n/)[0];
  assert.match(run, /code\.length > 1 \? wolfTrack\(\(\) => document\.querySelector\("\.statusbar"\), "Notebook run", "Cell"\) : null/);
  assert.match(run, /if \(!ok \|\| cancelRequested\) \{\n\s+track\?\.stop\(\);/, "a failed or cancelled run keeps its track");
  assert.match(run, /announce\(cell\.status === "error" \? `Run stopped: cell \$\{[^}]+\} failed` : "Run stopped"\);/, "a stopped run says nothing");
  assert.match(run, /track\?\.set\(i \+ 1, code\.length\);/);
  assert.match(run, /track\?\.arrive\(\);\n\s+if \(code\.length\) announce\(`Run finished/);
  assert.match(run, /\} finally \{\n\s+runningAll = false;/);
  assert.match(src, /refreshVariables\(\);\n  cellFx\(cell\);  \/\/ after dbgAfterRun, which draws the results again\n  return ok;/);
});

test("Forge animations setting: Settings > Display, lockable by policy, applied at once; CSS honours it", () => {
  const s = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  assert.match(s, /settingRow\("Forge animations",\n\s+"Show subtle wolf, forge and spark animations during application activities\./);
  assert.match(s, /disabled: locked\("forge_animations"\) \? true : null/);
  assert.match(s, /saveSettings\(\{ forge_animations: e\.target\.checked \}\)/);
  assert.match(s, /reducedMotion\(\) \? "Off while Windows reduces motion"/);
  assert.match(s, /\[account, updates, display, previews,/);
  assert.match(s, /if \(res\) state\.settings = res;\n  applyMotion\(\);/);
  assert.match(s, /state\.settings = await rpc\("app_settings"\)[^\n]*\n  applyMotion\(\);/);
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  assert.match(css, /html\[data-motion="off"\] \.fx, html\[data-motion="off"\] \.wolf \{ display: none; \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{ \.fx, \.wolf \{ display: none; \}/);
  // Effects animate only transform and opacity, so they never move the layout.
  const keyframes = [...css.matchAll(/@keyframes fx-[\w-]+ \{([^\n]*)\}\n/g)].map((m) => m[1]);
  assert.ok(keyframes.length >= 6, "fx keyframes not found");
  for (const k of keyframes) assert.doesNotMatch(k.replace(/\b(opacity|transform)\b/g, ""), /[a-z-]+:/, k);
});

// Notebook files (notebook-files.js): the Saved in chips, search hits, and what Publish says changed in Fabric.
function notebookFilesHelpers() {
  const src = fs.readFileSync(path.join(ui, "notebook-files.js"), "utf8");
  const ctx = { registered: [], registerFeature(f) { ctx.registered.push(f); } };
  vm.runInNewContext(`${src}; Object.assign(this, { nbFilesPlaces, nbFilesShown, nbHitParts, nbHitCount, nbChangeTitle });`, ctx);
  return ctx;
}

test("All notebooks filters by where notebooks are saved", () => {
  const { nbFilesPlaces, nbFilesShown } = notebookFilesHelpers();
  const repo = { root: "C:/git/sales", label: "sales-notebooks" };
  const list = [{ name: "A", place: null }, { name: "B", place: repo }, { name: "C", place: repo }];
  assert.deepStrictEqual([...nbFilesPlaces(list).map((p) => `${p.label} ${p.count}`)], ["All 3", "This PC 1", "sales-notebooks 2"]);
  assert.deepStrictEqual([...nbFilesShown(list, "pc").map((n) => n.name)], ["A"]);
  assert.deepStrictEqual([...nbFilesShown(list, repo.root).map((n) => n.name)], ["B", "C"]);
  assert.strictEqual(nbFilesShown(list, null).length, 3);
});

test("A search hit marks the match, and the count covers name-only matches", () => {
  const { nbHitParts, nbHitCount } = notebookFilesHelpers();
  assert.deepStrictEqual([...nbHitParts({ text: "x = region_map.join()", start: 4, end: 14 })], ["x = ", "region_map", ".join()"]);
  const found = { notebooks: [{ name_match: true, hits: [] }, { name_match: false, hits: [{}, {}] }, { name_match: true, hits: [{}] }] };
  assert.strictEqual(nbHitCount(found), 4);
});

test("Publish names each change made in Fabric", () => {
  const { nbChangeTitle } = notebookFilesHelpers();
  assert.strictEqual(nbChangeTitle({ kind: "changed", position: 3 }), "Cell 3: changed");
  assert.strictEqual(nbChangeTitle({ kind: "added", position: 6 }), "Cell 6: added");
  assert.strictEqual(nbChangeTitle({ kind: "reordered", position: null }), "Cells were reordered");
});

test("Notebook files: commands, Home link, ribbon, Fabric preview and the Publish check are wired", () => {
  const { registered } = notebookFilesHelpers();
  assert.deepStrictEqual([...registered.map((f) => f.id)], ["notebookFiles"]);
  assert.ok(registered[0].views.notebooks);
  assert.deepStrictEqual([...registered[0].commands.map((c) => c.label)],
    ["All notebooks", "Search inside notebooks", "Open a notebook file", "Save a copy of this notebook"]);
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(app, /also: \["notebooks"\]/);
  assert.match(app, /btn\("All notebooks", "notebook", \(\) => go\("notebooks"\)/);
  assert.match(app, /rpc\("publish_notebook", \{ \.\.\.publishPayload\(\), replace_fabric_changes: gate\.replace \}\)/);
  assert.match(app, /rpc\("run_notebook_in_fabric", \{ \.\.\.publishPayload\(\), replace_fabric_changes: gate\.replace \}\)/);
  assert.match(fs.readFileSync(path.join(ui, "notebook.js"), "utf8"), /btn\("Save a copy", "add", \(\) => nbSaveCopyDialog\(nb\.name\)\)/);
  assert.match(fs.readFileSync(path.join(ui, "item-preview.js"), "utf8"), /preview\("fabricEdit", btn\("Edit on this PC"/);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /fabricEdit: "Edit Fabric notebooks on this PC"/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="validate\.js"><\/script>\n  <script src="notebook-files\.js"><\/script>/);
  const files = fs.readFileSync(path.join(ui, "notebook-files.js"), "utf8");
  assert.match(files, /confirmDialog\(`Delete \$\{name\}\?`, .*"Delete", \{ danger: true \}\)/);
  assert.match(files, /"Replace with mine", \{ danger: true \}/);
});

test("the update splash lists the build's notes and each change since the build this PC ran before", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function updateHighlights\([\s\S]*?\n}\n/)[0]}; this.f = updateHighlights;`, ctx);
  const plain = (v) => JSON.parse(JSON.stringify(v));
  const items = [{ title: "Notebook steps", commit: "c3" }, { title: "SCD templates ", commit: "c2" }, { title: "Keys move", commit: "c1" }];
  assert.deepStrictEqual(plain(ctx.f([], items, "c1")), ["Notebook steps", "SCD templates"]);
  assert.deepStrictEqual(plain(ctx.f(["Faster runs"], items, "c2")), ["Faster runs", "Notebook steps"]);
  assert.deepStrictEqual(plain(ctx.f([], items, "gone")), ["Notebook steps"], "an unknown build shows only the newest change");
  assert.deepStrictEqual(plain(ctx.f(["Notebook steps"], items, "")), ["Notebook steps"], "no repeats");
  assert.deepStrictEqual(plain(ctx.f([], items, "c3")), []);
  assert.match(src, /const UPDATE_SPLASH_OFF = "fd\.updatesplash\.off"/);
  assert.match(src, /if \(!asked && !updateSplashOn\(\)\) \{ showWhatsNew\(\); return false; \}/);
  assert.match(src, /label: "Show the update splash"/);
  assert.match(fs.readFileSync(path.join(ui, "styles.css"), "utf8"), /\.splash-actions, \.splash-optout \{ animation: none; \}/);
});

// UX-018: every schedule Remove asks first, names what stops, and points at Off.
test("removing a schedule asks first, in red, and says what stops", () => {
  const s = fs.readFileSync(path.join(ui, "schedules.js"), "utf8");
  const removes = s.split('btn("Remove", "delete", async () => {').slice(1);
  assert.strictEqual(removes.length, 3);
  for (const r of removes) assert.match(r.split("\n")[1], /if \(!\(await confirmRemoveSchedule\([^)]*\)\)\) return;/);
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(/function confirmRemoveSchedule\([\s\S]*?\n}\n/.exec(s)[0] + "\nthis.f = confirmRemoveSchedule;", ctx);
  ctx.confirmDialog = (...args) => args;
  const [title, text, ok, opts] = ctx.f("nb_sales", "Daily at 06:00 UTC", false);
  assert.strictEqual(title, "Remove the schedule for nb_sales?");
  assert.match(text, /^Daily at 06:00 UTC\. Its Windows task is deleted .* turn it Off\.$/);
  assert.strictEqual(ok, "Remove");
  assert.strictEqual(opts.danger, true);
  assert.match(ctx.f("Dev runs", "Every 30 minutes", true)[1], /Fabric deletes the schedule/);
  assert.match(ctx.f("Dev runs", "Every 30 minutes", true, false)[1], /turn it Off in Monitor > Schedules\.$/);
  assert.match(s, /confirmRemoveSchedule\(o\.what, s\.text, true, false\)/);
});

// UX-019: the Preview list in the docs is the app's own list, once.
test("the documentation's Preview list matches PREVIEW_FEATURES", () => {
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const block = features.match(/const PREVIEW_FEATURES = \{([\s\S]*?)\n\};/)[1];
  const entries = [...block.matchAll(/^ {2}(\w+): "([^"]+)",/gm)].map(([, id, name]) => [id, name]);
  assert.ok(entries.length > 20, `only ${entries.length} preview features found`);
  const ids = entries.map(([id]) => id);
  assert.deepStrictEqual(ids.filter((id, i) => ids.indexOf(id) !== i), [], "an id listed twice keeps only its last name");
  const readme = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "README.md"), "utf8");
  const table = readme.split("| Preview feature | Where |")[1];
  assert.ok(table, "docs/README.md has the Preview feature table");
  const rows = [];
  for (const line of table.split("\n").slice(2)) {
    if (!line.startsWith("|")) break;
    rows.push(line.split("|")[1].trim());
  }
  assert.deepStrictEqual([...rows].sort(), entries.map(([, name]) => name).sort());
  // The steps that stay visible are the ones the table says are always shown.
  const always = JSON.parse(features.match(/const PREVIEW_ALWAYS_SHOWN = (\[[^\]]*\])/)[1]);
  const names = Object.fromEntries(entries);
  const shownRows = table.split("\n").filter((l) => /always shown/.test(l)).map((l) => l.split("|")[1].trim());
  assert.deepStrictEqual(shownRows.sort(), always.map((id) => names[id]).sort());
  // The user guide points at that list instead of keeping a second one.
  const guide = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "user-guide.md"), "utf8");
  const section = guide.split("## Preview features\n")[1].split("\n## ")[0];
  assert.match(section, /README\.md#what-is-tested-and-what-isnt/);
});

// UX-019: one shortcut table, read by the global key handlers and drawn by the Keyboard shortcuts dialog.
test("the global key handlers and the Keyboard shortcuts dialog read one table", () => {
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${features.slice(features.indexOf("// ---------- keyboard shortcuts"), features.indexOf("function shortcutsDialog("))}
    Object.assign(this, { shortcutRegistry, registerShortcuts, isShortcut, typingIn, shortcutGroups });`, ctx);
  const appWide = [...ctx.shortcutRegistry];
  const ids = appWide.map((s) => s.id);
  assert.strictEqual(new Set(ids).size, ids.length);
  // Every entry is a key app.js actually handles, through the table.
  for (const id of ids) assert.ok(app.includes(`isShortcut(e, "${id}")`), `app.js handles ${id} through the table`);
  assert.doesNotMatch(app, /e\.key\.toLowerCase\(\) === "k"/, "Ctrl+K is read from the table");
  const key = (k, mods = {}) => ({ key: k, ctrlKey: false, metaKey: false, ...mods });
  assert.ok(ctx.isShortcut(key("K", { ctrlKey: true }), "palette"));
  assert.ok(ctx.isShortcut(key("k", { metaKey: true }), "palette"));
  assert.ok(!ctx.isShortcut(key("k"), "palette"));
  assert.ok(ctx.isShortcut(key("?"), "shortcuts"));
  assert.ok(ctx.isShortcut(key("Escape"), "close"));
  assert.ok(ctx.isShortcut(key(" "), "open") && ctx.isShortcut(key("Enter"), "open"));
  assert.ok(!ctx.isShortcut(key("ArrowLeft"), "menu") && ctx.isShortcut(key("ArrowLeft"), "tabs"));
  assert.ok(!ctx.isShortcut(key("x"), "nothing"));
  // "?" typed in a box stays a character.
  const el = (tagName, inEditor = false) => ({ tagName, isContentEditable: false, closest: () => (inEditor ? {} : null) });
  assert.ok(ctx.typingIn(el("INPUT")) && ctx.typingIn(el("TEXTAREA")) && ctx.typingIn(el("DIV", true)));
  assert.ok(!ctx.typingIn(el("BUTTON")) && !ctx.typingIn(null));
  // Another feature (the notebook) registers its keys once and they join the same list, shown only when it says.
  let inNotebook = false;
  ctx.registerShortcuts("Notebook", [{ keys: ["Shift+Enter"], label: "Run the cell", when: () => inNotebook },
    { keys: ["Ctrl+Shift+Enter"], label: "Run the selected cells", when: () => { throw new Error("broken"); } }]);
  assert.deepStrictEqual([...ctx.shortcutGroups().map(([g]) => g)], ["Anywhere", "Moving around", "Menus and tabs"]);
  inNotebook = true;
  const groups = ctx.shortcutGroups();
  assert.deepStrictEqual([...groups.map(([g, list]) => [g, list.length])],
    [["Anywhere", 3], ["Moving around", 2], ["Menus and tabs", 2], ["Notebook", 1]]);
  assert.ok(!ctx.isShortcut(key("Enter", { shiftKey: true }), undefined), "an entry without an id matches nothing");
  // The dialog lists the registry, opens from the command bar and Settings, and points at the notebook keys.
  const dialog = features.match(/function shortcutsDialog\([\s\S]*?\n}\n/)[0];
  assert.match(dialog, /shortcutGroups\(\)/);
  assert.match(dialog, /notebooks\.md#editing/);
  const settings = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  assert.match(settings, /btn\("Keyboard shortcuts", "help", shortcutsDialog/);
  assert.match(settings, /\{ label: "Keyboard shortcuts", [^}]*run: shortcutsDialog \}/);
  // The user guide's table has a row for every entry.
  const guide = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "user-guide.md"), "utf8");
  const section = guide.split("### Keyboard shortcuts\n")[1].split("\n#")[0];
  const rows = section.split("\n").filter((l) => l.startsWith("| **"));
  assert.strictEqual(rows.length, appWide.length);
  assert.match(fs.readFileSync(path.join(__dirname, "..", "..", "docs", "ui-guidelines.md"), "utf8"), /registerShortcuts\("Notebook"/);
});

// UX-019: every page has a help link into the user guide.
test("every page links to its section of the user guide", () => {
  const all = fs.readdirSync(ui).filter((f) => f.endsWith(".js")).map((f) => fs.readFileSync(path.join(ui, f), "utf8")).join("\n");
  const body = (fn) => all.match(new RegExp(`\\n(?:async )?function ${fn}\\([\\s\\S]*?\\n}\\n`))?.[0] || "";
  const views = new Map();
  for (const [, list] of all.matchAll(/views: \{([^}]*)\}/g)) for (const [, view, fn] of list.matchAll(/(\w+): (\w+)/g)) views.set(view, fn);
  assert.ok(views.size > 15, `only ${views.size} pages found`);
  // The notebook's help belongs to its own guide; an agent's page has its link in the advice under its state.
  const elsewhere = { notebook: null, agent: "agAdvice" };
  const missing = [...views].filter(([view, fn]) => {
    if (view in elsewhere) return elsewhere[view] && !(body(fn).includes(`${elsewhere[view]}(`) && body(elsewhere[view]).includes("learnMore("));
    return !/learnMore\(|howItWorks\(/.test(body(fn));
  }).map(([view]) => view);
  assert.deepStrictEqual(missing, []);
});

// UX-008: Back, Forward and reopening the last page. The history block of app.js runs on features.js
// with fake pages, a fake localStorage and a render() that counts.
function navHarness({ stored = null, preview = true } = {}) {
  const store = new Map(stored ? [["fd.nav.last", JSON.stringify(stored)]] : []);
  const dialogRoot = { childElementCount: 0 };
  const ctx = {
    console, h: () => ({}), icon: () => ({}), renders: 0,
    state: { view: "home", meta: { tab: "overview", entity: null }, notebook: null, openWorkspace: null, lakehouseView: null, settingsTab: "general" },
    monitorPage: { panel: "runs" }, catalogPage: { tab: "catalog" }, optimizePage: { tab: "overview" },
    platformPage: { tab: "overview" }, gitPage: { tab: "changes" }, wlPage: { id: null }, incPage: { id: null, env_of: null },
    agPage: { name: null }, sqlPage: { ref: null },
    closeMenu() {},
    localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) },
    document: { getElementById: (id) => (id === "dialog-root" ? dialogRoot : null) },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(ui, "features.js"), "utf8"), ctx);
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const block = app.slice(app.indexOf("// ---------- navigation history (UX-008)"), app.indexOf("function renderChrome()"));
  vm.runInContext(block + "\nthis.nav = nav; this.features = features;", ctx);
  ctx.previewShown = () => preview;
  ctx.render = () => { ctx.renders++; ctx.navSync(); };
  const page = (id, views, extra = {}) => ctx.registerFeature({ id, views, ...extra });
  page("home", { home() {} }, { rail: { label: "Home", icon: "home", order: 10 } });
  page("workspaces", { workspaces() {}, workspace() {}, fabric() {} }, { rail: { label: "Workspaces", icon: "w", order: 30, also: ["workspace", "fabric"] } });
  page("metadata", { metadata() {}, entity() {} }, { rail: { label: "Data mesh", icon: "m", order: 50, also: ["entity"] } });
  page("monitor", { monitor() {} }, { rail: { label: "Monitor", icon: "monitor", order: 70 } });
  page("platform", { platform() {} }, { rail: { label: "Platform", icon: "b", order: 55 }, preview: true });
  page("notebook", { notebook() {} });
  return { ctx, store, dialogRoot };
}

test("Back and Forward walk the pages you opened, with each page's tab (UX-008)", () => {
  const { ctx } = navHarness();
  ctx.render();
  ctx.monitorPage.panel = "schedules";
  ctx.go("monitor");
  ctx.monitorPage.panel = "incidents"; ctx.render();  // a tab clicked on the page
  ctx.state.meta.tab = "keys";
  ctx.go("metadata");
  assert.strictEqual(ctx.navCan(-1), true);
  assert.strictEqual(ctx.navCan(1), false);
  assert.strictEqual(ctx.navBack(), true);
  assert.strictEqual(ctx.state.view, "monitor");
  assert.strictEqual(ctx.monitorPage.panel, "incidents", "Back did not bring the tab back");
  ctx.navBack();
  assert.strictEqual(ctx.state.view, "home");
  assert.strictEqual(ctx.navBack(), false, "Back went past the first page");
  ctx.navForward();
  ctx.navForward();
  assert.strictEqual(ctx.state.view, "metadata");
  assert.strictEqual(ctx.state.meta.tab, "keys");
  // Opening a page from the middle drops what was ahead.
  ctx.navBack();
  ctx.go("workspaces");
  assert.strictEqual(ctx.navCan(1), false);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.nav.stack.map((e) => e.view))), ["home", "monitor", "workspaces"]);
});

test("Back after Open in Fabric returns to the page before, and opening the same page twice adds one entry (UX-008)", () => {
  const { ctx } = navHarness();
  ctx.render();
  ctx.go("monitor");
  ctx.go("monitor");
  ctx.go("fabric");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.nav.stack.map((e) => e.view))), ["home", "monitor", "fabric"]);
  ctx.navBack();
  assert.strictEqual(ctx.state.view, "monitor");
});

test("Back brings back the workspace you had open and skips a notebook that was closed (UX-008)", () => {
  const { ctx } = navHarness();
  ctx.render();
  ctx.state.openWorkspace = { id: "w1", displayName: "Sales" };
  ctx.go("workspace");
  ctx.state.notebook = { name: "nb" };
  ctx.go("notebook");
  ctx.state.openWorkspace = { id: "w2", displayName: "Finance" };
  ctx.go("workspace");
  ctx.state.notebook = null;  // closed from the rail
  ctx.navBack();
  assert.strictEqual(ctx.state.view, "workspace", "Back opened a closed notebook");
  assert.strictEqual(ctx.state.openWorkspace.id, "w1");
});

test("Back skips a preview page once preview features are hidden (UX-008)", () => {
  const { ctx } = navHarness();
  let shown = true;
  ctx.previewShown = () => shown;
  ctx.render();
  ctx.go("platform");
  ctx.go("monitor");
  shown = false;
  ctx.navBack();
  assert.strictEqual(ctx.state.view, "home");
});

test("the last page and its tab are saved and opened again on start, or Home if it's gone (UX-008)", () => {
  const a = navHarness();
  a.ctx.render();
  assert.strictEqual(a.store.get("fd.nav.last"), undefined, "the last page was overwritten before it was read back");
  a.ctx.navRestore();
  a.ctx.state.meta.tab = "sources";
  a.ctx.go("metadata");
  assert.strictEqual(a.store.get("fd.nav.last"), JSON.stringify({ view: "metadata", tab: "sources" }));

  const b = navHarness({ stored: { view: "metadata", tab: "sources" } });
  assert.strictEqual(b.ctx.navRestore(), "metadata");
  assert.strictEqual(b.ctx.state.meta.tab, "sources");
  assert.strictEqual(b.ctx.navCan(-1), false);
  assert.strictEqual(navHarness({ stored: { view: "removed-page" } }).ctx.navRestore(), "home");
  assert.strictEqual(navHarness({ stored: { view: "platform", tab: "keys" }, preview: false }).ctx.navRestore(), "home");
  assert.strictEqual(navHarness({ stored: { view: "workspace" } }).ctx.navRestore(), "workspaces", "an item page reopens on its rail page");
  assert.strictEqual(navHarness({ stored: { view: "notebook" } }).ctx.navRestore(), "home");
  assert.strictEqual(navHarness().ctx.navRestore(), "home");
  // A broken or blocked store starts on Home.
  const c = navHarness();
  c.ctx.localStorage.getItem = () => { throw new Error("blocked"); };
  c.ctx.localStorage.setItem = () => { throw new Error("blocked"); };
  assert.strictEqual(c.ctx.navRestore(), "home");
  assert.doesNotThrow(() => c.ctx.go("monitor"));
});

test("Alt+Left and Alt+Right go back and forward, but not while typing or in a dialog (UX-008)", () => {
  const { ctx, dialogRoot } = navHarness();
  ctx.render();
  ctx.go("monitor");
  const key = (k, target = { closest: () => null }, mods = { altKey: true }) => ({ key: k, target, ...mods, prevented: false, preventDefault() { this.prevented = true; } });
  assert.strictEqual(ctx.navKey(key("ArrowLeft", { closest: (s) => (/textarea/.test(s) ? {} : null) })), false);
  assert.strictEqual(ctx.state.view, "monitor");
  assert.strictEqual(ctx.navKey(key("ArrowLeft", undefined, { altKey: false })), false);
  dialogRoot.childElementCount = 1;
  assert.strictEqual(ctx.navKey(key("ArrowLeft")), false);
  dialogRoot.childElementCount = 0;
  const e = key("ArrowLeft");
  assert.strictEqual(ctx.navKey(e), true);
  assert.ok(e.prevented);
  assert.strictEqual(ctx.state.view, "home");
  ctx.navKey(key("ArrowRight"));
  assert.strictEqual(ctx.state.view, "monitor");
});

test("the command bar offers Go back and Go forward only when there is somewhere to go (UX-008)", () => {
  const { ctx } = navHarness();
  ctx.render();
  const labels = () => ctx.features.commands.filter((c) => !c.when || c.when()).map((c) => c.label);
  assert.ok(!labels().includes("Go back"));
  ctx.go("monitor");
  assert.ok(labels().includes("Go back"));
  assert.ok(!labels().includes("Go forward"));
  ctx.features.commands.find((c) => c.label === "Go back").run();
  assert.strictEqual(ctx.state.view, "home");
  assert.ok(labels().includes("Go forward"));
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.match(html, /id="nav-back"[^>]*title="Back \(Alt\+Left\)" aria-label="Back"/);
  assert.match(html, /id="nav-forward"[^>]*title="Forward \(Alt\+Right\)" aria-label="Forward"/);
});

// UX-011: one error panel, Retry on toasts, and friendlyError checked in the right order (PR-UX-02).
function errorHelpers() {
  const activity = fs.readFileSync(path.join(ui, "activity.js"), "utf8");
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const node = (tag, attrs = {}, ...kids) => {
    const n = { tag, attrs: { ...(attrs || {}) }, kids: kids.flat(Infinity).filter((k) => k != null && k !== false), on: {},
      disabled: false, hidden: false, isConnected: true, className: attrs?.class || "",
      get textContent() { return this.kids.map((k) => (typeof k === "object" ? k.textContent : String(k))).join(""); },
      set textContent(v) { this.kids = [String(v)]; },
      append(...k) { this.kids.push(...k); },
      setAttribute(k, v) { this.attrs[k] = v; },
      addEventListener(type, fn) { (this.on[type] ||= []).push(fn); },
      fire(type) { return Promise.all((this.on[type] || []).map((fn) => fn({ target: this }))); },
      matches: () => false, contains: () => false };
    for (const [k, v] of Object.entries(n.attrs)) if (k.startsWith("on") && v) { n.addEventListener(k.slice(2), v); delete n.attrs[k]; }
    return n;
  };
  const find = (n, pred) => !n || typeof n !== "object" ? null : pred(n) ? n : n.kids.reduce((hit, k) => hit || find(k, pred), null);
  const timers = [];
  const toastEl = node("div");
  const ctx = {
    h: node, btn: (label, _i, onClick, cls, attrs = {}) => node("button", { class: cls, onclick: onClick, ...attrs }, label),
    copied: [], toasts: [], logged: [], state: { view: "home" },
    navigator: { clipboard: { writeText: async (t) => { ctx.copied.push(t); } } },
    logActivity: (i) => ctx.logged.push(i),
    document: { getElementById: () => toastEl, activeElement: null },
    setTimeout: (fn, ms) => { timers.push({ fn, ms, live: true }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].live = false; },
  };
  const code = activity.match(/function friendlyError\([\s\S]*?\n}\n/)[0] + activity.match(/function errorPanel\([\s\S]*?\n}\n/)[0] +
    app.match(/function toast\([\s\S]*?\n}\n/)[0] + app.match(/async function attempt\([\s\S]*?\n}\n/)[0];
  vm.runInNewContext(`${code}; Object.assign(this, { friendlyError, errorPanel, realToast: toast, attempt });`, ctx);
  return { ctx, node, find, timers, toastEl, live: () => timers.filter((t) => t.live) };
}

test("friendlyError checks the engine first, then files on this PC, then Fabric's patterns", () => {
  const { ctx } = errorHelpers();
  const cases = [
    ["The engine stopped answering, so KratosForge restarted it. (sql_page)", /stopped answering/],
    ["The Python sidecar stopped. It will restart on the next action. 404", /local engine stopped while doing this/],
    ["PermissionError: [WinError 4551] Application Control policy has blocked this file", /Windows blocked part of the local engine/],
    ["FileNotFoundError: [Errno 2] No such file or directory: 'C:\\\\data\\\\sales.csv'", /^There is no file or folder at C:\\data\\sales\.csv on this PC\./],
    ["FileNotFoundError: The notebook nb_sales does not exist on this PC", /^The notebook nb_sales does not exist on this PC$/],
    ["PermissionError: [Errno 13] Permission denied: '/home/me/out.csv'", /^This PC didn't allow access to \/home\/me\/out\.csv\./],
    ["IsADirectoryError: [Errno 21] Is a directory: '/data'", /^\/data is a folder/],
    ["FileExistsError: [Errno 17] File exists: 'out'", /^Something already exists at out/],
    ["TimeoutError: The query ran longer than 30 seconds and was stopped.", /^The query ran longer than 30 seconds/],
    ["TimeoutError: [Errno 110] Connection timed out", /Couldn't reach Fabric/],
    ["RuntimeError: 404 Not Found: ItemNotFound", /Fabric couldn't find that item/],
    ["RuntimeError: 403 Forbidden", /don't have permission/],
    ["ConnectionError: Max retries exceeded", /Couldn't reach Fabric/],
    ["ValueError: Pick a lakehouse first.", /^Pick a lakehouse first\.$/],
    ["ZeroDivisionError: division by zero", /inside the app/],
  ];
  for (const [raw, want] of cases) {
    const f = ctx.friendlyError(raw);
    assert.match(f.text, want, raw);
    assert.doesNotMatch(f.text, /^[A-Z][A-Za-z]*(Error|Exception):/, `${raw} starts with an exception name`);
  }
  assert.doesNotMatch(ctx.friendlyError("FileNotFoundError: [Errno 2] No such file or directory: 'x.csv'").text, /Fabric/);
  assert.strictEqual(ctx.friendlyError("FileNotFoundError: [Errno 2] No such file or directory: 'x.csv'").details,
    "FileNotFoundError: [Errno 2] No such file or directory: 'x.csv'");
});

test("errorPanel says what happened, what to do, and keeps the raw text under Details with Copy", async () => {
  const { ctx, find } = errorHelpers();
  let tries = 0;
  const panel = ctx.errorPanel("RuntimeError: 429 TooManyRequests", { label: "Couldn't read the history", retry: async () => { tries++; } });
  assert.strictEqual(panel.attrs.role, "alert");
  assert.strictEqual(panel.kids[0].textContent, "Couldn't read the history");
  assert.match(panel.kids[1].textContent, /^Fabric is busy/);
  assert.match(panel.kids[2].textContent, /^Try again/);
  const retry = find(panel, (n) => n.tag === "button" && n.textContent === "Retry");
  assert.ok(retry, "a Retry button");
  await retry.fire("click");
  assert.strictEqual(tries, 1, "Retry calls the function it was given");
  assert.strictEqual(retry.disabled, false, "Retry can be pressed again after a failure");
  const details = find(panel, (n) => n.tag === "details");
  assert.strictEqual(details.kids[0].textContent, "Details");
  assert.strictEqual(details.kids[1].textContent, "RuntimeError: 429 TooManyRequests");
  await find(details, (n) => n.tag === "button" && n.textContent === "Copy").fire("click");
  assert.deepStrictEqual(ctx.copied, ["RuntimeError: 429 TooManyRequests"]);

  const plain = ctx.errorPanel("Pick a lakehouse first.");
  assert.ok(!find(plain, (n) => n.tag === "button"), "no Retry without retry, no Copy without details");
  assert.ok(!find(plain, (n) => n.tag === "details"));
  assert.strictEqual(ctx.errorPanel("SignInCancelled: cancelled: closed"), null, "a cancelled sign-in shows nothing");
});

test("an error toast is an alert with Retry, and stays while the pointer is on it", async () => {
  const { ctx, find, toastEl, live } = errorHelpers();
  let calls = 0;
  const failing = async () => { calls++; throw "RuntimeError: 429 TooManyRequests"; };
  assert.strictEqual(await ctx.attempt(failing, "Couldn't load runs", { retry: true }), undefined);
  assert.strictEqual(toastEl.attrs.role, "alert");
  assert.match(toastEl.textContent, /^Couldn't load runs: Fabric is busy/);
  const retry = find(toastEl, (n) => n.tag === "button" && n.textContent === "Retry");
  assert.ok(retry, "the toast has Retry");
  assert.strictEqual(live().at(-1).ms, 8000);
  toastEl.fire("mouseenter");
  assert.strictEqual(live().length, 0, "hovering pauses the auto-dismiss");
  toastEl.fire("mouseleave");
  assert.strictEqual(live().length, 1, "leaving starts it again");
  await retry.fire("click");
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(calls, 2, "Retry runs the same call again");

  await ctx.attempt(failing, "No retry here");
  assert.ok(!find(toastEl, (n) => n.tag === "button"), "no Retry unless the caller asks for one");
  ctx.realToast("Saved");
  assert.strictEqual(toastEl.attrs.role, "status");
  assert.strictEqual(live().at(-1).ms, 4000);
});

test("the listed screens show a sentence or the error panel, never the raw exception (UX-011)", () => {
  const read = (f) => fs.readFileSync(path.join(ui, f), "utf8");
  const sc = read("shortcuts.js");
  assert.doesNotMatch(sc, /class: "error-text" \}, String\(err\)/);
  assert.strictEqual((sc.match(/errorPanel\(err, \{ label: "Couldn't read [^"]+", retry: \w+ \}\)/g) || []).length, 3);
  assert.doesNotMatch(sc, /problems: \[String\(err\)\]/);
  assert.doesNotMatch(read("deps.js"), /problem: String\(err\)/);
  assert.match(read("query.js"), /errorPanel\(tab\.error, \{ retry: tab\.errorRetry \}\)/);
  assert.doesNotMatch(read("app.js"), /could not open: \$\{err\}/);
  assert.match(read("app.js"), /attempt\(\(\) => rpc\("list_workspaces"\), "Could not list workspaces", \{ retry: again \}\)/,
    "listing workspaces, a read, offers Retry on its toast");
});

// UX-020: one look for "still loading" and "nothing here yet", built by shared helpers.
function stateHelpers() {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const code = app.match(/function loading\([\s\S]*?\n}\n/)[0] + app.match(/function emptyState\([\s\S]*?\n}\n/)[0];
  const node = (tag, attrs = {}, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k != null && k !== false) });
  const ctx = { h: node, icon: (name, size) => node("svg", { name, size }) };
  vm.runInNewContext(`${code}; Object.assign(this, { loading, emptyState });`, ctx);
  const text = (n) => typeof n === "string" ? n : n.kids.map(text).join("");
  return { ...ctx, text };
}

test("loading() is a status line with a spinner and the label, plus any classes asked for (UX-020)", () => {
  const { loading, text } = stateHelpers();
  const plain = loading();
  assert.strictEqual(plain.attrs.role, "status");
  assert.deepStrictEqual(plain.attrs.class.split(" "), ["loading", "muted-text"]);
  assert.strictEqual(plain.kids[0].attrs.class, "spinner");
  assert.strictEqual(plain.kids[0].attrs["aria-hidden"], "true");
  assert.strictEqual(text(plain), "Loading...");
  const runs = loading("Loading runs...", "monitor-empty");
  assert.deepStrictEqual(runs.attrs.class.split(" "), ["loading", "muted-text", "monitor-empty"]);
  assert.strictEqual(text(runs), "Loading runs...");
  const quiet = loading("Loading notebooks...", "", { live: false });
  assert.strictEqual(quiet.attrs.role, null);
  assert.strictEqual(quiet.kids[0].attrs["aria-hidden"], "true");
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  assert.match(css, /\.loading > \.spinner \{[^}]*animation: spin/);
  assert.match(css, /prefers-reduced-motion: reduce\) \{ \.loading > \.spinner/);
});

test("emptyState() draws the .empty markup: icon, title, text and action, each optional (UX-020)", () => {
  const { emptyState, text } = stateHelpers();
  const action = { tag: "button", attrs: {}, kids: ["New entity"] };
  const full = emptyState({ icon: "branch", title: "No entities yet", text: "Add one from a template.", action });
  assert.strictEqual(full.attrs.class, "empty");
  assert.deepStrictEqual(full.kids.map((k) => k.tag), ["svg", "span", "span", "button"]);
  assert.deepStrictEqual(full.kids[0].attrs, { name: "branch", size: 32 });
  assert.deepStrictEqual([full.kids[1].attrs.class, text(full.kids[1])], ["title", "No entities yet"]);
  assert.deepStrictEqual([full.kids[2].attrs.class, text(full.kids[2])], ["text", "Add one from a template."]);
  const bare = emptyState({ title: "No links yet" });
  assert.deepStrictEqual(bare.kids.map((k) => k.attrs.class), ["title"]);
  const two = emptyState({ title: "x", action: [action, action] });
  assert.strictEqual(two.kids.filter((k) => k.tag === "button").length, 2);
});

test("hand-built empty states and the converted Loading lines use the shared helpers (UX-020)", () => {
  for (const name of ["mesh.js", "meshflow.js", "meshmap.js", "meta.js", "platform.js", "git.js"]) {
    const src = fs.readFileSync(path.join(ui, name), "utf8");
    assert.doesNotMatch(src, /h\("div", \{ class: "empty" \}/, `${name} builds .empty by hand`);
    assert.match(src, /emptyState\(\{/, name);
  }
  for (const name of ["agents.js", "incidents.js", "workloads.js", "schedules.js", "integrations.js"]) {
    assert.match(fs.readFileSync(path.join(ui, name), "utf8"), /loading\(/, name);
  }
});

test("Home draws at once while the component check runs, and calls nothing until it returns (UX-020)", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const starting = app.match(/function startingHome\([\s\S]*?\n}\n/)[0];
  assert.doesNotMatch(starting, /rpc\(|invoke\(|homeCache/);
  // Exactly one live region on the starting Home: one loading() call left live, the rest quiet.
  assert.strictEqual((starting.match(/\bloading\(/g) || []).length, 2);
  assert.strictEqual((starting.match(/\{ live: false \}/g) || []).length, 1);
  assert.match(app, /async function renderHome\(\) \{\n  if \(state\.starting\) \{ setView\(startingHome\(\)\); return; \}/);
  const boot = app.slice(app.lastIndexOf("hydrateIcons();"));
  assert.ok(boot.indexOf("renderHome()") < boot.indexOf("checkComponents()"), "Home must draw before the check starts");
  assert.ok(boot.indexOf("state.starting = false") < boot.indexOf("render();"), "the check's end must clear starting before Home draws");
});

// Once keys come from the platform, the entity's keys card names the database instead of a MERGE.
test("the keys card says where keys come from instead of an LH_Keys MERGE", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const card = meta.match(/function keysCard\(e\)[\s\S]*?\n}\n/)[0];
  assert.match(card, /e\.keys_sql \? h\("pre"[\s\S]*: e\.keys_from \? h\("p"/);
  assert.match(card, /Keys come from \$\{e\.keys_from\}/);
});

// Notebook utilities: Databricks dbutils works on this PC; the snippets and docs say how.
test("Databricks dbutils snippets and docs", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /\["Databricks widget", 'dbutils\.widgets\.text\("run_date"/);
  assert.match(nb, /\["Databricks secret \(Key Vault scope\)", 'secret = dbutils\.secrets\.get\("<vault-name>"/);
  const docs = fs.readFileSync(path.join(ui, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(docs, /\*\*Databricks `dbutils`\*\* is there too/);
  assert.match(docs, /the scope is an \*\*Azure Key Vault name\*\*/);
});

// dbutils.fs: a bare absolute path is DBFS in Databricks, so it never means this PC's disk.
test("dbutils docs say bare absolute paths are refused", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(doc, /A bare absolute path like `\/mnt\/raw` or `\/tmp\/x` is DBFS in Databricks, so it is refused here/);
});
