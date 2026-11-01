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
  // A bad number reaches the engine, which refuses it; NaN would arrive as null and mean "the default".
  assert.strictEqual(JSON.parse(JSON.stringify(proveRulesFrom({ abs: "abc" }))).abs, "abc");
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
  for (const c of cases) {
    const input = Array.isArray(c.in) ? c.in.join("") : c.in;
    assert.strictEqual(ctx.redactReport(input, c.level), c.out, input);
  }
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
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{[^\n]*\n {2}html:not\(\[data-motion-override\]\) \*, html:not\(\[data-motion-override\]\) \*::before/);
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
    platformChangeLines, platformStepText, PLATFORM_STATES, capacityLines, CAPACITY_STATES });`, ctx);
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

test("an exception about to end raises one notice a day, and an ended one is a problem", () => {
  const src = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function exceptionNotice\([\s\S]*?\n}\n/)[0]}; this.exceptionNotice = exceptionNotice;`, ctx);
  const row = { entity: "gold.fact_sales", rule: "custom_code_test", expires: "2026-10-20", status: "expiring",
    notice: "gold.fact_sales: the exception for custom_code_test expires in 5 days (2026-10-20)" };
  const notice = ctx.exceptionNotice(row, "2026-10-15");
  assert.strictEqual(notice.id, "exception:gold.fact_sales:custom_code_test:2026-10-20:2026-10-15");
  assert.strictEqual(notice.status, "info");
  assert.strictEqual(notice.notice, true);
  assert.match(notice.text, /expires in 5 days/);
  assert.strictEqual(ctx.exceptionNotice({ ...row, status: "active" }, "2026-10-15"), null);
  const ended = ctx.exceptionNotice({ ...row, status: "expired" }, "2026-10-21");
  assert.strictEqual(ended.status, "failed");
  assert.strictEqual(ended.notice, false);
  assert.match(src, /\["exceptions", "Exceptions"\]/);
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
  vm.runInNewContext(`${dateHelpers()}${src.match(/function usageBilled\([\s\S]*?\n}\n/)[0]}; this.usageBilled = usageBilled;`, ctx);
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
  vm.runInNewContext(`${code}; Object.assign(this, { dbgDiff, dbgHidden, dbgTrail, dbgBpMap, dbgStackText, dbgKeyAction });`, ctx);
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
    attempt: (fn) => fn(), friendlyError: (e) => ({ text: e.message }),
    rpc: async (m, p) => {
      if (m === "risk_acknowledge") return (acks.push(p), { ok: true });
      if (ctx.result instanceof Error) throw ctx.result;
      return ctx.result;
    } };
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

  // A check that couldn't run never goes ahead silently and never blocks: the reason, then Continue or Cancel.
  ctx.result = new Error("This notebook has no deployment.");
  for (const [label, expected] of [["Cancel", false], ["Continue", true]]) {
    gate = ctx.riskGate(target);
    await settle();
    assert.ok(dialog, "an error shows a dialog");
    assert.deepStrictEqual(Array.from(dialog.actions, (a) => a.label), ["Cancel", "Continue"]);
    press(label);
    assert.strictEqual(await gate, expected);
  }
  assert.deepStrictEqual(acks.map((a) => a.risk_id), ["r2"], "nothing is acknowledged when nothing was checked");
});

test("Fabric's hub is a Monitor tab of its own, so no list shares the page with it (#190)", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(src, /const hubShown = hub && !POP_OUT && !fabricOut;/);
  assert.match(src, /page monitor\$\{hubShown \? "" : " no-fabric"\}/);
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
  assert.match(say("SignInError: broker_error: Status_IncorrectConfiguration, error code 3399614466"), /for this service/);  // #1079
  assert.match(say("SignInError: AADSTS65002: Consent between first party application and first party resource"), /for this service/);
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
  vm.runInNewContext(["attempt", "notebookPayload", "saveNotebook", "openNotebook", "newNotebook", "setNotebookCompute"].map(fn).join("\n") +
    "; this.saveNotebook = saveNotebook; this.openNotebook = openNotebook; this.newNotebook = newNotebook;" +
    " this.setNotebookCompute = setNotebookCompute;", ctx);
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
  assert.match(src, /saveExplicitly\(\(\) => \{ toast\("Saved"\);/);
  assert.match(src, /const el = ok \? anchor\(\) : null;/, "Ctrl+S says Saved when the save failed");
  assert.match(src, /state\.notebook\?\.dirty && !\(await saveNotebook\(\)\)\) return false;/);
  assert.match(src, /if \(await onClose\(\) === false\) return;/);
  // Changing where it runs doesn't say "now runs in Fabric" when that change wasn't saved.
  Object.assign(ctx, { nbWhere: (n) => ({ fabric: n.compute === "fabric" }), renderNotebook: () => {} });
  toasts.length = 0;
  await ctx.setNotebookCompute("fabric");
  assert.ok(toasts.length === 1 && toasts[0][1], JSON.stringify(toasts));
  nb.compute = undefined;
  // Opening a replay bundle doesn't replace a notebook whose save failed.
  const replay = fs.readFileSync(path.join(ui, "replay.js"), "utf8");
  assert.match(replay, /if \(state\.notebook\?\.dirty && !\(await saveNotebook\(\)\)\) return;[^\n]*\n\s*loadIntoState\(nb\);/);
  // A save that works still clears dirty.
  ctx.rpc = async () => ({});
  assert.strictEqual(await ctx.saveNotebook(), true);
  assert.strictEqual(nb.dirty, false);
});

// UX-009: domains, the glossary and platform setup each have one home: domains on Data mesh > Domains,
// the glossary and setup on the Platform page.
test("domains are edited only on Data mesh > Domains, the glossary and platform setup only on Platform", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const groups = meta.match(/const META_TAB_GROUPS = \[[\s\S]*?\n\];/)[0];
  assert.ok(!/"glossary"/.test(groups), "Data mesh still has a Glossary tab");
  const mesh = fs.readFileSync(path.join(ui, "mesh.js"), "utf8");
  const domainsTab = mesh.match(/async function renderMeshDomainsTab\([\s\S]*?\n}\n/)[0];
  for (const edit of ["domainDialog(", "importFabricDomains(", "platform_delete_domain"]) {
    assert.ok(!domainsTab.includes(edit), `Domains > This project still calls ${edit}`);
  }
  const platform = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  const orgDomains = platform.match(/async function renderMeshOrgDomainsTab\([\s\S]*?\n}\n/)[0];
  assert.match(orgDomains, /importFabricDomains\(rerender\)/);
  assert.match(orgDomains, /goPlatform\("glossary"\)/);
  assert.match(groups, /\["domains", "Domains", "mesh"\]/);
  assert.ok(!/"domains"/.test(platform.match(/const PLATFORM_TAB_BAR = \[[\s\S]*?\n\];/)[0]), "Platform still has a Domains tab");
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
  assert.deepStrictEqual(always, ["promote", "syncKeys", "varlib", "quarantineReplay", "risk", "vscode"]);
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
  vm.runInNewContext(`const WRITE_PASS_DAYS = 7; ${dateHelpers()}${src.match(/function savedWriteRow\([\s\S]*?\n}\n/)[0]}; this.f = savedWriteRow;`, ctx);
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

test("Monitor's tabs are by source, and Operations holds the workload views in the mockup's order (UX-010)", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const literal = src.match(/const MONITOR_OPS = (\[[\s\S]*?\n\]);/)[1];
  const ops = JSON.parse(JSON.stringify(vm.runInNewContext(literal)));
  assert.deepStrictEqual(ops.map((o) => o.label), ["Workloads", "Queue", "Agents", "Run as", "Incidents"]);
  // Every view keeps its panel id, so links and commands that open it still land there.
  assert.deepStrictEqual(ops.map((o) => o.panel), ["workloads", "queue", "agents", "runas", "incidents"]);
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  for (const o of ops) assert.ok(features.includes(`${o.feature}:`), `${o.feature} is a registered preview feature`);
  const sources = vm.runInNewContext(`${/const MONITOR_SOURCES = \[[\s\S]*?\];/.exec(src)[0].replace("const ", "")}; MONITOR_SOURCES`);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(sources.map(([, label]) => label))), ["KratosForge", "Fabric"]);
  // Each tab's pages are on the view row's switcher.
  const pages = src.slice(src.indexOf("const pages = source ==="), src.indexOf("const shown = pages.filter"));
  assert.deepStrictEqual([...pages.matchAll(/"(Monitoring hub|Schedules|Runs|Operations)"/g)].map((m) => m[1]),
    ["Monitoring hub", "Schedules", "Runs", "Schedules", "Operations"]);
});

test("Home's All schedules opens Fabric schedules when every upcoming schedule is in Fabric, else KratosForge schedules", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const at = src.indexOf('btn("All schedules"');
  const link = src.slice(at, src.indexOf('"tool link small")', at));
  assert.match(link, /items\.length && items\.every\(\(x\) => x\.where === "Fabric"\) \? "fabric_schedules" : "schedules"/);
  // "Fabric" is the where Home gives a Fabric schedule's row.
  assert.match(src, /where: "Fabric", text: sc\.text/);
});

test("Monitor > KratosForge > Operations > Agents explains agents beside the list, with links to sections of agent.md (testing item 34)", () => {
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
  assert.strictEqual(pc.text, "No environment");
  assert.match(pc.attrs.title, /this PC's schedules/);
  const fabric = ctx.runEnvCell({ env: "", where: "fabric" });
  assert.strictEqual(fabric.text, "No environment");
  assert.match(fabric.attrs.title, /Fabric workspace/);
  assert.doesNotMatch(fabric.attrs.title, /this PC/);
});

test("Monitor > Runs calls a job file's run a job run, not a notebook write (PB-079)", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function historyKind\([\s\S]*?\n}\n/)[0]}; this.historyKind = historyKind;`, ctx);
  assert.strictEqual(ctx.historyKind({ job: "Notebook write to Sales" }), "Notebook write");
  assert.strictEqual(ctx.historyKind({ job: "daily_sales" }), "Job run");
  assert.strictEqual(ctx.historyKind({}), "Job run");
  assert.match(src, /activity: r\.job, kind: historyKind\(r\)/);
  assert.doesNotMatch(src, /kind: "Notebook write"/);
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
  // A page on the Set up or Govern tab is named with its tab: "Platform > Set up > Business keys".
  const bar = vm.runInNewContext(`${/const PLATFORM_TAB_BAR = \[[\s\S]*?\n\];/.exec(read("platform.js"))[0].replace("const ", "")}; PLATFORM_TAB_BAR`);
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
    for (const m of src.matchAll(/Platform > (?:(Set up|Govern) > )?([A-Z][\w ]*?)(?=[).,"'\n]| > )/g)) {
      const menu = bar.find(([, label]) => label === m[1]);
      const ok = m[1] ? menu?.[2].some((id) => platform.find(([t]) => t === id)[1] === m[2]) : bar.some(([, label, items]) => items && label === m[2]) || plat.has(m[2]) && !bar.some(([, , items]) => items?.some((id) => platform.find(([t]) => t === id)[1] === m[2]));
      if (!ok) bad.push(`${path.basename(f)}: Platform > ${m[1] ? `${m[1]} > ` : ""}${m[2]}`);
    }
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

test("Monitor > KratosForge > Operations > Workloads and Agents carry the shared How this works panel, each linked to its guide section (testing item 18)", () => {
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
  assert.match(how, /adds its lakehouse and table findings there as new rows, never "\n\s*\+ "the names of people or cost/);
  assert.match(xray, /function xrayShared\(shared\)/);
  assert.match(how, /anyone who can read that lakehouse can read them/);
  assert.match(how, /a courtesy, not security/);
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

// #856 (PB-083): the catalog's Endorsed items filter is off, and its reason (the button's title) says
// where endorsement really shows. There is no embedded Fabric catalog to point at.
test("the Endorsed items filter's reason names the admin tenant scan, data products and Open Fabric's catalog", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const py = fs.readFileSync(path.join(__dirname, "..", "..", "sidecar", "fabric_desktop", "service.py"), "utf8");
  const reason = py.match(/ENDORSED_FILTER_REASON = \(([\s\S]*?)\)\n/)?.[1].replace(/"\s*"/g, "") || "";
  assert.match(reason, /admin tenant scan/);
  assert.match(reason, /Data mesh > Products/);
  assert.match(reason, /Scan now/);
  assert.match(reason, /Fabric administrator/);
  assert.match(reason, /Open Fabric's catalog/);
  assert.match(app, /btn\("Open Fabric's catalog"/, "the button the reason names is on the catalog page");
  assert.doesNotMatch(py + app, /catalog below|embedded on the same page/i);
  const docs = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "troubleshooting.md"), "utf8");
  const row = docs.split("\n").find((l) => l.includes("**Endorsed items** is greyed out")) || "";
  assert.match(row, /admin tenant scan/);
  assert.match(row, /Scan now/);
  assert.match(row, /Open Fabric's catalog/);
  assert.doesNotMatch(row, /embedded/);
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

// Plan step 7: OneLake > Access > All workspaces reads the access cache and flags what's worth a look.
test("the access cache view flags what's worth a look and asks before setting anything up", () => {
  const src = fs.readFileSync(path.join(ui, "access-cache.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src}\nthis.flags = accessCacheFlags; this.filters = ACCESS_CACHE_FILTERS;`, ctx);
  const w = {
    people: [{ name: "Ann", type: "User", role: "Admin", guest: false }, { name: "Readers", type: "Group", role: "Viewer", guest: false },
      { name: "Bo", type: "User", role: "Viewer", guest: true }, { name: "Loader", type: "ServicePrincipal", role: "Admin", guest: false }],
    lakehouses: [{ item_name: "LH_Gold", known: true, roles: [] }, { item_name: "LH_Unread", known: false, roles: [] }],
    summary: { guests: 1 }, stale: false,
  };
  const flags = ctx.flags(w);
  assert.strictEqual(flags.map((f) => f.kind).join(), "byname,guests,apps,nosecurity");
  assert.strictEqual(flags[3].lakehouse.item_name, "LH_Gold");  // a lakehouse never read isn't flagged as having no roles
  const keep = Object.fromEntries(ctx.filters.map(([id, , fn]) => [id, fn]));
  assert.ok(keep.byname(w, flags) && keep.guests(w, flags) && !keep.stale(w, flags));
  assert.ok(keep.stale({ ...w, lakehouses: [{ stale: true }] }, []));
  // The first refresh and the role change only happen after the person confirms the sidecar's plan.
  assert.match(src, /includes\("Confirm to continue"\)[\s\S]*confirmDialog[\s\S]*approve: true/);
  const estate = fs.readFileSync(path.join(ui, "estate.js"), "utf8");
  const panel = estate.match(/howItWorks\("onelake\.access-all", \{[\s\S]*?\n    \}\)/)?.[0] || "";
  assert.match(panel, /changes: "Refresh changes adds rows to the access cache in the central lakehouse/);
  assert.match(panel, /OneLake security roles/);
});

// HD-052 (UI-8): pages are built with h(), which only makes text nodes, so data can't become markup.
// A new raw HTML (or code) sink in app/ui fails here unless it is reviewed and added below with why
// it is safe. Keys are the file and the exact trimmed line, so editing an allowed line needs a new review.
test("no raw HTML sinks in app/ui outside the reviewed list", () => {
  const sink = /\.(?:innerHTML|outerHTML)\b|\binsertAdjacentHTML\b|\bdocument\.write(?:ln)?\b|\bcreateContextualFragment\b|\bDOMParser\b|\b(?:set|parse)HTMLUnsafe\b|\bsrcdoc\b|\beval\s*\(|\bnew Function\b|\bFunction\s*\(/;
  const allowed = new Set([
    // markdown(): a fixed placeholder string, no data.
    `app.js: wrap.innerHTML = '<p class="placeholder">Empty Markdown cell. Double-click to edit.</p>';`,
    // markdown(): every piece of cell text goes through inlineMd(), which esc()apes &<>"' before adding
    // <code>, <strong>, <em> and <u title>; headings and lists add only fixed tags. See the two
    // Markdown tests at the top of this file.
    `app.js: wrap.innerHTML = html.join("");`,
    // sqlParsePlan(): Fabric's showplan parsed as XML ("application/xml" runs no script and loads nothing);
    // the document is never put on the page, only attribute values are read and drawn as text with h().
    `sqlplan.js: const doc = new DOMParser().parseFromString(String(xml || ""), "application/xml");`,
    // Data mesh map links: path coordinates are numbers from getBoundingClientRect, and the only data
    // (project and product ids in <title>) goes through esc().
    `meshmap.js: svg.innerHTML = \`<defs><marker id="mesh-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z"/></marker></defs>\${paths.join("")}\`;`
  ]);
  const found = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) { if (e.name !== "vendor") walk(p); continue; }
      if (!/\.(js|html)$/.test(e.name)) continue;
      const rel = path.relative(ui, p).split(path.sep).join("/");
      for (const line of fs.readFileSync(p, "utf8").split("\n")) if (sink.test(line)) found.push(`${rel}: ${line.trim()}`);
    }
  };
  walk(ui);
  assert.deepStrictEqual(found.filter((f) => !allowed.has(f)), [], "a new raw HTML sink: build it with h(), or review it and add it to the list");
  assert.deepStrictEqual([...allowed].filter((a) => !found.includes(a)), [], "an allowed sink is gone from the code: remove it from the list");
});

// HD-052: no href, src or other URL attribute is built from data, and nothing navigates the app's
// window. Links open through open_link (https and mailto only) or openInFabric. A new URL sink
// fails here unless it is reviewed and added below with why it is safe; literal values pass.
test("no URL sinks built from data in app/ui outside the reviewed list", () => {
  const attr = "href|src|srcset|action|formaction|xlink:href|poster|data|ping|background";
  const sink = new RegExp(
    `\\b(?:href|src|srcset|formaction|poster|ping)\\s*:\\s*(?!["'][^"'\`$]*["'])[^\\s,}]` +  // h("a", { href: x }): only a quoted literal passes
    `|[{,]\\s*(?:href|src|srcset|formaction|poster|ping)\\s*[,}]` +  // h("a", { href }): shorthand takes a variable
    `|\\.(?:href|src|srcset|action|formAction|poster|ping)\\s*=(?!=)` +  // node.src = x
    `|setAttribute(?:NS)?\\([^)]*["'](?:${attr})["']` +
    `|\\bwindow\\.open\\(|(?<!(?:const|let|var)\\s+)\\blocation\\s*=(?!=)|\\blocation\\.(?:href\\s*=(?!=)|assign|replace)\\b`);
  const allowed = new Set([
    // Image output: only the picture types imageOutputOk() accepts, with base64 data, and <img> never runs script.
    "notebook.js: return h(\"div\", { class: \"output\" }, h(\"img\", { class: \"out-img\", alt: \"Chart from this cell\", src: `data:${o.mime};base64,${o.data}` }));",
    // HTML output frame: the shell's fixed frame URL, no data (HD-020).
    "notebook.js: src: window.__TAURI__.core.convertFileSrc(\"frame\", \"nbout\") });",
    // Vega charts: a fixed file in vendor/.
    "notebook.js: script.src = \"vendor/vega/vega-frame.js\";",
  ]);
  const found = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) { if (e.name !== "vendor") walk(p); continue; }
      if (!/\.(js|html)$/.test(e.name)) continue;
      const rel = path.relative(ui, p).split(path.sep).join("/");
      for (const line of fs.readFileSync(p, "utf8").split("\n")) if (sink.test(line)) found.push(`${rel}: ${line.trim()}`);
    }
  };
  walk(ui);
  assert.deepStrictEqual(found.filter((f) => !allowed.has(f)), [], "a new URL sink: use a literal, open_link or openInFabric, or review it and add it to the list");
  assert.deepStrictEqual([...allowed].filter((a) => !found.includes(a)), [], "an allowed URL sink is gone from the code: remove it from the list");
});

test("an image output is shown only for picture types with base64 data", () => {
  const src = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const fn = src.match(/const IMAGE_OUTPUT_TYPES[\s\S]*?\nfunction imageOutputOk[\s\S]*?\n}\n/)[0];
  const imageOutputOk = new Function(`${fn}; return imageOutputOk;`)();
  assert.ok(imageOutputOk({ mime: "image/png", data: "iVBORw0KGgo=" }));
  assert.ok(imageOutputOk({ mime: "image/svg+xml", data: "PHN2Zy8+\n" }));
  for (const bad of [{ mime: "text/html", data: "PGI+" }, { mime: "image/png;x", data: "AAAA" },
    { mime: "image/png", data: "AAAA\" onerror=x" }, { mime: "image/png", data: 1 }, { mime: undefined, data: "AAAA" }]) {
    assert.ok(!imageOutputOk(bad), JSON.stringify(bad));
  }
});

// HD-020 (SH-1, UI-1): the page has a Content Security Policy, and notebook HTML output runs on
// its own origin, never in the app's, where the Tauri API is.
test("the app has a strict CSP and notebook HTML output stays off the app's origin", () => {
  const conf = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "src-tauri", "tauri.conf.json"), "utf8"));
  const csp = conf.app.security.csp;
  assert.ok(csp, "tauri.conf.json must set app.security.csp");
  const text = typeof csp === "string" ? csp : Object.entries(csp).map(([k, v]) => `${k} ${[].concat(v).join(" ")}`).join("; ");
  const directives = Object.fromEntries(text.split(";").map((d) => d.trim().split(/\s+/)).filter((d) => d[0]).map(([k, ...v]) => [k, v]));
  assert.ok(!/unsafe-eval|wasm-unsafe-eval/.test(text), text);
  const scripts = directives["script-src"] || directives["default-src"] || [];
  assert.ok(scripts.length && !scripts.some((s) => /unsafe-inline|\*|^https?:$|^data:$|^blob:$/.test(s)), `script-src: ${scripts}`);
  assert.deepStrictEqual(directives["object-src"], ["'none'"]);
  assert.ok(!(directives["frame-src"] || []).includes("'self'"), "notebook output must not be framed from the app's origin");

  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(!/<script(?![^>]*\bsrc=)[^>]*>/.test(html), "index.html has an inline script");
  assert.ok(!/\son[a-z]+\s*=/i.test(html), "index.html has an inline event handler");

  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const frame = /function htmlFrame\([\s\S]*?\n}\n/.exec(nb)[0];
  assert.match(frame, /sandbox: "allow-scripts"/);
  assert.ok(!/allow-same-origin|srcdoc/.test(frame), frame);
  assert.match(frame, /convertFileSrc\("frame", "nbout"\)/);
  assert.ok(directives["frame-src"].includes("nbout:"), "frame-src must allow the nbout: output frame");
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
  assert.match(nbjs, /rpc\("start_cell", \{ code: cell\.source \+ nbInputSuffix\(cell\), lakehouse_ref: nbLakehouse\(nb\)/);
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
  assert.match(css, /\.splash-still, \.splash-still :is\(\.splash-emblem/);  // Off, Minimal and reduced motion show the forge still
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
  assert.match(platform, /!\(p\.tab === "keys" && p\.keysShared\)\);\n[\s\S]*?perEnv \? envSeg : null/);
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
  // On KratosForge schedules, first after the counts.
  assert.match(s, /failed\(pc\.filter\([^\n]*\)\),\n      setAsideNotice\(res\.pc_set_aside\),/);
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
    setKernel() {}, renderNotebook() {}, forgeEvent() {}, profileEarned() {}, updateResults() {}, registerFeature() {}, previewShown: () => true,
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
    motionLeave: () => null,  // motion.js: the fade after a dialog goes
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
function motionHarness({ setting = true, reduced = false, hidden = false, settings = null, appLoaded = true } = {}) {
  let clock = 0;
  let timers = [];
  let watch = null;
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
    state: { settings: settings || { forge_animations: { value: setting } } },
    document: { hidden, documentElement: { dataset: {} }, createElementNS: () => node(), getElementById: (id) => (id === "announce" ? live : null),
      addEventListener() {}, hasFocus: () => true },
    addEventListener() {},
    getComputedStyle: () => ({ position: "static" }),
    innerHeight: 900,
    matchMedia: () => ({ matches: reduced, addEventListener() {} }),
    setTimeout: (fn, ms) => { const t = { at: clock + ms, fn }; timers.push(t); return t; },
    clearTimeout: (t) => { timers = timers.filter((x) => x !== t); },
    Date: { now: () => clock },
    performance: { now: () => clock },
    // The long-task watch, fed by longTasks() below instead of the machine's load.
    PerformanceObserver: Object.assign(function (cb) { watch = cb; this.observe = () => {}; this.disconnect = () => { watch = null; }; },
      { supportedEntryTypes: ["longtask"] }),
    h: (tag, attrs = {}, ...children) => {
      const n = node(attrs.class || "");
      for (const [k, v] of Object.entries(attrs)) if (k !== "class") n.attrs[k] = v;
      n.append(...children.flat());
      return n;
    },
  };
  if (!appLoaded) delete ctx.state;  // app.js, which declares state, loads after motion.js
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(ui, "motion.js"), "utf8"), ctx);
  ctx.advance = (ms) => {
    clock += ms;
    for (let due; (due = timers.filter((t) => t.at <= clock).sort((a, b) => a.at - b.at)[0]);) { timers = timers.filter((t) => t !== due); due.fn(); }
  };
  const page = node();  // anchors the tests make sit on the page, as real ones do
  ctx.node = (cls) => { const n = node(cls); page.append(n); return n; };
  ctx.live = live;
  // Long main-thread tasks of these durations (ms), each starting now.
  ctx.longTasks = (...durations) => watch?.({ getEntries: () => durations.map((duration) => ({ startTime: clock, duration })) });
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
    state: { notebook: { cells, runningAll } }, calls, cells,
    document: { querySelector: (sel) => ({ sel }) },
    LONG_RUN_SECONDS: 10,
    forgeEvent: (name, anchor) => { calls.push(["fx", name, anchor.sel]); return {}; },
    announce: (text) => calls.push(["say", text]),
  };
  vm.runInNewContext(`${code}; this.cellFx = cellFx;`, ctx);
  return ctx;
}

test("Forge animations in the notebook: spark on success, ember on failure, nothing for Stop or Cancel", () => {
  const one = cellFxHarness(false);
  one.cells[1].status = "ok";
  one.cellFx(one.cells[1]);
  assert.deepStrictEqual(one.calls, [["say", "Cell 2 succeeded"], ["fx", "notebook.cell.succeeded", '[data-cell="b"] .cell-status > span']]);
  one.calls.length = 0;
  one.cells[2].status = "error";
  one.cellFx(one.cells[2]);
  assert.deepStrictEqual(one.calls, [["say", "Cell 3 failed"], ["fx", "notebook.cell.failed", '[data-cell="c"] .cell-status > span']]);
  // A cell that ran 10 s or more gets the Wolf Run in the status bar instead of a spark.
  one.calls.length = 0;
  Object.assign(one.cells[1], { status: "ok", seconds: 12 });
  one.cellFx(one.cells[1]);
  assert.deepStrictEqual(one.calls, [["say", "Cell 2 succeeded"], ["fx", "notebook.long.succeeded", ".statusbar"]]);
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
  assert.deepStrictEqual(all.calls, [["fx", "notebook.cell.failed", '[data-cell="b"] .cell-status > span']]);
});

