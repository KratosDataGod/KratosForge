"""The UI's SBOM and the engine SBOM's Python entry (.github/scripts/sbom_npm.py)."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sbom_npm", ROOT / ".github" / "scripts" / "sbom_npm.py")
sbom = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(sbom)  # type: ignore[union-attr]


def test_the_ui_sbom_lists_every_vendor_bundle_and_no_build_tool(tmp_path):
    out = tmp_path / "ui.json"
    assert sbom.main(["ui", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["bomFormat"] == "CycloneDX" and data["specVersion"] == "1.5"
    names = {(c["name"], c["properties"][0]["value"]) for c in data["components"]}
    vendor = [p for p in (ROOT / "app" / "ui" / "vendor").iterdir() if (p / "package-lock.json").is_file()]
    for folder in vendor:
        roots = json.loads((folder / "package.json").read_text(encoding="utf-8"))["devDependencies"]
        for name in roots:
            if name in sbom.BUILD_TOOLS:
                assert (name, f"app/ui/vendor/{folder.name}") not in names
            else:
                assert (name, f"app/ui/vendor/{folder.name}") in names, name
    assert not any(n.startswith("@esbuild/") or n == "esbuild" for n, _ in names)
    refs = [c["bom-ref"] for c in data["components"]]
    assert len(refs) == len(set(refs))


def test_lock_file_packages_carry_the_lock_files_hash():
    folder = ROOT / "app" / "ui" / "vendor" / "codemirror"
    lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    item = sbom.component("@codemirror/view", lock["node_modules/@codemirror/view"], "x")
    assert item["purl"] == f"pkg:npm/%40codemirror/view@{lock['node_modules/@codemirror/view']['version']}"
    b64 = lock["node_modules/@codemirror/view"]["integrity"].split("-", 1)[1]
    assert item["hashes"] == [{"alg": "SHA-512", "content": base64.b64decode(b64).hex()}]


def test_the_vega_bundle_lists_the_packages_its_notices_name():
    rows = sbom.notices(ROOT / "app" / "ui" / "vendor" / "vega")
    assert rows and ("d3-array", {"version": rows[0][1]["version"], "license": "ISC"}) == rows[0]
    assert all(r[1]["version"] and r[1]["license"] for r in rows)


def test_python_is_added_to_the_engine_sbom(tmp_path):
    path = tmp_path / "engine.json"
    path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": [{"name": "duckdb"}]}), encoding="utf-8")
    sha = hashlib.sha256(b"x").hexdigest()
    url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
    assert sbom.main(["add-python", str(path), "3.12.10", sha, url]) == 0
    added = json.loads(path.read_text(encoding="utf-8"))["components"]
    assert added[0] == {"name": "duckdb"}
    assert added[1]["name"] == "python" and added[1]["version"] == "3.12.10"
    assert added[1]["hashes"] == [{"alg": "SHA-256", "content": sha}]
    assert added[1]["purl"].startswith("pkg:generic/python@3.12.10?download_url=https%3A%2F%2Fwww.python.org")
