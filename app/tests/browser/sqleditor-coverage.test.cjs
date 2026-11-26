// The SQL editor's reading of SQL (sqleditor.js), the paths the sql-* tests leave out: nested block
// comments and DuckDB's $$ strings, date and date-time parameters, CTEs after the first, derived tables,
// hovering a table, an alias, or a column whose table's columns aren't read yet, a CTE offered as a
// table, values as literals for Spark SQL and T-SQL, function help the engine couldn't give (asked
// again next time), hover cards for a function with more forms, a wide table and an unread column's
// table, and a peek that failed. Each is run in the app's page; what it answers is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const TABLES = [{ schema: "dbo", name: "orders" }, { schema: "dbo", name: "customers" }, { schema: "sales", name: "wide" }];
const COLS = { orders: [{ name: "id", type: "int" }, { name: "amount", type: "decimal" }],
  wide: Array.from({ length: 14 }, (_, k) => ({ name: `c${k}`, type: "int" })) };

// Set before the app opens: the engine's function help fails once, then answers.
const db = { helpFails: 1 };
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    sql_function_help: ({ name }) => {
      if (db.helpFails > 0) { db.helpFails--; throw new Error("The engine is busy."); }
      return { forms: [1, 2, 3, 4].map((k) => ({ signature: `${name}(x${k})`, summary: k === 1 ? "Rounds a number." : "" })), more: 1 };
    },
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });
beforeEach(() => { if (!skip) app.errors.length = 0; });

// Runs fn's source in the page with the tables and columns at hand.
const inPage = (fn, arg) => page().evaluate(([src, tables, cols, arg]) => {
  const provider = { columns: (t) => (cols[t.name] ? Promise.resolve(cols[t.name]) : Promise.reject(new Error("unreadable"))) };
  // eslint-disable-next-line no-new-func
  return new Function("tables", "cols", "provider", "arg", `return (${src})(tables, cols, provider, arg);`)(tables, cols, provider, arg);
}, [fn.toString(), TABLES, COLS, arg]);

test("nested block comments and DuckDB's $$ strings hide what's inside them", { skip }, async () => {
  const out = await inPage(() => {
    const text = "SELECT 1 /* a /* b; */ c; */ ; SELECT 2";
    return {
      masked: sqlMask(text).text,
      inert: sqlMask(text, text.indexOf("c;")).inert,
      split: sqlSplitStatements(text).map((s) => s.text.trim().replace(/;$/, "").trim()),
      duck: sqlSplitStatements("SELECT $$a;b$$ AS x; SELECT 3", { dialect: "duckdb" }).map((s) => s.text.trim().replace(/;$/, "")),
    };
  });
  assert.strictEqual(out.masked, "SELECT 1                     ; SELECT 2");
  assert.strictEqual(out.inert, true);
  assert.deepStrictEqual(out.split, ["SELECT 1 /* a /* b; */ c; */", "SELECT 2"]);
  assert.deepStrictEqual(out.duck, ["SELECT $$a;b$$ AS x", "SELECT 3"]);
  assert.deepStrictEqual(app.errors, []);
});

test("date and date-time parameters take real dates only", { skip }, async () => {
  const out = await inPage(() => [sqlParamProblem("date", "2026-02-30"), sqlParamProblem("date", "2026-02-28"),
    sqlParamProblem("datetime", "2026-10-01 14:30"), sqlParamProblem("datetime", "2026-13-01 14:30"), sqlParamProblem("datetime", "2026-10-01")]);
  assert.deepStrictEqual(out, ["Write a date as 2026-10-01.", "", "", "Write a date and time as 2026-10-01 14:30.", "Write a date and time as 2026-10-01 14:30."]);
  assert.deepStrictEqual(app.errors, []);
});

test("every CTE is found, and a derived table is known by its alias", { skip }, async () => {
  const out = await inPage(() => sqlTableRefs(sqlMask("WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a, (SELECT * FROM orders) d JOIN b ON 1 = 1").text));
  assert.deepStrictEqual(out.ctes, ["a", "b"]);
  assert.deepStrictEqual(out.refs, [{ parts: ["a"], name: "a", alias: null }, { parts: [], name: null, alias: "d" },
    { parts: ["orders"], name: "orders", alias: null }, { parts: ["b"], name: "b", alias: null }]);
  assert.deepStrictEqual(app.errors, []);
});

