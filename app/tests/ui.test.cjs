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

// The page's scripts share one global scope, so a top-level name declared in two of them makes
// the later file silently replace the earlier one (integrations.js once replaced activity.js's when()).
test("no two page scripts declare the same top-level name", () => {
  const html = fs.readFileSync(path.join(ui, "index.html"), "utf8");
  const scripts = [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]).filter((s) => !s.startsWith("vendor/"));
  assert.ok(scripts.length > 10, "index.html scripts not found");
  const owner = new Map();
  const clashes = [];
  const decl = /^(?:async\s+)?(?:function\*?\s+|(?:const|let|var|class)\s+)([A-Za-z_$][\w$]*)/gm;
  for (const name of scripts) {
    for (const [, id] of fs.readFileSync(path.join(ui, name), "utf8").matchAll(decl)) {
      if (owner.has(id) && owner.get(id) !== name) clashes.push(`${id}: ${owner.get(id)} and ${name}`);
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
  const raw = "The engine stopped answering, so Fabric Desktop by KratosBI restarted it. poll_cell had no answer after 60 seconds.";
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
  assert.match(dialog, /https:\/\/github\.com\/KratosDataGod\/fabric-desktop/);
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
  const ctx = { openScheduleDialog: (o) => opened.push(o.mode), publishWorkspaceName: () => "Sales Prod" };
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
  for (const [id, file] of [["promote", "meta.js"], ["syncKeys", "meta.js"], ["varlib", "estate.js"], ["replay", "dataops.js"]]) {
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
  assert.deepStrictEqual(always, ["promote", "syncKeys", "varlib", "replay", "risk"]);
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

test("Monitor has four tabs, and Operations holds the workload views in the mockup's order (UX-010)", () => {
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
    ["Runs", "Schedules", "Operations", "Cost"]);
});

test("Monitor > Agents explains agents beside the list, with links to sections of agent.md (testing item 34)", () => {
  const src = fs.readFileSync(path.join(ui, "agents.js"), "utf8");
  const doc = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "agent.md"), "utf8");
  const slug = (t) => t.toLowerCase().replace(/[^a-z0-9 -]/g, "").replace(/ /g, "-");
  const anchors = new Set([...doc.matchAll(/^#+ (.+)$/gm)].map((m) => slug(m[1])));
  const linked = [...src.matchAll(/agDoc\("([^"]+)"/g)].map((m) => m[1]);
  assert.ok(linked.length >= 6);
  for (const a of linked) assert.ok(anchors.has(a), `agent.md has a section #${a}`);
  assert.match(src, /return h\("div", \{ class: "sc-two ag-two" \}, box, agGuide\(\)\);/);
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
