"""Checks that every dependency a lock file pins carries a hash, and that each published SBOM lists
exactly what its lock file ships, each entry with a hash that matches the lock file. A dependency
added to a lock file without both fails CI. Standard library only.

    locks               every npm, Cargo and hashed pip lock file in the repository: each entry
                        comes from the expected registry and carries a hash.
    app SBOM METADATA   the shell's SBOM (cargo cyclonedx) against `cargo metadata --locked
                        --filter-platform x86_64-pc-windows-msvc` and Cargo.lock: it describes the
                        Windows build, lists every crate that build compiles and no other, and each
                        crate's hash is Cargo.lock's checksum.
    ui SBOM             the UI's SBOM (sbom_npm.py ui) against the vendor lock files: every bundled
                        package is listed with its lock file's hash. Packages inside a prebuilt
                        bundle come from its THIRD-PARTY-NOTICES.md table and have no hash of their own.
    engine SBOM REQS    the engine's SBOM (cyclonedx-py, then sbom_npm.py add-hashes) against
                        sidecar/engine-requirements.txt: the same packages at the same versions,
                        each with the hash of the file pip installed, which must be one the
                        requirements file pins. The engine's own package (built in CI) and the
                        embeddable Python (its pinned hash) are the only other entries.
    vscode SBOM VSIX    the VS Code extension's SBOM (sbom_npm.py vscode) against the .vsix: it names the
                        file by its SHA-256, lists every file inside it with its SHA-256 and no other,
                        the .vsix holds no node_modules, and each build tool carries its lock file hash.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
NPM_REGISTRY = "https://registry.npmjs.org/"
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
WINDOWS = "x86_64-pc-windows-msvc"
# Hashed pip lock files: installed with --require-hashes. constraints.txt and ci-constraints.txt are
# version constraints, not files pip installs from, so they aren't lock files here.
PIP_LOCKS = ["sidecar/engine-requirements.txt", ".github/requirements/*.txt",
             "sidecar/fabric_desktop/engines/dbconnect_locks/*.txt"]
NPM_LOCKS = ["app/ui/vendor/*/package-lock.json", "vscode/package-lock.json"]
CARGO_LOCKS = ["app/src-tauri/Cargo.lock"]
OWN_PACKAGE = "fabric-desktop"  # the engine's own package, built from this repository in CI
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!_-]+)$")
_HASH = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")


def _sbom_npm() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sbom_npm", ROOT / ".github" / "scripts" / "sbom_npm.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def norm(name: str) -> str:
    """A Python package name as PyPI compares it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _files(patterns: list[str], root: Path) -> list[Path]:
    return sorted({p for pattern in patterns for p in root.glob(pattern)})


def pip_pins(path: Path) -> tuple[dict[str, tuple[str, set[str]]], list[str]]:
    """The pins of a hashed pip requirements file, {name: (version, sha256s)}, and what is wrong with it."""
    text = path.read_text(encoding="utf-8")
    lines = [ln.split(" #", 1)[0].strip() for ln in re.sub(r"\\\r?\n", " ", text).splitlines()]
    pins: dict[str, tuple[str, set[str]]] = {}
    problems = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        spec, *options = line.split()
        found = _PIN.match(spec)
        if not found:
            problems.append(f"{path.name}: {spec!r} isn't an exact pin (name==version) from the index")
            continue
        hashes = set()
        for option in options:
            sha = _HASH.match(option)
            if not sha:
                problems.append(f"{path.name}: {found[1]} has {option!r}; only --hash=sha256:<hex> may follow a pin")
            else:
                hashes.add(sha[1])
        if not hashes:
            problems.append(f"{path.name}: {found[1]}=={found[2]} has no --hash")
        if norm(found[1]) in pins:
            problems.append(f"{path.name}: {found[1]} is pinned twice")
        pins[norm(found[1])] = (found[2], hashes)
    return pins, problems


def npm_lock_problems(path: Path) -> list[str]:
    problems = []
    packages = json.loads(path.read_text(encoding="utf-8")).get("packages", {})
    for key, entry in packages.items():
        if not key or entry.get("link"):
            continue  # the project itself, or a folder inside it
        where = f"{path.parent.name}/package-lock.json {key}"
        if not str(entry.get("resolved", "")).startswith(NPM_REGISTRY):
            problems.append(f"{where}: resolved from {entry.get('resolved')!r}, not {NPM_REGISTRY}")
        if not any(part.startswith("sha512-") for part in str(entry.get("integrity", "")).split()):
            problems.append(f"{where}: no sha512 integrity hash")
    return problems


