"""The shell's Rust release and the bundled JavaScript builds are pinned, so neither can change what
ships without a PR: the shell job installs the Rust version rust-toolchain.toml names, and each bundled
JavaScript build has a lock file with a hash for every package."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_the_shell_is_built_with_the_pinned_rust_release():
    channel = tomllib.loads((ROOT / "app" / "src-tauri" / "rust-toolchain.toml").read_text(encoding="utf-8"))["toolchain"]["channel"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", channel), channel  # a release, never stable/beta/nightly
    shell = CI.split("\n  shell:\n", 1)[1]
    assert re.search(r'dtolnay/rust-toolchain@[0-9a-f]{40}[^\n]*\n\s+with:\n(?:\s+#[^\n]*\n)?\s+toolchain: "' + re.escape(channel) + '"', shell)
    assert "toolchain: stable" not in CI


def test_each_bundled_javascript_build_has_a_hashed_lock_file():
    for name in ("codemirror", "vega"):
        folder = ROOT / "app" / "ui" / "vendor" / name
        lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))
        wanted = json.loads((folder / "package.json").read_text(encoding="utf-8"))["devDependencies"]
        packages = {k: v for k, v in lock["packages"].items() if k}
        assert packages and all(v.get("integrity", "").startswith("sha512-") for v in packages.values()), name
        for dep, version in wanted.items():  # the lock holds exactly the versions package.json names
            assert packages[f"node_modules/{dep}"]["version"] == version, (name, dep)


def test_ci_tools_install_from_hashed_files():
    # A tool CI installs on its own (not the engine with its constraints, not a local package) comes from a
    # hashed file under .github/requirements, never a bare name==version that trusts PyPI's file that day.
    loose = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if "pip install" not in line or "-c constraints.txt" in line or "--target" in line:
                continue
            if "--no-deps --no-build-isolation -e ." in line:   # the engine itself, after its hashed packages
                continue
            if not re.search(r'--require-hashes -r "?(\.\./)?\.github', line):
                loose.append(f"{workflow.name}: {line.strip()}")
    assert not loose, loose
    for path in (ROOT / ".github" / "requirements").glob("*.txt"):
        pins = re.findall(r"^\S+==\S+ \\$", path.read_text(encoding="utf-8"), re.M)
        hashes = re.findall(r"--hash=sha256:[0-9a-f]{64}", path.read_text(encoding="utf-8"))
        assert pins and len(hashes) >= len(pins), path.name


def test_the_shells_crates_are_linted_and_gated_on_licence_and_source():
    """The shell job fails on any clippy warning; the audit job runs cargo-deny's licence, ban and source
    checks (deny.toml) beside cargo-audit, read for the Windows exe, with crates.io the only source."""
    shell = CI.split("\n  shell:\n", 1)[1].split("\n  preview-integrity:\n", 1)[0]
    assert "cargo clippy --release --locked --all-targets -- -D warnings" in shell
    assert "components: clippy" in shell
    toolchain = tomllib.loads((ROOT / "app" / "src-tauri" / "rust-toolchain.toml").read_text(encoding="utf-8"))
    assert "clippy" in toolchain["toolchain"]["components"]
    audit = CI.split("\n  audit:\n", 1)[1].split("\n  sbom-app:\n", 1)[0]
    assert "check licenses bans sources" in audit and "cargo audit --file app/src-tauri/Cargo.lock" in audit
    deny = tomllib.loads((ROOT / "app" / "src-tauri" / "deny.toml").read_text(encoding="utf-8"))
    assert deny["graph"]["targets"] == ["x86_64-pc-windows-msvc"]
    assert (deny["sources"]["unknown-registry"], deny["sources"]["unknown-git"]) == ("deny", "deny")
    assert deny["sources"]["allow-registry"] == ["https://github.com/rust-lang/crates.io-index"]
    assert deny["bans"]["wildcards"] == "deny"
    assert not any(lic.startswith(("GPL", "LGPL", "AGPL")) for lic in deny["licenses"]["allow"])


def _pins(path: Path) -> dict[str, str]:
    """{normalised name: version} of a requirements or constraints file."""
    found = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", path.read_text(encoding="utf-8"), re.M)
    return {re.sub(r"[-_.]+", "-", name).lower(): version for name, version in found}


def test_ci_installs_the_engines_packages_from_hashed_locks_at_constraints_versions():
    """The sidecar, lint, vscode-extension and ui-browser jobs install the engine's packages and test
    tools from hashed lock files (.github/requirements/test-<os>.txt, lint.txt), not from PyPI by
    constraints.txt's versions alone. The locks must say what constraints.txt says: a version bumped
    there and not regenerated here fails, with the command in each lock's header."""
    requirements = ROOT / ".github" / "requirements"
    constraints = _pins(ROOT / "sidecar" / "constraints.txt")
    ci_tools = _pins(ROOT / "sidecar" / "ci-constraints.txt")
    build = _pins(requirements / "engine-build.txt")
    locks = {name: _pins(requirements / f"{name}.txt") for name in ("test-linux", "test-windows", "lint")}
    for name, pins in locks.items():
        wrong = {p: (v, constraints.get(p, ci_tools.get(p))) for p, v in pins.items()
                 if v != constraints.get(p, ci_tools.get(p, build.get(p)))}
        # colorama comes with pytest on Windows only and constraints.txt (made on Linux) doesn't list it.
        wrong.pop("colorama", None)
        assert not wrong, f"{name}.txt doesn't match constraints.txt (lock, constraints): {wrong}; regenerate it"
        assert pins["setuptools"] == build["setuptools"], f"{name}.txt's setuptools isn't engine-build.txt's"
    missing = set(constraints) - set(locks["test-linux"]) - set(locks["test-windows"]) - {"pip"}
    assert not missing, f"constraints.txt pins {sorted(missing)}, which neither test lock installs; regenerate them"
    assert set(locks["test-windows"]) - set(locks["test-linux"]) <= {"colorama", "pymsalruntime", "pywin32"}
    assert set(ci_tools) <= set(locks["lint"])
    # No workflow installs anything by constraints alone any more; the editable engine adds no packages.
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        assert "-c constraints.txt" not in workflow.read_text(encoding="utf-8"), workflow.name
    assert "--require-hashes -r ../.github/requirements/test-linux.txt -r ../.github/requirements/tsql-live.txt" in (
        ROOT / ".github" / "workflows" / "tsql-contract-live.yml"
    ).read_text(encoding="utf-8")
    assert CI.count("--require-hashes -r ../.github/requirements/test-linux.txt") == 2
    assert '--require-hashes -r "../.github/requirements/$lock"' in CI and "lock=test-windows.txt" in CI
    assert "--require-hashes -r ../.github/requirements/lint.txt" in CI
    assert CI.count("python -m pip install --no-deps --no-build-isolation -e .") == 4