test("Forge animations in the notebook: runs of several cells use the wolf track and one announcement", () => {
  const src = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const run = src.match(/async function runCells\([\s\S]*?\n}\n/)[0];
  assert.match(run, /if \(!ok \|\| nb\.cancelRequested\) \{\n\s+track\?\.stop\(\);/, "a failed or cancelled run keeps its track");
  assert.match(run, /code\.length > 1 \? wolfTrack\(\(\) => document\.querySelector\("\.statusbar"\), "Notebook run", "Cell", "on this PC"\) : null/);
  assert.match(run, /if \(code\.length > 1\) forgeEvent\("notebook\.run\.started", document\.querySelector\("button\[data-run\]"\)\);/);
  assert.match(run, /announce\(cell\.status === "error" \? `Run stopped: cell \$\{[^}]+\} failed` : "Run stopped"\);/, "a stopped run says nothing");
  assert.match(run, /track\?\.set\(i \+ 1, code\.length\);/);
  assert.match(run, /track\?\.arrive\(\);\n\s+if \(code\.length\) announce\(`Run finished/);
  assert.match(run, /\} finally \{\n\s+nb\.runningAll = false;/);
  assert.match(src, /refreshVariables\(\);\n  cellFx\(cell\);  \/\/ after dbgAfterRun, which draws the results again\n  return ok;/);
});

test("Motion & effects settings: four levels, family switches, reduced-motion choice, lockable, applied at once; CSS honours them", () => {
  const s = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const card = s.match(/function motionSettingsCard\([\s\S]*?\n}\n/)[0];
  assert.match(card, /h\("h2", \{\}, "Appearance"\),\n\s+h\("h3", \{\}, "Layout"\)[\s\S]*Object\.keys\(LAYOUT_CHOICES\)\.map\(choice\),\n\s+h\("h3", \{\}, "Motion & effects"\)/);
  assert.match(card, /type: "radio", name: "motion_level"/);
  assert.match(card, /disabled: locked\("motion_level"\) \? true : null, onchange: \(\) => save\(\{ motion_level: n \}\)/);
  assert.match(card, /\[0, 1, 2, 3\]\.map\(levelRow\)/);
  assert.match(card, /toggle\(key, label, s\[key\]\?\.value \?\? key !== "motion_achievements", level < 2\)/, "family switches don't grey out below Refined");
  assert.match(card, /disabled: disabled \|\| locked\(key\) \? true : null/);
  assert.match(card, /"Respect Windows' reduced-motion setting"/);
  for (const key of ["motion_wolf", "motion_sparks", "motion_ambient"]) assert.match(s, new RegExp(`\\["${key}", "`));
  // Achievement celebrations shows only while Profile (Preview) can award something (#361).
  assert.match(s, /const motionFamilies = \(\) => \(previewShown\(\) \? \[\.\.\.MOTION_FAMILIES, MOTION_ACHIEVEMENTS\] : MOTION_FAMILIES\);/);
  assert.match(card, /motionFamilies\(\)\.map\(/);
  assert.match(s, /previewShown\(\) \? row\("Achievement Reveal", [^\n]*\n\s+\(\) => forgeEvent\("achievement\.unlocked", award\)\) : null,/);
  assert.match(s, /\["motion_ambient", "Running indicators",/);
  assert.match(s, /const display = s\.motion_level \? motionSettingsCard\(s, locked\) : null;/);
  assert.match(s, /\[account, updates, display, previews,/);
  assert.match(s, /if \(res\) state\.settings = res;\n  applyMotion\(\);/);
  assert.match(s, /state\.settings = await rpc\("app_settings"\)[^\n]*\n  applyMotion\(\);/);
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  assert.match(css, /html\[data-motion="off"\] \.fx, html\[data-motion="off"\] \.wolf \{ display: none; \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{ html:not\(\[data-motion-override\]\) :is\(\.fx, \.wolf\) \{ display: none; \}/);
  assert.match(css, /html\[data-fx-wolf="off"\] \.wolf, html\[data-fx-sparks="off"\] :is\(\.fx-spark, \.fx-fail, \.fx-strike, \.fx-cool\),/);
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
    ["All notebooks", "Search inside notebooks", "Open a notebook file", "Save a copy of this notebook", "New notebook folder",
      "Move this notebook to a folder"]);
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
  assert.match(fs.readFileSync(path.join(ui, "styles.css"), "utf8"), /\.splash-actions, \.splash-optout\) \{ animation: none; \}/);
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
  assert.match(ctx.f("Dev runs", "Every 30 minutes", true, false)[1], /turn it Off in Monitor > Fabric > Schedules\.$/);
  assert.match(s, /confirmRemoveSchedule\(o\.what, s\.text, true, false\)/);
});

// Notebook sessions: each open notebook keeps its own kernel state, a running notebook keeps
// running when another is opened, and reopening shows its outputs (kernel_sessions.py).
test("each notebook has its own kernel state, and in-engine notebooks share one busy kernel", () => {
  const a = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const n = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const grab = (src, name) => new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`).exec(src)[0];
  const ctx = { ownProcess: false };
  vm.createContext(ctx);
  vm.runInContext(`const state = {}; const openNotebooks = new Map(); const KEPT_NOTEBOOKS = 2;
    function kernelInOwnProcess() { return ownProcess; }
    ${/Object\.defineProperty\(state, "kernel", \{[\s\S]*?\n\}\);\n/.exec(a)[0]}
    ${grab(n, "kernelBusy")}${grab(a, "nbRunning")}${grab(a, "keepOutputs")}${grab(a, "rememberNotebook")}
    this.state = state; this.open = openNotebooks; this.keepOutputs = keepOutputs; this.remember = rememberNotebook;`, ctx);
  const nb = (name, cells = []) => ({ name, cells, kernelStatus: "idle" });
  const A = nb("A", [{ cell_id: "c1", source: "x = 1", outputs: [{ text: "1" }], count: 3, status: "ok" }]);
  const B = nb("B");
  ctx.state.notebook = A; ctx.remember(A);
  ctx.state.notebook = B; ctx.remember(B);
  A.kernelStatus = "busy";
  assert.strictEqual(ctx.state.kernel, "busy");  // inside the engine, one shared kernel
  ctx.ownProcess = true;
  assert.strictEqual(ctx.state.kernel, "idle");  // B's own session is free
  ctx.state.kernel = "stopped";
  assert.strictEqual(B.kernelStatus, "stopped");
  assert.strictEqual(A.kernelStatus, "busy");
  // Reopened from disk: unchanged cells keep their outputs; changed ones don't.
  const again = nb("A", [{ cell_id: "c1", source: "x = 1", outputs: [] }, { cell_id: "c2", source: "y", outputs: [] }]);
  ctx.keepOutputs(A, again);
  assert.strictEqual(again.cells[0].count, 3);
  assert.deepStrictEqual(again.cells[1].outputs, []);
  // Only idle notebooks are forgotten past the limit.
  ctx.state.notebook = nb("C"); ctx.remember(ctx.state.notebook);
  assert.deepStrictEqual([...ctx.open.keys()], ["A", "C"]);
  // A cell keeps polling its own notebook's session after another notebook is opened.
  assert.match(n, /async function runCell\(cell, \{ timeLimit = true, code = null, debug = false, nb = state\.notebook \} = \{\}\)/);
  assert.match(n, /rpc\("poll_cell", \{ version: snap\.version, wait: 0\.4, notebook: nb\.name \}\)/);
  assert.match(n, /rpc\("interrupt_cell", \{ notebook: nb\?\.name \}\)/);
  assert.match(n, /rpc\("stop_kernel_process", \{ notebook: nb\?\.name \}\)/);
  assert.match(n, /sessionsButton\(\),/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="sessions\.js"><\/script>/);
});

// Notebook environments: packages are added through a confirm that says where they come from,
// and the dialog never opens a confirm on top of itself (showDialog would settle it twice).
test("notebook environments confirm installs and close their dialog before asking", () => {
  const e = fs.readFileSync(path.join(ui, "nbenv.js"), "utf8");
  const install = /async function install\(\) \{[\s\S]*?\n  \}\n/.exec(e)[0];
  assert.ok(install.indexOf("closeDialog();") < install.indexOf("confirmDialog("));
  assert.match(install, /from PyPI, or from the package index this PC's pip is set up for/);
  assert.match(install, /install only ones you trust/);
  const del = /async function del\(\) \{[\s\S]*?\n  \}\n/.exec(e)[0];
  assert.ok(del.indexOf("closeDialog();") < del.indexOf("confirmDialog("));
  assert.match(del, /\{ danger: true \}/);
  assert.match(e, /return preview\("environments",/);
  assert.match(fs.readFileSync(path.join(ui, "notebook.js"), "utf8"), /sessionsButton\(\), environmentButton\(nb\),/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="sessions\.js"><\/script>\s*<script src="nbenv\.js"><\/script>/);
});

test("breakpoints follow their lines, and the debugger keys match VS Code", () => {
  const { dbgBpMap, dbgStackText, dbgKeyAction } = dbgHelpers();
  // Two lines inserted above line 3; line 5 deleted onto line 4; line 9 is past the end now.
  const moved = { 1: 1, 3: 5, 4: 6, 5: 6, 9: 11 };
  assert.deepStrictEqual([...dbgBpMap([1, 3, 4, 5, 9], (n) => moved[n] ?? null, 10)], [1, 5, 6]);
  assert.strictEqual(dbgStackText([{ function: "(cell)", line: 5 }, { function: "double", line: 3 }]), "(cell) line 5 › double line 3");
  const key = (k, extra = {}) => dbgKeyAction({ key: k, shiftKey: false, ctrlKey: false, altKey: false, metaKey: false, ...extra });
  assert.strictEqual(key("F5"), "continue");
  assert.strictEqual(key("F10"), "next");
  assert.strictEqual(key("F11"), "step");
  assert.strictEqual(key("F11", { shiftKey: true }), "out");
  assert.strictEqual(key("F5", { ctrlKey: true }), null);
  assert.strictEqual(key("Enter"), null);
});

test("the notebook editor wires breakpoints and the debugger through debugging.js", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const dbg = fs.readFileSync(path.join(ui, "debugging.js"), "utf8");
  for (const hook of ["dbgEditorMarks(cell)", "dbgEdited(cell, u)", "dbgToggleAtCursor(cell)", "dbgStartArgs(cell, debug)",
    "dbgSnapshot(cell, snap)", "dbgPausedPanel(cell)", "dbgGutterClick(cell, view, line)", "debugCell(cell)"]) {
    assert.ok(nb.includes(hook), hook);
  }
  assert.match(dbg, /rpc\("debug_cell"/);
  // Breakpoints are a Preview feature: nothing is sent or shown while it is hidden.
  assert.match(dbg, /function dbgBreakpoints\(cell\) \{\n  return dbgOn\(\)/);
  assert.match(dbg, /function dbgStartArgs\(cell, debug\) \{\n  const lines = dbgBreakpoints\(cell\);\n  if \(!dbgOn\(\)\) return \{\};/);
  // The debugger keys are in the Keyboard shortcuts dialog, only while the Preview feature is shown.
  assert.match(dbg, /registerShortcuts\("Notebook debugger", \[[\s\S]*keys: \["F9"\][\s\S]*keys: \["Shift\+F11"\][\s\S]*\]\);/);
  assert.strictEqual((dbg.match(/when: \(\) => state\.view === "notebook" && dbgOn\(\)/g) || []).length, 5);
});

// Item 18 follow-up: Queue, Incidents and Run as each have How this works beside the list, like
// Workloads and Agents, linked to their own section of the guide.
test("Queue, Incidents and Run as say how they work beside the list", () => {
  const guide = fs.readFileSync(path.join(ui, "..", "..", "docs", "user-guide.md"), "utf8");
  for (const [file, fn, id, anchor] of [["queue.js", "queuePanel", "monitor.queue", "queue-preview"],
    ["incidents.js", "incidentsPanel", "monitor.incidents", "incidents-preview"], ["runas.js", "runAsPanel", "monitor.runas", "run-as-preview"]]) {
    const src = fs.readFileSync(path.join(ui, file), "utf8");
    const body = new RegExp(`function ${fn}\\(\\) \\{[\\s\\S]*?\\n\\}\\n`).exec(src)[0];
    assert.match(body, new RegExp(`withHowItWorks\\(box, howItWorks\\("${id.replace(".", "\\.")}"`), file);
    for (const part of ["reads:", "changes:", "results:", `anchor: "${anchor}"`]) assert.ok(body.includes(part), `${file} ${part}`);
    const heading = { "queue-preview": "### Queue (Preview)", "incidents-preview": "### Incidents (Preview)", "run-as-preview": "### Run as (Preview)" }[anchor];
    assert.ok(guide.includes(heading), heading);
  }
  const inc = fs.readFileSync(path.join(ui, "incidents.js"), "utf8");
  assert.match(inc, /Check now never posts/);
});

// Cost allocation: the workload page shows its runs' capacity use from workload_capacity_use, with
// each figure's classification, and says runs on this PC aren't counted (QA-008 wording).
test("the workload page shows its capacity use, classified, and never a figure for this PC", () => {
  const src = fs.readFileSync(path.join(ui, "workloads.js"), "utf8");
  assert.match(src, /workloadIncidentsCard\(w\),\n    workloadCapacityCard\(w\),/);
  const card = /function workloadCapacityCard\(w\) \{[\s\S]*?\n\}\n/.exec(src)[0];
  assert.match(card, /rpc\("workload_capacity_use", \{ id: w\.id, days: 30 \}\)/);
  assert.match(card, /measureFigure\("Capacity use of its Fabric runs", u\.total\)/);
  assert.match(card, /OneLake reads and writes still use some capacity/);
  assert.doesNotMatch(card, /no Fabric capacity|saved|savings of/i);
});

test("a replay notebook offers to delete what its replays wrote, with a danger confirm", () => {
  const src = fs.readFileSync(path.join(ui, "replay.js"), "utf8");
  const fn = src.match(/function replayDeleteOutputs\([\s\S]*?\n}\n/)[0];
  assert.match(fn, /confirmDialog\([\s\S]*\{ danger: true \}/);
  assert.match(fn, /rpc\("replay_delete_outputs"/);
  assert.match(src, /disabled: busy \|\| c\.live, title: c\.live \? "End the replay first\."/);
});

// Motion & effects levels (motion.js): what each level, family switch and the reduced-motion choice let play.
test("Motion levels: Minimal plays no branded effects, Refined and Expressive do, and <html> says which", () => {
  const at = (level, extra = {}) => motionHarness({ settings: { motion_level: { value: level }, ...extra } });
  const minimal = at(1);
  assert.strictEqual(minimal.fx(minimal.node(), "spark"), null);
  assert.strictEqual(minimal.motionDiagnostics().skipped.level, 1);
  minimal.applyMotion();
  assert.strictEqual(minimal.document.documentElement.dataset.motionLevel, "1");
  assert.strictEqual(minimal.document.documentElement.dataset.motion, "off");
  for (const level of [2, 3]) {
    const ctx = at(level);
    assert.ok(ctx.fx(ctx.node(), "spark"), `level ${level} played no spark`);
    ctx.applyMotion();
    assert.strictEqual(ctx.document.documentElement.dataset.motionLevel, String(level));
  }
  const off = at(0);
  assert.strictEqual(off.motionLevel(), 0);
  assert.strictEqual(motionHarness({ setting: false }).motionLevel(), 0, "the first switch turned off no longer means Off");
  assert.strictEqual(motionHarness({ setting: true }).motionLevel(), 2, "Refined is not the default");
});

test("Motion families: each switch silences only its own effects", () => {
  const ctx = motionHarness({ settings: { motion_level: { value: 2 }, motion_sparks: { value: false } } });
  assert.strictEqual(ctx.fx(ctx.node(), "spark"), null);
  assert.strictEqual(ctx.fx(ctx.node(), "strike"), null, "the anvil strike is a spark effect");
  assert.strictEqual(ctx.motionDiagnostics().skipped.family, 2);
  assert.ok(ctx.fx(ctx.node(), "wolfrun"), "turning sparks off stopped the wolf");
  ctx.applyMotion();
  assert.strictEqual(ctx.document.documentElement.dataset.fxSparks, "off");
  assert.strictEqual(ctx.document.documentElement.dataset.fxWolf, "on");
  const noWolf = motionHarness({ settings: { motion_level: { value: 3 }, motion_wolf: { value: false } } });
  const track = noWolf.wolfTrack(() => noWolf.bar ||= noWolf.node(), "Notebook run", "Cell", "on this PC");
  track.set(1, 4);
  noWolf.advance(700);
  assert.strictEqual(noWolf.bar.querySelector(".wolf"), null, "a wolf ran with wolf animations off");
  assert.strictEqual(noWolf.bar.querySelector(".wolf-text").textContent, "Cell 2 of 4 on this PC", "the progress lost its words or where it runs");
});

test("Motion and Windows' reduced motion: Off unless the person opted out, and then the stylesheet steps aside", () => {
  const follow = motionHarness({ reduced: true, settings: { motion_level: { value: 3 } } });
  assert.strictEqual(follow.motionLevel(), 0);
  follow.applyMotion();
  assert.strictEqual(follow.document.documentElement.dataset.motionOverride, undefined);
  const opted = motionHarness({ reduced: true, settings: { motion_level: { value: 3 }, motion_follow_system: { value: false } } });
  assert.strictEqual(opted.motionLevel(), 3);
  assert.ok(opted.fx(opted.node(), "spark"));
  opted.applyMotion();
  assert.strictEqual(opted.document.documentElement.dataset.motionOverride, "on");
});

test("Motion budget: at most three effects at once, each removed on time, the count goes back to zero", () => {
  const ctx = motionHarness({ settings: { motion_level: { value: 2 } } });
  const kinds = ["arrive", "settle", "strike", "wolfrun"];
  const made = kinds.map((k) => ctx.fx(ctx.node(), k));
  assert.ok(made.slice(0, 3).every(Boolean));
  assert.strictEqual(made[3], null, "a fourth effect played at once");
  assert.strictEqual(ctx.motionDiagnostics().skipped.budget, 1);
  assert.strictEqual(ctx.motionDiagnostics().live, 3);
  ctx.advance(1100);
  assert.strictEqual(ctx.motionDiagnostics().live, 0, "an effect was never cleaned up");
  assert.ok(made.slice(0, 3).every((el) => !el.isConnected), "an effect element stayed on the page");
  const strike = ctx.fx(ctx.node(), "wolfrun");
  assert.ok(strike && strike.kids.length >= 2, "the Wolf Run has no runner or trail");
});

test("Motion under load: a long task before app.js has loaded doesn't throw", () => {
  // The load watch starts in motion.js, which loads before app.js declares state; a busy start-up
  // (or a test run under parallel load) reported long tasks in between: "state is not defined".
  const ctx = motionHarness({ appLoaded: false });
  assert.doesNotThrow(() => ctx.longTasks(600));
  assert.strictEqual(ctx.motionDiagnostics().drop, 1);
  assert.doesNotThrow(() => ctx.advance(30000));  // the cooldown's applyMotion, still before app.js
});

test("Motion under load: long tasks past the budget drop effects a level for the cooldown, then it comes back", () => {
  const ctx = motionHarness({ settings: { motion_level: { value: 2 } } });
  ctx.longTasks(300);
  ctx.advance(5001);
  ctx.longTasks(300);  // the first left the window, so this is 300 ms, not 600
  assert.strictEqual(ctx.motionLevel(), 2, "dropped under the budget");
  ctx.longTasks(250);
  assert.strictEqual(ctx.motionLevel(), 1, "550 ms of long tasks in the window did not drop a level");
  assert.strictEqual(ctx.document.documentElement.dataset.motionLevel, "1", "<html> still says the chosen level");
  assert.strictEqual(ctx.fx(ctx.node(), "spark"), null, "a branded effect played under load");
  assert.strictEqual(ctx.motionDiagnostics().skipped.level, 1);
  ctx.advance(20000);
  ctx.longTasks(600);  // more load during the cooldown neither drops again nor extends it
  assert.strictEqual(ctx.motionLevel(), 1);
  assert.strictEqual(ctx.motionDiagnostics().drop, 1);
  ctx.advance(9999);
  assert.strictEqual(ctx.motionLevel(), 1, "the cooldown ended early");
  ctx.advance(1);
  assert.strictEqual(ctx.motionLevel(), 2, "the cooldown ran past 30 s");
  ctx.advance(50);
  assert.strictEqual(ctx.document.documentElement.dataset.motionLevel, "2", "<html> was not told the level came back");
  assert.ok(ctx.fx(ctx.node(), "spark"), "no spark after the cooldown");
  const expressive = motionHarness({ settings: { motion_level: { value: 3 } } });
  expressive.longTasks(600);
  assert.strictEqual(expressive.motionLevel(), 2, "Expressive under load is not Refined");
  const minimal = motionHarness({ settings: { motion_level: { value: 1 } } });
  minimal.longTasks(600);
  assert.strictEqual(minimal.motionLevel(), 1, "load took Minimal lower");
});

test("Forge events: every event names a real effect, unknown ones play nothing, and each is counted", () => {
  const ctx = motionHarness({ settings: { motion_level: { value: 2 } } });
  const events = vm.runInContext("FORGE_EVENTS", ctx);
  const kinds = vm.runInContext("MOTION", ctx);
  for (const [name, kind] of Object.entries(events)) assert.ok(kinds[kind], `${name} plays ${kind}, which isn't an effect`);
  for (const [kind, m] of Object.entries(kinds)) {
    assert.ok(m.ms > 0 && m.ms <= 900, `${kind} is longer than a signature moment`);
    assert.ok(["wolf", "sparks", "achievements", "ambient"].includes(m.family), `${kind} has no family switch`);
    assert.ok(m.level >= 2, `${kind} plays at Minimal`);
  }
  assert.ok(ctx.forgeEvent("deploy.succeeded", ctx.node()), "a deployment struck no anvil");
  assert.strictEqual(ctx.forgeEvent("cell.typed", ctx.node()), null);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.motionDiagnostics().events)), { "deploy.succeeded": 1, "cell.typed": 1 });
  const src = fs.readFileSync(path.join(ui, "git.js"), "utf8");
  assert.match(src, /toast\(`Deployed \$\{out\.done\.length\}[^\n]*\n\s+forgeEvent\("deploy\.succeeded", document\.getElementById\("toast"\)\);/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /view\.replaceChildren\(\.\.\.children\);\n  viewTransition\(view\);/);
});

// Item 21: a table's whole lineage, hop by hop, both ways, out to the projects reading its products.
function lineageHelper() {
  const src = fs.readFileSync(path.join(ui, "meshflow.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function mfLineage\([\s\S]*?\n}\n/)[0]}; this.mfLineage = mfLineage;`, ctx);
  return ctx.mfLineage;
}

test("lineage walks every hop both ways, to the products and the projects that read them", () => {
  const mfLineage = lineageHelper();
  const entities = [
    { name: "bronze.customers", source: "landing crm/customers", reads: [] },
    { name: "bronze.orders", source: "files orders/*.csv", reads: [] },
    { name: "silver.customers", source: "bronze.customers", reads: ["bronze.customers"] },
    { name: "silver.orders", reads: ["bronze.orders", "silver.customers"] },
    { name: "gold.dim_customer", reads: ["silver.customers"] },
    { name: "gold.fact_sales", reads: ["silver.orders", "gold.dim_customer", "sales.link_rates"] },
  ];
  const ports = [["Customer 360", "gold.dim_customer"], ["Orders", "bronze.orders"]];
  const readers = { "Customer 360": ["finance", "marketing"] };
  const cols = JSON.parse(JSON.stringify(mfLineage(entities, ports, readers, "silver.customers")));
  assert.deepStrictEqual(cols.map((c) => c.label), ["Source", "It reads", "This table", "Reads it", "Then", "Data products", "Other projects"]);
  assert.deepStrictEqual(cols[0].items.map((x) => x.name), ["landing crm/customers"]);
  assert.deepStrictEqual(cols[3].items.map((x) => x.name), ["gold.dim_customer", "silver.orders"]);
  assert.deepStrictEqual(cols[4].items.map((x) => x.name), ["gold.fact_sales"]);
  assert.deepStrictEqual(cols[5].items.map((x) => x.name), ["Customer 360"], "Orders is upstream, not something it ends up in");
  assert.deepStrictEqual(cols[6].items.map((x) => `${x.name}:${x.note}`), ["finance:Customer 360", "marketing:Customer 360"]);
  // Upstream of the fact: two hops, both roots, and the link it reads from another project.
  const up = JSON.parse(JSON.stringify(mfLineage(entities, ports, readers, "gold.fact_sales", "up")));
  assert.deepStrictEqual(up.map((c) => c.label), ["Source", "Before that", "Before that", "It reads", "This table"]);
  assert.deepStrictEqual(up[0].items.map((x) => x.name), ["files orders/*.csv", "landing crm/customers", "sales.link_rates"]);
  assert.deepStrictEqual(up[3].items.map((x) => x.name), ["gold.dim_customer", "silver.orders"]);
  // A cycle in hand-edited configs never loops.
  const loop = [{ name: "a", reads: ["b"] }, { name: "b", reads: ["a"] }];
  assert.strictEqual(mfLineage(loop, [], {}, "a").length, 3);
  const flow = fs.readFileSync(path.join(ui, "meshflow.js"), "utf8");
  assert.match(flow, /pick\("both", "Whole lineage"\), pick\("up", "Only what it reads"\), pick\("down", "Only what reads it"\)/);
  assert.match(flow, /preview\("lineage", btn\("Columns", "flow", \(\) => columnLineage\(e\.name\)/);
});

// UX-006: Advanced settings fold closed by default, and a value policy or the environment sets is read-only.
test("a fold can start closed, and the choice made on this PC wins after that", () => {
  const src = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const store = new Map();
  const localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const h = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids, classes: new Set(String(attrs.class || "").split(" ").filter(Boolean)),
    append(...k) { this.kids.push(...k); }, setAttribute(k, v) { this.attrs[k] = v; },
    get classList() { const c = this.classes; return { toggle: (n, on) => (on ? c.add(n) : c.delete(n)), contains: (n) => c.has(n) }; } });
  const ctx = { h, localStorage };
  vm.runInNewContext(`${src.match(/function foldable\([\s\S]*?\n}\n/)[0]}; this.f = foldable;`, ctx);
  assert.ok(!ctx.f("x", "Head", []).classes.has("folded"), "folds open unless asked");
  const box = ctx.f("settings.advanced", "Advanced", [], { start: true });
  assert.ok(box.classes.has("folded"));
  const toggle = box.kids[0].kids[0];
  assert.strictEqual(toggle.attrs["aria-expanded"], "false");
  toggle.attrs.onclick();
  assert.strictEqual(store.get("fd.fold.settings.advanced"), "0");
  assert.ok(!ctx.f("settings.advanced", "Advanced", [], { start: true }).classes.has("folded"), "opened once, it opens next time");
});

test("Settings keeps App ID and engine limits in a folded Advanced section, read-only when policy or the environment sets them", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/const SOURCE_TEXT = \{[\s\S]*?\n};\n/)[0]}; ${src.match(/function fixedBy\([\s\S]*?\n}\n/)[0]}; this.f = fixedBy; this.T = SOURCE_TEXT;`, ctx);
  assert.strictEqual(ctx.f({ client_id: { source: "environment" } }, "client_id"), "environment");
  assert.strictEqual(ctx.f({ sql_page_rows: { source: "policy" } }, "sql_page_rows"), "policy");
  for (const source of ["user", "default", "built_in"]) assert.strictEqual(ctx.f({ k: { source } }, "k"), null);
  assert.strictEqual(ctx.f({}, "kernel_process"), null);
  assert.strictEqual(ctx.T.policy, "Set by your organization's policy");
  assert.strictEqual(ctx.T.environment, "Set by the FABRIC_DESKTOP_CLIENT_ID environment variable");
  const render = src.slice(src.indexOf("async function renderSettings"), src.indexOf("// \"policy\" or \"environment\""));
  const account = render.slice(render.indexOf("// Account"), render.indexOf("// Updates"));
  assert.ok(!account.includes("App (client) ID"), "the App ID left Account");
  assert.ok(!render.includes("Cell time limit") && !render.includes("sqlSettingsSection("), "limits left the main page");
  assert.match(render, /\[account, updates, display, previews, usage, platform, notify, about, advanced\]/);
  const adv = src.match(/function advancedSection\([\s\S]*?\n}\n/)[0];
  for (const text of ["App (client) ID", "Cell time limit", "Notebook memory limit", "Run notebooks in their own process", "sqlSettingsSection("]) assert.ok(adv.includes(text), text);
  assert.match(adv, /foldable\("settings\.advanced", h\("h2", \{\}, "Advanced"\)[\s\S]*\{ start: true/);
  assert.match(adv, /readonly: idFixed \? true : null/);
  assert.match(adv, /idFixed \? null : btn\("Save"/, "no Save for an ID that wouldn't apply");
  const sql = src.match(/function sqlSettingsSection\([\s\S]*?\n}\n/)[0];
  assert.match(sql, /readonly: locked\(key\)/);
  assert.match(sql, /readonly: locked\("sql_temp_dir"\)/);
  assert.match(sql, /fixedBy\(s, "sql_temp_dir"\)/);
  assert.match(src, /learnMore\("settings"\)/, "the page keeps its guide link");
  assert.match(fs.readFileSync(path.join(__dirname, "..", "..", "docs", "user-guide.md"), "utf8"), /^### Advanced settings$/m);
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
  assert.ok(ctx.isShortcut(key("ArrowLeft", { altKey: true }), "history") && !ctx.isShortcut(key("ArrowLeft"), "history"));
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
    [["Anywhere", 3], ["Moving around", 3], ["Menus and tabs", 2], ["Notebook", 1]]);
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
  // The notebook's help belongs to its own guide; an agent's page has its link in the advice under its state,
  // and Monitor's is in monitorAbout beside its tabs.
  const elsewhere = { notebook: null, agent: "agAdvice", monitor: "monitorAbout" };
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
    console, h: () => ({}), icon: () => ({}), renders: 0, POP_OUT: null,
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

// The attributes object an h(...) call opens with "{" just before index i: its text, read up to the
// brace that closes it, past strings, template literals and nested objects (#1061).
function attrsAt(src, i) {
  const open = src.lastIndexOf("{", i);
  if (open < 0 || !/^\{\s*$/.test(src.slice(open, i))) return null;
  const stack = ["{"];
  for (let j = open + 1; j < src.length; j++) {
    const c = src[j];
    const top = stack[stack.length - 1];
    if (top === '"' || top === "'") {
      if (c === "\\") j++;
      else if (c === top) stack.pop();
    } else if (top === "`") {
      if (c === "\\") j++;
      else if (c === "`") stack.pop();
      else if (c === "$" && src[j + 1] === "{") { stack.push("{"); j++; }
    } else if (c === '"' || c === "'" || c === "`" || c === "{") stack.push(c);
    else if (c === "}") {
      stack.pop();
      if (!stack.length) return src.slice(open, j + 1);
    }
  }
  return null;
}

// UX-012 (#961): every row of tabs is a tablist of tabs, so the shared handler gives it Left, Right,
// Home and End and a screen reader says which tab is selected.
test("every row of tabs is a tablist with selected state", () => {
  // A row whose tablist is its first child, so a jump button can sit after the list (OneLake, #961).
  const firstChild = (src, attrs, at) => {
    const rest = src.slice(at + attrs.length);
    const m = rest.match(/^\s*,\s*h\("\w+",\s*\{/);
    return m ? attrsAt(rest, m[0].length) : null;
  };
  for (const file of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    const src = fs.readFileSync(path.join(ui, file), "utf8");
    for (const [re, role] of [[/class: "(subtabs|explorer-tabs)\b/g, 'role: "tablist"'], [/class: (`tab\$\{|"tab")/g, 'role: "tab"']]) {
      for (const m of src.matchAll(re)) {
        const line = src.slice(0, m.index).split("\n").length;
        const attrs = attrsAt(src, m.index);
        assert.ok(attrs, `${file}:${line}: a tab row or tab whose attributes object can't be read`);
        if (role === 'role: "tab"') {
          assert.ok(attrs.includes(role), `${file}:${line}: a tab without ${role}`);
          assert.ok(attrs.includes('"aria-selected"'), `${file}:${line}: a tab without aria-selected`);
        } else {
          const child = attrs.includes(role) ? null : firstChild(src, attrs, src.lastIndexOf("{", m.index));
          assert.ok(attrs.includes(role) || (child && child.includes(role)), `${file}:${line}: a tab row without ${role}`);
        }
      }
    }
  }
  // The reader itself: a role just past the object's closing brace doesn't count.
  const near = 'h("div", { class: "subtabs", onclick: () => { go(`x${y}`); } }, h("b", {}, \'role: "tablist"\'))';
  assert.ok(!attrsAt(near, near.indexOf("class:")).includes('role: "tablist"'));
  assert.strictEqual(attrsAt(near, near.indexOf("class:")), '{ class: "subtabs", onclick: () => { go(`x${y}`); } }');
});

// One size formatter for every page (#929): no page keeps its own copy, and a byte count reads the same everywhere.
test("sizes read the same on every page", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${app.match(/function sizeText\([\s\S]*?\n}\n/)[0]}; this.f = sizeText;`, ctx);
  const cases = [[0, "0 B"], [17, "17 B"], [1023, "1023 B"], [1024, "1.0 KB"], [2048, "2.0 KB"], [1.5 * 1024 ** 2, "1.5 MB"],
    [99.5 * 1024 ** 2, "99.5 MB"], [212 * 1024 ** 2, "212 MB"], [1023.97 * 1024, "1.0 MB"], [1.1 * 1024 ** 3, "1.1 GB"],
    [5 * 1024 ** 4, "5.0 TB"], ["2048", "2.0 KB"], [-1, ""], [null, ""], [undefined, ""], ["", ""], [NaN, ""]];
  for (const [n, want] of cases) assert.strictEqual(ctx.f(n), want, String(n));
  // Side by side keeps decimal sizes for uploads, beside the decimal MB/s of the line they went over.
  for (const file of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    const src = fs.readFileSync(path.join(ui, file), "utf8").replace(/function sizeText\([\s\S]*?\n}\n/, "");
    assert.doesNotMatch(src, /\[\s*"(B|bytes)",\s*"KB"/, `${file} keeps its own size units: use sizeText`);
  }
});

// The shared date and time helpers (activity.js), for tests that run a page's code on its own.
function dateHelpers(names = ["readDate", "dateText", "timeText", "dateTimeText"]) {
  const src = fs.readFileSync(path.join(ui, "activity.js"), "utf8");
  return names.map((name) => src.match(new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`))[0]).join("\n");
}

test("dates and times read the same on every page", () => {
  const ctx = {};
  vm.runInNewContext(`${dateHelpers(["readDate", "dateText", "timeText", "dateTimeText", "relTime"])}
this.f = { dateText, timeText, dateTimeText, relTime };`, ctx);
  const { dateText, timeText, dateTimeText, relTime } = ctx.f;
  const at = "2026-10-28T19:03:07Z";
  const d = new Date(at);
  assert.strictEqual(dateText(at), d.toLocaleDateString(undefined, { dateStyle: "medium" }));
  assert.strictEqual(dateText(d), dateText(at), "a Date reads the same as its ISO string");
  assert.strictEqual(dateText(d.getTime()), dateText(at), "and as milliseconds");
  assert.ok(dateTimeText(at).startsWith(dateText(at)), dateTimeText(at));
  assert.strictEqual(timeText(at), d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }));
  assert.notStrictEqual(timeText(at, { seconds: true }), timeText(at), "seconds only when asked");
  assert.strictEqual(dateText("2026-10-28T00:00:00Z", { utc: true }), new Date(Date.UTC(2026, 9, 28)).toLocaleDateString(undefined, { dateStyle: "medium", timeZone: "UTC" }));
  for (const f of [dateText, timeText, dateTimeText, relTime]) {
    for (const bad of [null, undefined, "", "not a date", NaN]) {
      const out = f(bad);
      assert.strictEqual(out, "", `${f.name}(${String(bad)})`);
    }
  }
  assert.strictEqual(dateTimeText(null, { empty: "never" }), "never");
  assert.ok(dateText(at, { weekday: true }).includes(d.toLocaleDateString(undefined, { weekday: "short" })), "weekday when asked");
  assert.strictEqual(dateText("soon", { empty: "?" }), "?");
  const ago = (s) => new Date(Date.now() - s * 1000).toISOString();
  assert.strictEqual(relTime(ago(5)), "just now");
  assert.strictEqual(relTime(ago(600)), "10 min ago");
  assert.strictEqual(relTime(ago(7200)), "2 h ago");
  assert.strictEqual(relTime(ago(90000)), "yesterday");
  const lastYear = new Date(Date.now() - 400 * 86400000);
  assert.strictEqual(relTime(lastYear.toISOString()), dateText(lastYear));
  // Every page uses these: no page formats a date or time itself.
  for (const file of fs.readdirSync(ui).filter((f) => f.endsWith(".js") && f !== "activity.js")) {
    const page = fs.readFileSync(path.join(ui, file), "utf8");
    assert.doesNotMatch(page, /\.toLocale(Date|Time)String\(|dateStyle|timeStyle|new Date\([^()]*\)\.toLocaleString\(/,
      `${file} formats a date itself: use dateText, timeText, dateTimeText or relTime`);
  }
});

// L4: notebook resources. Names are checked before the engine sees them, a delete asks first in
// red, and the Resources tab keeps its snippets under the files.
test("notebook resource names, sizes and the Resources tab", () => {
  const src = fs.readFileSync(path.join(ui, "nbresources.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/const NB_RES_RESERVED = [^\n]+\n/)[0]}
${src.match(/function nbResNameProblem\([\s\S]*?\n}\n/)[0]}
${fs.readFileSync(path.join(ui, "app.js"), "utf8").match(/function sizeText\([\s\S]*?\n}\n/)[0]}
this.problem = nbResNameProblem; this.size = sizeText;`, ctx);
  for (const ok of ["regions.csv", "helpers.py", ".env", "a b (2).json"]) assert.strictEqual(ctx.problem(ok), "", ok);
  for (const bad of ["", "..", "../x.csv", "a/b", "a\\b", "CON", "nul.txt", "Com3.csv", "x.", "a:b", "a?b", "x".repeat(121)]) {
    assert.notStrictEqual(ctx.problem(bad), "", bad);
  }
  assert.strictEqual(ctx.size(17), "17 B");
  assert.strictEqual(ctx.size(2048), "2.0 KB");
  assert.strictEqual(ctx.size(5 * 1024 * 1024), "5.0 MB");
  assert.match(src, /confirmDialog\(`Delete \$\{file\.name\}\?`,[\s\S]*?"Delete", \{ danger: true \}\)/);
  assert.match(src, /copyText\(`builtin\/\$\{file\.name\}`, "Path"\)/);
  assert.match(src, /const NB_RES_MAX_FILES = 100;/);
  assert.match(src, /const NB_RES_MAX_BYTES = 50 \* 1024 \* 1024;/);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /content\.append\(nbResourcesSection\(state\.notebook\), h\("div", \{ class: "nb-res-head" \}, h\("b", \{\}, "Code snippets"\)\)/);
  assert.ok(nb.includes("...SNIPPETS.map("), "the snippets stay");
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="nbresources.js"') > html.indexOf('src="replay.js"'), "replayBase64 loads first");
});

// Once keys come from the platform, the entity's keys card names the database instead of a MERGE.
test("the keys card says where keys come from instead of an LH_Keys MERGE", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const card = meta.match(/function keysCard\(e\)[\s\S]*?\n}\n/)[0];
  assert.match(card, /e\.keys_sql \? h\("pre"[\s\S]*: e\.keys_from \? h\("p"/);
  assert.match(card, /Keys come from \$\{e\.keys_from\}/);
});

// A running cell shows how far its spark query is when the engine can tell (spark_local.progress).
test("a running cell shows its query's progress and drops it when the cell ends", () => {
  const n = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(n, /cell\.progress = snap\.status === "running" \? snap\.progress \?\? null : null;/);
  const line = /function statusLine\(cell\) \{[\s\S]*?\n\}\n/.exec(n)[0];
  assert.match(line, /s === "running" && cell\.progress != null \? h\("span", \{ class: "cell-progress"/);
  assert.match(line, /h\("progress", \{ max: 100, value: cell\.progress, "aria-label": "Query progress" \}\)/);
});

// PySpark on this PC: the notebooks page lists what KratosForge adds to DuckDB's Spark API.
test("notebooks docs list the PySpark calls added on this PC", () => {
  const page = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  for (const call of ["Window.partitionBy(...).orderBy(...)", "df.agg(...)", "selectExpr", "fillna", "pivot", "explode", "date_format"]) {
    assert.ok(page.includes(call), call);
  }
});

// Notebook magics: a snippet shows %sql inside Python, and the magics page is linked everywhere docs are.
test("notebook snippets include a %sql query into a DataFrame, and the magics page is linked", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /\["Query into a DataFrame", "df = %sql SELECT \* FROM dbo\.my_table LIMIT 100\\ndisplay\(df\)"\]/);
  const root = path.join(__dirname, "..", "..");
  for (const [file, link] of [["docs/README.md", "(notebook-magics.md)"],
    ["zensical.toml", '"notebook-magics.md"'], ["docs/notebooks.md", "(notebook-magics.md)"]]) {
    assert.ok(fs.readFileSync(path.join(root, file), "utf8").includes(link), file);
  }
});

// Notebook cell languages: Databricks' %sql alone on the first line is a SQL cell, %md shows Markdown.
test("notebook cells read Databricks %sql and %md headers", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(/function cellLang\([\s\S]*?\n}\n/.exec(nb)[0] + /const mdMagic = [^\n]*\n/.exec(nb)[0]
    + "\nthis.lang = cellLang; this.md = mdMagic;", ctx);
  const code = (source) => ({ type: "code", source });
  assert.strictEqual(ctx.lang(code("%sql\nSELECT 1")), "sql");
  assert.strictEqual(ctx.lang(code("  %%sql\nSELECT 1")), "sql");
  assert.strictEqual(ctx.lang(code("%sql SELECT 1")), "python");
  assert.strictEqual(ctx.lang(code("%python\nx = 1")), "python");
  assert.strictEqual(ctx.md(code("%md # Title")), true);
  assert.strictEqual(ctx.md(code("%%markdown\n# Title")), true);
  assert.strictEqual(ctx.md(code("%mdx")), false);
  assert.strictEqual(ctx.md({ type: "markdown", source: "%md" }), false);
  assert.match(nb, /if \(o\.type === "markdown"\) return h\("div", \{ class: "output md-view" \}, markdown\(o\.text\)\);/);
});

// Notebook shell cells: %sh, %%sh, %%bash, %%cmd and %%powershell are plain text in the editor.
test("notebook shell cells are recognised for the editor", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(/const shellMagic = [^\n]*\n/.exec(nb)[0] + "\nthis.sh = shellMagic;", ctx);
  const code = (source) => ({ type: "code", source });
  for (const s of ["%sh ls", "%%sh\nls", "%%bash\nls", "  %%cmd\ndir", "%%powershell\nGet-Date"]) assert.strictEqual(ctx.sh(code(s)), true, s);
  for (const s of ["%shx", "!ls", "x = 1", "%%sql\nSELECT 1"]) assert.strictEqual(ctx.sh(code(s)), false, s);
  assert.match(nb, /if \(shellMagic\(cell\)\) return \[\];/);
});

// IPython magics in notebooks: the magics page lists what runs and why %cd and %%javascript don't.
test("notebook magics page lists the IPython magics", () => {
  const page = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  for (const m of ["`%lsmagic`", "`%%capture name`", "`%%writefile path`", "`%timeit expression`", "`%cd`", "`%%javascript`"]) {
    assert.ok(page.includes(m), m);
  }
});

// %run with parameters: the magics page shows Fabric's JSON form and Databricks' $name form.
test("notebook magics page shows both %run parameter forms", () => {
  const page = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.ok(page.includes('%run Load sales {"day": "2026-01-01", "full": true}'));
  assert.ok(page.includes('%run ./etl/Load sales $day="2026-01-01"'));
});

// %%configure on this PC: the notebooks page links to the section that explains it.
test("notebooks page links %%configure to its section on the magics page", () => {
  const root = path.join(__dirname, "..", "..", "docs");
  assert.ok(fs.readFileSync(path.join(root, "notebooks.md"), "utf8").includes("(notebook-magics.md#configure)"));
  assert.ok(fs.readFileSync(path.join(root, "notebook-magics.md"), "utf8").includes("\n## %%configure\n"));
});

// %%tsql cells are highlighted as SQL in the notebook editor.
test("notebook %%tsql cells get SQL highlighting", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /if \(\/\^\\s\*%%tsql\(\\s\|\$\)\/\.test\(cell\.source\)\) return \[CM\.sql\(/);
});

// Plan step 4: a domain's admins in Fabric show as owners marked From Fabric, never in place of the platform's own.
test("domain owners from Fabric show only until the platform sets its own", () => {
  const src = fs.readFileSync(path.join(ui, "mesh.js"), "utf8");
  const pick = (name) => src.slice(src.indexOf(`function ${name}(`)).split("\n}\n")[0] + "\n}\n";
  const text = (n) => (n == null ? "" : typeof n === "string" ? n : [].concat(n.children || []).map(text).join(""));
  const ctx = { h: (tag, attrs, ...children) => ({ tag, attrs, children: children.flat().filter((c) => c != null && c !== false) }) };
  vm.runInNewContext(["fabricOwners", "domainOwnersCell", "domainOwnerLine"].map(pick).join("\n") +
    "\nthis.cell = domainOwnersCell; this.line = domainOwnerLine;", ctx);
  const fabric = JSON.stringify([{ name: "Ana", type: "User" }, { name: "Sales leads", type: "Group" }]);
  assert.strictEqual(text(ctx.cell({ owners: "", fabric_owners: fabric })), "Ana, Sales leads From Fabric");
  assert.strictEqual(text(ctx.cell({ owners: "lee@contoso.com", fabric_owners: fabric })), "lee@contoso.comIn Fabric: Ana, Sales leads");
  assert.strictEqual(text(ctx.cell({ owners: "", fabric_owners: "not json" })), "not set");
  assert.strictEqual(text(ctx.line({ owners: "", fabric_owners: fabric })), "Owner: Ana +1 (from Fabric)");
  assert.strictEqual(text(ctx.line({ owners: "lee@contoso.com", fabric_owners: fabric })), "Owner: lee@contoso.com");
  assert.strictEqual(ctx.line({ owners: "", fabric_owners: null }), null);
  const map = fs.readFileSync(path.join(ui, "meshmap.js"), "utf8");
  assert.match(map, /domainOwnerLine\(d\)/);
});

// Notebook result viewer (frames.py): a table output with a `frame` pages, searches and sorts the
// whole result in the engine and downloads it; a result the kernel no longer holds says to run again.
function resultViewerHarness(pages) {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const grab = (name) => new RegExp(`(?:async )?function ${name}\\([\\s\\S]*?\\n}\\n`).exec(nb)[0];
  const el = (tag, attrs = {}, ...kids) => {
    const node = { tag, attrs, kids: kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false),
      listeners: {}, textContent: "", disabled: false,
      append(...k) { this.kids.push(...k.flat(Infinity).filter(Boolean)); },
      replaceChildren(...k) { this.kids = k; },
      addEventListener(type, fn) { this.listeners[type] = fn; } };
    if (attrs.value !== undefined) node.value = attrs.value;
    return node;
  };
  const calls = [];
  const toasts = [];
  const ctx = { h: el, btn: (label, _i, onclick) => el("button", { onclick }, label), previewMarked: (_id, n) => n,
    toast: (t, err) => toasts.push([t, !!err]), friendlyError: String, state: { notebook: { name: "Sales" } },
    attempt: async (fn) => fn(), setTimeout: (fn) => fn(), clearTimeout() {},
    rpc: async (method, args) => { calls.push([method, args]); return pages.shift(); } };
  vm.createContext(ctx);
  vm.runInContext(["resultTable", "renderResultViewer", "copyResultPage", "downloadResult"].map(grab).join("\n") +
    "\nconst RESULT_PAGE = 100;\nthis.view = renderResultViewer; this.download = downloadResult;", ctx);
  return { ctx, calls, toasts };
}
const findAll = (node, pred, out = []) => { if (node && typeof node === "object") { if (pred(node)) out.push(node); (node.kids || []).forEach((k) => findAll(k, pred, out)); } return out; };
const texts = (node) => (typeof node === "string" || typeof node === "number" ? String(node) : (node.kids || []).map(texts).join(""));

test("the notebook result viewer pages, searches and sorts the whole result in the engine", async () => {
  const page = (offset, rows, total) => ({ frame: "f1", offset, rows, total, columns: ["n"], types: ["integer"] });
  const { ctx, calls } = resultViewerHarness([page(100, [[100]], 5000), page(0, [[4999]], 5000), page(0, [[42]], 1)]);
  const o = { frame: "f1", columns: ["n"], types: ["integer"], rows: Array.from({ length: 1000 }, (_, i) => [i]), total_rows: 5000 };
  const wrap = ctx.h("div"); const bar = ctx.h("div");
  ctx.view(o, wrap, bar, [true], "Showing the first 1000 of 5000 rows");
  const status = findAll(wrap, (n) => n.attrs?.role === "status")[0];
  assert.strictEqual(status.textContent, "Rows 1 to 100 of 5,000");
  assert.match(texts(findAll(wrap, (n) => n.tag === "th")[0]), /^ninteger$/);
  const [prev, next] = findAll(wrap, (n) => n.tag === "button" && ["Previous", "Next"].includes(texts(n)));
  assert.strictEqual(prev.disabled, true);
  await next.attrs.onclick();
  assert.deepStrictEqual(plain(calls[0]), ["notebook_frame_page", { frame: "f1", offset: 100, limit: 100, sort: null, desc: false, q: "" }]);
  assert.strictEqual(status.textContent, "Rows 101 to 101 of 5,000");
  await findAll(wrap, (n) => n.attrs?.class === "th-sort")[0].attrs.onclick();
  assert.deepStrictEqual(plain(calls[1][1]), { frame: "f1", offset: 0, limit: 100, sort: 0, desc: false, q: "" });
  const search = findAll(bar, (n) => n.attrs?.type === "search")[0];
  search.value = "42";
  search.listeners.input();
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(calls[2][1].q, "42");
  assert.strictEqual(status.textContent, "Rows 1 to 1 of 1 matching");
});

test("a result the kernel no longer holds says to run the cell again; downloads name the file saved", async () => {
  const { ctx, toasts, calls } = resultViewerHarness([{ frame: "f1", gone: true }, { path: "C:\\Users\\me\\Downloads\\Sales result.xlsx", rows: 5000, format: "xlsx" }]);
  const o = { frame: "f1", columns: ["n"], rows: [[1]], total_rows: 1 };
  const wrap = ctx.h("div");
  const bar = ctx.h("div");
  ctx.view(o, wrap, bar, [true], "1 row");
  await findAll(wrap, (n) => n.attrs?.class === "th-sort")[0].attrs.onclick();
  assert.match(findAll(wrap, (n) => n.attrs?.role === "status")[0].textContent, /^Run the cell again/);
  const items = findAll(bar, (n) => n.attrs?.role === "menuitem").map(texts);
  assert.deepStrictEqual(plain(items), ["CSV", "Excel", "Parquet"]);
  await ctx.download(o, "xlsx");
  assert.deepStrictEqual(plain(calls[1]), ["notebook_frame_download", { frame: "f1", format: "xlsx", name: "Sales" }]);
  assert.deepStrictEqual(plain(toasts.at(-1)), ["Saved 5,000 rows to C:\\Users\\me\\Downloads\\Sales result.xlsx", false]);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /if \(o\.view === "table" && o\.frame\) \{\n    renderResultViewer\(o, wrap, bar, numeric, count\);/);
  assert.match(nb, /previewMarked\("resultViewer", menu\)/);
});

test("a slow search answer that arrives after a newer one is dropped", async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  const answers = [slow, Promise.resolve({ frame: "f1", offset: 0, rows: [["new"]], total: 1 })];
  const { ctx } = resultViewerHarness([]);
  ctx.rpc = () => answers.shift();
  const o = { frame: "f1", columns: ["s"], rows: [["a"]], total_rows: 1 };
  const wrap = ctx.h("div"); const bar = ctx.h("div");
  ctx.view(o, wrap, bar, [false], "1 row");
  const search = findAll(bar, (n) => n.attrs?.type === "search")[0];
  search.value = "o"; search.listeners.input();
  search.value = "new"; search.listeners.input();
  await new Promise((r) => setImmediate(r));
  release({ frame: "f1", offset: 0, rows: [["old"]], total: 9 });
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(plain(o.viewer.page.rows), [["new"]]);
  assert.strictEqual(findAll(wrap, (n) => n.attrs?.role === "status")[0].textContent, "Rows 1 to 1 of 1 matching");
});

// notebookutils.fs.ls entries carry modifyTime, as in Fabric.
test("notebook docs list the fs.ls entry fields with modifyTime", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(doc, /`isFile` and `modifyTime` \(milliseconds since 1970, UTC; Databricks' `modificationTime` works too\)/);
});

test("Profile opens from the account menu and the command bar, and Validate passes on what it earned (KF-DX-001)", () => {
  const p = fs.readFileSync(path.join(ui, "profile.js"), "utf8");
  assert.match(p, /registerFeature\(\{\s*id: "profile", preview: true,\s*views: \{ profile: renderProfile \}/);
  assert.doesNotMatch(p, /rail:/, "Profile must not take a rail slot");
  for (const m of ["profile_get", "profile_save", "profile_exclude", "profile_backfill", "profile_report", "profile_report_save", "profile_export", "profile_delete"]) {
    assert.ok(p.includes(`"${m}"`), `${m} has no caller`);
  }
  assert.match(p, /confirmDialog\("Delete your profile data\?"[\s\S]*?\{ danger: true, typeToConfirm: "DELETE" \}/);
  assert.match(p, /aria-valuenow/, "the XP bar has no progressbar values");
  assert.match(p, /const RECORDING_ON = "Recording is on: [^"]*Turn it off in Profile > Settings\."/);
  assert.match(p, /a\.first\) \? ` \$\{RECORDING_ON\}`/, "the first achievement toast doesn't say recording is on");
  const s = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  assert.match(s, /previewShown\(\) \? \{ label: "Profile \(Preview\)"/);
  assert.match(s, /showMenu\(anchor, items\.filter\(Boolean\)\)/);
  const v = fs.readFileSync(path.join(ui, "validate.js"), "utf8");
  assert.match(v, /toast\(`\$\{nb\.name\}: \$\{r\.summary \|\| r\.state\}`, r\.state === "failed"\);\n  profileEarned\(r\.earned\);/);
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="profile.js"') > html.indexOf('src="features.js"'), "profile.js must load after features.js");
});

test("the profile report preview renders headings, bullets and source tags without HTML from the text (KF-DX-001)", () => {
  const p = fs.readFileSync(path.join(ui, "profile.js"), "utf8");
  const src = p.slice(p.indexOf("function markdownBlocks"), p.indexOf("// ---------- Settings"));
  const ctx = { h: (tag, attrs, ...kids) => ({ tag, kids: kids.flat(), append(k) { this.kids.push(k); } }) };
  vm.createContext(ctx);
  vm.runInContext(`${src}; this.markdownBlocks = markdownBlocks;`, ctx);
  const out = ctx.markdownBlocks("# Title\n\n## Key contributions\n\n- Validated <b>x</b> *(Verified, E1)*\n- Two\n\nText");
  assert.strictEqual(out.map((b) => b.tag).join(","), "h2,h3,ul,p");
  assert.strictEqual(out[2].kids.length, 2);
  assert.strictEqual(JSON.stringify(out[2].kids[0].kids.map((k) => (typeof k === "string" ? k : k.tag))), JSON.stringify(["Validated <b>x</b> ", "em"]));
  const escaped = ctx.markdownBlocks("- Validated notebook \\*x\\* \\[a\\]\\(b\\) *(Verified, E1)*")[0].kids[0].kids;
  assert.strictEqual(escaped.filter((k) => typeof k === "string").join(""), "Validated notebook *x* [a](b) ");
  assert.strictEqual(escaped.filter((k) => typeof k !== "string").length, 1, "an escaped name became emphasis");
});

// Running part of a cell (nbselect.js): the selected text, or the cursor's line dedented, in the
// cell's language.
test("Ctrl+Shift+Enter runs the selection or the cursor's line, dedented, with the cell's magic", () => {
  const src = fs.readFileSync(path.join(ui, "nbselect.js"), "utf8");
  const ctx = { document: { addEventListener() {} }, registerShortcuts() {}, MAGIC: { sql: "%%sql", html: "%%html" } };
  vm.createContext(ctx);
  vm.runInContext(`${src}\nthis.f = selectionCode;`, ctx);
  ctx.cellLang = (c) => c.lang;
  const py = { lang: "python" };
  const code = "for i in range(3):\n    x = i\n    print(x)";
  assert.strictEqual(ctx.f(py, code, 25, 25), "x = i");  // cursor on line 2, nothing selected
  assert.strictEqual(ctx.f(py, code, 23, code.length), "x = i\nprint(x)");
  assert.strictEqual(ctx.f(py, "a = 1\n\nb", 6, 6), null);  // a blank line has nothing to run
  const sql = { lang: "sql" };
  assert.strictEqual(ctx.f(sql, "%%sql\nSELECT 1;\nSELECT 2", 16, 24), "%%sql\nSELECT 2");
  assert.strictEqual(ctx.f(sql, "%%sql\nSELECT 1", 2, 2), null);  // the magic line alone
  assert.strictEqual(ctx.f(sql, "%sql\nSELECT 1;\nSELECT 2", 15, 23), "%sql\nSELECT 2");  // Databricks' %sql stays %sql
  assert.strictEqual(ctx.f(sql, "%sql\nSELECT 1", 2, 2), null);
});

test("several selected cells: the notebook hooks are in place", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /if \(selectionClick\(cell, e\)\) return;/);
  assert.match(nb, /if \(last\.group\) return undoDeleteGroup\(last\);/);
  assert.match(nb, /if \(code == null\) depsRan\(cell, ok\);/);
  assert.match(nb, /\.\.\.\(code != null \? \{ code \} : dbgStartArgs\(cell, debug\)\) \}\);/);
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="nbselect.js"') > html.indexOf('src="notebook.js"'));
});

// Organising cells (nbcells.js): which cells a collapsed heading hides, and the hooks in notebook.js.
test("a collapsed heading hides the cells up to the next heading of the same or a higher level", () => {
  const src = fs.readFileSync(path.join(ui, "nbcells.js"), "utf8");
  const ctx = { document: { addEventListener() {} } };
  vm.createContext(ctx);
  vm.runInContext(`${src}\nthis.f = { collapsedCells, headingLevel, visibleCells };\nthis.markdownHeadings = markdownHeadings;`, ctx);
  const { collapsedCells, headingLevel, visibleCells } = ctx.f;
  const md = (id, source, collapsed) => ({ id, type: "markdown", source, meta: collapsed ? { heading_collapsed: true } : {} });
  const code = (id) => ({ id, type: "code", source: "# not a heading", meta: { heading_collapsed: true } });
  assert.strictEqual(headingLevel(md(1, "Intro\n\n## Two")), 2);
  assert.strictEqual(headingLevel(md(1, "#hashtag")), 0);
  assert.strictEqual(headingLevel(code(1)), 0);
  // A comment inside a fenced code block isn't a heading.
  assert.strictEqual(headingLevel(md(1, "Example:\n```python\n# load the table\n```")), 0);
  assert.strictEqual(headingLevel(md(1, "~~~\n# x\n~~~\n### After")), 3);
  assert.strictEqual(headingLevel(md(1, "````\n```\n# x\n````\n## Out")), 2);
  // A fence with an info string doesn't close one (CommonMark).
  assert.strictEqual(headingLevel(md(1, "```\n```python\n# x\n```\n### After")), 3);
  // Setext headings: a line underlined with === is level 1, with --- level 2; a --- after a blank line or a list isn't.
  assert.strictEqual(headingLevel(md(1, "Title\n=====")), 1);
  assert.strictEqual(headingLevel(md(1, "Intro text\n\nSection\n---")), 2);
  assert.strictEqual(headingLevel(md(1, "Some text\n\n---\n")), 0);
  assert.strictEqual(headingLevel(md(1, "- item\n---")), 0);
  assert.strictEqual(headingLevel(md(1, "    indented code\n---")), 0);  // an indented code line isn't heading text
  assert.strictEqual(headingLevel(md(1, "- item\ncontinues here\n---")), 0);  // nor a list item's next line
  assert.strictEqual(headingLevel(md(1, "- item\n\nAfter the list\n---")), 2);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.markdownHeadings("A long\ntitle\n===\n\n## Next"))),
    [{ level: 1, text: "A long title" }, { level: 2, text: "Next" }]);
  const cells = [md(1, "# A", true), code(2), md(3, "## A.1", true), code(4), md(5, "# B"), code(6), md(7, "### C", true), code(8)];
  const { hidden, counts } = collapsedCells(cells);
  assert.deepStrictEqual([...hidden].sort(), [2, 3, 4, 8]);
  assert.strictEqual(counts.get(1), 3);
  assert.strictEqual(counts.get(7), 1);
  assert.deepStrictEqual([...visibleCells(cells).map((c) => c.id)], [1, 5, 6, 7]);
  assert.strictEqual(visibleCells(cells.slice(4, 6)).length, 2);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /visibleCells\(nb\.cells\)\.map\(renderCell\)/);
  assert.match(nb, /\.\.\.cellHeadExtras\(cell\)/);
  assert.match(nb, /return \[\.\.\.items, \.\.\.cellMenuExtras\(cell\)\];/);
  assert.match(nb, /cell\.status === "error" && state\.notebook\?\.cells\.includes\(cell\) && revealCell\(cell\)/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="notebook\.js"><\/script>(\n {2}<script src="[\w.-]+"><\/script>)*\n {2}<script src="nbcells\.js"><\/script>/);
});

// Tables and files from the notebook's Explorer (nbdrag.js): what a drop inserts and the ways to load.
test("dropping a table or file inserts its load code on an empty line, else its name for the cell's language", () => {
  const src = fs.readFileSync(path.join(ui, "nbdrag.js"), "utf8");
  const ctx = { document: { addEventListener() {} }, readSnippet: (p) => `df = lakehouse.read_csv("${p}")\ndf` };
  vm.createContext(ctx);
  vm.runInContext(`${src}\nthis.f = { dataItemDropText, dataItemLoads };`, ctx);
  const { dataItemDropText, dataItemLoads } = ctx.f;
  const table = { kind: "table", schema: "dbo", table: "sales" };
  assert.strictEqual(dataItemDropText(table, "python", { blankLine: true }), 'df = lakehouse.read_table("dbo.sales")\ndf');
  assert.strictEqual(dataItemDropText(table, "python"), '"dbo.sales"');
  assert.strictEqual(dataItemDropText(table, "python", { inString: true }), "dbo.sales");
  assert.strictEqual(dataItemDropText(table, "sql", { blankLine: true }), "dbo.sales");
  assert.strictEqual(dataItemDropText(table, "markdown"), "`dbo.sales`");
  assert.deepStrictEqual([...dataItemLoads(table).map((l) => l[0])], ["Load with Polars", "Load with Spark", "Query with SQL", "Load with pandas"]);
  assert.strictEqual(dataItemLoads(table)[3][1], 'df = lakehouse.read_table("dbo.sales").to_pandas()\ndf');
  assert.deepStrictEqual([...dataItemLoads({ kind: "file", path: "Files/a.parquet" }).map((l) => l[0])], ["Load with Polars", "Load with Spark", "Load with pandas"]);
  assert.match(dataItemLoads({ kind: "file", path: "Files/a.csv" })[2][1], /^import pandas as pd\ndf = pd\.read_csv\(lakehouse\.local_path\("Files\/a\.csv"\)\)\ndf$/);
  const odd = { kind: "file", path: "Files/notes/readme.txt" };
  assert.deepStrictEqual([...dataItemLoads(odd).map((l) => l[0])], ["Load with Polars", "Read the file's text"]);
  assert.strictEqual(dataItemDropText({ kind: "file", path: 'Files/a "b".csv' }, "python"), '"Files/a \\"b\\".csv"');
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.strictEqual((app.match(/\.\.\.\(onInsert \? dataItemAttrs\(pick\) : \{\}\)/g) || []).length, 2);
  // Names are quoted wherever they go into code, so a quote in a file name can't end the string.
  const ctx2 = { document: { addEventListener() {} } };
  vm.createContext(ctx2);
  vm.runInContext(`${/function readSnippet\([\s\S]*?\n}\n/.exec(app)[0]}\n${src}\nthis.f = { dataItemLoads, dataItemDropText };`, ctx2);
  const evil = { kind: "file", path: 'Files/x"); import os; os.system("calc"); (".csv' };
  assert.strictEqual(ctx2.f.dataItemLoads(evil)[0][1], `df = lakehouse.read_csv(${JSON.stringify(evil.path)})\ndf`);
  assert.match(app, /onInsert\(`df = lakehouse\.read_table\(\$\{JSON\.stringify\(`\$\{schema\}\.\$\{t\}`\)\}\)\\ndf`\)/);
  const odd2 = { kind: "table", schema: "dbo", table: "my `odd` table" };
  assert.strictEqual(ctx2.f.dataItemDropText(odd2, "sql"), "dbo.`my ``odd`` table`");
  assert.match(ctx2.f.dataItemLoads(odd2)[2][1], /FROM dbo\.`my ``odd`` table` LIMIT/);
});

// Notebook find and replace (nbfind.js): matches across cells with the bar's flags, regex groups in
// the replacement, and edits applied from the end of a cell.
test("notebook find matches every cell with case, whole word and regex, and replaces with groups", () => {
  const src = fs.readFileSync(path.join(ui, "nbfind.js"), "utf8");
  const ctx = { CM: { StateEffect: { define: () => ({}) } }, document: { addEventListener() {} }, registerShortcuts() {} };
  vm.createContext(ctx);
  vm.runInContext(`${src}\nthis.f = { findMatches, findReplacement, findApply };`, ctx);
  const { findMatches, findReplacement, findApply } = ctx.f;
  const cells = [{ type: "markdown", source: "# Sales" }, { type: "code", source: "sales = 1\nsales_count = sales" }];
  assert.strictEqual(findMatches(cells, "sales").matches.length, 4);
  assert.strictEqual(findMatches(cells, "sales", { matchCase: true }).matches.length, 3);
  assert.strictEqual(findMatches(cells, "sales", { word: true }).matches.length, 3);
  assert.strictEqual(findMatches(cells, "sales", { markdown: false }).matches.length, 3);
  assert.strictEqual(findMatches(cells, "a.e").matches.length, 0);  // not a regex unless asked
  assert.strictEqual(findMatches(cells, "x*", { regex: true }).matches.length, 0);  // empty matches skipped
  assert.match(findMatches(cells, "(", { regex: true }).error, /isn't a valid regular expression/);
  const [m] = findMatches(cells, "(s)ales", { regex: true, matchCase: true }).matches;
  assert.strictEqual(findReplacement(m, "(s)ales", "$1old", { regex: true, matchCase: true }), "sold");
  assert.strictEqual(findReplacement(m, "x", "$1", {}), "$1");  // plain text replacement is literal
  // Lookarounds and anchors see the whole cell, as when they matched.
  const xa = [{ type: "code", source: "xa ya xa" }];
  const found = findMatches(xa, "(?<=x)a", { regex: true }).matches;
  assert.strictEqual(found.length, 2);
  assert.deepStrictEqual([...found.map((f) => findReplacement(f, "(?<=x)a", "B", { regex: true }))], ["B", "B"]);
  assert.strictEqual(findApply(xa[0].source, found.map((f) => ({ from: f.from, to: f.to, insert: "B" }))), "xB ya xB");
  const lines = [{ type: "code", source: "a1\nb2" }];
  assert.strictEqual(findMatches(lines, "^b(\\d)", { regex: true }).matches.length, 0);  // ^ is the start of the cell
  const [start] = findMatches(lines, "^(a)(\\d)", { regex: true }).matches;
  assert.strictEqual(findReplacement(start, "^(a)(\\d)", "$2$1", { regex: true }), "1a");
  assert.strictEqual(findApply("ab ab", [{ from: 0, to: 2, insert: "xyz" }, { from: 3, to: 5, insert: "q" }]), "xyz q");
});

test("the notebook keeps its matches marked and the bar over the cells", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /nbFindBar\(\), h\("div", \{ class: "cells-inner" \}/);
  assert.match(nb, /findHighlighter\(cell\),/);
  assert.match(nb, /findCellChanged\(\);/);
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="nbfind.js"') > html.indexOf('src="notebook.js"'));
});

// Notebook cell comments (notebook-comments.js): mentions only highlight, line ranges follow the
// selection, and the panel lists threads in cell order with deleted cells' threads last.
test("cell comments: mentions, selected lines and the panel's rows", () => {
  const src = fs.readFileSync(path.join(ui, "notebook-comments.js"), "utf8");
  const ctx = {};
  vm.createContext(ctx);
  for (const name of ["commentParts", "commentLines", "commentRows", "commentAuthor", "ownComment"]) {
    vm.runInContext(new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`).exec(src)[0], ctx);
  }
  const plain = (v) => JSON.parse(JSON.stringify(v));
  assert.deepStrictEqual(plain(ctx.commentParts("ask @bob.smith, <b>now</b>. me@")), [
    { text: "ask ", mention: false }, { text: "@bob.smith", mention: true }, { text: ", <b>now</b>. me@", mention: false }]);
  const code = "a = 1\nb = 2\nc = 3";
  assert.strictEqual(ctx.commentLines(code, 3, 3), null, "no selection: the whole cell");
  assert.deepStrictEqual(plain(ctx.commentLines(code, 7, 1)), [1, 2]);
  assert.deepStrictEqual(plain(ctx.commentLines(code, 6, 12)), [2, 2], "ending at a line's start leaves that line out");
  assert.deepStrictEqual(plain(ctx.commentLines(code, 0, code.length)), [1, 3]);
  const cells = [{ cell_id: "a" }, { cell_id: "b" }];
  const threads = [
    { id: "t1", cell_id: "b", resolved: false, comments: [{ at: "2026-10-20T10:00:00+00:00" }] },
    { id: "t2", cell_id: "gone", resolved: false, comments: [{ at: "2026-10-20T09:00:00+00:00" }] },
    { id: "t3", cell_id: "a", resolved: false, comments: [{ at: "2026-10-20T11:00:00+00:00" }] },
    { id: "t4", cell_id: "a", resolved: true, comments: [{ at: "2026-10-20T08:00:00+00:00" }] },
  ];
  assert.deepStrictEqual(plain(ctx.commentRows(threads, cells, "open").map((r) => [r.thread.id, r.n])), [["t3", 1], ["t1", 2], ["t2", null]]);
  assert.deepStrictEqual(plain(ctx.commentRows(threads, cells, "resolved").map((r) => r.thread.id)), ["t4"]);
  assert.deepStrictEqual(plain(ctx.commentRows(threads, cells, "open", "a").map((r) => r.thread.id)), ["t3"]);
  // "You" only for the current account's (or this PC's) id; others keep their stored name.
  const me = { name: "ann@contoso.com", id: "aad:1" };
  assert.strictEqual(ctx.commentAuthor("ann@contoso.com", "aad:1", me), "You");
  assert.strictEqual(ctx.commentAuthor("bob@contoso.com", "aad:2", me), "bob@contoso.com");
  assert.strictEqual(ctx.commentAuthor("", "pc:other", me), "Someone on this PC");
  assert.strictEqual(ctx.commentAuthor("", "", { name: "", id: "" }), "Someone on this PC", "no id is never you");
  assert.strictEqual(ctx.ownComment({ author: "ann@contoso.com", author_id: "aad:9" }, me), false, "the name alone isn't ownership");
  assert.strictEqual(ctx.ownComment({ author_id: "aad:1" }, me), true);
  assert.strictEqual(ctx.ownComment({}, { name: "", id: "" }), false);
  // Comment text only ever becomes text nodes, and deleting asks first, in red.
  assert.doesNotMatch(src, /innerHTML/);
  assert.match(src, /"Delete", \{ danger: true \}/);
  assert.match(src, /nobody is notified/);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  for (const hook of ["commentBadge(cell)", "commentMenuItems(cell)", "commentsToggle(nb)", "commentsPanel(nb)"]) assert.ok(nb.includes(hook), hook);
});

// The notebook's Best practices panel (nbpractices.js): the engine's cell numbers map back to the
// notebook's cells (markdown cells aren't sent), counts by severity, and the panel is wired in.
test("notebook best practices: cell numbers, counts and wiring", () => {
  const src = fs.readFileSync(path.join(ui, "nbpractices.js"), "utf8");
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(/const PRACTICE_SEVERITIES = .*\n/.exec(src)[0].replace("const ", "var "), ctx);
  for (const name of ["practiceSources", "practiceRows", "practiceLabel"]) {
    vm.runInContext(new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`).exec(src)[0], ctx);
  }
  const plain = (v) => JSON.parse(JSON.stringify(v));
  const cells = [{ type: "markdown", source: "# Load" }, { type: "code", source: "rows = df.collect()" },
    { type: "markdown", source: "note" }, { type: "code", source: "" }];
  const { sources, at } = ctx.practiceSources(cells);
  assert.deepStrictEqual(plain(sources), ["rows = df.collect()", ""]);
  const got = ctx.practiceRows({ findings: [{ rule_id: "NB003", severity: "Warning", cell: 1, line: 1 },
    { rule_id: "NB001", severity: "Error", cell: 2, line: 3 }] }, at);
  assert.deepStrictEqual(plain(got.rows.map((f) => [f.rule_id, f.n])), [["NB003", 2], ["NB001", 4]], "engine cell 1 is notebook cell 2");
  assert.deepStrictEqual(plain(got.counts), { Error: 1, Warning: 1, Info: 0 });
  assert.strictEqual(ctx.practiceLabel({ open: true, rows: got.rows }), "Best practices · 2");
  assert.strictEqual(ctx.practiceLabel({ open: false, rows: got.rows }), "Best practices");
  assert.strictEqual(ctx.practiceLabel(undefined), "Best practices");
  // Read only: it never runs a cell or writes; findings only ever become text nodes.
  assert.doesNotMatch(src, /innerHTML|runCell|markDirty|rpc\("(save|run)/);
  assert.match(src, /rpc\("governance_practice_settings", \{ kind: "notebook" \}\)/);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  for (const hook of ["practicesToggle(nb)", "practicesPanel(nb)"]) assert.ok(nb.includes(hook), hook);
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="nbpractices.js"') > html.indexOf('src="notebook-comments.js"'));
});

// SQL best practices on the SQL page (query.js): a finding's line and column (code points, from 1)
// become an offset in the text, and the chip's label counts the findings.
test("SQL best practices: finding offsets and the chip label", () => {
  const src = fs.readFileSync(path.join(ui, "query.js"), "utf8");
  const ctx = {};
  vm.createContext(ctx);
  for (const name of ["sqlPracticeOffset", "sqlPracticeLabel"]) {
    vm.runInContext(new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`).exec(src)[0], ctx);
  }
  const text = "SELECT *\nFROM sales\nWHERE YEAR(d) = 1";
  assert.strictEqual(text.slice(ctx.sqlPracticeOffset(text, 3, 7)).slice(0, 4), "YEAR");
  assert.strictEqual(ctx.sqlPracticeOffset(text, 1, 8), 7);
  assert.strictEqual(ctx.sqlPracticeOffset("-- \u{1F600}\nSELECT *", 2, 8), 12, "an emoji is one code point, as the engine counts");
  assert.strictEqual(ctx.sqlPracticeLabel({ findings: [{}, {}] }), "Best practices · 2");
  assert.strictEqual(ctx.sqlPracticeLabel(null), "Best practices");
  assert.match(src, /rpc\("governance_practice_settings", \{ kind: "sql" \}\)/);
  assert.match(src, /SQL_PRACTICE_TARGETS = \["warehouse", "endpoint", "sqldb"\]/);
  // The compatibility result never waits for the practices (they draw when they arrive).
  assert.doesNotMatch(src, /Promise\.all\(\[rpc\("sql_analyze"/);
  assert.match(src, /practices\(\)\.then\(/);
});

// Notebook parity L3: a notebook's Runs panel lists this PC's scheduled runs and its Fabric runs.
test("the notebook Runs panel labels each run and offers Refresh status only for Fabric runs still going", () => {
  const src = fs.readFileSync(path.join(ui, "runs.js"), "utf8");
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${app.match(/function fabricStatus\([\s\S]*?\n}\n/)[0]}
    ${src.slice(0, src.indexOf("// ---------- state"))}
    Object.assign(this, { NB_RUN_TRIGGERS, nbRunStatus, nbRunWhere, nbRunsUnfinished, nbRunsSummary, nbRunKey });`, ctx);
  const pcOk = { source: "pc", status: "ok", trigger: "schedule", started: "2026-10-20T06:00:00+00:00", schedule_id: "s1" };
  const pcBad = { source: "pc", status: "failed", trigger: "manual" };
  const going = { source: "fabric", status: "InProgress", trigger: "fabric", workspace_name: "Sales", job_id: "j1" };
  const done = { source: "fabric", status: "Completed", trigger: "run_now" };
  assert.deepStrictEqual([pcOk, pcBad, going, done, { source: "pc", status: "skipped" }].map(ctx.nbRunStatus),
    ["ok", "failed", "running", "ok", "skipped"]);
  assert.deepStrictEqual([pcOk, going, done].map(ctx.nbRunWhere), ["This PC", "Fabric: Sales", "Fabric"]);
  assert.strictEqual(ctx.nbRunsUnfinished([pcOk, going, done]), 1);
  assert.strictEqual(ctx.nbRunsUnfinished([pcOk, done]), 0, "nothing to ask Fabric about");
  assert.strictEqual(ctx.nbRunsSummary([pcOk, pcBad, going, done]), "4 runs, 1 failed, 1 still going");
  assert.strictEqual(ctx.nbRunsSummary([pcOk]), "1 run");
  assert.strictEqual(ctx.nbRunsSummary([]), "No runs yet");
  assert.strictEqual(ctx.nbRunKey(pcOk), "pc:2026-10-20T06:00:00+00:00:s1");
  for (const t of ["schedule", "manual", "fabric", "run_now", "prove", "side_by_side"]) assert.ok(ctx.NB_RUN_TRIGGERS[t], t);
  // Wired into the ribbon and the notebook body, tagged Preview, and the engine call is read-only.
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.strictEqual(nb.match(/runsToggle\(nb\)/g).length, 2, "Home and Run tabs");
  assert.match(nb, /runsPanel\(nb\), provePanel\(nb\)/);
  assert.match(src, /preview\("notebookRuns", btn\("Recent runs"/);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /\n  notebookRuns: "Notebook runs",/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="runs.js"><\/script>/);
  assert.match(src, /rpc\("notebook_runs", \{ name: nb\.name, limit: NB_RUNS_LIMIT, refresh \}\)/);
  const audit = fs.readFileSync(path.join(__dirname, "..", "..", "sidecar", "fabric_desktop", "audit.py"), "utf8");
  assert.match(audit, /\bnotebook_runs\b/);
  // How this works names the one write, and no other panel stays open beside it.
  assert.match(src, /saves the answer in fabric_runs\.jsonl on this PC/);
  assert.match(src, /anchor: "notebook-runs-preview"/);
  for (const f of ["prove.js", "validate.js"]) assert.match(fs.readFileSync(path.join(ui, f), "utf8"), /if \(nb\.runs\?\.open\) nb\.runs\.open = false;/);
});

// Notebook parity L5: export and import formats (notebook-formats.js, notebook_formats.py).
test("notebook export sends the cells with their outputs, and says where the file goes", () => {
  const src = fs.readFileSync(path.join(ui, "notebook-formats.js"), "utf8");
  const ctx = { btoa: (s) => Buffer.from(s, "binary").toString("base64") };
  vm.createContext(ctx);
  vm.runInContext(`${src.match(/const NBF_EXPORTS = [\s\S]*?\];/)[0]}
${src.match(/function nbfExportPayload\([\s\S]*?\n}\n/)[0]}
${src.match(/function nbfExportMenu\([\s\S]*?\n}\n/)[0]}
${src.match(/function nbfBase64\([\s\S]*?\n}\n/)[0]}
this.payload = nbfExportPayload; this.menu = nbfExportMenu; this.b64 = nbfBase64;`, ctx);
  const nb = { name: "Sales", lakehouse: null, cells: [
    { type: "markdown", source: "# T", meta: {}, cell_id: "m", outputs: [{ type: "text", text: "x" }], count: null },
    { type: "code", source: "1", meta: {}, cell_id: "c", outputs: [{ type: "text", text: "1" }], count: 4 }] };
  const p = JSON.parse(JSON.stringify(ctx.payload(nb, "ipynb_outputs")));
  assert.deepStrictEqual(p.cells.map((c) => [c.outputs.length, c.count]), [[0, null], [1, 4]]);
  assert.strictEqual(p.format, "ipynb_outputs");
  const items = ctx.menu();
  assert.match(items[0].info, /new file in your Downloads folder\. A file already there is never replaced/);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(items.slice(1).map((i) => i.label))), ["Jupyter notebook (.ipynb)", "Jupyter notebook with outputs (.ipynb)",
    "Fabric source (.py)", "Databricks source (.py)", "Web page (.html)"]);
  const bytes = new Uint8Array(70000).map((_, i) => i % 251);
  assert.strictEqual(ctx.b64(bytes.buffer), Buffer.from(bytes).toString("base64"));
  assert.match(fs.readFileSync(path.join(ui, "notebook.js"), "utf8"), /ribbonMenu\("Share", [^\n]*btn\("Save a copy", "add", \(\) => nbSaveCopyDialog\(nb\.name\)\),[^\n]*nbfExportButton\(menu\)\]/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /btn\("Import from Fabric or Databricks", "arrowUpload", nbfImportDialog, "tool outline"\)/);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /nbFormats: "Notebook import and export formats"/);
  assert.match(src, /changes: "Writes one new notebook file \(\.ipynb\) for each notebook in the file/);
});

// #854: what a Databricks import or export didn't carry over, one row per note with its cell.
test("Databricks conversion notes show each construct with its cell, on import and after export", () => {
  const src = fs.readFileSync(path.join(ui, "notebook-formats.js"), "utf8");
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(`const h = (tag, attrs, ...kids) => ({ tag, attrs, kids: kids.flat(Infinity).filter((k) => k != null) });
${src.match(/const NBF_NOTE_KINDS = [^\n]*/)[0]}
${src.match(/function nbfPlural\([\s\S]*?\n}\n/)[0]}
${src.match(/function nbfNotesTable\([\s\S]*?\n}\n/)[0]}
${src.match(/function nbfCheckNotes\([\s\S]*?\n}\n/)[0]}
this.table = nbfNotesTable; this.check = nbfCheckNotes;`, ctx);
  const text = (n) => (typeof n === "string" ? n : (n.kids || []).map(text).join("|"));
  const rows = ctx.table([{ cell: null, kind: "dropped", what: "Outputs" }, { cell: 3, kind: "check", what: "dbfs:/ path" },
    { cell: 4, kind: "fabric", what: "widgets" }]).kids[1].kids;
  assert.deepStrictEqual(Array.from(rows, text), ["Whole notebook|Not carried over|Outputs", "3|Check before running|dbfs:/ path",
    "4|Before running in Fabric|widgets"]);
  const blocks = ctx.check({ notebooks: [{ name: "Load", notes: [{ cell: 1, kind: "changed", what: "%sh" }] }, { name: "Clean", notes: [] }] });
  assert.strictEqual(blocks.length, 1);
  assert.match(text(blocks[0]), /^Load: 1 thing to know about the conversion/);
  assert.match(src, /\.\.\.nbfCheckNotes\(check\)\);/);
  assert.match(src, /showDialog\("What Databricks source doesn't hold"/);
});

// Version history (notebook-versions.js): day groups, the change summary, and where it's reached.
function notebookVersionsHelpers() {
  const ctx = { registerFeature: () => {} };
  vm.createContext(ctx);
  vm.runInContext(`${fs.readFileSync(path.join(ui, "notebook-versions.js"), "utf8")}
this.groups = nbvGroups; this.summary = nbvChangeSummary; this.title = nbvTitle;`, ctx);
  return ctx;
}

test("version history groups versions by day and sums up a compare", () => {
  const f = notebookVersionsHelpers();
  const today = new Date("2026-10-20T15:00:00");
  const v = (at, kind = "auto", label = "") => ({ at, kind, label });
  const groups = f.groups([v("2026-10-20T14:00:00"), v("2026-10-20T09:00:00", "checkpoint", "Before refactor"), v("2026-10-19T22:00:00")], today);
  assert.deepStrictEqual([...groups].map((g) => [g.day, g.rows.length]), [["Today", 2], ["Yesterday", 1]]);
  assert.strictEqual(f.title(v("x", "checkpoint", "Before refactor")), "Before refactor");
  assert.strictEqual(f.title(v("x", "before restore")), "Before a restore");
  assert.strictEqual(f.summary([]), "No changes to cells.");
  assert.strictEqual(f.summary([{ kind: "changed" }, { kind: "changed" }, { kind: "added" }, { kind: "reordered" }]),
    "2 cells changed, 1 cell added, cells reordered.");
  const src = fs.readFileSync(path.join(ui, "notebook-versions.js"), "utf8");
  assert.match(src, /confirmDialog\(`Delete \$\{v\.kind === "checkpoint"[\s\S]*?"Delete", \{ danger: true \}\)/);
  assert.match(src, /The notebook as it is now is kept as a version first/);
  assert.match(fs.readFileSync(path.join(ui, "notebook.js"), "utf8"), /btn\("Save a copy", "add", \(\) => nbSaveCopyDialog\(nb\.name\)\), btn\("Versions", "history", nbVersions\)/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="notebook-files\.js"><\/script>\n  <script src="notebook-versions\.js"><\/script>/);
});

// Optimize > Settings history (item 30): changes from fdten_ and fdcap_, named and filtered, and a
// read that asks before it creates the tables.
test("Settings history names capacities, filters by scope and asks before creating its tables", async () => {
  const src = fs.readFileSync(path.join(ui, "optimize.js"), "utf8");
  assert.match(src, /\["settings", "Settings history"\]\]/);
  const fn = (name) => src.match(new RegExp(`(async )?function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  const ctx = { calls: [], toasts: [], renders: 0 };
  vm.createContext(ctx);
  vm.runInContext(`${fn("settingsHistoryNames")}${fn("settingsHistoryRows")}${fn("settingsHistoryRead")}
    this.names = settingsHistoryNames; this.rows = settingsHistoryRows; this.read = settingsHistoryRead;`, ctx);
  const changes = [  // newest first, as tenant_settings_history returns them
    { scope_type: "capacity", scope_id: "cap-1", setting: "sku", changed_after: "t1", changed_by_time: "t2", old_json: '"F64"', new_json: '"F128"' },
    { scope_type: "capacity", scope_id: "cap-1", setting: "displayName", changed_after: "t1", changed_by_time: "t2", old_json: '"Old"', new_json: '"Prod"' },
    { scope_type: "tenant", scope_id: "ten", setting: "ExportToExcel", changed_after: null, changed_by_time: "t1", old_json: null, new_json: '{"enabled":true}' },
    { scope_type: "capacity", scope_id: "cap-1", setting: "displayName", changed_after: null, changed_by_time: "t1", old_json: null, new_json: '"Old"' },
    { scope_type: "capacity", scope_id: "cap-2", setting: "override:Copilot", changed_after: "t1", changed_by_time: "t2", old_json: "true", new_json: null },
  ];
  const names = ctx.names(changes);
  assert.strictEqual(JSON.stringify(names), '{"cap-1":"Prod"}');  // the newest name; cap-2 has none yet
  assert.strictEqual(ctx.rows(changes, names, "all").length, 5);
  assert.strictEqual(ctx.rows(changes, names, "tenant").map((r) => r.scope).join(), "Tenant");
  assert.strictEqual(ctx.rows(changes, names, "capacity").length, 4);
  const [sku] = ctx.rows(changes, names, "cap-1");
  assert.strictEqual(JSON.stringify([sku.after, sku.by, sku.scope, sku.setting, sku.was, sku.now]), '["t1","t2","Capacity Prod","sku","\\"F64\\"","\\"F128\\""]');
  const [ov] = ctx.rows(changes, names, "cap-2");
  assert.strictEqual(JSON.stringify([ov.scope, ov.setting, ov.now]), '["Capacity cap-2","Override: Copilot",null]');

  // The first read asks; only a yes passes approve.
  ctx.toast = (t, err) => ctx.toasts.push([t, !!err]);
  ctx.render = () => { ctx.renders += 1; };
  ctx.friendlyError = (e) => ({ text: String(e.message).replace(/^PermissionError: /, "") });
  ctx.attempt = async (f) => { try { return await f(); } catch { return undefined; } };
  ctx.rpc = async (method, args) => {
    ctx.calls.push([method, JSON.stringify(args)]);
    if (!args.approve) throw new Error("PermissionError: Setting up adds 3 tables. Confirm to continue.");
    return { outcome: "read", changes: [{}, {}] };
  };
  ctx.confirmDialog = async (title, text) => { ctx.asked = [title, text]; return ctx.yes; };
  ctx.yes = false;
  await ctx.read("capacity_settings_read", "Capacities");
  assert.strictEqual(ctx.asked[1], "Setting up adds 3 tables.");
  assert.strictEqual(JSON.stringify(ctx.calls), '[["capacity_settings_read","{}"]]');
  ctx.yes = true;
  await ctx.read("capacity_settings_read", "Capacities");
  assert.strictEqual(ctx.calls[2][1], '{"approve":true}');
  assert.strictEqual(JSON.stringify(ctx.toasts.at(-1)), '["Capacities read: 2 changes",false]');
  // An unreadable read is recorded by the engine and shown as an error toast, not asked about again.
  ctx.rpc = async () => ({ outcome: "unreadable", reason: "Only a Fabric administrator can read tenant settings.", changes: [] });
  await ctx.read("tenant_settings_read", "Tenant settings");
  assert.strictEqual(JSON.stringify(ctx.toasts.at(-1)), '["Tenant settings: Only a Fabric administrator can read tenant settings.",true]');
  assert.strictEqual(ctx.renders, 2);
  // Include capacity overrides passes admin, and says when the capacities were read but the overrides weren't.
  ctx.calls.length = 0;
  for (const [overrides, toast] of [["read", '["Capacities read: nothing changed",false]'],
    ["unreadable", '["Capacities read: nothing changed. Overrides weren\'t read: only a Fabric administrator can read them.",true]'],
    ["failed", '["Capacities read: nothing changed. Overrides couldn\'t be read this time.",true]']]) {
    ctx.rpc = async (method, args) => (ctx.calls.push([method, JSON.stringify(args)]), { outcome: "read", overrides, changes: [] });
    await ctx.read("capacity_settings_read", "Capacities", { admin: true });
    assert.strictEqual(JSON.stringify(ctx.toasts.at(-1)), toast);
  }
  assert.ok(ctx.calls.every(([, a]) => a === '{"admin":true}'), JSON.stringify(ctx.calls));

  const how = src.match(/howItWorks\("optimize\.settings", \{[\s\S]*?\n  \}\)/)[0];
  for (const t of ["fdcap_snapshot", "fdcap_change", "fdcap_read", "fdten_snapshot", "fdten_change", "fdten_read", "no tenant or capacity setting is ever changed", "asks before it creates the tables"]) {
    assert.ok(how.includes(t), t);
  }
});

// Inspect over every row: a table output with a `frame` shows the engine's profile of the whole result.
test("Inspect profiles every row of a held result, and says so when the kernel no longer holds it", async () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const grab = (name) => new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`).exec(nb)[0];
  const el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(Infinity).filter((k) => k !== null && k !== undefined), textContent: "",
    append(...k) { this.kids.push(...k.flat(Infinity).filter(Boolean)); }, replaceChildren(...k) { this.kids = k.flat(Infinity).filter(Boolean); },
    querySelector(sel) { return findAll(this, (n) => n.attrs?.class === sel.slice(1))[0]; } });
  const answers = [{ frame: "f", rows: 10000, more_columns: 0, columns: [
    { name: "n", type: "integer", kind: "number", missing: 0, distinct: 9990, min: 0, max: 9999, mean: 4999.5, std: 2886.9, q1: 2499.75, median: 4999.5, q3: 7499.25, histogram: [5, 10] },
    { name: "s", type: "string", kind: "text", missing: 1, distinct: 2, top: [["a", 6000], [null, 3]] },
    { name: "bad", type: "double", kind: "number", error: "Couldn't profile this column: Out of Range" }] },
    { frame: "h", rows: 5000000, profiled_rows: 1000000, sampled: true, more_columns: 0, columns: [{ name: "v", type: "double", kind: "number", missing: 0, distinct: 2, infinite: 3, min: 1, max: 2, mean: 1.5, std: 0.5, q1: 1, median: 1.5, q3: 2, histogram: [1, 1] }] },
    { frame: "f", gone: true }];
  const ctx = { h: el, isNumber: (v) => typeof v === "number", friendlyError: String, rpc: async () => answers.shift() };
  vm.createContext(ctx);
  vm.runInContext(grab("renderFrameProfile") + grab("renderInspect") + "this.p = renderFrameProfile;", ctx);
  const o = { frame: "f", columns: ["n", "s"], rows: [[1, "a"]] };
  const box = ctx.p(o, [true, false], "Showing the first 1000 of 10000 rows");
  assert.match(texts(box), /Profiling every row\.\.\.$/);
  await new Promise((r) => setImmediate(r));
  const t = texts(box);
  assert.match(t, /Profile of all 10,000 rows$/);
  assert.match(t, /Distinct≈ 9,990/);
  assert.match(t, /Median4999\.5/);
  assert.match(t, /null3/);
  assert.match(t, /baddoubleCouldn't profile this column: Out of Range/);
  const big = ctx.p({ frame: "h", columns: ["v"], rows: [[1]] }, [true], "x");
  await new Promise((r) => setImmediate(r));
  assert.match(texts(big), /Infinite3/);
  assert.match(texts(big), /Profile of a random sample of 1,000,000 of 5,000,000 rows$/);
  assert.ok(o.profile && ctx.p(o, [true, false], "x") !== box, "a held profile redraws without asking again");
  const o2 = { frame: "g", columns: ["n"], rows: [[1]] };
  const box2 = ctx.p(o2, [true], "1 row");
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(o2.profileGone, true);
  assert.match(box2.querySelector(".more").textContent, /Run the cell again to profile every row\.$/);
  assert.match(nb, /\} else if \(o\.frame && !o\.profileGone\) \{\n    wrap\.append\(renderFrameProfile\(o, numeric, count\)\);/);
});

// The chart builder (charts.js): the engine aggregates every row; the cell keeps its chart by column name.
function chartsHarness(answer) {
  const src = fs.readFileSync(path.join(ui, "charts.js"), "utf8");
  const node = (tag, attrs = {}, ...kids) => ({ tag, attrs: { ...attrs }, kids: kids.flat(Infinity).filter((k) => k !== null && k !== undefined),
    textContent: "", listeners: {}, setAttribute(k, v) { this.attrs[k] = v; }, append(...k) { this.kids.push(...k.flat(Infinity).filter(Boolean)); },
    replaceChildren(...k) { this.kids = k.flat(Infinity).filter(Boolean); }, replaceWith(n) { this.replaced = n; },
    addEventListener(t, fn) { this.listeners[t] = fn; } });
  const calls = [];
  let dirty = 0;
  const ctx = { h: node, btn: (label, _i, onclick) => node("button", { onclick }, label), friendlyError: String, markDirty: () => { dirty += 1; },
    document: { createElementNS: (_ns, tag) => node(tag) },
    rpc: async (m, a) => { calls.push([m, a]); return typeof answer === "function" ? answer(a) : answer; } };
  vm.createContext(ctx);
  vm.runInContext(src + "\nthis.api = { chartConfig, renderChartBuilder, chartSvg, addChart, removeChart, chartName, chartCount };", ctx);
  return { ctx, calls, dirty: () => dirty, node };
}
const allNodes = (n, out = []) => { if (n && typeof n === "object") { out.push(n); (n.kids || []).forEach((k) => allNodes(k, out)); } return out; };

test("the chart builder asks the engine for every row and keeps the cell's chart by column name", async () => {
  const d = { kind: "stacked", rows: 3000, truncated: false, categories: ["East", "West"],
    series: [{ name: "p1", values: [1, 2] }, { name: "Other", values: [3, null] }] };
  const { ctx, calls, dirty } = chartsHarness(d);
  const o = { frame: "f", columns: ["region", "product", "amount"], rows: [["East", "p1", 1]] };
  const cell = { meta: {} };
  const cfg = ctx.api.chartConfig(o, [false, false, true], cell);
  assert.deepStrictEqual(plain(cfg), { kind: "bar", x: "region", y: "amount", agg: "sum", series: null, title: "" });
  const box = ctx.api.renderChartBuilder(o, [false, false, true], cell);
  assert.deepStrictEqual(plain(calls[0]), ["notebook_frame_chart", { frame: "f", kind: "bar", x: 0, y: 2, agg: "sum", series: null }]);
  const kind = allNodes(box).find((n) => n.tag === "select" && n.attrs["aria-label"] === "Chart");
  kind.attrs.onchange({ target: { value: "stacked" } });
  const series = allNodes(box.replaced).find((n) => n.tag === "select" && n.attrs["aria-label"] === "Series");
  series.attrs.onchange({ target: { value: "product" } });
  assert.deepStrictEqual(plain(cell.meta.chart), { kind: "stacked", x: "region", y: "amount", agg: "sum", series: "product", title: "" });
  assert.strictEqual(dirty(), 2);
  assert.deepStrictEqual(plain(calls.at(-1)[1]), { frame: "f", kind: "stacked", x: 0, y: 2, agg: "sum", series: 1 });
  await new Promise((r) => setImmediate(r));
  // A rerun keeps the saved chart; a renamed column falls back to the default.
  assert.strictEqual(ctx.api.chartConfig({ columns: ["region", "product", "amount"] }, [false, false, true], cell).series, "product");
  assert.strictEqual(ctx.api.chartConfig({ columns: ["area", "amount"] }, [false, true], cell).kind, "bar");
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /o\.view \|\|= o\.summary \? "inspect" : o\.frame && cell\?\.meta\?\.chart \? "chart" : "table";/);
  assert.match(nb, /previewMarked\("chartBuilder"/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="notebook\.js"><\/script>\n  <script src="charts\.js"><\/script>/);
});

test("one result keeps several charts, each with its own settings in the cell", async () => {
  const d = { kind: "bar", rows: 2, truncated: false, categories: ["East"], series: [{ name: null, values: [1] }] };
  const { ctx, calls, dirty } = chartsHarness(d);
  const o = { frame: "f", columns: ["region", "month", "amount"], rows: [["East", "Jan", 1]] };
  const cell = { meta: {} };
  const numeric = [false, false, true];
  assert.strictEqual(ctx.api.chartCount(cell), 1);
  assert.strictEqual(ctx.api.chartName(cell, 0), "Chart");
  assert.strictEqual(ctx.api.addChart(o, numeric, cell), 1);
  assert.strictEqual(ctx.api.chartCount(cell), 2);
  assert.ok(cell.meta.chart, "adding a chart saves the first one too, where older builds read it");
  assert.strictEqual(ctx.api.chartName(cell, 1), "Chart 2");
  let removed = null;
  const box = ctx.api.renderChartBuilder(o, numeric, cell, 1, (n, what) => { removed = [n, what]; });
  const x = allNodes(box).find((n) => n.tag === "select" && n.attrs["aria-label"] === "X axis");
  x.attrs.onchange({ target: { value: "month" } });
  assert.strictEqual(cell.meta.charts[0].x, "month");
  assert.strictEqual(cell.meta.chart.x, "region", "a second chart's settings stay its own");
  assert.strictEqual(ctx.api.chartConfig(o, numeric, cell, 0).x, "region");
  const title = allNodes(box.replaced).find((n) => n.tag === "input");
  title.value = "Amount by month, all regions in one chart";
  title.listeners.change();
  assert.deepStrictEqual(removed, [1, "renamed"]);
  assert.strictEqual(ctx.api.chartName(cell, 1), "Amount by month, all re…");
  const twins = { meta: { chart: { title: "Sales" }, charts: [{ title: "Sales" }, { title: "Sales" }] } };
  assert.deepStrictEqual([0, 1, 2].map((n) => ctx.api.chartName(twins, n)), ["Sales", "Sales (2)", "Sales (3)"]);
  assert.ok(dirty() >= 3);
  assert.ok(!allNodes(ctx.api.renderChartBuilder(o, numeric, cell, 0)).some((n) => n.tag === "button" && n.kids.includes("Remove chart")));
  const rm = allNodes(box.replaced).find((n) => n.tag === "button" && n.kids.includes("Remove chart"));
  rm.attrs.onclick();
  assert.deepStrictEqual(removed, [1, "removed"]);
  assert.strictEqual(cell.meta.charts, undefined);
  assert.strictEqual(ctx.api.chartCount(cell), 1);
  await new Promise((r) => setImmediate(r));
  assert.ok(calls.length >= 2);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /previewMarked\("severalCharts"/);
  assert.match(nb, /charts < MAX_CHARTS/);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /severalCharts: "Several charts per result"/);
});

test("every chart kind draws marks with a tooltip, and an empty result says so", () => {
  const { ctx } = chartsHarness(null);
  const kinds = {
    bar: { kind: "bar", rows: 2, categories: ["a", "b"], series: [{ name: null, values: [1, -2] }] },
    stacked: { kind: "stacked", rows: 2, categories: ["a"], series: [{ name: "x", values: [1] }, { name: "y", values: [2] }] },
    line: { kind: "line", rows: 2, categories: [1, 2], series: [{ name: null, values: [1, null] }] },
    area: { kind: "area", rows: 2, categories: [1, 2], series: [{ name: "x", values: [1, 2] }, { name: "y", values: [2, 1] }] },
    pie: { kind: "pie", rows: 2, categories: ["a", "b"], series: [{ name: null, values: [1, 3] }] },
    scatter: { kind: "scatter", rows: 2, points: [[1, 2, "x"], [2, 3, "y"]], sampled: false, total: 2 },
    histogram: { kind: "histogram", rows: 2, min: 0, max: 10, counts: [1, 0, 1] },
    box: { kind: "box", rows: 2, boxes: [{ x: "a", min: 0, q1: 1, median: 2, q3: 3, max: 4, n: 5 }] },
  };
  for (const [k, d] of Object.entries(kinds)) {
    const svg = ctx.api.chartSvg(d, { kind: k, x: "x", y: "v", title: "" });
    const titled = allNodes(svg).filter((n) => n.tag === "title");
    assert.ok(titled.length > 0, `${k} has tooltips`);
    assert.strictEqual(svg.attrs.role, "img");
  }
  const empty = ctx.api.chartSvg({ kind: "bar", rows: 0, categories: [], series: [] }, { kind: "bar" });
  assert.ok(allNodes(empty).some((n) => n.textContent === "Nothing to chart"));
  const pie = ctx.api.chartSvg({ kind: "pie", rows: 9, categories: [1, 2, 3, 4, 5, 6, 7, 8, 9], series: [{ name: null, values: [1, 1, 1, 1, 1, 1, 1, 1, 1] }] }, { kind: "pie" });
  assert.strictEqual(allNodes(pie).filter((n) => n.tag === "path").length, 7, "a pie folds past six slices into Other");
});

// Data Wrangler (wrangler.js): steps go to the engine, which writes the Polars code and previews it on every row.
function wranglerHarness(answer) {
  const src = fs.readFileSync(path.join(ui, "wrangler.js"), "utf8");
  const { node } = chartsHarness(null);
  const calls = [];
  const inserted = [];
  const ctx = { h: node, btn: (label, _i, onclick, _c, attrs = {}) => node("button", { onclick, ...attrs }, label), friendlyError: String,
    resultTable: (columns) => ({ ...node("table", {}), columns, querySelectorAll: () => [] }), state: { selected: null },
    insertCell: (code) => inserted.push(code), toast: () => {}, attempt: (fn) => fn(), navigator: { clipboard: { writeText: async () => {} } },
    rpc: async (m, a) => { calls.push([m, JSON.parse(JSON.stringify(a))]); return typeof answer === "function" ? answer(a) : answer; } };
  vm.createContext(ctx);
  vm.runInContext(src + "\nthis.api = { wrangleName, renderWrangler, renderWrangleAdd, addWrangleCell };", ctx);
  return { ctx, calls, inserted };
}

test("Data Wrangler sends its steps to the engine and adds the code as a new cell under the output", async () => {
  const d = { code: "import polars as pl\n...", columns: ["id", "name"], types: ["integer", "string"], rows: [[1, "Ann"]],
    rows_before: 4, rows_after: 3, changed: ["name"], sampled: false, error: null, steps: ["Remove duplicate rows"] };
  const { ctx, calls, inserted } = wranglerHarness(d);
  assert.strictEqual(ctx.api.wrangleName({ source: "orders = spark.sql('x')\ndisplay(orders)" }), "orders");
  assert.strictEqual(ctx.api.wrangleName({ source: "df2" }), "df2");
  assert.strictEqual(ctx.api.wrangleName({ source: "display(spark.sql('x'))" }), "df");
  const o = { frame: "f", columns: ["id", "name"], rows: [] };
  const cell = { id: "c1", source: "display(orders)" };
  ctx.api.renderWrangler(o, cell);
  assert.deepStrictEqual(calls[0], ["notebook_frame_wrangle", { frame: "f", steps: [], name: "orders" }]);
  // Adding a step: pick the operation, then its columns, then Add step.
  o.wrangle.adding = "dedupe";
  const form = ctx.api.renderWrangleAdd(o, o.wrangle, () => {});
  const cols = allNodes(form).find((n) => n.tag === "select" && n.attrs.multiple);
  cols.selectedOptions = [{ value: "id" }];
  allNodes(form).find((n) => n.tag === "button" && n.kids.includes("Add step")).attrs.onclick();
  assert.deepStrictEqual(plain(o.wrangle.steps), [{ op: "dedupe", columns: ["id"] }]);
  ctx.api.renderWrangler(o, cell);
  assert.deepStrictEqual(calls[1][1].steps, [{ op: "dedupe", columns: ["id"] }]);
  await new Promise((r) => setImmediate(r));
  ctx.api.addWrangleCell(cell, d.code);
  assert.strictEqual(ctx.state.selected, "c1");
  assert.deepStrictEqual(inserted, [d.code]);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /o\.frame \? seg\("clean", "Clean"\) : null/);
  assert.match(nb, /previewMarked\("dataWrangler"/);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /dataWrangler: "Data Wrangler"/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="charts\.js"><\/script>\n  <script src="wrangler\.js"><\/script>/);
  // The code is shown as text, never as HTML.
  assert.doesNotMatch(fs.readFileSync(path.join(ui, "wrangler.js"), "utf8"), /innerHTML|insertAdjacentHTML|outerHTML/);
});

test("a Data Wrangler step that can't run is marked with its reason and blocks adding the code", async () => {
  const d = { code: "x", columns: ["id"], types: ["integer"], rows: [], rows_before: 1, rows_after: 1, changed: [], sampled: false,
    error: { step: 0, message: "'lots' isn't a number." }, steps: ["Fill missing values: id"] };
  const { ctx } = wranglerHarness(d);
  const o = { frame: "f", columns: ["id"], rows: [], wrangle: { steps: [{ op: "fill", columns: ["id"], value: "lots" }], name: "df", data: null, adding: "" } };
  const box = ctx.api.renderWrangler(o, { id: "c" });
  await new Promise((r) => setImmediate(r));
  const alert = allNodes(box).find((n) => n.attrs?.role === "alert");
  assert.ok(alert.kids.includes("'lots' isn't a number."));
  const add = allNodes(box).find((n) => n.tag === "button" && n.kids.includes("Add code to notebook"));
  assert.strictEqual(add.attrs.disabled, true);
  // After a restart the kernel no longer holds the result.
  const gone = wranglerHarness({ gone: true });
  const box2 = gone.ctx.api.renderWrangler({ frame: "f", columns: [], rows: [] }, null);
  await new Promise((r) => setImmediate(r));
  assert.ok(allNodes(box2).some((n) => /Run the cell again to clean this result/.test(n.textContent)));
});

// Rich outputs: Markdown output uses the Markdown cell's escaping renderer, JSON is a tree of text,
// and neither adds an HTML sink (HD-052).
test("Markdown and JSON cell outputs render without new HTML sinks", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const fn = /function jsonTree\([\s\S]*?\n}\n/.exec(nb)[0];
  assert.doesNotMatch(fn, /innerHTML|outerHTML|insertAdjacentHTML|srcdoc/);
  const el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(Infinity).filter((k) => k !== null && k !== undefined) });
  const ctx = { h: el };
  vm.createContext(ctx);
  vm.runInContext(fn + "this.t = jsonTree;", ctx);
  const tree = ctx.t({ a: [1, "<b>x</b>", null], b: { c: true } }, null, 0);
  const text = (n) => (typeof n === "string" || typeof n === "number" ? String(n) : (n.kids || []).map(text).join(""));
  const t = text(tree);
  assert.strictEqual(t, '{2}a: [3]0: 11: "<b>x</b>"2: nullb: {1}c: true');
  assert.strictEqual(tree.attrs.open, true);
  assert.strictEqual(ctx.t({ x: { y: { z: 1 } } }, null, 0).kids[1].kids[1].attrs.open, null);
});

// Snowflake SQL on DuckDB (SF-04): the translation report is plain text in a <pre>, never HTML.
test("The Snowflake translation output renders as text", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const fn = /function renderOutput\([\s\S]*?\n}\n/.exec(nb)[0];
  const el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(Infinity).filter((k) => k !== null && k !== undefined) });
  const ctx = { h: el };
  vm.createContext(ctx);
  vm.runInContext(fn + "this.r = renderOutput;", ctx);
  const evil = "Statement 1: <img src=x onerror=alert(1)>";
  const out = ctx.r({ type: "translation", text: evil, statements: [] }, null);
  assert.strictEqual(out.attrs.class, "output translation-output");
  assert.strictEqual(out.kids[0].tag, "pre");
  assert.deepStrictEqual(out.kids[0].kids, [evil]);
});

// IntelliSense (nbintel.js): Python cells get hover, signature help and the syntax check, and
// completions carry lazy docs; other languages get none of it.
test("Python cells get hover, signature help, syntax errors and completion docs", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /CM\.autocompletion\(\{ override: \[pythonCompletions\], activateOnTypingDelay: 150 \}\),\n\s+\.\.\.intelExtensions\(\)\]/);
  assert.match(nb, /info: intelCompletionInfo\(base, i\.label\)/);
  const intel = fs.readFileSync(path.join(ui, "nbintel.js"), "utf8");
  assert.match(intel, /rpc\("kernel_inspect", \{ code: line\.text, cursor: pos - line\.from \}\)/);
  assert.match(intel, /rpc\("kernel_inspect", \{ code, call: true \}\)/);
  assert.match(intel, /rpc\("notebook_syntax", \{ source \}\)/);
  assert.match(intel, /function intelLive\(\) \{\n\s+return state\.notebook && !nbWhere\(state\.notebook\)\.fabric;/);
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="nbintel.js"') > html.indexOf('src="notebook.js"'));
});

// Notebook utilities: notebookutils.lakehouse reads on this PC; changes stay in Fabric.
test("notebookutils.lakehouse and isValidToken are documented", () => {
  const docs = fs.readFileSync(path.join(ui, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(docs, /\| `lakehouse` \(Preview\) \| `get`, `getWithProperties`, `list` and `listTables`, read from Fabric as you/);
  assert.match(docs, /`create`, `update`, `delete` and `loadTable` run in Fabric only/);
  assert.match(docs, /`isValidToken` says whether a token hasn't expired yet/);
});

// Notebook utilities: notebookutils.notebook reads Fabric notebook items; changes go through Publish.
test("notebookutils.notebook item reads are documented", () => {
  const docs = fs.readFileSync(path.join(ui, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(docs, /\| `notebook` items \(Preview\) \| `get`, `list` and `getDefinition` read Fabric notebook items as you/);
  assert.match(docs, /use \*\*Publish to OneLake\*\*, which shows what it replaces first/);
});

// notebookutils rows that read Fabric live are marked Preview until they run on a tenant.
test("notebookutils docs mark the live Fabric reads Preview", () => {
  const docs = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(docs, /Rows marked Preview read Fabric live as you and haven't been run on a tenant yet\./);
});

// notebookutils.variableLibrary reads any library from the attached lakehouse's workspace, read only.
test("notebook docs say any variable library is read from the lakehouse's workspace", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(doc, /Any other library, and VL_Sources when there is no metadata project, is read from the attached lakehouse's workspace/);
  assert.match(doc, /Read only \(Preview, not yet run on a tenant\)/);
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

// Parameters and widgets bar (Preview): above the cells, values added after their cell when it runs.
test("parameters and widgets bar sits above the cells and adds its values to the run", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /computeNote\(nb\), inputsBar\(nb\), visibleCells\(nb\.cells\)\.map\(renderCell\)/);
  assert.match(nb, /code: cell\.source \+ nbInputSuffix\(cell\)/);
  const bar = fs.readFileSync(path.join(ui, "nbinputs.js"), "utf8");
  assert.match(bar, /previewMarked\("inputs"/);
  assert.match(bar, /Runs on this PC, in Fabric and side by side use these values; the notebook's code doesn't change\./);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /inputs: "Parameters and widgets bar"/);
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('<script src="nbinputs.js"></script>') > html.indexOf('<script src="notebook.js"></script>'));
});

// dbutils.fs: a bare absolute path is DBFS in Databricks, so it never means this PC's disk.
test("dbutils docs say bare absolute paths are refused", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(doc, /A bare absolute path like `\/mnt\/raw` or `\/tmp\/x` is DBFS in Databricks, so it is refused here/);
});

// Widget values from the bar apply from the start of every cell, even the one that creates the widget.
test("the inputs bar docs say widget values apply in the creating cell", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(doc, /`dbutils\.widgets\.get` returns them from the start of every cell, including the cell that creates the widget/);
});

// Notebook utilities: notebook.run and runMultiple honor timeouts on this PC; the docs say so.
test("notebook.run timeouts and validateDAG are documented", () => {
  const docs = fs.readFileSync(path.join(ui, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(docs, /`run` returns the exit value as a string and stops the other notebook with a `TimeoutError` after `timeout_seconds`/);
  assert.match(docs, /`run`, `exit`, `runMultiple` and `validateDAG`/);
  assert.doesNotMatch(docs, /Timeouts and `concurrency` aren't enforced here/);
});

// notebook.run timeouts land between Python steps; the docs say so.
test("notebook.run docs say where the timeout lands", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(doc, /The timeout lands between Python steps, so a single long call such as `time\.sleep\(4\)` finishes before it stops/);
});

// The Variables pane (nbvars.js): filtering on name or type, and the sort orders.
test("variables filter on name or type and sort by name, type or memory with unknown sizes last", () => {
  const src = fs.readFileSync(path.join(ui, "nbvars.js"), "utf8");
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(`${src}\nthis.f = variableRows;`, ctx);
  const rows = [
    { name: "sales", type: "DataFrame", memory: 50000 },
    { name: "Count", type: "int", memory: 28 },
    { name: "sdf", type: "DataFrame", memory: null },
    { name: "cfg", type: "dict", memory: 400 },
  ];
  const names = (opts) => [...ctx.f(rows, opts).map((r) => r.name)];
  assert.deepStrictEqual(names(), ["cfg", "Count", "sales", "sdf"]);
  assert.deepStrictEqual(names({ filter: "frame" }), ["sales", "sdf"]);
  assert.deepStrictEqual(names({ filter: " CO " }), ["Count"]);
  assert.deepStrictEqual(names({ sort: "memory" }), ["sales", "cfg", "Count", "sdf"]);
  assert.deepStrictEqual(names({ sort: "type" }), ["sales", "sdf", "cfg", "Count"]);
  assert.deepStrictEqual(names({ sort: "name", desc: true }), ["sdf", "sales", "Count", "cfg"]);
  assert.strictEqual(rows[0].name, "sales");  // the kernel's rows aren't reordered
  assert.doesNotMatch(fs.readFileSync(path.join(ui, "notebook.js"), "utf8"), /function variableTable/);
});

// Notebook %pip: the magics page says installs go to the notebook's own environment.
test("the magics page documents %pip installing into the notebook's environment", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /## Installing packages with %pip/);
  assert.match(doc, /\(notebooks\.md#notebook-environments-preview\)/);
});

// Notebook %autoreload: the magics page documents autoreload and %aimport.
test("the magics page documents %autoreload and %aimport", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /### Editing your own modules: %autoreload/);
  assert.match(doc, /`%load_ext autoreload`, `%autoreload 2`, `%aimport name`/);
});

// Notebook %run -b: the magics and resources pages say how to run a file from Built-in resources.
test("the magics page documents %run -b and the resources page links to it", () => {
  const root = path.join(__dirname, "..", "..");
  const magicsDoc = fs.readFileSync(path.join(root, "docs", "notebook-magics.md"), "utf8");
  assert.match(magicsDoc, /### Running a file from the notebook's resources: %run -b/);
  assert.match(fs.readFileSync(path.join(root, "docs", "notebook-resources.md"), "utf8"), /notebook-magics\.md#running-a-file-from-the-notebooks-resources-run-b/);
});

test("a workload page says whether its actions reached the platform's audit", () => {
  const src = fs.readFileSync(path.join(ui, "workloads.js"), "utf8");
  const fn = /function platformAuditNote\([\s\S]*?\n}\n/.exec(src)[0];
  const ctx = { h: (tag, attrs, ...kids) => kids.join("") };
  vm.runInNewContext(`${fn}this.f = platformAuditNote;`, ctx);
  assert.strictEqual(ctx.f(null), null);
  assert.strictEqual(ctx.f({ platform: "this_pc", waiting: 0 }), "Each one is also on Platform > Govern > Audit.");
  assert.match(ctx.f({ platform: "fabric", waiting: 2, error: "Signed out." }),
    /^2 actions or changes haven't reached Platform > Govern > Audit yet: Signed out\. This PC keeps them/);
  assert.match(ctx.f({ platform: "fabric", waiting: 1, error: "" }), /^1 action or change hasn't reached Platform > Govern > Audit yet\. /);
  assert.match(ctx.f({ platform: "none", waiting: 3 }), /^Kept on this PC only: there's no platform yet\. 3 actions/);
  assert.strictEqual(ctx.f({ platform: "none", waiting: 0 }), "Kept on this PC only: there's no platform yet.");
});

test("the notebook, several-cells and find keys are listed in the Keyboard shortcuts dialog", () => {
  for (const [file, group, key] of [["notebook.js", "Notebook", "Shift+Enter"], ["nbselect.js", "Notebook: several cells", "Shift+M"], ["nbfind.js", "Notebook: find and replace", "Ctrl+H"]]) {
    const src = fs.readFileSync(path.join(ui, file), "utf8");
    const at = src.indexOf(`registerShortcuts(${JSON.stringify(group)}`);
    assert.ok(at > 0, `${file} registers ${group}`);
    assert.ok(src.slice(at, src.indexOf("]);", at)).includes(`"${key}"`), `${group} lists ${key}`);
  }
});

// ipywidgets (ipywidgets.js): the kernel holds the state; a change goes to notebook_ipywidget_event and the answer is drawn.
function widgetsHarness(answer) {
  const src = fs.readFileSync(path.join(ui, "ipywidgets.js"), "utf8");
  const { node } = chartsHarness(null);
  const calls = [];
  const ctx = { state: { notebook: { name: "Sales" } }, h: node, friendlyError: String, previewMarked: (_id, n) => n, htmlFrame: (html) => node("iframe", { srcdoc: html }),
    renderOutput: (o) => node("pre", {}, o.text || o.type),
    rpc: async (m, a) => { calls.push([m, JSON.parse(JSON.stringify(a))]); return typeof answer === "function" ? answer(a) : answer; } };
  vm.createContext(ctx);
  vm.runInContext(src + "\nthis.api = { renderWidgetOutput, drawWidget };", ctx);
  return { ctx, calls };
}

test("ipywidgets controls send their change to the kernel and draw its answer", async () => {
  const view = { id: "w5", kind: "vbox", description: "", disabled: false, children: [
    { id: "w1", kind: "slider", description: "n", disabled: false, value: 5, min: 1, max: 10, step: 1 },
    { id: "w2", kind: "dropdown", description: "name", disabled: false, options: ["a", "b"], index: 0 },
    { id: "w3", kind: "checkbox", description: "flag", disabled: false, value: true },
    { id: "w6", kind: "button", description: "Go", disabled: false },
    { id: "w7", kind: "html", description: "", disabled: false, value: "<b>x</b>" },
    { id: "w4", kind: "output", description: "", disabled: false, outputs: [{ type: "stream", text: "5 a True\n" }] }] };
  const answer = { status: "ok", outputs: [{ type: "stream", text: "clicked\n" }], view: { ...view, children: [{ ...view.children[0], value: 9 }] } };
  const { ctx, calls } = widgetsHarness(answer);
  const o = { type: "widget", model: "w5", view };
  const wrap = ctx.api.renderWidgetOutput(o, { id: "c" });
  const nodes = allNodes(wrap);
  const range = nodes.find((n) => n.tag === "input" && n.attrs.type === "range");
  assert.deepStrictEqual([range.attrs.min, range.attrs.max, range.attrs.value], [1, 10, 5]);
  range.attrs.onchange({ target: { value: "9" } });
  assert.deepStrictEqual(calls[0], ["notebook_ipywidget_event", { model: "w1", event: { value: 9 }, root: "w5", notebook: "Sales" }]);
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(o.view.children[0].value, 9);
  assert.strictEqual(o.events[0].text, "clicked\n");
  assert.ok(wrap.replaced, "redrawn with the kernel's answer");
  const tick = () => new Promise((r) => setImmediate(r));
  nodes.find((n) => n.tag === "select").attrs.onchange({ target: { value: "1" } });
  // While the kernel answers, changes wait and only the latest is sent next.
  nodes.find((n) => n.tag === "input" && n.attrs.type === "checkbox").attrs.onchange({ target: { checked: false } });
  nodes.find((n) => n.tag === "button").attrs.onclick();
  assert.strictEqual(calls.length, 2);
  await tick();
  await tick();
  assert.deepStrictEqual(calls.slice(1).map(([, a]) => a.event), [{ index: 1 }, { click: true }]);
  // The HTML widget goes to the sandboxed frame; nothing in this file writes HTML itself.
  assert.ok(nodes.some((n) => n.tag === "iframe" && n.attrs.srcdoc === "<b>x</b>"));
  const src = fs.readFileSync(path.join(ui, "ipywidgets.js"), "utf8");
  assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML|outerHTML|srcdoc/);
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /if \(o\.type === "widget"\) return renderWidgetOutput\(o, cell\);/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="wrangler\.js"><\/script>\n  <script src="ipywidgets\.js"><\/script>/);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /ipywidgets: "Notebook widgets"/);
});

test("a widget after a restart asks for its cell again, and a busy kernel's refusal shows under it", async () => {
  const gone = widgetsHarness({ gone: true, message: "This control is no longer held by the kernel (it restarted). Run the cell again." });
  const o = { type: "widget", model: "w1", view: { id: "w1", kind: "button", description: "Go", disabled: false } };
  const wrap = gone.ctx.api.renderWidgetOutput(o, null);
  allNodes(wrap).find((n) => n.tag === "button").attrs.onclick();
  await new Promise((r) => setImmediate(r));
  assert.match(o.gone, /Run the cell again/);
  const busy = widgetsHarness(() => Promise.reject(new Error("A cell is running. The control works again when it finishes.")));
  const o2 = { type: "widget", model: "w1", view: { id: "w1", kind: "text", description: "q", disabled: false, value: "" } };
  const wrap2 = busy.ctx.api.renderWidgetOutput(o2, null);
  allNodes(wrap2).find((n) => n.tag === "input").listeners.change();
  await new Promise((r) => setImmediate(r));
  assert.ok(allNodes(wrap2.replaced).some((n) => n.attrs?.role === "alert"));
  // Disabled controls are drawn disabled.
  const d = busy.ctx.api.drawWidget({ id: "w9", kind: "slider", description: "", disabled: true, value: 1, min: 0, max: 2, step: 1 }, () => {}, null, 0);
  assert.strictEqual(allNodes(d).find((n) => n.tag === "input").attrs.disabled, true);
});

// notebookutils.udf.getFunctions lists a User data functions item; calls run in Fabric.
test("notebook docs describe udf.getFunctions as read only, Preview", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(doc, /\| `udf` \(Preview\) \| `getFunctions\(name, workspaceId\)` reads a User data functions item as you/);
  assert.match(doc, /Calling a function runs it in Fabric on its workspace's capacity, so on this PC the call says so instead/);
});

// Altair charts (renderVegaLite): the spec goes into the frame's script with every < escaped, so text in
// the data can't end the script tag, and the Vega libraries are the vendored build, not a CDN.
test("an Altair chart's spec can't break out of the frame's script", () => {
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(/function vegaPage\([\s\S]*?\n}\n/.exec(nb)[0] + "\nthis.vegaPage = vegaPage;", ctx);
  const evil = "</script><script>parent.postMessage('x','*')</script><!--";
  const page = ctx.vegaPage("/* vega */", { data: { values: [{ name: evil }] }, mark: "bar" });
  assert.strictEqual(page.match(/<\/script>/g).length, 2, "only the page's own two scripts end");
  assert.ok(!page.includes(evil) && !page.includes("<!--"));
  const json = /vegaEmbed\("#chart", ([\s\S]*?), \{ actions/.exec(page)[1];
  assert.strictEqual(JSON.parse(json).data.values[0].name, evil, "the spec arrives unchanged");
  const loader = /function loadVega\([\s\S]*?\n}\n/.exec(nb)[0];
  assert.match(loader, /script\.src = "vendor\/vega\/vega-frame\.js"/);
  const built = fs.readFileSync(path.join(ui, "vendor", "vega", "vega-frame.js"), "utf8");
  assert.match(built.split("\n")[0], /vega 6\.4\.0, vega-lite 6\.4\.3, vega-embed 7\.3\.0/);
  assert.ok(!/<\/script|<!--/i.test(built));
});

// Folders for notebooks (notebook-files.js, notebook_folders_service.py): the grouped tree, where a
// notebook can move, and the folder actions' wiring.
function notebookFolderHelpers() {
  const src = fs.readFileSync(path.join(ui, "notebook-files.js"), "utf8");
  const ctx = { registerFeature() {} };
  vm.runInNewContext(`${src}; Object.assign(this, { nbFolderTree, nbFolderChoices, nbNameInFolder, nbLeaf, nbFolderOf, nbFolderDepth, nbRenameProblemLines });`, ctx);
  return ctx;
}

test("notebooks group into a folder tree per place, with counts that include subfolders and empty folders", () => {
  const { nbFolderTree } = notebookFolderHelpers();
  const repo = { root: "C:/r", label: "repo" };
  const list = [{ name: "Sales/Daily load" }, { name: "Sales/Archive/Q1" }, { name: "Top" }, { name: "Silver/Orders", place: repo }];
  const folders = [{ path: "Sales", place: null }, { path: "Sales/Archive", place: null }, { path: "Scratch", place: null }, { path: "Silver", place: repo }];
  const tree = nbFolderTree(list, folders);
  const plain = (f) => ({ name: f.name, count: f.count, books: f.notebooks.map((n) => n.name), sub: f.folders.map(plain) });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(tree.folders.map(plain))), [
    { name: "Sales", count: 2, books: ["Sales/Daily load"], sub: [{ name: "Archive", count: 1, books: ["Sales/Archive/Q1"], sub: [] }] },
    { name: "Scratch", count: 0, books: [], sub: [] },
    { name: "Silver", count: 1, books: ["Silver/Orders"], sub: [] },
  ]);
  assert.deepStrictEqual([...tree.loose.map((n) => n.name)], ["Top"]);
  // A notebook in a folder the folder listing hasn't caught up with still gets its folder.
  assert.strictEqual(nbFolderTree([{ name: "New/X" }], []).folders[0].count, 1);
});

test("a notebook moves to folders of its own place, keeping its own name", () => {
  const { nbFolderChoices, nbNameInFolder, nbLeaf, nbFolderOf, nbFolderDepth } = notebookFolderHelpers();
  const repo = { root: "C:/r", label: "repo" };
  const folders = [{ path: "Sales" }, { path: "Finance", place: null }, { path: "Silver", place: repo }];
  assert.deepStrictEqual([...nbFolderChoices(folders, null)], ["", "Finance", "Sales"]);
  assert.deepStrictEqual([...nbFolderChoices(folders, repo)], ["", "Silver"]);
  assert.strictEqual(nbNameInFolder("Explore orders", "Sales"), "Sales/Explore orders");
  assert.strictEqual(nbNameInFolder("Sales/Archive/Q1", ""), "Q1");
  assert.deepStrictEqual([nbLeaf("A/B/C"), nbFolderOf("A/B/C"), nbFolderOf("C"), nbFolderDepth("A/B"), nbFolderDepth("")], ["C", "A/B", "", 2, 0]);
});

test("folder actions are wired: a menu entry, New folder, the empty-only delete in red, Preview on publish, docs", () => {
  const files = fs.readFileSync(path.join(ui, "notebook-files.js"), "utf8");
  assert.match(files, /label: "Move to folder…", icon: "folder", action: \(\) => nbMoveToFolderDialog\(n, folders\)/);
  assert.match(files, /btn\("New folder", "folder", \(\) => nbNewFolderDialog\(null/);
  assert.match(files, /confirmDialog\(`Delete folder \$\{f\.path\}\?`, .*"Delete", \{ danger: true \}\)/);
  // A folder with notebooks in it is refused before anything is asked or sent.
  assert.match(files, /if \(f\.count\) \{\n\s+nbFolderRefusal\(f, /);
  assert.match(files, /previewMarked\("notebookFolderPublish", /);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /notebookFolderPublish: "Publishing notebooks from folders"/);
  for (const m of ["notebook_folders", "notebook_folder_create", "notebook_folder_rename", "notebook_folder_delete", "notebook_move_to_folder"]) {
    assert.ok(files.includes(`"${m}"`), `the UI calls ${m}`);
  }
  assert.match(files, /function nbFoldersHowItWorks\(\)/);
  assert.match(fs.readFileSync(path.join(ui, "notebook.js"), "utf8"), /tab\("notebooks", "Notebooks"\)/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /rpc\("new_notebook", into\?\.folder \? \{ place, folder: into\.folder \} : \{ place \}\)/);
  const root = path.join(__dirname, "..", "..");
  for (const f of ["docs/README.md", "docs/user-guide.md", "zensical.toml"]) {
    assert.match(fs.readFileSync(path.join(root, f), "utf8"), /notebook-folders\.md/, `${f} links the folders page`);
  }
});

test("a move that leaves something behind names it and says what to do", () => {
  const { nbRenameProblemLines } = notebookFolderHelpers();
  const lines = nbRenameProblemLines([{ notebook: "EMEA/B", was: "Sales/B", step: "schedules", error: "OSError: disk full" }]);
  assert.deepStrictEqual([...lines], ["EMEA/B (was Sales/B): its schedules didn't follow (OSError: disk full). Open Monitor > KratosForge > Schedules and point its schedule on this PC at the new name."]);
  assert.deepStrictEqual([...nbRenameProblemLines([])], []);
  const files = fs.readFileSync(path.join(ui, "notebook-files.js"), "utf8");
  assert.strictEqual(files.match(/nbRenameWarning\((out|ok)\.problems\)/g).length, 3);
});

test("Format code (nbformat.js): the engine call, its hooks in notebook.js and the shortcut", () => {
  const src = fs.readFileSync(path.join(ui, "nbformat.js"), "utf8");
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="nbformat.js"') > html.indexOf('src="notebook.js"'));
  assert.match(src, /rpc\("notebook_format", \{ sources: sent \}\)/);
  assert.match(src, /c\.source === sent\[i\]/, "a cell typed in while formatting keeps what was typed");
  assert.match(nb, /\{ key: "Shift-Alt-f", run: \(\) => \{ formatCells\(\[cell\]\); return true; \} \}/);
  assert.match(nb, /btn\("Format notebook", "code", formatNotebook/);
  assert.match(nb, /label: "Format cell"/);
  assert.match(src, /registerShortcuts\("Notebook", \[\s*\{ keys: \["Shift\+Alt\+F"\]/);
});

// Notebook history: the magics page documents In, Out and %history.
test("the magics page documents In, Out and %history", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /\| `In`, `Out`, `_`, `__`, `___`/);
  assert.match(doc, /\| `%history`, `%history -n 4-6`/);
});

// Spark on this PC: createDataFrame takes a DDL schema string.
test("the Spark on this PC page says createDataFrame reads a DDL schema string", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`spark.createDataFrame` takes a list of column names, a `StructType` or a DDL string/);
  assert.match(doc, /A `NOT NULL` field\nrefuses a row with a null there/);
});

// Magics hardening: the magics page says how to write Windows paths with spaces.
test("the magics page says to quote paths with spaces and keeps backslashes", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /Put a path with spaces in quotes/);
  assert.match(doc, /inside double quotes `\\"` is a quote/);
});

// Spark on this PC: the NOT NULL check names PySpark's error and verifySchema.
test("the Spark on this PC page names the NOT NULL error and verifySchema", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`\[FIELD_NOT_NULLABLE_WITH_NAME\]` error; `verifySchema=False` skips the check/);
});

// Spark on this PC: NOT NULL covers array elements and map values.
test("the Spark on this PC page says NOT NULL covers array elements and map values", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /for array elements or map values a `StructType` declares\nnon-nullable/);
});

// Spark on this PC: an empty struct<> is refused with a clear message.
test("the Spark on this PC page says an empty struct<> isn't supported", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /An empty `struct<>` isn't supported here/);
});

// Magics: _sqldf follows the last statement with -- and /* */ comments skipped.
test("the magics page says _sqldf reads the last statement past comments", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /What counts is the cell's last statement,\n  with `--` and `\/\* \*\/` comments skipped/);
});

// Magics: %timeit names options it doesn't take; %who filters by type.
test("the magics page says %timeit -o isn't supported and %who filters by type", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /Other IPython options such as `-o` and `-q` say they aren't supported/);
  assert.match(doc, /`%who str int` lists only those types/);
});