def cargo_lock(path: Path) -> tuple[dict[tuple[str, str], str], list[str]]:
    """{(name, version): checksum} of Cargo.lock's crates.io packages, and what is wrong with it."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    sums: dict[tuple[str, str], str] = {}
    problems = []
    for package in data.get("package", []):
        name, version = package["name"], package["version"]
        if "source" not in package:
            continue  # a crate of this workspace
        if package["source"] != CRATES_IO:
            problems.append(f"{path.name}: {name} {version} comes from {package['source']}, not crates.io")
        elif not re.fullmatch(r"[0-9a-f]{64}", package.get("checksum", "")):
            problems.append(f"{path.name}: {name} {version} has no checksum")
        else:
            sums[(name, version)] = package["checksum"]
    return sums, problems


def check_locks(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for path in _files(PIP_LOCKS, root):
        problems += pip_pins(path)[1]
    for path in _files(NPM_LOCKS, root):
        problems += npm_lock_problems(path)
    for path in _files(CARGO_LOCKS, root):
        problems += cargo_lock(path)[1]
    if not problems and not (_files(PIP_LOCKS, root) and _files(NPM_LOCKS, root) and _files(CARGO_LOCKS, root)):
        problems.append("no lock files found: the patterns in sbom_check.py no longer match the repository")
    return problems


def _sha256(component: dict) -> set[str]:
    return {h["content"] for h in component.get("hashes", []) if h.get("alg") == "SHA-256"}


def windows_crates(metadata: dict) -> set[tuple[str, str]]:
    """The crates the Windows build compiles: the workspace root's normal and build dependencies, transitively."""
    resolve = metadata["resolve"]
    nodes = {n["id"]: n for n in resolve["nodes"]}
    packages = {p["id"]: p for p in metadata["packages"]}
    seen: set[str] = set()
    todo = [resolve["root"]]
    while todo:
        node = todo.pop()
        if node in seen:
            continue
        seen.add(node)
        for dep in nodes[node]["deps"]:
            if {k["kind"] for k in dep["dep_kinds"]} != {"dev"}:
                todo.append(dep["pkg"])
    seen.discard(resolve["root"])
    return {(packages[i]["name"], packages[i]["version"]) for i in seen}


def check_app(sbom: dict, metadata: dict, lock: Path = ROOT / "app" / "src-tauri" / "Cargo.lock") -> list[str]:
    problems = []
    triple = {p.get("name"): p.get("value") for p in sbom.get("metadata", {}).get("properties", [])}
    if triple.get("cdx:rustc:sbom:target:triple") != WINDOWS:
        problems.append(f"the shell's SBOM is for {triple.get('cdx:rustc:sbom:target:triple')!r}, not {WINDOWS} (cargo cyclonedx --target)")
    sums = cargo_lock(lock)[0]
    listed = {(c["name"], c["version"]): c for c in sbom.get("components", [])}
    built = windows_crates(metadata)
    for crate in sorted(built - set(listed)):
        problems.append(f"the shell's SBOM doesn't list {crate[0]} {crate[1]}, which the Windows build compiles")
    for crate in sorted(set(listed) - built):
        problems.append(f"the shell's SBOM lists {crate[0]} {crate[1]}, which the Windows build doesn't compile")
    for crate, component in sorted(listed.items()):
        if crate in built and _sha256(component) != {sums.get(crate)}:
            problems.append(f"the shell's SBOM hash for {crate[0]} {crate[1]} isn't Cargo.lock's checksum")
    return problems


def check_ui(sbom: dict, vendor: Path | None = None) -> list[str]:
    npm = _sbom_npm()
    vendor = vendor or npm.VENDOR
    problems = []
    listed = {c["bom-ref"]: c for c in sbom.get("components", [])}
    for folder in sorted(p for p in vendor.iterdir() if (p / "package-lock.json").is_file()):
        where = f"app/ui/vendor/{folder.name}"
        inside = npm.notices(folder)
        for name, entry in npm.bundled(folder, closure=inside is None):
            want = npm.component(name, entry, where)
            got = listed.get(want["bom-ref"])
            if got is None:
                problems.append(f"the UI's SBOM doesn't list {name} {entry.get('version')} from {where}")
            elif not want.get("hashes") or got.get("hashes") != want["hashes"]:
                problems.append(f"the UI's SBOM hash for {name} {entry.get('version')} ({where}) isn't its package-lock.json integrity")
        for name, row in inside or []:
            if npm.component(name, row, where)["bom-ref"] not in listed:
                problems.append(f"the UI's SBOM doesn't list {name} {row['version']} from {where}/THIRD-PARTY-NOTICES.md")
    return problems


