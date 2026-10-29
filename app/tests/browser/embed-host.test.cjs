// PBE-03: the Power BI embed host (app/src-tauri/embed-host) under the policies the shell sets. A stand-in main
// window (embed-host/parent.js) on the app's origin frames the host on its pbiembed origin, and a stand-in Power BI
// frame (embed-host/powerbi-frame.js) answers on app.powerbi.com, so no tenant, token or capacity is needed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const host = require("../../src-tauri/embed-host/host.js");

let playwright = null;
try { playwright = require("playwright"); } catch { /* not installed: the browser tests skip */ }
const skip = !playwright && "Playwright is not installed";

const ROOT = path.join(__dirname, "..", "..", "..");
const HOST_DIR = path.join(ROOT, "app", "src-tauri", "embed-host");
const HERE = path.join(__dirname, "embed-host");
const MAIN_RS = fs.readFileSync(path.join(ROOT, "app", "src-tauri", "src", "main.rs"), "utf8");
const EMBED = "https://app.powerbi.com/reportEmbed?reportId=r1&groupId=w1";

// The host's policy, as main.rs serves it (EMBED_CSP, one string split over lines with \ continuations).
function hostCsp() {
  const m = MAIN_RS.match(/const EMBED_CSP: &str = "([\s\S]*?)";/);
  assert.ok(m, "EMBED_CSP not found in main.rs");
  return m[1].replace(/\\\n\s*/g, "");
}

// The main window's policy (tauri.conf.json), as the shell sets it.
function appCsp() {
  const conf = JSON.parse(fs.readFileSync(path.join(ROOT, "app", "src-tauri", "tauri.conf.json"), "utf8"));
  return Object.entries(conf.app.security.csp).map(([k, v]) => `${k} ${[].concat(v).join(" ")}`).join("; ");
}

test("the vendored SDK is the file the npm tarball holds", () => {
  const meta = JSON.parse(fs.readFileSync(path.join(HOST_DIR, "vendor", "package.json"), "utf8")).vendored;
  const sum = crypto.createHash("sha256").update(fs.readFileSync(path.join(HOST_DIR, "vendor", "powerbi.min.js"))).digest("hex");
  assert.strictEqual(sum, meta.file_sha256);
  assert.match(fs.readFileSync(path.join(HOST_DIR, "vendor", "powerbi.min.js"), "utf8").slice(0, 80), new RegExp(`powerbi-client v${meta.version.replace(/\./g, "\\.")}`));
});

test("embed addresses off app.powerbi.com are refused", () => {
  assert.ok(host.embedUrlOk(EMBED));
  for (const url of ["http://app.powerbi.com/x", "https://evil.example/x", "https://app.powerbi.com.evil.example/x",
    "https://u@app.powerbi.com/x", "https://app.powerbi.com:444/x", "https://msit.powerbi.com/x", "javascript:alert(1)", "", null, 5]) {
    assert.ok(!host.embedUrlOk(url), String(url));
  }
});

test("only known fields reach the SDK, with an Entra token and Power BI's page list hidden", () => {
  const models = { TokenType: { Aad: 0, Embed: 1 } };
  const config = host.embedConfig({ kind: "report", id: "r1", embedUrl: EMBED, accessToken: "t", pageName: "P1",
    tokenType: "Embed", settings: { hyperlinkClickBehavior: 2 }, eventHooks: { x: 1 } }, models);
  assert.deepStrictEqual(config, { type: "report", id: "r1", embedUrl: EMBED, accessToken: "t", tokenType: 0, pageName: "P1",
    settings: { panes: { pageNavigation: { visible: false } } } });
  assert.throws(() => host.embedConfig({ kind: "qna", id: "x", embedUrl: EMBED, accessToken: "t" }, models), /reports, visuals/);
  assert.throws(() => host.embedConfig({ kind: "report", id: "x", embedUrl: "https://evil.example/r", accessToken: "t" }, models), /only opens/);
  assert.throws(() => host.embedConfig({ kind: "report", id: "x", embedUrl: EMBED }, models), /token/);
  assert.throws(() => host.embedConfig({ kind: "visual", id: "x", embedUrl: EMBED, accessToken: "t", pageName: "P" }, models), /visual/);
  assert.throws(() => host.embedConfig({ kind: "tile", id: "x", embedUrl: EMBED, accessToken: "t" }, models), /dashboard/);
});

test("event details lose anything that looks like a token or secret", () => {
  const cyclic = { a: 1 };
  cyclic.self = cyclic;
  const out = host.clean({ accessToken: "x", Authorization: "y", nested: { client_secret: "z", keep: "k" }, fn() {}, cyclic, long: "a".repeat(5000) });
  assert.deepStrictEqual(Object.keys(out).sort(), ["cyclic", "long", "nested"]);
  assert.deepStrictEqual(out.nested, { keep: "k" });
  assert.ok(out.long.length < 2100);
  assert.ok(!JSON.stringify(out).includes("\"x\""));
  assert.deepStrictEqual(host.eventDetail("pageChanged", { newPage: { name: "p", displayName: "P", report: cyclic } }),
    { newPage: { name: "p", displayName: "P" } });
});

let browser, page;