// Magics: %pip takes combined quiet flags and refuses options after a package in a requirements file.
test("the magics page says %pip takes -qU and refuses options after a package", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /even after a\n  package on the same line/);
  assert.match(doc, /\(also combined, like `-qU`\) and `-rfile.txt` work as\n  in pip/);
});

// Magics: autoreload repoints a number or string only under its own name.
test("the magics page says autoreload leaves your own values alone", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /A number or string follows only\n  under its own name/);
  assert.match(doc, /also `pip install --user`\n  ones/);
});

// Magics: %run and %%writefile refuse what they would otherwise drop.
test("the magics page says %run and %%writefile refuse leftover words", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /Anything else on the line is an\n  error rather than being dropped/);
  assert.match(doc, /A name with spaces needs quotes, or the cell says so instead of writing to the first word/);
});

// Magics: %env says when a name can't be set.
test("the magics page says %env refuses a name it can't set", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /`%env 1BAD=x` says so instead of doing nothing/);
});

// Magics: a CTE that inserts isn't a query; %env's space form explains a bad name.
test("the magics page says WITH ... INSERT is a write and covers %env 1BAD x", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-magics.md"), "utf8");
  assert.match(doc, /`WITH x AS \(\.\.\.\) INSERT INTO \.\.\.` is a write, not a query/);
  assert.match(doc, /and so does `%env 1BAD x`/);
});