def check_engine(sbom: dict, requirements: Path) -> list[str]:
    pins, problems = pip_pins(requirements)
    listed: dict[str, dict] = {}
    for component in sbom.get("components", []):
        if component.get("type") == "platform" and component.get("name") == "python":
            if not _sha256(component):
                problems.append("the engine's SBOM lists the embeddable Python without its hash")
            continue
        listed[norm(component["name"])] = component
    for name in sorted(set(pins) - set(listed)):
        problems.append(f"the engine's SBOM doesn't list {name} {pins[name][0]}, which engine-requirements.txt pins")
    for name in sorted(set(listed) - set(pins) - {OWN_PACKAGE}):
        problems.append(f"the engine's SBOM lists {name} {listed[name].get('version')}, which engine-requirements.txt doesn't pin")
    for name in sorted(set(pins) & set(listed)):
        version, hashes = pins[name]
        component = listed[name]
        if component.get("version") != version:
            problems.append(f"the engine's SBOM has {name} {component.get('version')}; engine-requirements.txt pins {version}")
        got = _sha256(component)
        if not got:
            problems.append(f"the engine's SBOM lists {name} {version} without a hash")
        elif not got <= hashes:
            problems.append(f"the engine's SBOM hash for {name} {version} isn't one engine-requirements.txt pins")
    return problems


def check_vscode(sbom: dict, vsix: Path) -> list[str]:
    npm = _sbom_npm()
    problems = []
    sha = hashlib.sha256(vsix.read_bytes()).hexdigest()
    head = sbom.get("metadata", {}).get("component", {})
    if head.get("hashes") != [{"alg": "SHA-256", "content": sha}]:
        problems.append(f"the VS Code extension's SBOM doesn't name {vsix.name} by its SHA-256 ({sha})")
    inside = dict(npm.vsix_files(vsix))
    listed = {c["name"]: c for c in sbom.get("components", []) if c.get("type") == "file"}
    for path, digest in inside.items():
        if "node_modules/" in path:
            problems.append(f"{vsix.name} holds {path}: it is packaged with --no-dependencies, so it should hold no npm package")
        got = listed.get(path)
        if got is None:
            problems.append(f"the VS Code extension's SBOM doesn't list {path}")
        elif got.get("hashes") != [{"alg": "SHA-256", "content": digest}]:
            problems.append(f"the VS Code extension's SBOM hash for {path} isn't the file's")
    for path in sorted(set(listed) - set(inside)):
        problems.append(f"the VS Code extension's SBOM lists {path}, which isn't in {vsix.name}")
    refs = {c["bom-ref"]: c for c in sbom.get("components", [])}
    for name, entry in npm.vscode_build_tools():
        want = npm.component(name, entry, "vscode")
        got = refs.get(want["bom-ref"])
        if got is None:
            problems.append(f"the VS Code extension's SBOM doesn't list its build tool {name} {entry.get('version')}")
        elif not want.get("hashes") or got.get("hashes") != want["hashes"] or got.get("scope") != "excluded":
            problems.append(f"the VS Code extension's SBOM entry for {name} {entry.get('version')} isn't its lock file hash, scope excluded")
    return problems


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if argv == ["locks"]:
        problems = check_locks()
    elif argv[:1] == ["app"] and len(argv) == 3:
        problems = check_app(_load(argv[1]), _load(argv[2]))
    elif argv[:1] == ["ui"] and len(argv) == 2:
        problems = check_ui(_load(argv[1]))
    elif argv[:1] == ["vscode"] and len(argv) == 3:
        problems = check_vscode(_load(argv[1]), Path(argv[2]))
    elif argv[:1] == ["engine"] and len(argv) == 3:
        problems = check_engine(_load(argv[1]), Path(argv[2]))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        print(f"{len(problems)} problem(s). Every dependency needs a hash in its lock file and a matching entry in the SBOM.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
