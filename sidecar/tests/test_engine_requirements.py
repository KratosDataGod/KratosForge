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
