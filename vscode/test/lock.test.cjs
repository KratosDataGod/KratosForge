// The extension's package-lock stays something the dependency audit can vouch for: nothing in it
// ships (every package is a build tool), each comes from the npm registry with a sha512 hash, the
// pins in package.json are the versions locked, and CI's audit still reads this lock file.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const HERE = path.join(__dirname, "..");
const ROOT = path.join(HERE, "..");
const pkg = JSON.parse(fs.readFileSync(path.join(HERE, "package.json"), "utf8"));
const lock = JSON.parse(fs.readFileSync(path.join(HERE, "package-lock.json"), "utf8"));
const packages = Object.entries(lock.packages).filter(([where]) => where);

test("the extension has no runtime dependencies, so nothing from npm goes into the .vsix", () => {
  assert.strictEqual(pkg.dependencies, undefined);
  assert.strictEqual(pkg.optionalDependencies, undefined);
  assert.strictEqual(pkg.bundleDependencies ?? pkg.bundledDependencies, undefined);
  const shipped = packages.filter(([, p]) => !p.dev).map(([where]) => where);
  assert.deepStrictEqual(shipped, [], "a package in the lock isn't marked dev, so it could ship");
  assert.match(fs.readFileSync(path.join(HERE, ".vscodeignore"), "utf8"), /^node_modules\/\*\*$/m);
  assert.match(fs.readFileSync(path.join(HERE, "scripts", "package.cjs"), "utf8"), /"--no-dependencies"/);
});

test("every locked package comes from the npm registry with a sha512 hash", () => {
  assert.strictEqual(lock.lockfileVersion, 3);
  assert.ok(packages.length > 0);
  for (const [where, p] of packages) {
    if (p.link) continue;
    assert.match(p.resolved || "", /^https:\/\/registry\.npmjs\.org\//, `${where} isn't from the npm registry`);
    assert.match(p.integrity || "", /^sha512-[A-Za-z0-9+/]+=*$/, `${where} has no sha512 hash`);
  }
});

test("each build tool is pinned to an exact version, and that's the version locked", () => {
  for (const [name, wanted] of Object.entries(pkg.devDependencies)) {
    assert.match(wanted, /^\d+\.\d+\.\d+$/, `${name} isn't pinned to one version`);
    assert.strictEqual(lock.packages[`node_modules/${name}`]?.version, wanted, `${name}: package.json and the lock differ`);
  }
  assert.deepStrictEqual(lock.packages[""].devDependencies, pkg.devDependencies);
});

test("CI's dependency audit still reads this lock file, and audit exceptions keep their dates", () => {
  const action = fs.readFileSync(path.join(ROOT, ".github", "actions", "dependency-audit", "action.yml"), "utf8");
  assert.match(action, /vscode\/package-lock\.json/);
  const lines = fs.readFileSync(path.join(ROOT, ".github", "audit-exceptions.txt"), "utf8").split("\n")
    .filter((l) => l.trim() && !l.startsWith("#"));
  for (const line of lines) assert.match(line, /^\S+\s+until=\d{4}-\d{2}-\d{2}\s+\S/, `an exception without a date or reason: ${line}`);
});