test("hover finds a table before a dot, an alias on its own, a table not in the statement, and a column whose table isn't read yet", { skip }, async () => {
  const out = await inPage((tables) => {
    const at = (text, word, n = 0) => { let i = -1; for (let k = 0; k <= n; k++) i = text.indexOf(word, i + 1); return i; };
    const kind = (t) => t && { kind: t.kind, table: t.table?.name ?? null, unread: t.unread?.map((x) => x.name) };
    const q1 = "SELECT orders.id FROM orders";
    const q2 = "SELECT o FROM orders o";
    const q3 = "SELECT customers FROM orders";
    const q4 = "SELECT amount FROM orders";
    return [kind(sqlHoverTarget(q1, at(q1, "orders"), "spark", tables)), kind(sqlHoverTarget(q2, at(q2, "o "), "spark", tables)),
      kind(sqlHoverTarget(q3, at(q3, "customers"), "spark", tables, () => [])), kind(sqlHoverTarget(q4, at(q4, "amount"), "spark", tables))];
  });
  assert.deepStrictEqual(out, [{ kind: "table", table: "orders", unread: undefined }, { kind: "alias", table: "orders", unread: undefined },
    { kind: "table", table: "customers", unread: undefined }, { kind: "column", table: null, unread: ["orders"] }]);
  assert.deepStrictEqual(app.errors, []);
});

test("a CTE is offered as a table; values become literals for Spark SQL and T-SQL", { skip }, async () => {
  const out = await inPage((tables) => {
    const opts = sqlCompletionOptions({ qualifier: null, refs: [], ctes: ["recent"], previous: "from" }, "spark", tables, () => []);
    return [opts.find((o) => o.label === "recent"), sqlLiteral("it's a \\ path", "string", "spark"), sqlLiteral("it's", "nvarchar", "warehouse")];
  });
  assert.deepStrictEqual(out[0], { label: "recent", type: "class", detail: "CTE", boost: 20 });
  assert.strictEqual(out[1], "'it''s a \\\\ path'");
  assert.strictEqual(out[2], "N'it''s'");
  assert.deepStrictEqual(app.errors, []);
});

test("function help the engine couldn't give is asked again; its card says how many more forms there are", { skip }, async () => {
  const out = await inPage(async (tables, cols, provider) => {
    const first = await sqlFunctionHelp("spark", "round");
    const card = await sqlHoverCard({ kind: "function", name: "round" }, "spark", provider);
    const text = (nodes) => nodes.filter(Boolean).map((n) => n.textContent);
    const wide = await sqlHoverCard({ kind: "table", table: tables[2] }, "spark", provider);
    const unread = await sqlHoverCard({ kind: "column", name: "amount", table: null, unread: [tables[1], tables[0]] }, "spark", provider);
    const later = await sqlHoverCard({ kind: "column", name: "AMOUNT", table: tables[0], column: null }, "spark", provider);
    const found = await sqlTargetTable({ kind: "column", name: "amount", unread: [tables[1], tables[0]] }, provider);
    return { first, card: text(card), wide: text(wide).at(-1), unread: text(unread), later: text(later), found: found.name };
  });
  assert.strictEqual(out.first, null);
  assert.deepStrictEqual(out.card, ["round(x1)function", "round(x2)", "round(x3)", "Rounds a number.", "2 more forms"]);
  assert.strictEqual(out.wide, "2 more columns");
  assert.deepStrictEqual(out.unread, ["amountdecimal", "Column of orders"]);
  assert.deepStrictEqual(out.later, ["amountdecimal", "Column of orders"]);
  assert.strictEqual(out.found, "orders");
  assert.deepStrictEqual(app.errors, []);
});

test("a peek that failed says why and puts focus on Close; Esc closes it", { skip }, async () => {
  await page().evaluate(() => {
    const dom = document.createElement("div"); dom.id = "t-view"; dom.textContent = "x"; document.body.append(dom);
    const view = { dom, coordsAtPos: () => ({ left: 10, right: 10, top: 10, bottom: 20 }), focus: () => {} };
    sqlPeek(view, 0).show({ title: "dbo.orders", kind: "table", error: "The definition can't be read." });
  });
  const peek = page().getByRole("region", { name: "Definition of dbo.orders" });
  assert.strictEqual(await peek.getByRole("alert").innerText(), "The definition can't be read.");
  assert.ok(await peek.getByRole("button", { name: "Close" }).evaluate((b) => b === document.activeElement));
  await page().keyboard.press("Escape");
  await peek.waitFor({ state: "detached" });
  await page().evaluate(() => document.getElementById("t-view").remove());
  assert.deepStrictEqual(app.errors, []);
});
