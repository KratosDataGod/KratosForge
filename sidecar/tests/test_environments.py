"""Notebook environments (environments.py): packages a notebook adds on top of the engine's,
installed into a folder of its own and seen only by notebooks attached to it."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from test_prove import call, run_cell  # noqa: F401

from fabric_desktop.environments import Environments, check_name, check_packages
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
    if os.name == "nt":  # Windows runs .cmd files, not shebang scripts
        launcher = tmp_path / "fakepython.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
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


def test_windows_device_names_are_refused():
    for bad in ["CON", "nul", "Com1", "lpt9.env", "aux.txt", "con .txt", "prn  .x"]:
        with pytest.raises(ValueError, match="Windows keeps for a device"):
            check_name(bad)
    assert check_name("Console tools") == "Console tools" and check_name("com10") == "com10"


def test_an_environment_whose_folder_cant_be_made_isnt_listed(tmp_path):
    env = Environments(tmp_path)
    env.folder.write_text("not a folder")  # anything that stops the folder being made
    with pytest.raises(OSError):
        env.create("Data science")
    assert env.list() == []


def test_delete_clears_folders_an_earlier_delete_left(tmp_path):
    env = Environments(tmp_path)
    env.create("Old")
    stuck = env.folder / ".deleting-0000aaaa"  # a delete Windows couldn't finish (a file still open)
    (stuck / "site-packages").mkdir(parents=True)
    env.delete("Old")
    assert sorted(p.name for p in env.folder.iterdir()) == []


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
        assert run_cell(s, "import demo_pkg\ndemo_pkg.VERSION", notebook="A")["outputs"] == \
            [{"type": "text", "text": "'2.0'"}]
        out = run_cell(s, "import demo_pkg", notebook="B")
        assert out["status"] == "error" and "demo_pkg" in str(out["outputs"])
        # The engine's own packages come first: an environment can't replace them.
        last = run_cell(s, "import sys\nsys.path[-1]", notebook="A")["outputs"][0]["text"]
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


def test_percent_pip_in_a_cell_installs_waits_and_says_when_to_restart(tmp_path, monkeypatch, fake_python):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setattr("fabric_desktop.pip_magic.POLL_SECONDS", 0.05)
    s = Service(root=tmp_path / "app")
    s.environments.python = fake_python
    call(s, "save_app_settings", changes={"kernel_process": True})

    def cell(code, notebook="NB"):
        out = run_cell(s, code, notebook=notebook)
        return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    try:
        # No environment yet: one is made and attached; this session started without it.
        status, text = cell("%pip install -q demo-pkg==3.0")
        assert status == "ok" and "Installed demo-pkg==3.0 in environment NB" in text and "Restart the session" in text
        assert s.environments.of("NB") == "NB"
        # A new session has it; a further install is visible at once.
        for k in s.sessions.kernels():
            k.close()
        status, text = cell("import demo_pkg\nprint(demo_pkg.VERSION)")
        assert status == "ok" and "3.0" in text
        status, text = cell("%pip install other-pkg==1.0\nimport other_pkg\nprint(other_pkg.VERSION)")
        assert status == "ok" and "Restart the session" not in text and text.strip().endswith("1.0")
        status, text = cell("%pip install broken==1.0")
        assert status == "error" and "No matching distribution" in text
        status, text = cell("%pip install https://example.com/x.whl")
        assert status == "error"
        status, text = cell("%pip install --index-url https://evil/ x")
        assert status == "error" and "isn't supported" in text
    finally:
        for k in s.sessions.kernels():
            k.close()


def test_percent_pip_without_a_process_kernel_is_skipped_with_a_note():
    from fabric_desktop.notebook import Kernel
    out = Kernel().run("%pip install x==1")
    assert out["status"] == "ok" and "own process" in out["outputs"][0]["text"]
    out = Kernel().run("%conda install x")
    assert out["status"] == "error" and "%pip install" in out["outputs"][-1]["evalue"]


def test_percent_pip_reads_a_requirements_file_from_the_resources(tmp_path):
    from fabric_desktop import pip_magic
    res = tmp_path / "N.resources" / "builtin"
    res.mkdir(parents=True)
    (res / "requirements.txt").write_text("# pins\nopenpyxl==3.1.5\n\nrich>=13  # pretty\n")
    read = lambda p: pip_magic.read_text(p, str(tmp_path / "N.resources"))  # noqa: E731
    assert pip_magic.specs(["-q", "-r", "builtin/requirements.txt", "x"], read) == ["openpyxl==3.1.5", "rich>=13", "x"]
    (res / "bad.txt").write_text("--extra-index-url https://evil/\nx\n")
    with pytest.raises(ValueError, match="isn't supported"):
        pip_magic.specs(["-r", "builtin/bad.txt"], read)


def test_percent_pip_reads_combined_flags_attached_requirement_files_and_pips_line_rules():
    from fabric_desktop import pip_magic
    files = {"r.txt": "a  # note\n#whole line\nb#egg\nc \\\r\n  >=1\r\npandas>=2 ; python_version > \"3\"\n",
             "hash.txt": "polars==1.9 \\\n    --hash=sha256:abc\n",
             "index.txt": "x --extra-index-url https://evil/\n"}
    read = files.__getitem__
    wanted = ["a", "b#egg", "c   >=1", 'pandas>=2 ; python_version > "3"']
    assert pip_magic.specs(["-qq", "-Uq", "-rr.txt"], read) == wanted
    assert pip_magic.specs(["--requirement=r.txt", "-v", "y"], read) == wanted + ["y"]
    for bad in ("hash.txt", "index.txt"):  # an option after a package on the same line is still an option
        with pytest.raises(ValueError, match="isn't supported"):
            pip_magic.specs(["-r", bad], read)
    with pytest.raises(ValueError, match="-qx isn't supported"):
        pip_magic.specs(["-qx", "y"], read)


def test_removing_a_package_leaves_nothing_that_imports(tmp_path, fake_python):
    env = Environments(tmp_path, python=fake_python)
    env.create("Removal")
    assert wait(env, env.start("Removal", ["demo-pkg==1.0"]))["state"] == "done"
    target = env.site_packages("Removal")
    cache = target / "demo_pkg" / "__pycache__"  # what importing it leaves, not listed in its RECORD
    cache.mkdir()
    (cache / "__init__.cpython-311.pyc").write_bytes(b"")
    (target / "other").mkdir()  # another package's folder stays
    assert wait(env, env.start("Removal", ["demo-pkg"], remove=True))["state"] == "done"
    assert not (target / "demo_pkg").exists() and (target / "other").is_dir()


def test_an_install_cant_bring_back_an_environment_deleted_as_it_starts(tmp_path, fake_python):
    import threading
    env = Environments(tmp_path, python=fake_python)
    env.create("Racy")
    result = {}

    def start():
        try:
            result["job"] = env.start("Racy", ["demo-pkg==1.0"])
        except ValueError as exc:
            result["error"] = str(exc)
    with env._lock:  # a delete is under way: start waits for it
        t = threading.Thread(target=start)
        t.start()
        time.sleep(0.2)
        data = env._read()
        data["environments"].pop("Racy")
        env._write(data)
    t.join(10)
    assert "no environment called Racy" in result.get("error", ""), result
    assert env.list() == []
