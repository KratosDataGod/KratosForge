"""``%pip`` in a cell (pip_magic.run): the commands it answers itself, the ones it refuses, and an install
followed to the end through the engine. The engine is stood in for, so nothing is installed."""

import importlib.metadata

import pytest

from fabric_desktop import pip_magic


def no_file(path):
    raise AssertionError(f"no requirements file should be read, got {path}")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(pip_magic, "POLL_SECONDS", 0)


class Engine:
    """install_packages and package_job: a job that runs for ``polls`` polls, then ends in ``final``."""

    def __init__(self, polls=2, final="done", log=""):
        self.polls, self.final, self.log, self.calls = polls, final, log, []

    def __call__(self, method, *args):
        self.calls.append((method, args))
        if method == "install_packages":
            return {"id": "job-1", "state": "running", "environment": "nb-sales"}
        self.polls -= 1
        state = self.final if self.polls <= 0 else "running"
        return {"id": "job-1", "state": state, "environment": "nb-sales", "log": self.log}


# --------------------------------------------------------------------------- what %pip answers itself

def test_list_and_freeze_show_this_sessions_packages_sorted(capsys):
    pip_magic.run("list", None, no_file)
    listed = capsys.readouterr().out.splitlines()
    names = [line.split()[0].lower() for line in listed]
    assert names == sorted(names) and "pytest" in names
    pip_magic.run("freeze", None, no_file)
    frozen = capsys.readouterr().out.splitlines()
    assert f"pytest=={importlib.metadata.version('pytest')}" in frozen


def test_show_names_each_package_and_warns_for_one_that_isnt_there(capsys):
    pip_magic.run("show pytest no-such-package-kf", None, no_file)
    out = capsys.readouterr().out
    assert f"Name: pytest\nVersion: {importlib.metadata.version('pytest')}\nSummary:" in out
    assert "WARNING: Package(s) not found: no-such-package-kf" in out


@pytest.mark.parametrize("line, message", [
    ("uninstall polars", "Environment page"),
    ("download polars", "install, list, freeze and show"),
    ("", "install, list, freeze and show"),
    ("install", "needs packages"),
    ("install --index-url https://example.org/simple x", "isn't supported on this PC"),
    ("install -r", "needs a file"),
    ("install 'polars", "couldn't read"),
])
def test_what_pip_wont_do_in_a_cell_is_refused_in_words(line, message):
    engine = Engine()
    with pytest.raises(ValueError, match=message):
        pip_magic.run(line, engine, no_file)
    assert engine.calls == []  # nothing reached the engine


def test_without_its_own_process_an_install_is_skipped_with_a_note(capsys):
    pip_magic.run("install openpyxl==3.1.5", None, no_file)
    assert "This line was skipped" in capsys.readouterr().out


# --------------------------------------------------------------------------- an install through the engine

def test_an_install_is_followed_until_pip_finishes(capsys):
    engine = Engine(polls=3)
    pip_magic.run("install -q pytest", engine, no_file)
    assert engine.calls[0] == ("install_packages", (["pytest"],))
    assert [c[0] for c in engine.calls[1:]] == ["package_job"] * 3 and engine.calls[1][1] == ("job-1",)
    out = capsys.readouterr().out
    assert out.startswith("Installing pytest into environment nb-sales...")
    assert "Installed pytest in environment nb-sales." in out
    assert "already imported needs a restart" in out  # pytest is visible to this session


def test_a_package_this_session_cant_see_yet_asks_for_a_restart(capsys):
    pip_magic.run("install no-such-package-kf==1.0", Engine(polls=1), no_file)
    assert "Restart the session to use them" in capsys.readouterr().out


def test_a_failed_install_shows_the_end_of_pips_log():
    log = "x" * 5000 + "\nERROR: No matching distribution found for nothing-kf\n"
    with pytest.raises(RuntimeError) as err:
        pip_magic.run("install nothing-kf", Engine(polls=1, final="failed", log=log), no_file)
    text = str(err.value)
    assert text.startswith("pip couldn't install nothing-kf:\n") and text.endswith("found for nothing-kf")
    assert len(text) < 4100  # only the last 4000 characters of the log


def test_a_failed_install_without_a_log_still_says_so():
    with pytest.raises(RuntimeError, match="pip couldn't install nothing-kf:\n$"):
        pip_magic.run("install nothing-kf", Engine(polls=1, final="failed", log=None), no_file)


def test_an_install_that_never_ends_gives_up(monkeypatch):
    monkeypatch.setattr(pip_magic, "GIVE_UP_SECONDS", -1)
    engine = Engine(polls=10)
    with pytest.raises(TimeoutError, match="Environment page shows the install"):
        pip_magic.run("install slow-kf", engine, no_file)
    assert [c[0] for c in engine.calls] == ["install_packages"]


def test_requirements_files_are_read_for_the_install(capsys):
    read = []

    def read_file(path):
        read.append(path)
        return "pytest  # the test runner\n\n"
    engine = Engine(polls=1)
    pip_magic.run("install -r builtin/requirements.txt", engine, read_file)
    assert read == ["builtin/requirements.txt"] and engine.calls[0] == ("install_packages", (["pytest"],))


# --------------------------------------------------------------------------- names and files

@pytest.mark.parametrize("spec, visible", [("pytest>=8", True), ("pytest[extra]", True), ("  pytest", True),
                                           ("no-such-package-kf", False), ("==1.0", False), ("", False)])
def test_a_package_is_visible_by_its_name_whatever_its_version(spec, visible):
    assert pip_magic._visible(spec) is visible


def test_a_requirements_file_on_this_pc_is_read_without_notepads_bom(tmp_path):
    path = tmp_path / "req.txt"
    path.write_bytes("﻿polars==1.9.0\n".encode())
    assert pip_magic.read_text(str(path), "") == "polars==1.9.0\n"


def test_a_requirements_file_in_builtin_comes_from_the_notebooks_resources(tmp_path):
    resources = tmp_path / "Sales.resources"
    (resources / "builtin").mkdir(parents=True)
    (resources / "builtin" / "requirements.txt").write_text("rich>=13\n")
    assert pip_magic.read_text("builtin/requirements.txt", str(resources)) == "rich>=13\n"
