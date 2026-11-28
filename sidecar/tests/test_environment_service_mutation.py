"""environment_service.py paths that mutation testing (batch 77, docs/quality/mutation-testing.md) showed
no test pinned: which settings stop a package install and what each says, how the package source is named,
the "allowed" flag, uninstall and job pass-throughs, a new environment's name length, and which kernels
get a notebook's packages. The settings are stood in for; nothing here installs anything."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import environment_service as es
from fabric_desktop.kernel_proc import ProcessKernel


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def settings(monkeypatch, **values):
    base = {"allow_notebook_packages": True, "notebook_package_folder": "", "allow_pypi_packages": True,
            "kernel_process": True}
    base.update(values)
    monkeypatch.setattr(es.app_settings, "value", lambda key: base[key])


class Envs:
    def __init__(self, names=()):
        self.names, self.calls = list(names), []

    def list(self):
        return [{"name": n} for n in self.names]

    def of(self, notebook):
        return None

    def create(self, name):
        self.names.append(name)
        self.calls.append(("create", name))

    def attach(self, notebook, name):
        self.calls.append(("attach", notebook, name))
        return {"attached": name}

    def start(self, name, packages, **kw):
        self.calls.append(("start", name, packages, kw))
        return {"job": "j1"}

    def job(self, job_id):
        return {"job": job_id, "state": "done"}

    def paths_for(self, notebook):
        return ["lib"]


class Sessions:
    def __init__(self):
        self.asked = []

    def host_of(self, notebook):
        return None

    def guests(self, notebook):
        return []

    def session(self, notebook, create=True):
        self.asked.append(create)
        return None


class Svc(es.EnvironmentMixin):
    def __init__(self, envs=None):
        self._environments = envs or Envs()
        self.sessions = Sessions()


def test_packages_turned_off_by_the_organization_are_refused(monkeypatch):
    settings(monkeypatch, allow_notebook_packages=False)
    with pytest.raises(PermissionError, match="doesn't allow installing packages for notebooks on this PC"):
        Svc()._packages_allowed()


def test_a_package_folder_setting_that_cant_be_used_is_refused_with_its_reason(monkeypatch):
    settings(monkeypatch, notebook_package_folder="https://wheels.example/simple")
    with pytest.raises(PermissionError, match="package folder setting .*Ask IT to fix notebook_package_folder"):
        Svc()._packages_allowed()


def test_no_pypi_and_no_folder_is_refused(monkeypatch):
    settings(monkeypatch, allow_pypi_packages=False)
    with pytest.raises(PermissionError, match="doesn't allow installing packages from PyPI and names no package folder"):
        Svc()._packages_allowed()


def test_each_refusal_stops_an_install_before_anything_starts(monkeypatch):
    envs = Envs()
    settings(monkeypatch, allow_notebook_packages=False)
    with pytest.raises(PermissionError):
        Svc(envs).environment_install("Env", ["pandas"])
    assert envs.calls == []


@pytest.mark.parametrize("folder, pypi, words", [
    ("", True, "PyPI"),
    ("", False, "None: your organization doesn't allow PyPI and names no package folder"),
    ("D:\\wheels", True, "Your organization's package folder, then PyPI"),
    ("D:\\wheels", False, "Your organization's package folder only"),
])
def test_the_package_source_is_named_for_each_setting(monkeypatch, folder, pypi, words):
    settings(monkeypatch, notebook_package_folder=folder, allow_pypi_packages=pypi)
    assert es.EnvironmentMixin._package_source() == (folder, pypi, words)


def test_an_unset_package_folder_is_no_folder(monkeypatch):
    settings(monkeypatch, notebook_package_folder=None)
    assert es.EnvironmentMixin._package_source()[0] == ""


@pytest.mark.parametrize("value, allowed", [(None, True), (True, True), (False, False)])
def test_packages_are_allowed_unless_the_setting_is_false(monkeypatch, value, allowed):
    settings(monkeypatch, allow_notebook_packages=value)
    assert Svc().notebook_environments()["allowed"] is allowed


def test_uninstall_starts_a_removal_and_job_reads_the_job(monkeypatch):
    envs = Envs()
    svc = Svc(envs)
    assert svc.environment_uninstall("Env", ["pandas"]) == {"job": "j1"}
    assert envs.calls == [("start", "Env", ["pandas"], {"remove": True})]
    assert svc.environment_job("j9") == {"job": "j9", "state": "done"}


def test_a_new_environment_for_a_long_notebook_name_is_cut_to_50_characters(monkeypatch):
    settings(monkeypatch)
    envs = Envs()
    svc = Svc(envs)
    svc.notebook_packages_install("n" * 60, ["pandas"])
    assert envs.calls[0] == ("create", "n" * 50)


def test_attaching_looks_up_the_session_without_making_one(monkeypatch):
    svc = Svc()
    assert svc.environment_attach("nb", "Env") == {"attached": "Env", "restart_needed": False}
    assert svc.sessions.asked == [False]


def test_only_a_process_kernel_for_a_named_notebook_gets_the_notebooks_packages():
    svc = Svc()
    other = SimpleNamespace(calls={})
    svc._prepare_kernel("nb", other)
    assert other.calls == {} and not hasattr(other, "library_paths")
    proc = ProcessKernel.__new__(ProcessKernel)
    proc.calls = {}
    svc._prepare_kernel("", proc)
    assert proc.calls == {}
    svc._prepare_kernel("nb", proc)
    assert proc.library_paths == ["lib"] and set(proc.calls) == {"install_packages", "package_job"}