// Spark on this PC: sample, dropna and unpivot check their arguments.
test("the Spark on this PC page says sample, dropna and unpivot check arguments as Spark does", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`sample`, `dropna` and `unpivot` check their arguments as Spark does/);
});

// Spark on this PC: na functions and describe refuse unknown columns.
test("the Spark on this PC page says na functions name a missing column", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`\[UNRESOLVED_COLUMN\]` error instead of skipping it, and `replace` refuses lists of different lengths/);
});

// Spark on this PC: agg's dict form works after groupBy and pivot.
test("the Spark on this PC page says agg takes a dict after groupBy and pivot", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`agg` takes Spark's `\{column: function\}` form after `groupBy` and `pivot` too/);
});

// Spark on this PC: drop and withColumnRenamed skip a missing column, as Spark does.
test("the Spark on this PC page says drop and withColumnRenamed skip a missing column", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`drop` and `withColumnRenamed` match names ignoring case and skip a column that isn't there/);
});

// Spark on this PC: dropping every column says why it can't.
test("the Spark on this PC page says dropping every column is an error", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /Dropping every column is an error here: Spark keeps a frame with no columns/);
});

// Spark on this PC: summary takes statistics and adds quartiles.
test("the Spark on this PC page says summary takes statistics and adds quartiles", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`summary` takes statistics, not columns, and\nadds the quartiles/);
});

