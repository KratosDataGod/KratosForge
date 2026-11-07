"""The engine is installed from hashed pins that name the same versions as constraints.txt.

CI builds the bundled engine with ``pip install --require-hashes -r engine-requirements.txt``, so a
package file that changes after it was pinned stops the build (enterprise audit EA-020). The
hashed list is generated from constraints.txt; these checks catch a bump made in one file only.
"""

from __future__ import annotations

import re
from pathlib import Path

from packaging.utils import canonicalize_name

SIDECAR = Path(__file__).resolve().parents[1]
CI = (SIDECAR.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", re.M)


def _pins(name: str) -> dict[str, str]:
    return {canonicalize_name(n): v for n, v in PIN.findall((SIDECAR / name).read_text(encoding="utf-8"))}


def _hashed() -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {}
    current = None
    for line in (SIDECAR / "engine-requirements.txt").read_text(encoding="utf-8").splitlines():
        if (m := PIN.match(line)):
            current = canonicalize_name(m.group(1))
            entries[current] = []
        elif current and (h := re.search(r"--hash=sha256:([0-9a-f]+)", line)):
            entries[current].append(h.group(1))
    return entries


def test_every_engine_package_is_pinned_at_the_constraints_version():
    constraints, hashed = _pins("constraints.txt"), _pins("engine-requirements.txt")
    assert "pip" in hashed and "fabric-desktop" not in hashed
    wrong = {n: (v, constraints.get(n)) for n, v in hashed.items() if constraints.get(n) != v}
    assert not wrong, f"engine-requirements.txt and constraints.txt disagree: {wrong}"


def test_every_engine_package_has_a_sha256():
    bad = {n: hs for n, hs in _hashed().items() if not hs or any(len(h) != 64 for h in hs)}
    assert not bad


def test_the_engine_build_installs_with_require_hashes():
    assert "--require-hashes --no-deps -r sidecar\\engine-requirements.txt" in CI
    assert "-m pip check" in CI


def _engine_steps() -> list[str]:
    """The engine job's step names, in order."""
    job = CI.split("\n  engine:\n", 1)[1].split("\n  shell:\n", 1)[0]
    return [name or uses for name, uses in re.findall(r"^      - (?:name: (.+)|uses: (\S+))", job, re.M)]


def _hashed_file(path: Path) -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if (m := PIN.match(line)):
            current = canonicalize_name(m.group(1))
            entries[current] = []
        elif current and (h := re.search(r"--hash=sha256:([0-9a-f]{64})\b", line)):
            entries[current].append(h.group(1))
    return entries


def test_the_engine_jobs_build_tools_come_from_hashed_files():
    for name, tool in (("engine-build.txt", "setuptools"), ("sbom.txt", "cyclonedx-bom")):
        entries = _hashed_file(SIDECAR.parent / ".github" / "requirements" / name)
        assert tool in entries and all(entries.values()), name
        assert f"--require-hashes -r .github\\requirements\\{name}" in CI
    assert "pip wheel --no-build-isolation --no-deps" in CI
    assert "cyclonedx-bom==" not in CI  # installed only from the hashed file


def test_nothing_runs_on_the_engine_between_signing_and_its_hash():
    # The zip step hashes the engine; the shell bakes that hash in. A step between signing and the
    # zip could change the folder after it was checked, so the zip comes straight after signing.
    steps = _engine_steps()
    sign, zip_ = steps.index("Sign the engine's packages"), steps.index("Zip the engine")
    assert zip_ == sign + 1, steps[sign:zip_ + 1]
    assert steps.index("List every package in the engine (SBOM)") > zip_
