"""Where notebook environments install packages from (environments.install_commands): the policy's
approved wheel folder first, then PyPI only when the policy allows it."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from fabric_desktop import app_settings
from fabric_desktop.environments import Environments, install_commands
from fabric_desktop.service import Service

# Stands in for pip: records every call, and finds a package only in the --find-links folder
# (when --no-index) or "on PyPI" (any name starting with "pypi-").
FAKE_PIP = r'''
import json, os, sys, pathlib
args = sys.argv[1:]
with open(os.environ["FAKE_PIP_CALLS"], "a") as f:
    f.write(json.dumps(args) + "\n")
target = pathlib.Path(args[args.index("--target") + 1])
folder = pathlib.Path(args[args.index("--find-links") + 1]) if "--find-links" in args else None
for spec in [a for a in args if "==" in a and not a.startswith("-")]:
    name, version = spec.split("==")
    if name == "bad-build":
        print("error: subprocess-exited-with-error", file=sys.stderr)
        sys.exit(1)
    in_folder = folder is not None and any(folder.glob(name.replace("-", "_") + "-*.whl"))
    if not in_folder and ("--no-index" in args or not name.startswith("pypi-")):
        print(f"ERROR: No matching distribution found for {spec}", file=sys.stderr)
        sys.exit(1)
    (target / name.replace("-", "_")).mkdir(parents=True, exist_ok=True)
print("Successfully installed")
'''


@pytest.fixture
def fake_python(tmp_path, monkeypatch):
    script = tmp_path / "fakepy.py"
    script.write_text(FAKE_PIP)
    calls = tmp_path / "calls.jsonl"
    calls.write_text("")
    monkeypatch.setenv("FAKE_PIP_CALLS", str(calls))
    if os.name == "nt":
        launcher = tmp_path / "fakepython.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        launcher = tmp_path / "fakepython"
        launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
        launcher.chmod(0o755)
    return str(launcher), calls


def calls_of(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def wait(env: Environments, job: dict, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while job["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        job = env.job(job["id"])
    return job


def wheel(folder: Path, name: str, version: str = "1.0") -> Path:
    """A real, tiny pure-Python wheel pip can install with no network."""
    mod = name.replace("-", "_")
    dist = f"{mod}-{version}.dist-info"
    files = {f"{mod}/__init__.py": f"VERSION = {version!r}\n",
             f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
             f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(text.encode())}")
    record.append(f"{dist}/RECORD,,")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{mod}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(out, "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
        z.writestr(f"{dist}/RECORD", "\n".join(record) + "\n")
    return out


def test_without_a_folder_the_command_is_as_before(tmp_path):
    cmds = install_commands("py", tmp_path / "t", tmp_path / "c.txt", ["a==1"])
    assert len(cmds) == 1 and "--no-index" not in cmds[0] and "--find-links" not in cmds[0] and cmds[0][-1] == "a==1"


def test_the_folder_is_tried_alone_first_then_pypi_with_the_folder_still_offered(tmp_path):
    first, then = install_commands("py", tmp_path / "t", tmp_path / "c.txt", ["a==1"], folder="D:\\wheels")
    assert first[first.index("--find-links") + 1] == "D:\\wheels" and "--no-index" in first
    assert "--no-index" not in then and then[then.index("--find-links") + 1] == "D:\\wheels"
    assert "-c" in first and "-c" in then  # the engine's versions still constrain both
    assert install_commands("py", tmp_path / "t", tmp_path / "c.txt", ["a==1"], folder="D:\\wheels", pypi=False) == [first]


def test_a_package_in_the_folder_never_reaches_pypi(tmp_path, fake_python):
    python, calls = fake_python
    folder = tmp_path / "wheels"
    wheel(folder, "approved-pkg")
    env = Environments(tmp_path / "data", python=python)
    env.create("A")
    job = wait(env, env.start("A", ["approved-pkg==1.0"], folder=str(folder), pypi=True))
    assert job["state"] == "done", job["log"]
    assert [("--no-index" in c) for c in calls_of(calls)] == [True]
    assert "From your organization's package folder" in job["log"]


def test_a_package_not_in_the_folder_goes_to_pypi_only_when_allowed(tmp_path, fake_python):
    python, calls = fake_python
    folder = tmp_path / "wheels"
    folder.mkdir()
    env = Environments(tmp_path / "data", python=python)
    env.create("A")
    job = wait(env, env.start("A", ["pypi-pkg==1.0"], folder=str(folder), pypi=True))
    assert job["state"] == "done", job["log"]
    assert [("--no-index" in c) for c in calls_of(calls)] == [True, False]
    assert "From PyPI, after your organization's package folder" in job["log"]

    calls.write_text("")
    job = wait(env, env.start("A", ["pypi-pkg==1.0"], folder=str(folder), pypi=False))
    assert job["state"] == "failed"
    assert [("--no-index" in c) for c in calls_of(calls)] == [True]  # one try, no index
    assert "isn't in your organization's package folder" in job["log"]


def test_a_package_that_is_nowhere_fails_after_both_tries(tmp_path, fake_python):
    python, calls = fake_python
    folder = tmp_path / "wheels"
    folder.mkdir()
    env = Environments(tmp_path / "data", python=python)
    env.create("A")
    job = wait(env, env.start("A", ["nowhere==1"], folder=str(folder), pypi=True))
    assert job["state"] == "failed" and len(calls_of(calls)) == 2


def test_a_failure_other_than_not_found_doesnt_send_the_install_to_pypi(tmp_path, fake_python):
    python, calls = fake_python
    folder = tmp_path / "wheels"
    wheel(folder, "bad-build")
    env = Environments(tmp_path / "data", python=python)
    env.create("A")
    job = wait(env, env.start("A", ["bad-build==1.0"], folder=str(folder), pypi=True))
    assert job["state"] == "failed" and [("--no-index" in c) for c in calls_of(calls)] == [True]


def test_a_folder_that_cant_be_reached_stops_the_install_without_pypi(tmp_path, fake_python):
    python, calls = fake_python
    env = Environments(tmp_path / "data", python=python)
    env.create("A")
    job = wait(env, env.start("A", ["pypi-pkg==1.0"], folder=str(tmp_path / "gone"), pypi=True))
    assert job["state"] == "failed" and "can't be reached" in job["log"]
    assert calls_of(calls) == []


def test_a_real_pip_installs_a_wheel_from_the_folder_with_no_index(tmp_path):
    folder = tmp_path / "wheels"
    wheel(folder, "kf-folder-demo", "2.5")
    probe = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("this Python has no pip")
    env = Environments(tmp_path / "data")
    env.create("A")
    job = wait(env, env.start("A", ["kf-folder-demo==2.5"], folder=str(folder), pypi=False), timeout=120)
    assert job["state"] == "done", job["log"]
    assert [d["name"] for d in env.installed("A")] == ["kf-folder-demo"]


@pytest.mark.parametrize("folder, ok", [("D:\\wheels", True), ("\\\\server\\share\\wheels", True), ("/srv/wheels", True),
                                        ("wheels", False), ("https://pkgs.contoso.com/simple", False),
                                        ("file:///D:/wheels", False)])
def test_the_policy_folder_must_be_a_full_path_and_not_a_web_address(folder, ok):
    assert (app_settings.package_folder_problem(folder) is None) is ok


def policy(tmp_path, monkeypatch, data):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(app_settings, "policy_path", lambda: path)
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user" / "app-settings.json")
    monkeypatch.setattr(app_settings, "user_can_change", lambda p: False, raising=False)


def test_allow_pypi_packages_of_the_wrong_kind_is_read_as_false(tmp_path, monkeypatch):
    policy(tmp_path, monkeypatch, {"allow_pypi_packages": "true"})
    assert app_settings.value("allow_pypi_packages") is False


def test_the_service_passes_the_policys_source_and_says_it(tmp_path, monkeypatch):
    folder = tmp_path / "wheels"
    folder.mkdir()
    policy(tmp_path, monkeypatch, {"notebook_package_folder": str(folder), "allow_pypi_packages": False,
                                   "kernel_process": True})
    svc = Service(root=tmp_path / "root")
    seen = {}
    monkeypatch.setattr(svc.environments, "start", lambda name, specs, **kw: seen.update(kw) or {"id": "x"})
    svc.environment_create("A")
    svc.environment_install("A", ["a==1"])
    assert seen == {"folder": str(folder), "pypi": False}
    assert svc.notebook_environments()["source"] == "Your organization's package folder only"


def test_no_folder_and_no_pypi_means_no_installs(tmp_path, monkeypatch):
    policy(tmp_path, monkeypatch, {"allow_pypi_packages": False, "kernel_process": True})
    svc = Service(root=tmp_path / "root")
    svc.environment_create("A")
    with pytest.raises(PermissionError, match="names no package folder"):
        svc.environment_install("A", ["a==1"])


def test_a_policy_folder_that_is_a_web_address_is_reported_and_refused(tmp_path, monkeypatch):
    policy(tmp_path, monkeypatch, {"notebook_package_folder": "https://pkgs.contoso.com/simple", "kernel_process": True})
    assert any("notebook_package_folder is a web address" in p for p in app_settings.policy_problems())
    svc = Service(root=tmp_path / "root")
    svc.environment_create("A")
    with pytest.raises(PermissionError, match="web address"):
        svc.environment_install("A", ["a==1"])


def test_no_folder_and_no_pypi_never_runs_pip(tmp_path, fake_python):
    python, calls = fake_python
    env = Environments(tmp_path / "data", python=python)
    env.create("A")
    job = wait(env, env.start("A", ["pypi-pkg==1.0"], folder="", pypi=False))
    assert job["state"] == "failed" and calls_of(calls) == []