// Spark on this PC: substring position 0 and concat with a NULL match Spark.
test("the Spark on this PC page says substring counts position 0 as 1 and concat keeps NULL", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`substring` counts position 0 as 1 and `concat` is NULL when any input is NULL/);
});

// Spark on this PC: initcap lower-cases the rest of each word.
test("the Spark on this PC page says initcap lower-cases the rest of each word", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`initcap` lower-cases the rest of each word, as Spark does/);
});

// Spark on this PC: unix_timestamp takes dates and timestamps.
test("the Spark on this PC page says unix_timestamp takes a date or timestamp", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`unix_timestamp` takes a date or timestamp as well as text, reads it in the session\ntime zone/);
});

// Spark on this PC: unix_timestamp without a year starts from 1970, as Spark does.
test("the Spark on this PC page says a pattern without a year reads from 1970-01-01", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /A pattern without a year reads its date from 1970-01-01, as Spark does/);
});

// Spark on this PC: quoted repeated pattern letters, groupBy() and ignorenulls.
test("the Spark on this PC page covers quoted 'yyyy', groupBy() and first/last ignoring NULLs", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /Quoted text that repeats a pattern letter \(`'yyyy'`\)/);
  assert.match(doc, /`groupBy\(\)` with no columns aggregates the whole frame/);
});

