"""The Python the engine says it needs is the Python the app ships and CI tests on (#1330).

pyproject.toml said 3.11 or later while the app shipped 3.12.10, every CI job ran 3.12, and
constraints.txt pinned bokeh 3.10.1, which won't install on 3.11. So the floor named a Python
nothing tested and the pinned install couldn't use. These checks keep every place that names the
floor on the same version: change them together, after CI runs the tests on the new version.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

SIDECAR = Path(__file__).resolve().parents[1]
REPO = SIDECAR.parent
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _floor() -> tuple[int, int]:
    spec = SpecifierSet(tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))
                        ["project"]["requires-python"])
    [lower] = [s for s in spec if s.operator == ">="]
    v = Version(lower.version)
    return v.major, v.minor


def _min_python(path: Path) -> tuple[int, int]:
    [found] = re.findall(r"^MIN_PYTHON = \((\d+), (\d+)\)", path.read_text(encoding="utf-8"), re.M)
    return int(found[0]), int(found[1])


def test_the_floor_is_the_python_the_app_ships():
    [shipped] = re.findall(r'PYTHON_VERSION: "(\d+\.\d+)\.\d+"', CI)
    assert _floor() == tuple(map(int, shipped.split(".")))


def test_every_ci_job_runs_the_floor_python():
    versions = set(re.findall(r'python-version: "([^"]+)"', CI))
    assert versions == {"{}.{}".format(*_floor())}


def test_the_startup_checks_ask_for_the_same_python():
    assert _min_python(REPO / "app" / "src-tauri" / "src" / "updater.py") == _floor()
    assert _min_python(SIDECAR / "fabric_desktop" / "dbx_livetest.py") == _floor()


def test_the_type_check_runs_as_the_floor_python():
    mypy = tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]
    assert mypy["python_version"] == "{}.{}".format(*_floor())


def test_what_people_read_names_the_same_python():
    want = "{}.{} or later".format(*_floor())
    for path in (REPO / "README.md", REPO / "app" / "ui" / "app.js", REPO / "docs" / "install.md",
                 REPO / "docs" / "architecture.md", REPO / "docs" / "live-testing.md"):
        named = re.findall(r"(?<![\d.])3\.\d+ or later", path.read_text(encoding="utf-8"))
        assert named and set(named) == {want}, (path.name, named)
