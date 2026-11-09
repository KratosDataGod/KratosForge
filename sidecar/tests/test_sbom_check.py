"""Every dependency a lock file pins carries a hash, and each SBOM lists what its lock file ships
with a matching hash (.github/scripts/sbom_check.py, run by ci.yml's sbom-app and engine jobs)."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github" / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


check = _load("sbom_check")
npm = _load("sbom_npm")
A, B, C = "a" * 64, "b" * 64, "c" * 64


def test_every_lock_file_in_the_repository_pins_hashed_registry_packages():
    assert check.check_locks() == []
    assert check.main(["locks"]) == 0


def test_an_npm_package_without_integrity_or_from_elsewhere_fails(tmp_path):
    good = {"version": "1.0.0", "resolved": "https://registry.npmjs.org/x/-/x-1.0.0.tgz", "integrity": "sha512-AAAA"}
    lock = {"packages": {"": {"name": "root"}, "node_modules/x": good, "node_modules/local": {"link": True},
                         "node_modules/y": {**good, "integrity": ""},
                         "node_modules/z": {**good, "resolved": "git+https://example.com/z.git"},
                         "node_modules/w": {**good, "integrity": "sha1-AAAA"}}}
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    problems = check.npm_lock_problems(tmp_path / "package-lock.json")
    assert [p.split(": ", 1)[0].rsplit(" ", 1)[1] for p in problems] == ["node_modules/y", "node_modules/z", "node_modules/w"]


def test_a_pip_pin_needs_an_exact_version_and_a_sha256_and_nothing_else(tmp_path):
    path = tmp_path / "req.txt"
    path.write_text("# comment\n"
                    f"ok==1.0 \\\n    --hash=sha256:{A} \\\n    --hash=sha256:{B}\n"
                    "nohash==2.0\n"
                    "loose>=1.0 \\\n    --hash=sha256:" + C + "\n"
                    "git+https://example.com/x.git\n"
                    f"--index-url https://mirror.example.com/simple\n"
                    f"md5==1.0 --hash=md5:{'d' * 32}\n", encoding="utf-8")
    pins, problems = check.pip_pins(path)
    assert pins["ok"] == ("1.0", {A, B})
    joined = "\n".join(problems)
    assert "nohash==2.0 has no --hash" in joined
    assert "'loose>=1.0' isn't an exact pin" in joined
    assert "'git+https://example.com/x.git' isn't an exact pin" in joined
    assert "'--index-url' isn't an exact pin" in joined
    assert "md5 has '--hash=md5:" in joined


def _cargo(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "Cargo.lock"
    path.write_text('version = 4\n\n[[package]]\nname = "shell"\nversion = "0.1.0"\n\n'
                    f'[[package]]\nname = "serde"\nversion = "1.0.0"\nsource = "{check.CRATES_IO}"\nchecksum = "{A}"\n\n'
                    f'[[package]]\nname = "windows-sys"\nversion = "0.60.0"\nsource = "{check.CRATES_IO}"\nchecksum = "{B}"\n\n'
                    f'[[package]]\nname = "gtk"\nversion = "0.18.0"\nsource = "{check.CRATES_IO}"\nchecksum = "{C}"\n' + extra,
                    encoding="utf-8")
    return path


def test_a_crate_from_git_or_without_a_checksum_fails(tmp_path):
    lock = _cargo(tmp_path, '\n[[package]]\nname = "forked"\nversion = "1.0.0"\nsource = "git+https://example.com/f#abc"\n\n'
                            f'[[package]]\nname = "bare"\nversion = "1.0.0"\nsource = "{check.CRATES_IO}"\n')
    sums, problems = check.cargo_lock(lock)
    assert set(sums) == {("serde", "1.0.0"), ("windows-sys", "0.60.0"), ("gtk", "0.18.0")}
    assert problems == ["Cargo.lock: forked 1.0.0 comes from git+https://example.com/f#abc, not crates.io",
                        "Cargo.lock: bare 1.0.0 has no checksum"]


def _metadata() -> dict:
    """cargo metadata --filter-platform for Windows: shell needs serde and windows-sys; serde_test only for its tests."""
    def node(i, *deps):
        return {"id": i, "deps": [{"pkg": d, "dep_kinds": [{"kind": k}]} for d, k in deps]}
    packages = [{"id": i, "name": i.split("@")[0], "version": i.split("@")[1]}
                for i in ("shell@0.1.0", "serde@1.0.0", "windows-sys@0.60.0", "serde_test@1.0.0")]
    nodes = [node("shell@0.1.0", ("serde@1.0.0", None), ("windows-sys@0.60.0", "build"), ("serde_test@1.0.0", "dev")),
             node("serde@1.0.0"), node("windows-sys@0.60.0"), node("serde_test@1.0.0")]
    return {"packages": packages, "resolve": {"root": "shell@0.1.0", "nodes": nodes}}


def _app_sbom(*crates: tuple[str, str, str], triple: str = check.WINDOWS) -> dict:
    return {"metadata": {"properties": [{"name": "cdx:rustc:sbom:target:triple", "value": triple}]},
            "components": [{"name": n, "version": v, "hashes": [{"alg": "SHA-256", "content": h}]} for n, v, h in crates]}


def test_the_shell_sbom_lists_exactly_the_windows_builds_crates_with_cargo_lock_checksums(tmp_path):
    lock = _cargo(tmp_path)
    assert check.windows_crates(_metadata()) == {("serde", "1.0.0"), ("windows-sys", "0.60.0")}
    good = _app_sbom(("serde", "1.0.0", A), ("windows-sys", "0.60.0", B))
    assert check.check_app(good, _metadata(), lock) == []
    # The SBOM CI published before: made for Linux, so it had gtk and no windows-sys.
    linux = _app_sbom(("serde", "1.0.0", A), ("gtk", "0.18.0", C), triple="x86_64-unknown-linux-gnu")
    assert check.check_app(linux, _metadata(), lock) == [
        "the shell's SBOM is for 'x86_64-unknown-linux-gnu', not x86_64-pc-windows-msvc (cargo cyclonedx --target)",
        "the shell's SBOM doesn't list windows-sys 0.60.0, which the Windows build compiles",
        "the shell's SBOM lists gtk 0.18.0, which the Windows build doesn't compile"]
    wrong = _app_sbom(("serde", "1.0.0", C), ("windows-sys", "0.60.0", B))
    assert check.check_app(wrong, _metadata(), lock) == ["the shell's SBOM hash for serde 1.0.0 isn't Cargo.lock's checksum"]


def test_the_ui_sbom_matches_the_vendor_lock_files():
    sbom = npm.ui()
    assert check.check_ui(sbom) == []
    hashed = next(c for c in sbom["components"] if c.get("hashes"))
    dropped = copy.deepcopy(sbom)
    dropped["components"] = [c for c in dropped["components"] if c["bom-ref"] != hashed["bom-ref"]]
    assert check.check_ui(dropped) == [f"the UI's SBOM doesn't list {hashed['name']} {hashed['version']} "
                                       f"from {hashed['properties'][0]['value']}"]
    changed = copy.deepcopy(sbom)
    next(c for c in changed["components"] if c["bom-ref"] == hashed["bom-ref"])["hashes"] = [{"alg": "SHA-512", "content": "00"}]
    assert check.check_ui(changed)[0].startswith(f"the UI's SBOM hash for {hashed['name']} ")
    noticed = next(c for c in sbom["components"] if c["properties"][1]["value"] == "THIRD-PARTY-NOTICES.md")
    gone = copy.deepcopy(sbom)
    gone["components"] = [c for c in gone["components"] if c["bom-ref"] != noticed["bom-ref"]]
    assert check.check_ui(gone)[0].endswith("THIRD-PARTY-NOTICES.md")


def _engine(tmp_path: Path) -> Path:
    path = tmp_path / "engine-requirements.txt"
    path.write_text(f"Py_Arrow==26.0.0 \\\n    --hash=sha256:{A} \\\n    --hash=sha256:{B}\ndeltalake==1.6.6 \\\n    --hash=sha256:{C}\n",
                    encoding="utf-8")
    return path


def _engine_sbom(*packages: tuple[str, str, str | None]) -> dict:
    components = [{"type": "library", "name": n, "version": v, **({"hashes": [{"alg": "SHA-256", "content": h}]} if h else {})}
                  for n, v, h in packages]
    components.append({"type": "platform", "name": "python", "version": "3.12.10", "hashes": [{"alg": "SHA-256", "content": A}]})
    return {"components": components}


def test_the_engine_sbom_lists_the_pinned_packages_with_the_hash_pip_installed(tmp_path):
    reqs = _engine(tmp_path)
    good = _engine_sbom(("py-arrow", "26.0.0", B), ("DeltaLake", "1.6.6", C), ("fabric-desktop", "0.20.0", None))
    assert check.check_engine(good, reqs) == []
    # What cyclonedx-py writes on its own: no package hashes.
    bare = _engine_sbom(("py-arrow", "26.0.0", None), ("deltalake", "1.6.6", None))
    assert check.check_engine(bare, reqs) == ["the engine's SBOM lists deltalake 1.6.6 without a hash",
                                              "the engine's SBOM lists py-arrow 26.0.0 without a hash"]
    odd = _engine_sbom(("py-arrow", "26.0.1", "e" * 64), ("requests", "2.0", A))
    assert check.check_engine(odd, reqs) == [
        "the engine's SBOM doesn't list deltalake 1.6.6, which engine-requirements.txt pins",
        "the engine's SBOM lists requests 2.0, which engine-requirements.txt doesn't pin",
        "the engine's SBOM has py-arrow 26.0.1; engine-requirements.txt pins 26.0.0",
        "the engine's SBOM hash for py-arrow 26.0.0 isn't one engine-requirements.txt pins"]


def test_add_hashes_takes_each_packages_hash_from_pips_install_report(tmp_path):
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps(_engine_sbom(("Py_Arrow", "26.0.0", None), ("deltalake", "1.6.6", None),
                                            ("fabric-desktop", "0.20.0", None))), encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"version": "1", "install": [
        {"metadata": {"name": "py-arrow", "version": "26.0.0"}, "download_info": {"archive_info": {"hashes": {"sha256": B}}}},
        {"metadata": {"name": "deltalake", "version": "1.6.5"}, "download_info": {"archive_info": {"hashes": {"sha256": C}}}},
        {"metadata": {"name": "fabric-desktop", "version": "0.20.0"}, "download_info": {"dir_info": {}}}]}), encoding="utf-8")
    assert npm.main(["add-hashes", str(sbom), str(report)]) == 0
    hashes = {c["name"]: c.get("hashes") for c in json.loads(sbom.read_text(encoding="utf-8"))["components"]}
    assert hashes["Py_Arrow"] == [{"alg": "SHA-256", "content": B}]
    assert hashes["deltalake"] is None, "another version's hash is never borrowed"
    assert hashes["fabric-desktop"] is None
    assert hashes["python"] == [{"alg": "SHA-256", "content": A}], "a hash already there stays"
    assert check.check_engine(json.loads(sbom.read_text(encoding="utf-8")), _engine(tmp_path)) == [
        "the engine's SBOM lists deltalake 1.6.6 without a hash"]


def test_main_reports_each_problem_as_a_ci_error(tmp_path, capsys):
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps(_engine_sbom(("py-arrow", "26.0.0", None))), encoding="utf-8")
    assert check.main(["engine", str(sbom), str(_engine(tmp_path))]) == 1
    out = capsys.readouterr().out
    assert out.count("::error::") == 2
    assert check.main(["nope"]) == 2