// Query profile (qprofile.js): runs only when asked, says it reads OneLake again, and lists each step.
test("the query profile runs only when asked, warns about capacity and lists each step with its share", async () => {
  const src = fs.readFileSync(path.join(ui, "qprofile.js"), "utf8");
  const { node } = chartsHarness(null);
  const calls = [];
  let copied = null;
  let answer = { seconds: 1.38, rows: 6, rows_scanned: 12480113, peak_memory: 212 * 1024 * 1024, more_steps: 0, steps: [
    { depth: 0, name: "ORDER_BY", rows: 6, seconds: 0.001, share: 0.001, details: { "Order By": "revenue DESC" } },
    { depth: 1, name: "HASH_GROUP_BY", rows: 6, seconds: 0.29, share: 0.21, details: { Groups: "region" } },
    { depth: 2, name: "SEQ_SCAN", rows: 12480113, seconds: 0.97, share: 0.71, details: { Table: "silver.orders", Filters: "status='shipped'" } }] };
  const ctx = { h: node, friendlyError: (e) => `failed: ${e.message}`, toast: () => {}, attempt: (fn) => fn(),
    btn: (label, _i, onclick, _c, attrs = {}) => node("button", { onclick, ...attrs }, label),
    navigator: { clipboard: { writeText: async (t) => { copied = t; } } },
    rpc: async (m, a) => { calls.push([m, a]); if (answer instanceof Error) throw answer; return answer; } };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(ui, "app.js"), "utf8").match(/function sizeText\([\s\S]*?\n}\n/)[0] + src +
    "\nthis.api = { renderQueryProfile, qprofileText, sizeText };", ctx);
  const o = { frame: "f", spark: true };
  const box = ctx.api.renderQueryProfile(o);
  assert.strictEqual(calls.length, 0, "nothing runs until asked");
  const text = (n) => allNodes(n).flatMap((x) => x.kids.filter((k) => typeof k === "string")).join(" ");
  assert.match(text(box), /read from OneLake again, which uses capacity/);
  allNodes(box).find((n) => n.tag === "button" && n.kids.includes("Profile the query")).attrs.onclick();
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(plain(calls[0]), ["notebook_frame_query_profile", { frame: "f" }]);
  const t = text(box);
  assert.match(t, /Read table/);
  assert.match(t, /silver\.orders/);
  assert.match(t, /Slowest step/);
  assert.match(t, /12,480,113/);
  assert.match(t, /212 MB/);
  const slow = allNodes(box).filter((n) => n.tag === "tr" && n.attrs.class === "slowest");
  assert.strictEqual(slow.length, 1);
  assert.match(text(slow[0]), /Read table/);
  allNodes(box).find((n) => n.tag === "button" && n.kids.includes("Copy as text")).attrs.onclick();
  await new Promise((r) => setImmediate(r));
  assert.match(copied, /^Query profile: 1\.38 s/);
  assert.match(copied, /\n    Read table \(silver\.orders\): 12,480,113 rows, 0\.97 s, 71%/);
  // A restart since the result says to run the cell again; an error keeps the last profile.
  answer = new Error("boom");
  allNodes(box).find((n) => n.tag === "button" && n.kids.includes("Run again")).attrs.onclick();
  await new Promise((r) => setImmediate(r));
  assert.match(text(box), /failed: boom/);
  assert.match(text(box), /Read table/);
  answer = { gone: true };
  allNodes(box).find((n) => n.tag === "button" && n.kids.includes("Run again")).attrs.onclick();
  await new Promise((r) => setImmediate(r));
  assert.match(text(box), /Run the cell again/);
  assert.strictEqual(ctx.api.sizeText(1.1 * 1024 ** 3), "1.1 GB");
  const nb = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  assert.match(nb, /o\.frame && o\.spark \? seg\("qprofile", "Query profile"\) : null/);
  assert.match(nb, /previewMarked\("queryProfile"/);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /queryProfile: "Query profile"/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="ipywidgets\.js"><\/script>\n  <script src="qprofile\.js"><\/script>/);
  assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML|outerHTML/);
});

// Spark on this PC: slice takes a length and add_months returns a date.
test("the Spark on this PC page says slice takes a length and add_months returns a date", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`slice\(arr, start, length\)` takes a length and counts a\nnegative start from the end, and `add_months` returns a date/);
});

// Spark on this PC: slice refuses index 0 and add_months reads text.
test("the Spark on this PC page says slice refuses index 0 and add_months reads text as a date", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`slice` refuses index 0 and a negative\nlength with Spark's error/);
  assert.match(doc, /`add_months` reads text as a date \(text that isn't a date gives NULL\)/);
});

// Spark on this PC: element_at index 0 and months_between rounding.
test("the Spark on this PC page says element_at refuses index 0 and months_between rounds to 8 digits", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`element_at` refuses an array's index 0, and `months_between` counts the time of day and rounds to 8 digits/);
});

// Pop-out windows (Preview): the shell opens only these sections, and the app's own page in their
// windows is the only part that gets the app's permissions; their Fabric views get none.
test("pop-out windows load popout.js after app.js and get no more than the main window", () => {
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  assert.ok(html.indexOf('src="app.js"') < html.indexOf('src="popout.js"'), "popout.js uses app.js's helpers");
  const cap = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "src-tauri", "capabilities", "default.json"), "utf8"));
  assert.deepStrictEqual(cap.windows, ["main"]);
  assert.deepStrictEqual(cap.webviews, ["pop-out-runs", "pop-out-fabric"]);
  assert.deepStrictEqual(cap.permissions, ["core:default"]);
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(app, /const POP_OUT = \["runs", "fabric"\]\.find/);
  const shell = fs.readFileSync(path.join(__dirname, "..", "src-tauri", "src", "main.rs"), "utf8");
  assert.match(shell, /const POP_OUTS: \[\(&str, &str\); 2\] = \[\("runs", [^)]*\), \("fabric", [^)]*\)\];/);
});

// Shared sessions (sessions.js): Share sends the picked notebook to join this one's session; a guest can Leave.
test("Share adds another notebook to this session, and a notebook that shares one can leave it", async () => {
  const src = fs.readFileSync(path.join(ui, "sessions.js"), "utf8");
  const { node } = chartsHarness(null);
  const calls = [];
  const dialogs = [];
  const toasts = [];
  const confirms = [];
  let data = { process: true, notes: [], shared: {}, max_sessions: 5, idle_minutes: 0, sessions: [
    { notebook: "Sales cleanup", process: true, status: "idle", pid: 10, memory_bytes: 1, cells_run: 3, last_used: 1, active: true, shared_with: [] },
    { notebook: "Customer churn", process: true, status: "idle", pid: 11, memory_bytes: 1, cells_run: 1, last_used: 1, active: false, shared_with: [] }] };
  const ctx = { h: node, btn: (label, _i, onclick, _c, attrs = {}) => node("button", { onclick, ...attrs }, label),
    preview: (_id, n) => n, previewMarked: (_id, n) => n, toast: (t) => toasts.push(t), attempt: (fn) => fn(),
    when: String, sizeText: String, go: () => {}, openNotebook: () => {}, renderNotebook: () => {},
    closeDialog: () => {}, showDialog: (title, body) => dialogs.push(body),
    confirmDialog: async (title, text, ok, opts) => { confirms.push([title, text, ok, opts.danger]); return true; },
    openNotebooks: new Map([["Sales cleanup", {}], ["Sales report", {}]]), state: { notebook: { name: "Sales cleanup" }, view: "notebook" },
    rpc: async (m, a) => { calls.push([m, a]); return m === "kernel_sessions" ? data : m === "kernel_session_share" ? { host: a.host } : { left: "Sales cleanup" }; } };
  vm.createContext(ctx);
  vm.runInContext(src + "\nthis.api = { sessionsDialog, sessionsButton, sharedCount: (n) => sharedCount(n) };", ctx);
  await ctx.api.sessionsDialog();
  const text = (n) => allNodes(n).flatMap((x) => x.kids.filter((k) => typeof k === "string")).join(" ");
  const select = allNodes(dialogs[0]).find((n) => n.tag === "select");
  assert.deepStrictEqual(select.kids.map((o) => o.attrs.value), ["Customer churn", "Sales report"]);
  assert.match(text(dialogs[0]), /Share this notebook's session with/);
  assert.match(text(dialogs[0]), /same default lakehouse and environment/);
  select.value = "Sales report";
  allNodes(dialogs[0]).find((n) => n.tag === "button" && n.kids.includes("Share")).attrs.onclick();
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(plain(calls.find(([m]) => m === "kernel_session_share")[1]), { notebook: "Sales report", host: "Sales cleanup" });
  assert.deepStrictEqual(confirms[0].slice(2), ["Share", true]);
  assert.match(confirms[0][1], /Sales report's own session ends and its variables are cleared/);
  assert.match(toasts.join(" "), /Sales report now shares Sales cleanup's session. Its variables were cleared/);
  // The host's row names its guests, and the status bar says the session is shared.
  data = { ...data, shared: { "Sales report": "Sales cleanup" }, sessions: [{ ...data.sessions[0], shared_with: ["Sales report"] }] };
  await ctx.api.sessionsDialog();
  assert.match(text(dialogs[dialogs.length - 1]), /Shared with Sales report/);
  assert.strictEqual(ctx.api.sharedCount("Sales report"), 2);
  assert.ok(ctx.api.sessionsButton().kids.includes("Shared session · 2 notebooks"));
  // A notebook that shares one says whose, and leaves it.
  ctx.state.notebook = { name: "Sales report" };
  await ctx.api.sessionsDialog();
  const last = dialogs[dialogs.length - 1];
  assert.match(text(last), /Sales report shares Sales cleanup's session/);
  allNodes(last).find((n) => n.tag === "button" && n.kids.includes("Leave")).attrs.onclick();
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(plain(calls.find(([m]) => m === "kernel_session_leave")[1]), { notebook: "Sales report" });
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /sharedSessions: "Shared notebook sessions"/);
});

// The pure helpers at the top of pipelines.js (Pipelines, Preview).
function pipelineHelpers() {
  const src = fs.readFileSync(path.join(ui, "pipelines.js"), "utf8");
  const code = src.slice(src.indexOf("const PL_SETUP"), src.indexOf("// UTF-8 text as base64"));
  const ctx = {};
  vm.runInNewContext(`${code}; Object.assign(this, { plVerdict, plLayers, plAfter, plTimelineRows, plBar, plParamValue, plParamText, plStorable });`, ctx);
  return ctx;
}

test("pipelines: an activity runs here, needs a mock or runs only in Fabric", () => {
  const { plVerdict } = pipelineHelpers();
  const copy = { path: "$.a[1]", classification: "static" };
  assert.strictEqual(plVerdict(copy, [{ path: "$.a[1]", code: "not_local" }]), "mock");
  // What the Run dialog settles (a notebook to pick, lakehouse reads to allow) doesn't need a mock.
  assert.strictEqual(plVerdict({ path: "$.a[3]", classification: "local" }, [{ path: "$.a[3]", code: "notebook_unmapped" }]), "here");
  assert.strictEqual(plVerdict({ path: "$.a[3]", classification: "local" }, [{ path: "$.a[30]", code: "not_local" }]), "here");
  assert.strictEqual(plVerdict({ path: "$.a[4]", classification: "unsupported" }, []), "fabric");
});

test("pipelines: the graph puts each activity a column after what it depends on", () => {
  const { plLayers, plAfter } = pipelineHelpers();
  const acts = [
    { name: "C", depends_on: [{ activity: "A", conditions: ["Succeeded"] }, { activity: "B", conditions: ["Failed"] }] },
    { name: "A", depends_on: [] },
    { name: "B", depends_on: [{ activity: "A", conditions: ["Succeeded"] }] },
    { name: "D", depends_on: [{ activity: "missing" }] },
  ];
  assert.deepStrictEqual(JSON.parse(JSON.stringify(plLayers(acts).map((l) => l.map((a) => a.name)))), [["A", "D"], ["B"], ["C"]]);
  // A cycle (validation reports it) still draws every activity, once.
  const cycle = plLayers([{ name: "X", depends_on: [{ activity: "Y" }] }, { name: "Y", depends_on: [{ activity: "X" }] }]);
  assert.strictEqual(cycle.flat().map((a) => a.name).sort().join(), "X,Y");
  assert.strictEqual(plAfter(acts[0]), "after A, B fails");
  assert.strictEqual(plAfter({ depends_on: [{ activity: "V", conditions: ["Succeeded", "Failed"] }] }), "after V or fails");
  assert.strictEqual(plAfter(acts[1]), "");
});

test("pipelines: the run view nests iterations and child pipelines under their parent", () => {
  const { plTimelineRows, plBar } = pipelineHelpers();
  const record = { activities: [
    { id: "1", name: "Loop" }, { id: "2", name: "Step", parent: "1", label: "iteration 0" },
    { id: "3", name: "Child", child_run: { activities: [{ id: "c1", name: "Inner" }] } },
    { id: "4", name: "Step", parent: "1", label: "iteration 1" },
  ] };
  const rows = plTimelineRows(record);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(rows.map((r) => [r.act.name, r.depth]))), [["Loop", 0], ["Step", 1], ["Step", 1], ["Child", 0], ["Inner", 1]]);
  // Within a loop, rows go by iteration, then in the order they ran.
  const loop = plTimelineRows({ activities: [{ id: "L", name: "L" },
    { id: "a0", name: "A", parent: "L", label: "iteration 0" }, { id: "a1", name: "A", parent: "L", label: "iteration 1" },
    { id: "b0", name: "B", parent: "L", label: "iteration 0" }, { id: "b1", name: "B", parent: "L", label: "iteration 1" }] });
  assert.strictEqual(loop.map((r) => r.act.id).join(), "L,a0,b0,a1,b1");
  const bar = plBar({ started: "2026-10-23T10:00:05Z", ended: "2026-10-23T10:00:10Z" }, "2026-10-23T10:00:00Z", "2026-10-23T10:00:20Z");
  assert.deepStrictEqual({ ...bar }, { left: 25, width: 25 });
  assert.deepStrictEqual({ ...plBar({ started: "x" }, "2026-10-23T10:00:00Z", "2026-10-23T10:00:20Z") }, { left: 0, width: 0 });
});

test("pipelines: parameters are typed as Fabric declares them", () => {
  const { plParamValue, plParamText } = pipelineHelpers();
  assert.strictEqual(plParamValue("String", "[1]"), "[1]");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(plParamValue("array", '["a", "b"]'))), ["a", "b"]);
  assert.strictEqual(plParamValue("int", "3"), 3);
  assert.strictEqual(plParamValue("int", " "), undefined);
  assert.throws(() => plParamValue("object", "{nope"));
  assert.strictEqual(plParamText("string", "x"), "x");
  assert.strictEqual(plParamText("array", ["a"]), '["a"]');
  assert.strictEqual(plParamText("int", undefined), "");
});

