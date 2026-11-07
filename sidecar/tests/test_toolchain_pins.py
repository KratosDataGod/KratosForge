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
            if "--require-hashes -r .github" not in line:
                loose.append(f"{workflow.name}: {line.strip()}")
    assert not loose, loose
    for path in (ROOT / ".github" / "requirements").glob("*.txt"):
        pins = re.findall(r"^\S+==\S+ \\$", path.read_text(encoding="utf-8"), re.M)
        hashes = re.findall(r"--hash=sha256:[0-9a-f]{64}", path.read_text(encoding="utf-8"))
        assert pins and len(hashes) >= len(pins), path.name
