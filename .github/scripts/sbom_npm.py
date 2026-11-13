"""Software bills of materials (CycloneDX 1.5 JSON) for what the npm lock files put in the app, and
the engine's Python runtime added to the engine's SBOM. Standard library only: no new tool.

    ui OUT              the third-party npm packages bundled into the app's UI (app/ui/vendor/*),
                        read from each folder's package-lock.json: every package its bundle starts
                        from (package.json, build tools left out) with the integrity hash the lock
                        file holds, and everything those depend on. A folder with a
                        THIRD-PARTY-NOTICES.md bundles prebuilt files (Vega's), whose packages are the
                        versions their upstream build used, so those come from that table instead,
                        with no hash. The VS Code extension is listed as itself: it is packaged with
                        --no-dependencies, so it bundles no npm package.
    vscode OUT VSIX     the VS Code extension (kratosforge.vsix): the file itself with its SHA-256, each
                        file inside it with its SHA-256, and the build tools that made it (from
                        vscode/package-lock.json, with their integrity hashes, scope "excluded": none is
                        in the file, which is packaged with --no-dependencies). Made the same way twice
                        from the same .vsix and SOURCE_DATE_EPOCH, it comes out byte for byte the same.
    add-python SBOM VERSION SHA256 URL
                        add python.org's embeddable Python (the engine's runtime) to an existing SBOM
                        made by cyclonedx-py, which lists packages but not the interpreter.
    add-hashes SBOM REPORT
                        give each package in an SBOM made by cyclonedx-py the SHA-256 of the file
                        pip installed it from, read from that install's --report (cyclonedx-py
                        reads an installed environment, which no longer holds those hashes).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "app" / "ui" / "vendor"
# Packages that build a bundle but aren't in it.
BUILD_TOOLS = {"esbuild", "typescript", "@vscode/vsce"}
HASH_NAMES = {"sha512": "SHA-512", "sha384": "SHA-384", "sha256": "SHA-256", "sha1": "SHA-1"}


def purl(name: str, version: str) -> str:
    return f"pkg:npm/{quote(name, safe='/')}@{quote(version)}"  # a scope's @ becomes %40


def _resolve(packages: dict, parent: str, name: str) -> str | None:
    """Where npm puts ``name`` for the package at ``parent``: its own node_modules, then each folder above."""
    here = parent
    while True:
        key = f"{here}/node_modules/{name}" if here else f"node_modules/{name}"
        if key in packages:
            return key
        if not here:
            return None
        cut = here.rfind("/node_modules/")
        here = here[:cut] if cut >= 0 else ""


def notices(folder: Path) -> list[tuple[str, dict]] | None:
    """The packages a THIRD-PARTY-NOTICES.md table lists, or None when the folder has none."""
    path = folder / "THIRD-PARTY-NOTICES.md"
    if not path.is_file():
        return None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Package", "") and not set(cells[0]) <= set("-: "):
            rows.append((cells[0], {"version": cells[1], "license": cells[2]}))
    if not rows:
        raise ValueError(f"{path} has no package table")
    return rows


def bundled(folder: Path, closure: bool = True) -> list[tuple[str, dict]]:
    """Every package a vendor folder's bundle is built from, with its lock file entry; with closure
    False, only the packages package.json names."""
    manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    packages = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    roots = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}
    todo = [_resolve(packages, "", n) for n in sorted(roots) if n not in BUILD_TOOLS and not n.startswith("@types/")]
    seen: dict[str, dict] = {}
    while todo:
        key = todo.pop()
        if key is None:
            raise ValueError(f"{folder.name}: a package its bundle needs isn't in package-lock.json")
        if key in seen:
            continue
        entry = packages[key]
        seen[key] = entry
        for dep in {**entry.get("dependencies", {}), **entry.get("peerDependencies", {})} if closure else ():
            found = _resolve(packages, key, dep)
            optional = dep in entry.get("optionalDependencies", {}) or dep in entry.get("peerDependenciesMeta", {})
            if found is None and optional:
                continue
            todo.append(found)
    out = []
    for key, entry in seen.items():
        name = entry.get("name") or key.rsplit("node_modules/", 1)[1]
        out.append((name, entry))
    return sorted(out, key=lambda item: (item[0], item[1].get("version", "")))


def component(name: str, entry: dict, where: str, source: str = "package-lock.json") -> dict:
    version = entry.get("version", "")
    item: dict = {"type": "library", "bom-ref": f"{purl(name, version)}#{where}", "name": name,
                  "version": version, "purl": purl(name, version),
                  "properties": [{"name": "kratosforge:bundled-in", "value": where},
                                 {"name": "kratosforge:listed-from", "value": source}]}
    if entry.get("integrity"):
        hashes = []
        for part in entry["integrity"].split():
            alg, _, b64 = part.partition("-")
            if alg in HASH_NAMES:
                hashes.append({"alg": HASH_NAMES[alg], "content": base64.b64decode(b64).hex()})
        if hashes:
            item["hashes"] = hashes
    if isinstance(entry.get("license"), str):
        item["licenses"] = [{"expression": entry["license"]}]
    return item


def bom(name: str, components: list[dict]) -> dict:
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
                         "component": {"type": "application", "bom-ref": name, "name": name}},
            "components": components}


def ui() -> dict:
    components = []
    for folder in sorted(p for p in VENDOR.iterdir() if (p / "package-lock.json").is_file()):
        where = f"app/ui/vendor/{folder.name}"
        inside = notices(folder)
        components += [component(n, e, where) for n, e in bundled(folder, closure=inside is None)]
        components += [component(n, e, where, "THIRD-PARTY-NOTICES.md") for n, e in inside or []]
    vscode = json.loads((ROOT / "vscode" / "package.json").read_text(encoding="utf-8"))
    if vscode.get("dependencies"):
        raise ValueError("vscode/package.json has runtime dependencies now; list them in this SBOM")
    components.append({"type": "application", "bom-ref": "kratosforge-vscode", "name": vscode["name"],
                       "version": vscode.get("version", ""),
                       "properties": [{"name": "kratosforge:bundled-npm-packages", "value": "none (--no-dependencies)"}]})
    return bom("KratosForge UI and VS Code extension", components)


def vsix_files(vsix: Path) -> list[tuple[str, str]]:
    """(path, SHA-256) of each file in a .vsix, in the order the zip lists them."""
    with zipfile.ZipFile(vsix) as z:
        return [(i.filename, hashlib.sha256(z.read(i)).hexdigest()) for i in z.infolist() if not i.is_dir()]


def vscode_build_tools() -> list[tuple[str, dict]]:
    """The packages vscode/package.json names (all build and test tools), with their lock file entries."""
    folder = ROOT / "vscode"
    manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    packages = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    return [(n, packages[f"node_modules/{n}"]) for n in sorted(manifest.get("devDependencies", {}))]


def vscode(vsix: Path) -> dict:
    manifest = json.loads((ROOT / "vscode" / "package.json").read_text(encoding="utf-8"))
    if manifest.get("dependencies"):
        raise ValueError("vscode/package.json has runtime dependencies now; list them in this SBOM")
    with zipfile.ZipFile(vsix) as z:
        packaged = json.loads(z.read("extension/package.json"))
    version = packaged.get("version", "")
    sha = hashlib.sha256(vsix.read_bytes()).hexdigest()
    files = [{"type": "file", "bom-ref": f"file:{path}", "name": path, "hashes": [{"alg": "SHA-256", "content": digest}]}
             for path, digest in vsix_files(vsix)]
    tools = []
    for name, entry in vscode_build_tools():
        item = component(name, entry, "vscode")
        item["scope"] = "excluded"  # builds the file; not in it
        item["properties"].append({"name": "kratosforge:role", "value": "build or test tool"})
        tools.append(item)
    # Reproducible: the time is the commit's (SOURCE_DATE_EPOCH, as for the .vsix) and the serial number
    # follows from the file, so the same .vsix always gets the same SBOM.
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    when = datetime.fromtimestamp(int(epoch), UTC) if epoch else datetime.now(UTC)
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'kratosforge.vsix:' + sha)}",
            "version": 1,
            "metadata": {"timestamp": when.isoformat(timespec="seconds"),
                         "component": {"type": "application", "bom-ref": "kratosforge.vsix", "name": "kratosforge.vsix",
                                       "version": version, "purl": f"pkg:generic/kratosforge-vscode@{quote(version)}",
                                       "hashes": [{"alg": "SHA-256", "content": sha}],
                                       "description": "The KratosForge extension for VS Code (vscode/)"}},
            "components": files + tools}


def add_python(path: Path, version: str, sha256: str, url: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("components", []).append({
        "type": "platform", "bom-ref": f"python-embed@{version}", "name": "python", "version": version,
        "purl": f"pkg:generic/python@{version}?download_url={quote(url, safe='')}",
        "hashes": [{"alg": "SHA-256", "content": sha256}],
        "licenses": [{"license": {"id": "PSF-2.0"}}],
        "description": "python.org's embeddable Python, the engine's runtime"})
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()  # PEP 503, as pip compares names


def add_hashes(path: Path, report: Path) -> None:
    installed = {}
    for item in json.loads(report.read_text(encoding="utf-8"))["install"]:
        sha = item.get("download_info", {}).get("archive_info", {}).get("hashes", {}).get("sha256")
        if sha:
            installed[(_norm(item["metadata"]["name"]), item["metadata"]["version"])] = sha
    data = json.loads(path.read_text(encoding="utf-8"))
    for component in data.get("components", []):
        sha = installed.get((_norm(component.get("name", "")), component.get("version", "")))
        if sha and not component.get("hashes"):
            component["hashes"] = [{"alg": "SHA-256", "content": sha}]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main(argv: list[str]) -> int:
    if argv[:1] == ["ui"] and len(argv) == 2:
        Path(argv[1]).write_text(json.dumps(ui(), indent=2), encoding="utf-8")
        return 0
    if argv[:1] == ["vscode"] and len(argv) == 3:
        Path(argv[1]).write_text(json.dumps(vscode(Path(argv[2])), indent=2), encoding="utf-8")
        return 0
    if argv[:1] == ["add-python"] and len(argv) == 5:
        add_python(Path(argv[1]), argv[2], argv[3], argv[4])
        return 0
    if argv[:1] == ["add-hashes"] and len(argv) == 3:
        add_hashes(Path(argv[1]), Path(argv[2]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
