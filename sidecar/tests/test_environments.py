"""Notebook environments (environments.py): packages a notebook adds on top of the engine's,
installed into a folder of its own and seen only by notebooks attached to it."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from test_prove import call  # noqa: F401

from fabric_desktop.environments import Environments, check_packages
from fabric_desktop.service import Service

# Stands in for "python -m pip install --target T ... demo-pkg==1.0": writes the package and its
# dist-info (with a RECORD, as pip does) and checks the engine's versions were passed as constraints.
FAKE_PIP = r'''
import sys, pathlib
args = sys.argv[1:]
assert args[:2] == ["-m", "pip"] and args[2] == "install", args
target = pathlib.Path(args[args.index("--target") + 1])
pins = pathlib.Path(args[args.index("-c") + 1]).read_text()
assert "duckdb==" in pins and "polars==" in pins
for spec in [a for a in args if "==" in a and not a.startswith("-")]:
    name, version = spec.split("==")
    if name == "broken":
        print("ERROR: No matching distribution found for broken", file=sys.stderr)
        sys.exit(1)
    mod = name.replace("-", "_")
    (target / mod).mkdir(parents=True, exist_ok=True)
    (target / mod / "__init__.py").write_text(f"VERSION = {version!r}\n")
    info = target / f"{mod}-{version}.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    (info / "RECORD").write_text(f"{mod}/__init__.py,,\n{info.name}/METADATA,,\n{info.name}/RECORD,,\n")
print("Successfully installed", " ".join(args[args.index("-c") + 2:]))
'''


@pytest.fixture
def fake_python(tmp_path, monkeypatch):
    script = tmp_path / "fakepy.py"
    script.write_text(FAKE_PIP)
    launcher = tmp_path / "fakepython"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    launcher.chmod(0o755)
    return str(launcher)


def wait(env: Environments, job: dict, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while job["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        job = env.job(job["id"])
    return job


def test_only_package_names_are_accepted():
    assert check_packages(["scikit-learn==1.5.2", " polars-ds ", "great_expectations[spark]>=1"]) == \
        ["scikit-learn==1.5.2", "polars-ds", "great_expectations[spark]>=1"]
    for bad in ["pyspark==3.5.1", "databricks_connect", "--index-url=http://evil", "-r req.txt", "https://x/y.whl", "./local", "pkg @ file:///c", "not a pkg!",
                "pandas-2.0-py3-none-any.whl", "mylib.tar.gz", "mylib.ZIP"]:
        with pytest.raises(ValueError):
            check_packages([bad])


def test_install_list_and_remove(tmp_path, fake_python):
    env = Environments(tmp_path, python=fake_python)
    env.create("Data science")
    with pytest.raises(ValueError, match="already"):
        env.create("data science")
    job = wait(env, env.start("Data science", ["demo-pkg==1.0"]))
    assert job["state"] == "done", job["log"]
    [listed] = env.list()
    assert listed["packages"] == ["demo-pkg==1.0"]
    assert listed["installed"] == [{"name": "demo-pkg", "version": "1.0"}]
    bad = wait(env, env.start("Data science", ["broken==1"]))
    assert bad["state"] == "failed" and "No matching distribution" in bad["log"]
    assert env.list()[0]["packages"] == ["demo-pkg==1.0"]  # a failed install changes nothing recorded
    gone = wait(env, env.start("Data science", ["demo-pkg"], remove=True))
    assert gone["state"] == "done" and env.list()[0]["installed"] == []
    assert not (env.site_packages("Data science") / "demo_pkg" / "__init__.py").exists()


def test_attached_notebook_imports_the_package_and_others_dont(tmp_path, monkeypatch, fake_python):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    s.environments.python = fake_python
    try:
        with pytest.raises(Exception, match="own process"):
            call(s, "environment_install", name="x", packages=["demo-pkg==1.0"])
        call(s, "save_app_settings", changes={"kernel_process": True})
        call(s, "environment_create", name="DS")
        job = wait(s.environments, call(s, "environment_install", name="DS", packages=["demo-pkg==2.0"]))
        assert job["state"] == "done", job["log"]
        assert call(s, "environment_attach", notebook="A", name="DS") == {"notebook": "A", "environment": "DS",
                                                                         "restart_needed": False}
        assert call(s, "run_cell", code="import demo_pkg\ndemo_pkg.VERSION", notebook="A")["outputs"] == \
            [{"type": "text", "text": "'2.0'"}]
        out = call(s, "run_cell", code="import demo_pkg", notebook="B")
        assert out["status"] == "error" and "demo_pkg" in str(out["outputs"])
        # The engine's own packages come first: an environment can't replace them.
        last = call(s, "run_cell", code="import sys\nsys.path[-1]", notebook="A")["outputs"][0]["text"]
        assert last.strip("'").replace("\\\\", "\\") == str(s.environments.site_packages("DS"))
        assert call(s, "notebook_environments", notebook="A")["current"] == "DS"
        assert call(s, "environment_delete", name="DS") == {"deleted": "DS", "detached": ["A"]}
    finally:
        for k in s.sessions.kernels():
            k.close()


def test_percent_pip_from_a_cell_installs_into_the_notebooks_environment(tmp_path, monkeypatch, fake_python):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    s.environments.python = fake_python
    call(s, "save_app_settings", changes={"kernel_process": True})
    try:
        # The kernel asks the engine through its channel, as %pip will.
        kernel = s.sessions.kernel("Sales: daily")
        job = kernel.calls["install_packages"](["demo-pkg==3.0"])
        job = wait(s.environments, job)
        assert job["state"] == "done" and job["environment"] == "Sales_ daily"
        assert s.environments.of("Sales: daily") == "Sales_ daily"
        monkeypatch.setattr("fabric_desktop.app_settings.value",
                            lambda key: False if key == "allow_notebook_packages" else True)
        with pytest.raises(PermissionError, match="organization"):
            kernel.calls["install_packages"](["demo-pkg==3.1"])
    finally:
        for k in s.sessions.kernels():
            k.close()


def test_an_environment_in_use_isnt_half_deleted(tmp_path, monkeypatch, fake_python):
    env = Environments(tmp_path, python=fake_python)
    env.create("Data science")
    env.attach("NB", "Data science")
    real = Path.rename

    def in_use(self, target):  # as on Windows, while a notebook's process has its files open
        if self.name == "Data science":
            raise PermissionError("in use")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", in_use)
    with pytest.raises(RuntimeError, match="Stop its session"):
        env.delete("Data science")
    assert [e["name"] for e in env.list()] == ["Data science"] and env.of("NB") == "Data science"
    assert env.site_packages("Data science").is_dir()
    monkeypatch.setattr(Path, "rename", real)
    assert env.delete("Data science") == {"deleted": "Data science", "detached": ["NB"]}
    assert env.list() == [] and not any(env.folder.iterdir())