async function serve(context) {
  const file = (dir, name) => fs.readFileSync(path.join(dir, name));
  const type = (name) => (name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html");
  await context.route("http://tauri.localhost/**", (route) => {
    const name = new URL(route.request().url()).pathname.slice(1);
    route.fulfill({ status: 200, contentType: type(name), headers: { "Content-Security-Policy": appCsp() }, body: file(HERE, name) });
  });
  await context.route("http://pbiembed.localhost/**", (route) => {
    const name = new URL(route.request().url()).pathname.slice(1);
    route.fulfill({ status: 200, contentType: type(name), headers: { "Content-Security-Policy": hostCsp() }, body: file(HOST_DIR, name) });
  });
  await context.route("http://evil.localhost/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: '<iframe src="http://pbiembed.localhost/host.html"></iframe>' }));
  await context.route("https://app.powerbi.com/**", (route) => {
    if (new URL(route.request().url()).pathname.endsWith(".js")) {
      return route.fulfill({ status: 200, contentType: "text/javascript", body: file(HERE, "powerbi-frame.js") });
    }
    route.fulfill({ status: 200, contentType: "text/html", body: '<!doctype html><script src="/powerbi-frame.js"></script>' });
  });
  await context.route("https://example.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "x" }));
}

before(async () => {
  if (skip) return;
  browser = await playwright.chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const context = await browser.newContext();
  await serve(context);
  page = await context.newPage();
  await page.goto("http://tauri.localhost/parent.html");
  await page.waitForFunction(() => window.got.some((m) => m.event === "ready"));
});

after(async () => { if (browser) await browser.close(); });

const replyTo = (id) => page.waitForFunction((n) => window.got.find((m) => m.reply === n), id).then((h) => h.jsonValue());

test("the main window can't frame Power BI itself", { skip }, async () => {
  assert.ok(await page.evaluate(() => window.violations.some((v) => v.startsWith("frame-src https://app.powerbi.com"))));
});

test("a report opens through the host, and its events come back without the token", { skip }, async () => {
  const id = await page.evaluate((url) => window.command({ command: "embed", kind: "report", id: "r1", embedUrl: url, accessToken: "secret-token" }), EMBED);
  const reply = await replyTo(id);
  assert.strictEqual(reply.ok, true, reply.error);
  assert.strictEqual(reply.origin, "http://pbiembed.localhost");
  await page.waitForFunction(() => window.got.some((m) => m.event === "rendered"));
  const loaded = await page.evaluate(() => window.got.find((m) => m.event === "loaded"));
  assert.deepStrictEqual(loaded.detail, { kept: 1, nested: {} });
  assert.ok(!JSON.stringify(await page.evaluate(() => window.got)).includes("secret-token"));
  assert.ok(!JSON.stringify(await page.evaluate(() => window.got)).includes("leaked-token"));
});

test("pages and a new token go to Power BI's frame", { skip }, async () => {
  const pages = await replyTo(await page.evaluate(() => window.command({ command: "getPages" })));
  assert.deepStrictEqual(pages.result, [{ name: "ReportSection1", displayName: "Overview", isActive: true, visibility: 0 }]);
  const token = await replyTo(await page.evaluate(() => window.command({ command: "setToken", accessToken: "fresh" })));
  assert.strictEqual(token.ok, true, token.error);
});

test("Power BI's frame can't drive the host, and unknown commands are ignored", { skip }, async () => {
  await page.waitForTimeout(200);
  // The stand-in Power BI frame sent {kfEmbed: reset} to the host; the report must still be open.
  const pages = await replyTo(await page.evaluate(() => window.command({ command: "getPages" })));
  assert.strictEqual(pages.ok, true, pages.error);
  const before = await page.evaluate(() => window.got.length);
  await page.evaluate(() => window.command({ command: "eval", code: "1" }));
  await page.waitForTimeout(200);
  assert.strictEqual(await page.evaluate(() => window.got.length), before);
});

test("an embed address elsewhere is refused before the SDK sees it", { skip }, async () => {
  const reply = await replyTo(await page.evaluate(() => window.command({ command: "embed", kind: "report", id: "r1", embedUrl: "https://example.com/reportEmbed", accessToken: "t" })));
  assert.strictEqual(reply.ok, false);
  assert.match(reply.error, /only opens content on app\.powerbi\.com/);
});

test("the host frames nothing but Power BI, and only the app can frame it", { skip }, async () => {
  const hostFrame = page.frames().find((f) => f.url().startsWith("http://pbiembed.localhost/"));
  const blocked = await hostFrame.evaluate(() => new Promise((resolve) => {
    document.addEventListener("securitypolicyviolation", (e) => resolve(`${e.violatedDirective} ${e.blockedURI}`), { once: true });
    const f = document.createElement("iframe");
    f.src = "https://example.com/";
    document.body.append(f);
  }));
  assert.match(blocked, /^frame-src https:\/\/example\.com/);
  const fetched = await hostFrame.evaluate(() => fetch("https://app.powerbi.com/x").then(() => "fetched", () => "refused"));
  assert.strictEqual(fetched, "refused");
  const other = await browser.newPage();
  const refused = [];
  other.on("console", (m) => refused.push(m.text()));
  await serve(other.context());
  await other.goto("http://evil.localhost/");
  await other.waitForTimeout(500);
  assert.ok(!other.frames().some((f) => f.url().startsWith("http://pbiembed.localhost/")), "a page on another origin loaded the host");
  assert.ok(refused.some((t) => t.includes("frame-ancestors")), refused.join("\n"));
  await other.close();
});