test("pipelines: SecureString values and network leave are never kept on this PC", () => {
  const { plStorable } = pipelineHelpers();
  const kept = plStorable({ parameters: { day: "2026-10-23", token: "s3cret", Key: "k" }, allow_uncontained: true, keep_outputs: true },
    [{ name: "day", type: "String" }, { name: "token", type: "SecureString" }, { name: "Key", type: "securestring" }]);
  assert.strictEqual(JSON.stringify(kept.parameters), '{"day":"2026-10-23"}');
  assert.strictEqual(kept.allow_uncontained, false);
  assert.strictEqual(kept.keep_outputs, true);
  assert.doesNotMatch(JSON.stringify(kept), /s3cret/);
});

// Panel and dialog transitions (KF-MO-003): Minimal and above, transform and opacity only, never at Off.
test("Panel transitions: the fade-out card is made at Minimal, not at Off or with reduced motion, and hides from readers", () => {
  const at = (level, opts = {}) => {
    const ctx = motionHarness({ ...opts, settings: { motion_level: { value: level } } });
    ctx.document.body = ctx.node();
    const dialog = ctx.node("overlay");
    dialog.querySelector = () => null;
    dialog.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 600 });
    return [ctx, dialog];
  };
  for (const [level, opts] of [[0, {}], [3, { reduced: true }]]) {
    const [ctx, dialog] = at(level, opts);
    assert.strictEqual(ctx.motionLeave(dialog), null, `a card faded at level ${level}${opts.reduced ? " with reduced motion" : ""}`);
  }
  const [ctx, dialog] = at(1);
  const ghost = ctx.motionLeave(dialog);
  assert.ok(ghost, "no card faded at Minimal");
  assert.strictEqual(ghost.attrs["aria-hidden"], "true");
  assert.ok(ghost.cls.has("mo-ghost") && !ghost.cls.has("overlay"), "the fading card can be mistaken for the dialog");
  assert.strictEqual(ghost.parent, ctx.document.body);
  ctx.advance(250);
  assert.strictEqual(ghost.parent, null, "the fading card stayed");
  assert.strictEqual(ctx.motionLeave(null), null);
});

test("Panel transitions: the dialog and tray go before anything fades, and the CSS animates only transform and opacity", () => {
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(app, /motionLeave\(root\.querySelector\(":scope > \.overlay"\)\);\n\s*root\.replaceChildren\(\);/, "closeDialog doesn't take the dialog away at once");
  assert.match(fs.readFileSync(path.join(ui, "activity.js"), "utf8"), /motionLeave\(root\.querySelector\(":scope > \.tray"\)\); root\.replaceChildren\(\);/);
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  const frames = [...css.matchAll(/@keyframes (mo-[\w-]+) \{(.*)\}\n/g)];
  assert.ok(frames.length >= 6);
  for (const [, name, body] of frames) {
    for (const [, prop] of body.matchAll(/([\w-]+)\s*:/g)) assert.ok(["opacity", "transform"].includes(prop), `${name} animates ${prop}`);
  }
  assert.match(css, /html\[data-motion-level="0"\] :is\(\.mo-in, \.mo-in > \.dialog\) \{ animation: none; \}/);
  assert.match(css, /html\[data-motion-level="0"\] \.mo-ghost \{ display: none; \}/);
  const ins = [...css.matchAll(/\.mo-in[^{]*\{ animation: [\w-]+ (\d+)ms/g)];
  assert.ok(ins.length >= 4);
  for (const m of ins) assert.ok(+m[1] >= 150 && +m[1] <= 250, `${m[0]} is outside 150-250 ms`);
});

// The cooling spark (KF-MO-006): Validate, Prove and a confirmed publish cool; their failures are embers.
test("Cooling spark: a passed check cools for 500 ms, then is gone; failures play the ember", () => {
  const ctx = motionHarness({ settings: { motion_level: { value: 2 } } });
  const events = vm.runInContext("FORGE_EVENTS", ctx);
  for (const name of ["validate.passed", "prove.matched", "publish.verified"]) assert.strictEqual(events[name], "cool", name);
  for (const name of ["validate.failed", "prove.failed", "publish.failed"]) assert.strictEqual(events[name], "fail", name);
  const badge = ctx.node();
  const el = ctx.forgeEvent("validate.passed", badge);
  assert.ok(el && el.cls.has("fx-cool") && el.attrs["aria-hidden"] === "true", "no cooling spark");
  assert.strictEqual(ctx.live.textContent, "", "the cooling spark announced itself");
  ctx.advance(599);
  assert.strictEqual(badge.kids.length, 1, "removed before it finished cooling");
  ctx.advance(1);
  assert.strictEqual(badge.kids.length, 0, "the cooling spark stayed");
  assert.strictEqual(motionHarness({ settings: { motion_level: { value: 2 }, motion_sparks: { value: false } } }).forgeEvent("prove.matched", ctx.node()), null,
    "the cooling spark ignored the sparks switch");
  assert.strictEqual(motionHarness({ settings: { motion_level: { value: 1 } } }).forgeEvent("publish.verified", ctx.node()), null, "it played at Minimal");
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  for (const rule of css.match(/@keyframes fx-cool-[a-z]+ \{[^\n]*\n/g)) assert.doesNotMatch(rule, /color|background|width|height|top|left/, "cooling animates more than transform and opacity");
  const validate = fs.readFileSync(path.join(ui, "validate.js"), "utf8");
  assert.match(validate, /if \(r\.state !== "cancelled"\) forgeEvent\(ok \? "validate\.passed" : "validate\.failed"/, "a cancelled clean run would play an effect");
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /if \(result\.id\) forgeEvent\("publish\.verified"/, "a publish cools before Fabric gave back the item");
});

// KF-MO-008 Achievement Reveal (profile.js profileEarned): one message and one reveal per new award,
// nothing for an award already shown (this session or a stored one), kept per account.
test("Achievement Reveal: a new award is announced and revealed once, a repeat never, and deleting the profile resets it", () => {
  const p = fs.readFileSync(path.join(ui, "profile.js"), "utf8");
  const src = p.slice(p.indexOf("const PROFILE_CELEBRATED"), p.indexOf("registerFeature("));
  const stored = {};
  const said = [];
  const revealed = [];
  const toastEl = { id: "toast" };
  const ctx = {
    state: { user: "dev@contoso.com" }, RECORDING_ON: "Recording is on.",
    localStorage: { getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; } },
    setTimeout: (fn) => fn(),
    toast: (text) => said.push(text),
    forgeEvent: (name, anchor) => revealed.push([name, anchor.id]),
    document: { getElementById: (id) => (id === "toast" ? toastEl : null) },
  };
  vm.createContext(ctx);
  vm.runInContext(`${src}; this.profileEarned = profileEarned; this.profileForget = profileForget;`, ctx);
  const first = { id: "first-flame", name: "First Flame", xp: 50, first: true };  // the engine marks the profile's first award
  const steady = { id: "steady-hand", name: "Steady Hand", xp: 100 };
  ctx.profileEarned([first, steady]);
  assert.deepStrictEqual(said, ["Achievement earned: First Flame (+50 XP), Steady Hand (+100 XP). See it in Profile. Recording is on."]);
  assert.deepStrictEqual(revealed, [["achievement.unlocked", "toast"]], "not one reveal for the message");
  ctx.profileEarned([first]);
  ctx.profileEarned([]);
  ctx.profileEarned(undefined);
  assert.strictEqual(said.length, 1, "a repeat was announced");
  assert.strictEqual(revealed.length, 1, "a repeat was revealed");
  assert.deepStrictEqual(JSON.parse(stored["kf.profile.celebrated"]), ["dev@contoso.com|first-flame", "dev@contoso.com|steady-hand"]);
  ctx.state.user = "other@contoso.com";  // another account's profile earns its own
  ctx.profileEarned([first]);
  assert.strictEqual(revealed.length, 2);
  ctx.state.user = "dev@contoso.com";
  ctx.profileForget();
  assert.deepStrictEqual(JSON.parse(stored["kf.profile.celebrated"]), ["other@contoso.com|first-flame"], "deleting one profile forgot another's");
  ctx.profileEarned([first]);
  assert.strictEqual(revealed.length, 3, "an award earned again after deleting the profile stayed silent");
  // Storage that can't be read or written still never repeats within the session.
  ctx.localStorage.getItem = () => { throw new Error("blocked"); };
  ctx.localStorage.setItem = () => { throw new Error("blocked"); };
  ctx.profileEarned([first]);
  assert.strictEqual(revealed.length, 3);
  assert.match(p, /if \(await attempt\(\(\) => rpc\("profile_delete", \{\}\), "Could not delete your profile data"\)\) \{\n\s+profileForget\(\);/);
});

test("Remote runs (KF-MO-009): the navy wolf trots in an indeterminate bar, with no fill or count, and goes with the motion", () => {
  const find = (n, cls) => [n, ...n.all()].find((k) => k.cls.has(cls)) || null;
  const ctx = motionHarness({ settings: { motion_level: { value: 2 } } });
  const el = ctx.remoteProgress("Sales · Notebook run");
  const bar = find(el, "progress");
  assert.strictEqual(bar.attrs.role, "progressbar");
  assert.strictEqual(bar.attrs["aria-label"], "Sales · Notebook run");
  assert.strictEqual(bar.attrs["aria-valuetext"], "Running in Fabric");
  for (const k of ["aria-valuenow", "aria-valuemax"]) assert.ok(!(k in bar.attrs), `an indeterminate bar has ${k}`);
  assert.strictEqual(find(el, "wolf-idle")?.attrs["aria-hidden"], "true", "no wolf at Refined");
  assert.ok(!find(el, "wolf-fill"), "a remote run has a fill");
  assert.ok(find(el, "remote-where"), "nothing says where it runs");
  for (const opts of [{ settings: { motion_level: { value: 1 } } }, { reduced: true }, { settings: { motion_level: { value: 2 }, motion_wolf: { value: false } } }]) {
    const quiet = motionHarness(opts).remoteProgress("Sales · Notebook run");
    assert.strictEqual(find(quiet, "wolf"), null, `a wolf trotted: ${JSON.stringify(opts)}`);
    assert.ok(find(quiet, "indeterminate"), `the bar went with the wolf: ${JSON.stringify(opts)}`);
    assert.ok(find(quiet, "remote-where"), "the words saying where it runs went with the wolf");
  }
  // The bar hides only while the wolf can show; only jobs the app started in Fabric get it.
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  assert.match(css, /\.wolf\.wolf-idle \{[^}]*animation: fx-trot/);
  assert.match(css, /html\[data-motion="on"\]:not\(\[data-fx-wolf="off"\]\) \.progress\.remote:has\(\.wolf-idle\) > \.bar \{ display: none; \}/);
  assert.match(fs.readFileSync(path.join(ui, "activity.js"), "utf8"), /const remoteActivity = \(i\) => String\(i\.id \|\| ""\)\.startsWith\("fabric:"\) && i\.progress === undefined;/);
});

test("Forge events: every event in the registry is fired by the app, and the Preview window shows the navy wolf (MO-011)", () => {
  const ctx = motionHarness({ settings: { motion_level: { value: 2 } } });
  const events = Object.keys(vm.runInContext("FORGE_EVENTS", ctx));
  const app = fs.readdirSync(ui).filter((f) => f.endsWith(".js") && f !== "settings.js")
    .map((f) => fs.readFileSync(path.join(ui, f), "utf8").replace(/const FORGE_EVENTS = \{[\s\S]*?\n\};/, "")).join("\n");
  for (const name of events) assert.ok(app.includes(`"${name}"`), `${name} is in FORGE_EVENTS but nothing fires it`);
  assert.match(fs.readFileSync(path.join(ui, "motion.js"), "utf8"), /forgeEvent\("notebook\.run\.finished", wolf\)/);
  assert.match(fs.readFileSync(path.join(ui, "settings.js"), "utf8"), /row\("Navy wolf", [^\n]*\n\s+remoteHost, \(\) => \{ if \(!remoteHost\.childElementCount\) \{ remoteHost\.append\(remoteProgress\(/);
});

test("SQL Forge events (KF-MO-005): a slow success sparks, a failure plays the ember, after the redraw", () => {
  const ctx = motionHarness({ settings: { motion_level: { value: 2 } } });
  const events = vm.runInContext("FORGE_EVENTS", ctx);
  assert.strictEqual(events["sql.succeeded"], "spark");
  assert.strictEqual(events["sql.failed"], "fail");
  const q = fs.readFileSync(path.join(ui, "query.js"), "utf8");
  assert.match(q, /const SQL_SPARK_SECONDS = 2;/);
  const run = /async function run\(query[\s\S]*?\n  }\n/.exec(q)[0];
  assert.ok(run.lastIndexOf("runFx(status, allow)") > run.lastIndexOf("sqlPage.redraw?.()"), "the effect plays before the redraw replaces the pill");
});

test("Profile shows proofs, and Journey and Achievements only while progression is on (KF-DX-002)", () => {
  const p = fs.readFileSync(path.join(ui, "profile.js"), "utf8");
  assert.match(p, /p\.settings\.progression \? tab\("journey", "Journey"\) : null/);
  assert.match(p, /p\.settings\.progression \? tab\("achievements", "Achievements"\) : null/);
  assert.match(p, /if \(!p\.settings\.progression && \["journey", "achievements"\]\.includes\(profilePage\.tab\)\) profilePage\.tab = "overview";/);
  assert.match(p, /const proved = r\.type === "prove\.match";/);
  const v = fs.readFileSync(path.join(ui, "prove.js"), "utf8");
  assert.match(v, /toast\(`\$\{nb\.name\}: \$\{text\}`, r\.state === "failed"\);\n  if \(r\.state === "done"\) profileEarned\(r\.earned\);/);
  const css = fs.readFileSync(path.join(ui, "styles.css"), "utf8");
  const block = css.slice(css.indexOf("/* Profile (profile.js)"), css.indexOf("@media (max-width: 900px) { .profile-grid"));
  assert.doesNotMatch(block.replace(/#fff\b/g, ""), /#[0-9a-f]{3,6}\b/i, "profile styles use tokens");
});

// Spark on this PC: NOT NULL inside a struct and names of rewritten SQL functions.
test("the Spark on this PC page says NOT NULL inside a struct isn't checked and SQL keeps function names", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /`NOT NULL` on a field inside a struct\nis accepted in SQL but not checked/);
  assert.match(doc, /without an alias gets a short name close to Spark's/);
});

// Spark on this PC: rewritten SQL function names are close to Spark's, not identical.
test("the Spark on this PC page says how SQL function names differ from Spark's", () => {
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebook-spark-on-this-pc.md"), "utf8");
  assert.match(doc, /Spark's names differ for literals \(it drops their quotes\) and add defaults such as\n`months_between`'s `roundOff`/);
});

// Check for updates (account menu, Settings): only a newer build on a self-updating PC offers
// Install and restart; everything else says why and offers the channel's release page.
test("Check for updates says what the channel has and offers an install only for a newer build", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${src.match(/function updateCheckResult\([\s\S]*?\n}\n/)[0]}; this.f = updateCheckResult;`, ctx);
  const a = "5d69f21aa1b2c3d4e5f60718293a4b5c6d7e8f90", b = "a41c0e9aa1b2c3d4e5f60718293a4b5c6d7e8f90";
  const kind = (report) => ctx.f(report).kind;
  assert.strictEqual(kind({ commit: a, auto_update: true, latest: { commit: b, channel: "preview" } }), "newer");
  assert.match(ctx.f({ commit: a, auto_update: true, latest: { commit: a, channel: "preview" } }).text, /up to date.*Preview/);
  assert.strictEqual(kind({ commit: a, auto_update: false, latest: { commit: b, channel: "stable" } }), "managed");
  assert.match(ctx.f({ commit: a, latest: { commit: null, error: "URLError: offline", channel: "stable" } }).text, /Stable channel: URLError/);
  assert.strictEqual(kind({ commit: a, auto_update: true, latest: { commit: b, refused: "older than the last one", channel: "preview" } }), "error");
  assert.strictEqual(kind({ commit: null, latest: { commit: b } }), "local");
  const dialog = src.slice(src.indexOf("async function checkForUpdates"), src.indexOf("// ---------- What's new"));
  assert.match(dialog, /invoke\("check_for_update"\)/);
  assert.match(dialog, /installUpdate\(latest\.commit, /);
  assert.match(dialog, /openLink\(latest\.page\)/);
  assert.match(src.slice(src.indexOf("function accountMenu"), src.indexOf("function channelLabel")), /"Check for updates".*checkForUpdates/);
});

// Side by side: This PC's time split into processing and waiting on OneLake uploads.
test("side by side times roll over and split This PC's time into processing and uploads", () => {
  const src = fs.readFileSync(path.join(ui, "sidebyside.js"), "utf8");
  const ctx = { h: (tag, attrs, ...kids) => ({ tag, attrs, text: kids.flat().filter((k) => typeof k === "string").join("") }) };
  const fn = (name) => src.match(new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  vm.runInNewContext(`${fn("sbsTime")}${fn("sbsSize")}${fn("sbsLocalSplit")}; Object.assign(this, { sbsTime, sbsLocalSplit });`, ctx);
  assert.strictEqual(ctx.sbsTime(779.6), "13 min 0 s");  // was "12 min 60 s"
  assert.strictEqual(ctx.sbsTime(59.97), "1 min 0 s");
  assert.strictEqual(ctx.sbsTime(12.34), "12.3 s");
  assert.strictEqual(ctx.sbsTime(7199), "2 h 0 min");
  const lines = ctx.sbsLocalSplit({ seconds: 780, processing_seconds: 130, upload_wait_seconds: 650,
    upload: { seconds: 700, tables: 8, rows: 86000000, bytes: 3.3e9, skipped: 1, mb_per_second: 4.71 } });
  assert.strictEqual(lines[0].text, "Processing on this PC 2 min 10 sWaiting for OneLake uploads 10 min 50 s");
  assert.strictEqual(lines[1].text, "Uploaded 3.3 GB to OneLake in 11 min 40 s · about 4.7 MB/s · 1 unchanged, skipped");
  assert.strictEqual(ctx.sbsLocalSplit({ seconds: 5 })[0].text, "No wait to start");  // a run from before the split
});

// Run in Fabric and side by side wait for the bar's last save, so they get the values it shows.
test("Run in Fabric, side by side and Prove wait for the Parameters bar's values", () => {
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"),
    /await nbInputsSaved\(\);[^\n]*\n[^\n]*\n\s*const run = await attempt\(\(\) => rpc\("run_notebook_in_fabric"/);
  assert.match(fs.readFileSync(path.join(ui, "sidebyside.js"), "utf8"),
    /await nbInputsSaved\(\);[^\n]*\n\s*const run = await attempt\(\(\) => rpc\("side_by_side_start"/);
  assert.match(fs.readFileSync(path.join(ui, "prove.js"), "utf8"),
    /await nbInputsSaved\(\);[^\n]*\n\s*const run = await attempt\(\(\) => rpc\("prove_start"/);
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "notebooks.md"), "utf8");
  assert.match(doc, /\*\*Run in Fabric\*\*, \*\*Side by side\*\* and \*\*Prove this notebook\*\* use the bar's values too/);
});

// Add to VS Code: the page sends only the tool's id; the shell builds the vscode: link itself
// (vscode_link.rs), so no page can make it open another link.
test("Add to VS Code passes only the tool's id to the shell", () => {
  const src = fs.readFileSync(path.join(ui, "integrations.js"), "utf8");
  const calls = src.match(/invoke\("add_to_vscode", \{[^}]*\}/g) || [];
  assert.deepStrictEqual(calls, ['invoke("add_to_vscode", { clientId: client.id, insiders }']);
  assert.ok(!/vscode:mcp|vscode-insiders:/.test(src), "the link is built in the shell, not the page");
  const main = fs.readFileSync(path.join(__dirname, "..", "src-tauri", "src", "main.rs"), "utf8");
  assert.match(main, /fn add_to_vscode\(client_id: String, insiders: bool\)/);
  assert.match(main, /generate_handler!\[[\s\S]*add_to_vscode/);
});

test("the Capacity tab says how each capacity drifted from capacity.toml", () => {
  const { capacityLines, CAPACITY_STATES } = gitHelpers();
  assert.strictEqual(CAPACITY_STATES.not_visible, "not visible to you");
  const lines = capacityLines({ state: "differs", fields: [{ field: "sku", git: "F4", fabric: "F8" }, { field: "state", git: "Active", fabric: "Paused" }],
    workspaces: { same: 2, elsewhere: [{ id: "w1", name: "Sales", on: "F8 Dev" }, { id: "w2", name: "", on: null }],
      not_in_git: [{ id: "w3", name: "Ad hoc" }], not_visible: [{ id: "w4", name: "Hidden" }] } });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(lines)), ["Size: Git F4, Fabric F8", "State: Git Active, Fabric Paused",
    "Sales: Git expects it here; Fabric has it on F8 Dev", "w2: Git expects it here; Fabric has it on a capacity you can't see",
    "Ad hoc: on this capacity in Fabric, not listed in capacity.toml", "Hidden: you can't see this workspace, so it isn't compared",
    "2 workspaces are where Git expects"]);
});

// Settings > Advanced > OneLake writes > Files on this PC: what local writes hold, and the folder.
test("Files on this PC says what local writes hold and where", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const ctx = {};
  vm.runInNewContext(`${fs.readFileSync(path.join(ui, "app.js"), "utf8").match(/function sizeText\([\s\S]*?\n}\n/)[0]}${src.match(/function localWritesText\([\s\S]*?\n}\n/)[0]}; this.f = localWritesText;`, ctx);
  assert.strictEqual(ctx.f({ files: 0, folder: "C:\\x\\staging", uploading: { files: 0 } }), "Empty · C:\\x\\staging");
  assert.strictEqual(ctx.f({ files: 2, bytes: 3 * 1024 ** 3, folder: "C:\\x\\staging", uploading: { files: 2, bytes: 3 * 1024 ** 3 } }),
    "3.0 GB in 2 files, 2 waiting to upload or left over (3.0 GB) · C:\\x\\staging");
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  assert.match(app.slice(app.indexOf("function oneLakeWritesMenu"), app.indexOf("async function setNotebookOneLakeWrites")), /Open the folder of local writes/);
  const clear = src.slice(src.indexOf("async function clearLocalWrites"));
  assert.match(clear.slice(0, clear.indexOf("\n}\n")), /confirmDialog\("Delete left-overs\?"[\s\S]*danger: true[\s\S]*rpc\("local_writes_clear"\)/);
});

test("the Ossie card exports, previews and imports only after a confirm, and hides with preview features", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const card = meta.split("function ossieCard(plan)")[1].split("// ---------- profiling ----------")[0];
  assert.match(card, /preview\("ossie", /, "the card is a Preview feature and hides with them");
  assert.match(meta, /\n    ossieCard\(plan\),\n/, "the Semantic model tab shows the card");
  for (const method of ["meta_model_ossie_models", "meta_model_ossie_export", "meta_model_ossie_save", "meta_model_ossie_read",
    "meta_model_ossie_preview", "meta_model_ossie_import", "meta_model_tmdl_save"]) {
    assert.match(card, new RegExp(`"${method}"`), `${method} has a caller`);
  }
  // The first import call only asks; the write carries confirmed after confirmDialog said yes, danger for Replace.
  const go = card.split("go.onclick = async () => {")[1].split("showDialog(")[0];
  assert.ok(go.indexOf("confirmDialog(") < go.indexOf("confirmed: true"), "confirm comes before the write");
  assert.match(go, /\{ danger: c\.mode === "replace" \}/);
  assert.match(go, /if \(!ok\) return;/);
  assert.match(card, /OSSIE_MAX_BYTES/, "a file too large to be a model is refused before it's read");
});

test("the Direct Lake check is a Preview card that only reads, with fixes that open where the change is made", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const card = meta.split("function directLakeCard(plan, updateModel)")[1].split("// ---------- Apache Ossie")[0];
  assert.match(card, /preview\("directLakeCheck", /, "the card is a Preview feature and hides with them");
  assert.match(meta, /\n    directLakeCard\(plan, \(\) => build\(\)\),\n/, "the Semantic model tab shows the card");
  for (const method of ["meta_model_directlake_setup", "meta_model_directlake_check"]) {
    assert.match(card, new RegExp(`"${method}"`), `${method} has a caller`);
  }
  const calls = [...card.matchAll(/metaCall\("([a-z_]+)"/g)].map((x) => x[1]).sort();
  assert.deepStrictEqual(calls, ["meta_model_directlake_check", "meta_model_directlake_setup", "meta_model_ossie_models"], "it calls nothing that writes");
  assert.match(card, /reading from OneLake uses capacity/, "capacity wording (QA-008)");
  assert.match(card, /goMeta\("health"\)/, "compacting happens in Table health");
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  assert.match(features, /directLakeCheck: "Direct Lake check"/);
});

test("the data dictionary is a Preview card whose import saves only from its preview", () => {
  const meta = fs.readFileSync(path.join(ui, "meta.js"), "utf8");
  const card = meta.split("function dictionaryCard(plan)")[1].split("// ---------- Apache Ossie")[0];
  assert.match(card, /preview\("dataDictionary", /, "the card is a Preview feature and hides with them");
  assert.match(meta, /\n    dictionaryCard\(plan\),\n/, "the Semantic model tab shows the card");
  for (const method of ["meta_model_dictionary", "meta_model_dictionary_export", "meta_model_dictionary_save",
    "meta_model_dictionary_preview", "meta_model_dictionary_import"]) {
    assert.match(card, new RegExp(`"${method}"`), `${method} has a caller`);
  }
  // The save happens only from the button the preview turns on, and only with something to save.
  const dialog = card.split("function dictionaryImportDialog(plan, reload)")[1];
  assert.ok(dialog.indexOf('"meta_model_dictionary_preview"') < dialog.indexOf('"meta_model_dictionary_import"'));
  assert.match(dialog, /go\.disabled = !res\.changes\.length \|\| !!res\.problem;/);
  assert.match(dialog, /DICTIONARY_MAX_BYTES/, "a file too large to be a dictionary is refused before it's read");
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /dataDictionary: "Data dictionary"/);
});

// kratosforge:// links (deep_link.rs): a link only shows a notebook or a page. The page's handler
// opens or goes, and never runs, saves elsewhere, or publishes.
test("A kratosforge:// link only shows something", () => {
  const src = fs.readFileSync(path.join(ui, "vscode.js"), "utf8");
  const body = src.slice(src.indexOf("async function showLink"), src.indexOf("async function listenForLinks"));
  const calls = [...body.matchAll(/rpc\("([a-z_]+)"/g)].map((m) => m[1]).sort();
  assert.deepStrictEqual(calls, ["list_notebooks", "notebook_name_for_path"]);
  assert.ok(!/run|publish|execute/i.test(body.replace(/\/\/.*$/gm, "")), "the handler names nothing that runs");
  const main = fs.readFileSync(path.join(__dirname, "..", "src-tauri", "src", "main.rs"), "utf8");
  assert.match(main, /generate_handler!\[[\s\S]*take_pending_link/);
  assert.match(main, /fn register_link_scheme\(\) \{\n    if !cfg!\(windows\) \|\| cfg!\(debug_assertions\)/);
});

// New build banner (settings.js): the startup check every 4 hours while the app is open. Only a
// newer build shows it; a managed PC hears it's out with no install; Later hides that build only.
test("the new build banner shows only a newer build, through the existing update check", () => {
  const src = fs.readFileSync(path.join(ui, "settings.js"), "utf8");
  const fn = (name) => src.match(new RegExp(`function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  const ctx = {};
  vm.runInNewContext(`${fn("updateCheckResult")}${fn("updateNotice")}; this.f = updateNotice;`, ctx);
  const a = "5d69f21aa1b2c3d4e5f60718293a4b5c6d7e8f90", b = "a41c0e9aa1b2c3d4e5f60718293a4b5c6d7e8f90";
  const newer = ctx.f({ commit: a, auto_update: true, latest: { commit: b, channel: "preview", page: "https://x/latest" } });
  assert.strictEqual(newer.kind, "newer");
  assert.strictEqual(newer.commit, b);
  assert.match(newer.text, /a41c0e9 is out on Preview \(you're on 5d69f21\)/);
  assert.strictEqual(ctx.f({ commit: a, auto_update: false, latest: { commit: b, channel: "stable" } }).kind, "managed");
  assert.strictEqual(ctx.f({ commit: a, auto_update: true, latest: { commit: a, channel: "stable" } }), null);
  assert.strictEqual(ctx.f({ commit: a, latest: { commit: null, error: "offline", channel: "stable" } }), null);
  assert.strictEqual(ctx.f({ commit: a, auto_update: true, latest: { commit: b, refused: "older", channel: "stable" } }), null);
  assert.strictEqual(ctx.f({ commit: null, latest: { commit: b } }), null);
  const watch = fn("watchForUpdates");
  assert.deepStrictEqual([...watch.matchAll(/invoke\("([a-z_]+)"/g)].map((m) => m[1]), ["check_for_update"], "no new phone-home");
  assert.match(watch, /!updateWatchOn\(\) \|\| !previewShown\(\) \|\| !state\.commit/);
  assert.match(src, /const UPDATE_WATCH_MS = 4 \* 60 \* 60 \* 1000;/);
  const banner = fn("renderUpdateBanner");
  assert.match(banner, /n\.commit === updateLater\(\)/, "Later hides that build only");
  assert.match(banner, /installUpdate\(n\.commit/);
  assert.match(fn("installUpdate"), /saveNotebook\(\)[\s\S]*invoke\("update_app", \{ commit \}\)/, "saves before it hands over");
  assert.match(fn("checkForUpdates"), /installUpdate\(latest\.commit/);
  assert.match(fs.readFileSync(path.join(ui, "app.js"), "utf8"), /scheduleUpdateWatch\(\);/);
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<\/header>\n  <div id="update-root"><\/div>/);
});


// Scale plan 4.1 (D2): a team asks for a source with a Copy job, a platform admin approves and
// approving makes it. The request reads connections only; the approve confirm names every write
// and that runs use capacity; Run now asks first.
test("a source request can ask for a Copy job, and approving says what it makes", () => {
  const src = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  const fn = (name) => src.match(new RegExp(`(async )?function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  assert.match(fn("requestDialog"), /if \(kind === "source"\) return sourceRequestDialog\(target, done\);/);
  const ask = fn("sourceRequestDialog");
  assert.deepStrictEqual([...new Set([...ask.matchAll(/(?:rpc|reqQuietly|platformCall)\("([a-z_]+)"/g)].map((m) => m[1]))].sort(),
    ["fabric_connections", "platform_request"], "asking reads connections and sends the request, nothing else");
  assert.match(ask, /detail\.copy = \{ connection_id: conn\.value/);
  const confirm = fn("confirmCopyJob");
  assert.match(confirm, /CJ_Landing_\$\{r\.target\}/);
  assert.match(confirm, /Files\/\$\{r\.target\}\/\$\{t\.folder\}\//);
  assert.match(confirm, /starts no run\. Each run uses capacity/);
  assert.match(fn("confirmApproval"), /if \(r\.kind === "source" && d\.copy\) return confirmCopyJob\(r\);/);
  const run = fn("copyJobLine");
  assert.match(run, /confirmDialog\(`Run \$\{j\.name\} now\?`[\s\S]*uses capacity[\s\S]*platformCall\("run_fabric_item_now"/);
  assert.match(run, /adminOnly\(run\)/);
  assert.match(run, /riskGate\(\{ id: `fabric:\$\{j\.workspace_id\}:\$\{j\.id\}`, action: "run_item"/, "Run now spends capacity: the risk check comes first");
  assert.match(confirm, /central \? landsIn\(r\.lands_in, central\)/, "the approver sees the zone and lakehouse it lands in");
  assert.match(fn("landsIn"), /secure \? "secure " : ""\}zone \$\{where\.zone_name\}/);
});


// Engine picker (Preview): Engine replaces Runs on beside Run all, remote engines ask first and
// never start on their own, a switch that loses variables is a red confirm, and a cell the engine
// can't run is flagged with "Run this cell on Python" for that cell only.
test("the engine picker is a Preview feature that asks before remote compute", () => {
  const src = fs.readFileSync(path.join(ui, "engines.js"), "utf8");
  const nbsrc = fs.readFileSync(path.join(ui, "notebook.js"), "utf8");
  const fn = (name) => src.match(new RegExp(`(async )?function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  assert.match(fs.readFileSync(path.join(ui, "index.html"), "utf8"), /<script src="nbnext\.js"><\/script>\n  <script src="engines\.js"><\/script>/);
  assert.match(fs.readFileSync(path.join(ui, "features.js"), "utf8"), /engines: "Notebook engine picker",/);
  assert.match(src, /registerFeature\(\{ id: "engines", preview: true/);
  assert.match(nbsrc, /const engineCtl = \(\) => previewShown\(\) \? engineToolbar\(nb\) : \[computeBtn\];/, "Preview hidden keeps Runs on");
  assert.strictEqual((nbsrc.match(/\.\.\.engineCtl\(\)/g) || []).length, 2, "Home and Run toolbars both carry it");
  assert.match(fn("openEngineMenu"), /if \(!previewShown\(\)\) return showMenu\(anchor, computeMenu\(nb\)\);/);
  assert.match(fn("openEngineMenu"), /planned\.map\(\(e\) => \(\{ info:/, "planned engines are info rows, never choices");
  const choose = fn("chooseEngine");
  assert.match(choose, /rpc\("engine_prepare"[\s\S]*engineSwitchDialog[\s\S]*engineRemoteDialog[\s\S]*rpc\("engine_select"/, "prepare, switch, confirm, then select");
  assert.match(choose, /if \(e\.location === "remote" && plan\.confirm\) \{\n\s*confirmed = await engineRemoteDialog\(nb, e, plan\);\n\s*if \(!confirmed\) return;/);
  const confirm = fn("engineRemoteDialog");
  assert.match(confirm, /\["Risk check"/);
  assert.match(confirm, /Each start bills DBUs until its auto-stop/);
  assert.match(confirm, /It uses Fabric capacity until it closes/);
  assert.match(confirm, /never falls back to remote compute on its own/);
  assert.match(fn("engineSwitchDialog"), /btn\("Switch and close session", null, \(\) => done\("close"\), "tool danger-solid"\)/);
  assert.match(fn("engineSwitchDialog"), /"Keep both"/);
  assert.match(fn("chooseKernel"), /confirmDialog\([\s\S]*"Switch and close session", \{ danger: true \}\)/);
  assert.match(fn("engineFlagPanel"), /btn\("Run this cell on Python", "play", \(\) => runCellOnKernel\(cell\)/);
  assert.match(nbsrc, /if \(!kernel && nbOnEngine\(nb\) && !cell\.meta\?\.frozen\) return runEngineCell\(cell, nb\);/);
  for (const name of ["engines.js", "notebook.js"]) {
    assert.doesNotMatch(fs.readFileSync(path.join(ui, name), "utf8"), /no Fabric capacity/i, `${name}: local engines still use capacity for OneLake`);
  }
});
// PB-078: every feature the docs call Preview is one Settings > Preview features knows (an id in
// PREVIEW_FEATURES), or the docs say why it has none: it has no screen yet, or it's a setting in a
// file rather than a screen, which show_preview can't hide.
test("every feature the docs call Preview has a Preview id, or the docs say why not", () => {
  const root = path.join(__dirname, "..", "..");
  const features = fs.readFileSync(path.join(ui, "features.js"), "utf8");
  const ids = new Set([...features.match(/const PREVIEW_FEATURES = \{([\s\S]*?)\n\};/)[1].matchAll(/^ {2}(\w+): "/gm)].map(([, id]) => id));
  // A "(Preview)" heading's text (without the tag) and the id that hides its screens.
  const BY_ID = {
    runSeveral: ["Run several notebooks"], itemPins: ["Pinned items"],
    validate: ["Validate a notebook"], sqlPractices: ["SQL best practices"], nbFormats: ["Fabric and Databricks notebook formats"],
    sessions: ["Notebook sessions"], environments: ["Notebook environments"], deps: ["Cell dependencies and stale outputs"],
    debug: ["Variable history, watches and hidden state", "Breakpoints and stepping"], replay: ["Failure replay bundles"],
    fabricRun: ["Run in Fabric and schedules", "W6 Publish or run a notebook in Fabric"], notebookRuns: ["Recent runs", "Notebook runs"],
    sideBySide: ["Side by side"], prove: ["Prove this notebook"], fabricEdit: ["Edit a Fabric notebook on this PC", "When Publish finds changes made in Fabric"],
    notebookFolderPublish: ["Publishing a notebook from a folder"], practices: ["Notebook best practices"], fabricStep: ["Run the last cell on Fabric"],
    incidents: ["Incidents and your service desk", "Who's affected", "Incidents"],
    agents: ["Agents", "Taking over from a lost agent", "Which pipeline asked"], workloads: ["Workloads"], queue: ["Queue"], runAs: ["Run as"],
    warehouse: ["Warehouses", "Monitoring", "Queries on a warehouse", "Warehouse queries", "Warehouse monitoring"],
    pipelines: ["Pipeline definitions", "Pipeline activity compatibility", "Running pipelines on your PC", "Pipelines",
      "Data Factory copy jobs", "Data Factory dataflows", "Data Factory connections"],
    pipelineRuns: ["Pipeline runs in Monitor"], cancelFabricRun: ["Cancel a Fabric run"], sourceControl: ["Source control"], profile: ["Profile"],
    platform: ["Owners", "Notices to Teams", "One landing workspace for Dev, Test and Prod", "One list of source systems",
      "Platform notices", "Semantic model rules"],
    mesh: ["Home: the mesh for who you are", "Products", "Requests", "Domains", "Members and joining a domain", "Follow a product",
      "Tables, semantic models and reports", "Data products", "Entra ID groups per domain", "Copy jobs from requests"],
    trustBadges: ["Endorsement and business terms"], tests: ["Tests"], health: ["Table health"], model: ["10. Semantic model"],
    lineage: ["Column lineage"], xray: ["Estate X-ray"], itemPreview: ["Item preview"], shortcuts: ["Shortcuts"], access: ["Access"],
    bestPractices: ["Best practices: checks and templates"], optimize: ["Optimize"], popOut: ["Pop-out windows"],
    updateBanner: ["A new build while the app is open"], vscode: ["VS Code and KratosForge"],
    learn: ["Learn", "Writing a Learn lesson"],
    engines: ["Engines and connection profiles", "Databricks SQL warehouse", "Databricks Connect", "Fabric Spark", "Engine picker"],
  };
  // A "(Preview)" heading with no id, and the words in its own page that say why.
  const NO_ID = {
    "Reliability objectives": ["docs/metadata-guide.md", /settings in your project files rather than screens/],
    "Late rows and source deletes": ["docs/metadata-guide.md", /settings in your entity files, not a screen/],
    "Schema changes": ["docs/metadata-guide.md", /how runs treat a table's schema, not a screen of its own/],
    "Shared schedules": ["docs/agent.md", /a setting in the agents' schedules, not a screen/],
    "On a schedule": ["docs/shortcuts.md", /a setting in the agent's `agent\.toml`, not a screen/],
    "Local data projects": ["docs/local-data.md", /the screens for it come next/],
    "Databricks workspace discovery": ["docs/engines.md", /Discovery is a command, with no screen in the app yet/],
    "Snowflake SQL on this PC": ["docs/engines.md", /No screen can pick Snowflake SQL yet/],
    "Where to run a notebook": ["docs/finops/placement-advice.md", /has no screen in the app yet/],
    "Semantic model best practices": ["docs/semantic-model-best-practices.md", /The screens [\s\S]*? come next/],
    "Compare with a Fabric Environment item": ["docs/notebooks.md", /a command, with no screen in the app yet/],
  };
  const idOf = new Map(Object.entries(BY_ID).flatMap(([id, names]) => names.map((n) => [n, id])));
  for (const id of Object.keys(BY_ID)) assert.ok(ids.has(id), `${id} is in PREVIEW_FEATURES`);
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? (["product", "quality"].includes(e.name) ? [] : walk(path.join(dir, e.name))) : e.name.endsWith(".md") ? [path.join(dir, e.name)] : []);
  const missing = [];
  for (const file of walk(path.join(root, "docs"))) {
    for (const [, name] of fs.readFileSync(file, "utf8").matchAll(/^#+ (.+?) \(Preview\)\s*$/gm)) {
      if (!idOf.has(name) && !NO_ID[name]) missing.push(`${path.relative(root, file)}: ${name}`);
    }
  }
  assert.deepStrictEqual(missing, [], "each (Preview) heading needs an id in BY_ID, or a NO_ID reason its page gives");
  for (const [name, [file, why]] of Object.entries(NO_ID)) {
    assert.match(fs.readFileSync(path.join(root, file), "utf8"), why, `${file} says why ${name} has no Preview id`);
  }
  // The ones PB-078 named: batch_by and objectives are project settings, the rest have ids.
  assert.match(fs.readFileSync(path.join(root, "docs", "metadata-guide.md"), "utf8"),
    /settings in your project files rather than screens, such as reading \*\*in batches\*\* \(`batch_by`\), \[reliability objectives\]/);
  for (const id of ["releases", "lineage", "promote"]) assert.ok(ids.has(id), `${id} is in PREVIEW_FEATURES`);
  assert.match(fs.readFileSync(path.join(root, "docs", "metadata-guide.md"), "utf8"), /### Who can promote\n\n\*Preview\*, part of \*\*Promote\*\*/);
});

// #971: every box that asks for a folder on this PC has Browse… beside it (folderField in app.js),
// so nobody has to type a path. A box is a folder box when its placeholder is a Windows path, its
// name ends in "folder", or its field's label does (a folder name or a OneLake path is not one).
test("every folder box on this PC has a Browse… button", () => {
  const folderish = (text) => /(^|\s)folder$/i.test(text.trim());
  const missing = [];
  for (const name of fs.readdirSync(ui).filter((f) => f.endsWith(".js"))) {
    const src = fs.readFileSync(path.join(ui, name), "utf8");
    const inputs = new Map();
    for (const m of src.matchAll(/const (\w+) = h\("input", \{([^}]*)\}/g)) inputs.set(m[1], m[2]);
    const boxes = new Set();
    for (const [v, attrs] of inputs) {
      const label = attrs.match(/"aria-label": "([^"]*)"/)?.[1] || "";
      if (/type: "file"/.test(attrs)) continue; // already the browser's own folder picker
      if (/placeholder: "C:\\\\/.test(attrs) || folderish(label)) boxes.add(v);
    }
    for (const m of src.matchAll(/h\("label", \{ class: "field" \}, "([^"]*)", (\w+)\)/g)) {
      if (folderish(m[1]) && inputs.has(m[2])) boxes.add(m[2]);
    }
    for (const v of boxes) if (!src.includes(`folderField(${v},`)) missing.push(`${name}: ${v}`);
    // Inside a <label>, a box with no aria-label of its own would be named "Folder Browse for …".
    for (const [, v] of src.matchAll(/(?<!function )folderField\((\w+),/g)) {
      if (inputs.has(v) && !/"aria-label": /.test(inputs.get(v))) missing.push(`${name}: ${v} has no aria-label`);
    }
    // Field lists, such as gitPathDialog's: a folder field says which folder, so it gets Browse….
    for (const m of src.matchAll(/\{ label: "([^"]*)",([^}]*)\}/g)) {
      if ((folderish(m[1]) || /placeholder: "C:\\\\/.test(m[2])) && /placeholder:/.test(m[2]) && !/\bfolder: "/.test(m[2])) {
        missing.push(`${name}: field "${m[1]}"`);
      }
    }
  }
  assert.deepStrictEqual(missing, [], "folder boxes without Browse… (wrap the input in folderField)");
  const app = fs.readFileSync(path.join(ui, "app.js"), "utf8");
  const helper = app.match(/function folderField\(input, name = "a folder"\) \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(helper, /if \(input\.readOnly \|\| !window\.__TAURI__\?\.core\) return input;/, "a locked box gets no button");
  assert.match(helper, /invoke\("pick_folder", \{ start: input\.value\.trim\(\) \|\| null \}\)/);
  assert.match(helper, /if \(!chosen\) return;/, "Cancel leaves the box as it was");
  assert.match(helper, /browse\.disabled = true;[\s\S]*\.finally\(\(\) => \{ browse\.disabled = false; \}\)/, "one dialog at a time");
  const shell = fs.readFileSync(path.join(__dirname, "..", "src-tauri", "src", "main.rs"), "utf8");
  assert.match(shell, /generate_handler!\[[^\]]*\bpick_folder\b/, "the shell registers pick_folder");
});

// #1108: a waiting workspace access request says the role its person or group has today, as drawn.
test("workspace access requests show today's role, and the confirm says what approving changes", async () => {
  const src = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  const fn = (name) => src.match(new RegExp(`(async )?function ${name}\\([\\s\\S]*?\\n}\\n`))[0];
  const seen = [];
  const box = { WORKSPACE_ROLES: ["Viewer", "Contributor", "Member", "Admin"], h: (tag, attrs, text) => text,
    confirmDialog: async (title, text, ok) => { seen.push({ title, text, ok }); return true; }, result: null };
  vm.runInNewContext(`${fn("accessToday")}${fn("accessTodayLine")}${fn("confirmApproval")}
    result = { accessTodayLine, confirmApproval };`, box);
  const { accessTodayLine, confirmApproval } = box.result;
  const req = (role, today) => ({ kind: "workspace_access", target: "Sales Prod", today,
    detail: { role, principal: { type: "Group", name: "Sales Engineers", id: "g" } } });
  assert.strictEqual(accessTodayLine(req("Viewer", { role: "Admin" })), "Sales Engineers has Admin in Sales Prod today, so approving keeps Admin.");
  assert.strictEqual(accessTodayLine(req("Viewer", { role: "Viewer" })), "Sales Engineers has Viewer in Sales Prod today, so approving keeps Viewer.");
  assert.strictEqual(accessTodayLine(req("Contributor", { role: "Viewer" })), "Sales Engineers has Viewer in Sales Prod today; approving raises it to Contributor.");
  assert.strictEqual(accessTodayLine(req("Viewer", { role: "" })), "Sales Engineers has no role of its own in Sales Prod today. A role through another group isn't counted.");
  assert.strictEqual(accessTodayLine(req("Viewer", { checked: false })), "Couldn't check today's role: Fabric didn't answer. Approving never lowers a role either way.");
  assert.strictEqual(accessTodayLine(req("Viewer", undefined)), null, "nothing for a row the viewer can't decide");
  for (const today of [{ role: "Admin" }, { role: "Viewer" }, { role: "" }, { checked: false }, undefined]) {
    assert.strictEqual(JSON.stringify(await confirmApproval(req(today?.role === "Viewer" ? "Contributor" : "Viewer", today))), "{}");
  }
  const after = " Then the request is approved. If any step fails, it stays waiting and says which.";
  assert.deepStrictEqual(seen.map((s) => [s.text, s.ok]), [
    ["Sales Engineers already has Admin in Sales Prod, which is higher than Viewer. Approving keeps Admin and marks the request approved. Nothing changes in Fabric." + after, "Approve"],
    ["As you, KratosForge raises the group Sales Engineers from Viewer to Contributor in Sales Prod." + after, "Add and approve"],
    ["As you, KratosForge gives the group Sales Engineers the Viewer role in Sales Prod." + after, "Add and approve"],
    ["As you, KratosForge gives the group Sales Engineers the Viewer role in Sales Prod. If they already have a higher role, they keep it." + after, "Add and approve"],
    ["As you, KratosForge gives the group Sales Engineers the Viewer role in Sales Prod. If they already have a higher role, they keep it." + after, "Add and approve"]]);
  seen.length = 0;
  await confirmApproval(req("Member", { role: "Member" }));
  assert.deepStrictEqual([seen[0].text, seen[0].ok], ["Sales Engineers already has Member in Sales Prod, which is the role asked for. Approving keeps Member and marks the request approved. Nothing changes in Fabric." + after, "Approve"]);
  assert.match(src, /r\.kind === "workspace_access" \? accessTodayLine\(r\) : null/, "the line sits under the reason");
});

// #1195: declining a request whose approval already made something says it stays in Fabric, before the click.
test("declining or withdrawing a partly carried-out request says what is still in Fabric", () => {
  const src = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  const box = { result: null };
  vm.runInNewContext(`${src.match(/function leftInFabric\([\s\S]*?\n}\n/)[0]} result = leftInFabric;`, box);
  const left = (r) => JSON.parse(JSON.stringify(box.result(r)));  // out of the vm's realm
  // The same sentences as left_in_fabric in central/requests_service.py (test_request_left_in_fabric.py).
  assert.deepStrictEqual(left({ kind: "workspace", target: "Sales Forecasting - Dev", detail: { name: "Sales Forecasting - Dev", made: { workspace_id: "w1" } } }),
    ["The workspace Sales Forecasting - Dev, made by this request, is still in Fabric on its capacity: delete it there if it isn't wanted"]);
  assert.deepStrictEqual(left({ kind: "workspace_access", detail: { role: "Contributor", workspace_name: "Sales Prod", principal: { type: "Group", name: "Sales Engineers" }, made: { assigned: true } } }),
    ["The group Sales Engineers has the Contributor role or a higher one in Sales Prod: they had it before this request, or approving it gave it. Check which before removing it in the workspace's Manage access in Fabric"]);
  assert.deepStrictEqual(left({ kind: "source", detail: { made: { copy_job_id: "j1", copy_job_name: "CJ_Landing_crm", schedule_id: "s1" } } }),
    ["The Copy job CJ_Landing_crm, made by this request, is still in the landing workspace and its daily schedule still runs it: delete it there if it isn't wanted"]);
  for (const kind of ["workspace", "workspace_access", "source", "domain", "key_table"]) {
    assert.strictEqual(left({ kind, detail: { made: {} } }).length, 0, `${kind} with nothing made declines as before`);
    assert.strictEqual(left({ kind, detail: {} }).length, 0);
  }
  assert.match(src, /Declining deletes nothing in Fabric\. \$\{left\.join\("\. "\)\}\./, "the decline confirm says it");
  assert.match(src, /if \(res\.next\?\.length\) toast\(`Withdrew \$\{r\.target\}; \$\{res\.next\.join\("; "\)\}`/, "withdrawing says it after");
});

// #1206: a domain request's decline says where the domain it made still is.
test("declining a domain request whose domain was made says where it is", () => {
  const src = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  const box = { result: null };
  vm.runInNewContext(`${src.match(/function leftInFabric\([\s\S]*?\n}\n/)[0]} result = leftInFabric;`, box);
  const left = (r) => JSON.parse(JSON.stringify(box.result(r)));
  // The same sentences as left_in_fabric in central/requests_service.py (test_request_left_in_fabric.py).
  assert.deepStrictEqual(left({ kind: "domain", target: "hr", detail: { name: "HR", made: { domain_id: "hr", fabric_domain_id: "f-hr" } } }),
    ["The domain HR, made by this request, is still in Fabric and the platform: delete it in Fabric's admin portal, then on Data mesh > Domains, if it isn't wanted"]);
  assert.deepStrictEqual(left({ kind: "domain", target: "hr", detail: { name: "HR", made: { domain_id: "hr" } } }),
    ["The domain HR, made by this request, is still in the platform: delete it on Data mesh > Domains if it isn't wanted"]);
  assert.deepStrictEqual(left({ kind: "domain", target: "hr", detail: { name: "HR", made: { domain_id: "hr", fabric_domain_id: "f-hr", found: true } } }),
    ["The domain HR is in the platform and Fabric: it was there before this request, or approving it made it. Check which before deleting it"], "#1270: possibly someone else's");
});

// #1207: a workspace access request records whether approving gave, raised or kept the role, and a decline says exactly that.
test("declining a workspace access request says whether its approval gave, raised or kept the role", () => {
  const src = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  const box = { result: null };
  vm.runInNewContext(`${src.match(/function leftInFabric\([\s\S]*?\n}\n/)[0]} result = leftInFabric;`, box);
  const left = (made) => JSON.parse(JSON.stringify(box.result({ kind: "workspace_access",
    detail: { role: "Contributor", workspace_name: "Sales Prod", principal: { email: "dev@contoso.com" }, made } })));
  // The same sentences as left_in_fabric in central/requests_service.py (test_request_left_in_fabric.py).
  assert.deepStrictEqual(left({ assigned: true, change: "gave", from: "" }),
    ["dev@contoso.com keeps the Contributor role in Sales Prod that approving this request gave: remove it in the workspace's Manage access in Fabric if it isn't wanted"]);
  assert.deepStrictEqual(left({ assigned: true, change: "raised", from: "Viewer" }),
    ["dev@contoso.com keeps the Contributor role in Sales Prod that approving this request raised from Viewer: set it back in the workspace's Manage access in Fabric if it isn't wanted"]);
  assert.deepStrictEqual(left({ assigned: true, change: "kept", from: "Admin" }), [], "a higher role they had: nothing to remove");
  assert.deepStrictEqual(left({ assigned: true, change: "same", from: "Contributor" }),
    ["dev@contoso.com has the Contributor role in Sales Prod: they had it before this request, or approving it gave it. Check which before removing it in the workspace's Manage access in Fabric"]);
  assert.deepStrictEqual(left({ assigned: true }),
    ["dev@contoso.com has the Contributor role or a higher one in Sales Prod: they had it before this request, or approving it gave it. Check which before removing it in the workspace's Manage access in Fabric"], "a request recorded before #1207");
});

// #1260: a shortcut request's decline says the shortcut its approval made is still in OneLake.
test("declining a shortcut request whose shortcut was made says it is still in OneLake", () => {
  const src = fs.readFileSync(path.join(ui, "platform.js"), "utf8");
  const box = { result: null };
  vm.runInNewContext(`${src.match(/function leftInFabric\([\s\S]*?\n}\n/)[0]} result = leftInFabric;`, box);
  const left = (r) => JSON.parse(JSON.stringify(box.result(r)));
  // The same sentence as left_in_fabric in central/requests_service.py (test_shortcut_request_retries.py).
  assert.deepStrictEqual(left({ kind: "shortcut", target: "LH_Silver/Tables/dbo/supplier", detail: { name: "supplier", lakehouse_name: "LH_Silver", made: { shortcut_id: "s1" } } }),
    ["The shortcut supplier in LH_Silver, made by this request, is still in OneLake: remove it on OneLake > Shortcuts if it isn't wanted"]);
  assert.deepStrictEqual(left({ kind: "shortcut", detail: { name: "supplier", lakehouse_name: "LH_Silver", made: { shortcut_id: "s1", found: true } } }),
    ["The shortcut supplier in LH_Silver is in OneLake: it was there before this request, or approving it made it. Check which before removing it on OneLake > Shortcuts"]);
  assert.strictEqual(left({ kind: "shortcut", detail: { name: "supplier", made: {} } }).length, 0);
});

test("browser tests run axe through the harness, which waits for animations to end (#1337)", () => {
  // axe judges contrast on the colors as drawn; a check taken while a dialog fades in fails on
  // faded text (#1297). harness.cjs axeFindings turns motion off and waits; the audit has its own runner.
  const dir = path.join(__dirname, "browser");
  const own = new Set(["harness.cjs", "a11y-audit.test.cjs"]);
  const direct = fs.readdirSync(dir).filter((f) => f.endsWith(".cjs") && !own.has(f))
    .filter((f) => /axe\.run\(|addScriptTag\(\{ content: axeSource/.test(fs.readFileSync(path.join(dir, f), "utf8")));
  assert.deepStrictEqual(direct, []);
  assert.match(fs.readFileSync(path.join(dir, "a11y-audit.test.cjs"), "utf8"), /await animationsDone\(page\)/);
});
