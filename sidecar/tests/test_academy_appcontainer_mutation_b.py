"""Mutation tests for academy/appcontainer.py (batch 453): the parts that run on any PC, with Windows stood
in for (icacls, the ACL helpers, the capability SID and the profile are fakes). Nothing here changes an
ACL or starts an AppContainer; test_academy_appcontainer does that on Windows."""

from __future__ import annotations

import json
import ntpath
import os
import types
from pathlib import Path

import pytest

from fabric_desktop import auth
from fabric_desktop.academy import acl
from fabric_desktop.academy import appcontainer as ac
from fabric_desktop.academy import workspace as ws


def _done(code=0):
    return types.SimpleNamespace(returncode=code)


# ---- status and icacls


def test_status_says_why_not_off_windows_and_on_windows(monkeypatch):
    monkeypatch.setattr(ac, "_why_not", "")
    monkeypatch.setattr(ac.sys, "platform", "linux")
    assert ac.status() == {"available": False, "why_not": "AppContainers are a Windows feature."}
    monkeypatch.setattr(ac.sys, "platform", "win32")
    assert ac.status() == {"available": True, "why_not": ""}
    monkeypatch.setattr(ac, "_why_not", "policy")
    assert ac.status() == {"available": False, "why_not": "policy"}


def test_icacls_runs_with_a_minute_limit_and_answers_the_result(monkeypatch):
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, **kw)
        return "result"

    monkeypatch.setattr("subprocess.run", run)
    assert ac._icacls("a", "/b") == "result"
    assert seen["argv"][1:] == ["a", "/b", "/Q"] and seen["timeout"] == 60 and seen["check"] is False


def test_deny_app_packages_denies_both_sids_once_and_refuses_a_failure(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ac, "_denied", set())
    monkeypatch.setattr(ac, "_icacls", lambda *a: calls.append(a) or _done(0))
    ac.deny_app_packages([tmp_path])
    ac.deny_app_packages([tmp_path])
    assert [c[2] for c in calls] == [f"*{s}:(OI)(CI)(RX)" for s in ac.APP_PACKAGES]
    monkeypatch.setattr(ac, "_icacls", lambda *a: _done(1))
    with pytest.raises(OSError, match="keep exercises out of other"):
        ac.deny_app_packages([tmp_path / "other"])


def test_a_grant_is_made_only_when_missing_and_a_failure_names_the_path(monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(acl, "grant", lambda p, s, i: made.append((p, s, i)))
    monkeypatch.setattr(acl, "has_grant", lambda p, s, i: True)
    ac._grant(None, tmp_path / "a", "S-1", True)
    assert made == []
    monkeypatch.setattr(acl, "has_grant", lambda p, s, i: False)
    ac._grant(None, tmp_path / "a", "S-1", True)
    assert made == [(tmp_path / "a", "S-1", True)]

    def refuse(p, s, i):
        raise OSError("denied")

    monkeypatch.setattr(acl, "grant", refuse)
    with pytest.raises(OSError, match="wouldn't let the exercise runtime read a"):
        ac._grant(None, tmp_path / "a", "S-1", True)


# ---- the learner packages and the runtime


def _dist(files, requires=()):
    return types.SimpleNamespace(files=list(files), requires=list(requires))


def test_distributions_are_the_installed_ones_by_lowercase_hyphenated_name():
    got = ac._distributions()
    assert "pytest" in got and got["pytest"].metadata["Name"].lower() == "pytest"
    assert all(name == name.lower() and "_" not in name for name in got)


def test_learner_paths_follow_requirements_and_leave_out_engine_names(monkeypatch, tmp_path):
    for name in ("polars", "polars-1.0.dist-info", "pyarrow", "fabric_desktop"):
        (tmp_path / name).mkdir()
    dists = {"polars": _dist(["polars/__init__.py", "polars-1.0.dist-info/RECORD", "../bin/x",
                              "fabric_desktop/x.py"], ["pyarrow>=1", "pandas; extra == 'all'"]),
             "pyarrow": _dist(["pyarrow/x.py"])}
    monkeypatch.setattr(ac, "_distributions", lambda: dists)
    monkeypatch.setattr(ac, "LEARNER_PACKAGES", ("polars",))
    assert ac.learner_paths(tmp_path) == [tmp_path / "polars", tmp_path / "polars-1.0.dist-info",
                                          tmp_path / "pyarrow"]


def test_learner_paths_pass_over_a_requirement_not_installed_and_one_already_seen(monkeypatch, tmp_path):
    for name in ("polars", "pyarrow"):
        (tmp_path / name).mkdir()
    dists = {"polars": _dist(["polars/__init__.py"], ["pyarrow", "numpy>=2"]),  # numpy isn't installed
             "pyarrow": _dist(["pyarrow/x.py"], ["polars"])}  # and pyarrow needs polars again
    monkeypatch.setattr(ac, "_distributions", lambda: dists)
    monkeypatch.setattr(ac, "LEARNER_PACKAGES", ("polars",))
    assert ac.learner_paths(tmp_path) == [tmp_path / "polars", tmp_path / "pyarrow"]


def test_runtime_paths_name_the_interpreter_stdlib_sites_and_a_venv(monkeypatch, tmp_path):
    base, venv = tmp_path / "py", tmp_path / "venv"
    lib, site, extra = base / "Lib", base / "Lib" / "site-packages", tmp_path / "x" / "site-packages"
    for d in (base / "DLLs", base / "tools", site, extra, venv, tmp_path / "plain"):
        d.mkdir(parents=True)
    (base / "python.exe").write_text("", encoding="utf-8")
    (lib / "os.py").write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("", encoding="utf-8")
    import sysconfig
    monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": str(site), "platlib": str(site), "stdlib": str(lib)})
    monkeypatch.setattr(ac.sys, "base_prefix", str(base))
    monkeypatch.setattr(ac.sys, "prefix", str(venv))
    monkeypatch.setattr(ac.sys, "path", [str(extra), str(tmp_path / "plain"), ""])
    monkeypatch.setattr(ac, "learner_paths", lambda s: [s / "pkg"] if s == site else [])
    got = ac.runtime_paths()   # a folder's entries come in the file system's order
    assert sorted(got, key=str) == sorted([(base, False), (base / "python.exe", False), (base / "DLLs", True), (lib, False),
                   (lib / "os.py", False), (extra.resolve(), False), (site.resolve(), False),
                   (site.resolve() / "pkg", False), (venv, False), (venv / "pyvenv.cfg", False)], key=str)


# ---- granting the runtime


@pytest.fixture
def granting(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.write_text("x", encoding="utf-8")
    calls = []
    monkeypatch.setattr(ac, "_granted", {})
    monkeypatch.setattr(ac, "runtime_paths", lambda: [(a, False), (b, True)])
    monkeypatch.setattr(ac, "check_grants_keep_out", lambda paths, root: None)
    monkeypatch.setattr(ac, "_has_grant", lambda p, s: True)
    monkeypatch.setattr(ac, "_grant", lambda api, p, s, inherit: calls.append((p, inherit)))
    return types.SimpleNamespace(a=a, b=b, calls=calls, data=tmp_path / "data", lib=tmp_path / "lib")


def test_grant_runtime_grants_each_path_and_the_lib_roots_and_records_them(granting):
    granting.lib.mkdir()
    ac.grant_runtime(None, "S-1", [granting.lib], granting.data)
    assert granting.calls == [(granting.a, False), (granting.b, True), (granting.lib, True)]
    record = json.loads((granting.data / "academy" / ac.GRANTS_FILE).read_text(encoding="utf-8"))
    assert record == {"sid": "S-1", "capability": ac.CAPABILITY,
                      "paths": sorted(str(p) for p in (granting.a, granting.b, granting.lib))}


def test_grant_runtime_without_a_data_folder_keeps_no_record(granting):
    ac.grant_runtime(None, "S-1")
    assert len(granting.calls) == 2 and not granting.data.exists()


def test_a_second_grant_is_skipped_while_its_record_is_there(granting):
    ac.grant_runtime(None, "S-1", None, granting.data)
    granting.calls.clear()
    ac.grant_runtime(None, "S-1", None, granting.data)
    assert granting.calls == []
    (granting.data / "academy" / ac.GRANTS_FILE).unlink()
    ac.grant_runtime(None, "S-1", None, granting.data)
    assert len(granting.calls) == 2


def test_a_record_of_the_same_sid_skips_the_paths_it_lists(granting, monkeypatch):
    ac.grant_runtime(None, "S-1", None, granting.data)
    granting.calls.clear()
    monkeypatch.setattr(ac, "_granted", {})
    ac.grant_runtime(None, "S-1", None, granting.data)
    assert granting.calls == []
    monkeypatch.setattr(ac, "_granted", {})
    ac.grant_runtime(None, "S-2", None, granting.data)   # another SID: every path again
    assert len(granting.calls) == 2


def test_a_reinstalled_interpreter_is_granted_again(granting, monkeypatch):
    ac.grant_runtime(None, "S-1", None, granting.data)
    granting.calls.clear()
    monkeypatch.setattr(ac, "_granted", {})
    monkeypatch.setattr(ac, "_has_grant", lambda p, s: False)
    ac.grant_runtime(None, "S-1", None, granting.data)
    assert len(granting.calls) == 2


# ---- the record and revoking


def test_a_plain_path_is_absolute_text_icacls_cant_read_as_an_option(monkeypatch):
    monkeypatch.setattr(ac.os.path, "isabs", ntpath.isabs)
    assert ac._plain_path("C:\\Python")
    assert not ac._plain_path("/x") and not ac._plain_path("-x") and not ac._plain_path("C:\\a\0b")
    assert not ac._plain_path(5) and not ac._plain_path("rel")


def test_granted_count_counts_plain_paths_that_are_there(monkeypatch, tmp_path):
    monkeypatch.setattr(ac.os.path, "isabs", ntpath.isabs)
    monkeypatch.chdir(tmp_path)
    Path("C:\\here").write_text("", encoding="utf-8")   # a file of that name, here
    record = tmp_path / "academy" / ac.GRANTS_FILE
    record.parent.mkdir()
    record.write_text(json.dumps({"paths": ["C:\\here", "C:\\here", "C:\\gone", "rel"]}), encoding="utf-8")
    assert ac.granted_count(tmp_path) == 1
    record.write_text(json.dumps(["C:\\here"]), encoding="utf-8")
    assert ac.granted_count(tmp_path) == 0


@pytest.fixture
def revoking(monkeypatch, tmp_path):
    monkeypatch.setattr(ac.sys, "platform", "win32")
    monkeypatch.setattr(ac, "_api", lambda: None)
    monkeypatch.setattr(ac, "capability_sid", lambda api: (None, "S-1"))
    target = tmp_path / "granted"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(ac, "runtime_paths", lambda: [(target, False), (tmp_path / "gone", False)])
    record = tmp_path / "academy" / ac.GRANTS_FILE
    record.parent.mkdir()
    record.write_text("{}", encoding="utf-8")
    calls = []
    return types.SimpleNamespace(target=target, record=record, calls=calls, data=tmp_path)


def test_revoking_off_windows_does_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(ac.sys, "platform", "linux")
    monkeypatch.setattr(ac, "capability_sid", lambda api: pytest.fail("no Windows call"))
    assert ac.revoke_runtime(tmp_path) == {"removed": 0, "failed": []}


def test_revoking_removes_the_ace_then_the_record(revoking, monkeypatch):
    monkeypatch.setattr(ac, "_icacls", lambda *a: revoking.calls.append(a) or _done(0))
    monkeypatch.setattr(ac, "_granted", {"k": True})
    assert ac.revoke_runtime(revoking.data) == {"removed": 1, "failed": []}
    assert revoking.calls == [(str(revoking.target), "/remove:g", "*S-1")]
    assert not revoking.record.exists() and ac._granted == {}


def test_a_path_icacls_cant_change_is_listed_and_the_record_kept(revoking, monkeypatch):
    monkeypatch.setattr(ac, "_icacls", lambda *a: _done(5))
    assert ac.revoke_runtime(revoking.data) == {"removed": 0, "failed": [str(revoking.target)]}
    assert revoking.record.exists()


def test_revoking_while_an_exercise_runs_removes_nothing(revoking, monkeypatch):
    monkeypatch.setattr(ac, "_open_boxes", 1)
    monkeypatch.setattr(ac, "_icacls", lambda *a: pytest.fail("nothing removed"))
    assert ac.revoke_runtime(revoking.data) == {
        "removed": 0, "failed": [], "why": "An exercise is running. Try again when it has finished."}


def test_revoking_without_the_capability_says_why(revoking, monkeypatch):
    def no_sid(api):
        raise OSError("no")

    monkeypatch.setattr(ac, "capability_sid", no_sid)
    assert ac.revoke_runtime(revoking.data) == {
        "removed": 0, "failed": [], "why": "Windows wouldn't name the exercise runtime's capability."}


# ---- reading ACLs and the sealed folders


def _saved_acl(monkeypatch, text, code=0):
    def icacls(path, *args):
        if code == 0:
            Path(args[1]).write_bytes(("\ufeff" + text).encode("utf-16-le"))
        return _done(code)

    monkeypatch.setattr(ac, "_icacls", icacls)


def test_app_package_aces_pair_each_path_with_its_sddl(monkeypatch, tmp_path):
    _saved_acl(monkeypatch, "C:\\a b\nD:(A;OICI;0x1200a9;;;AC)(A;;FA;;;SY)\nC:\\c\nD:(D;ID;FA;;;S-1-15-2-2)\nC:\\odd")
    assert ac.app_package_aces(tmp_path, tree=True) == [("C:\\a b", "A", "OICI"), ("C:\\c", "D", "ID")]


def test_app_package_aces_refuse_an_acl_windows_wouldnt_save(monkeypatch, tmp_path):
    _saved_acl(monkeypatch, "", code=1)
    with pytest.raises(OSError, match="wouldn't read the permissions"):
        ac.app_package_aces(tmp_path)


@pytest.fixture
def sealed_folders(monkeypatch, tmp_path):
    cache = tmp_path / "cache" / "token.bin"
    cache.parent.mkdir()
    monkeypatch.setattr(auth, "default_cache_path", lambda: cache)
    monkeypatch.setattr(ac, "_sealed_folders_ok", set())
    data = tmp_path / "data"
    data.mkdir()
    return data


def test_a_folder_every_appcontainer_can_read_is_refused(monkeypatch, sealed_folders):
    monkeypatch.setattr(ac, "app_package_aces", lambda p: [("x", "A", "")] if p == sealed_folders else [])
    with pytest.raises(OSError, match=f"Every AppContainer can read {sealed_folders}, where"):
        ac.check_sealed_folders_closed(sealed_folders)


def test_a_deny_is_fine_and_a_folder_checked_once_isnt_read_again(monkeypatch, sealed_folders):
    read = []
    monkeypatch.setattr(ac, "app_package_aces", lambda p: read.append(p) or [("x", "D", "")])
    ac.check_sealed_folders_closed(sealed_folders)
    ac.check_sealed_folders_closed(sealed_folders)
    assert sorted(map(str, read)) == sorted([str(sealed_folders), str(sealed_folders.parent / "cache")])


def test_a_sealed_file_every_appcontainer_can_read_is_refused(monkeypatch, sealed_folders):
    (sealed_folders / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ac, "app_package_aces", lambda p: [("x", "A", "")] if p.name == "settings.json" else [])
    with pytest.raises(OSError, match="Every AppContainer can read settings.json, which holds sealed secrets"):
        ac.check_sealed_folders_closed(sealed_folders)


# ---- copying in


def test_copy_takes_2000_files_and_refuses_one_more(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(ac.MAX_COPY_FILES):
        (src / f"{i}.txt").write_text("", encoding="utf-8")
    ac.copy_tree(src, tmp_path / "a")
    assert len(os.listdir(tmp_path / "a")) == 2000
    (src / "one-more.txt").write_text("", encoding="utf-8")
    with pytest.raises(ws.Refused, match="more than 2,000 files"):
        ac.copy_tree(src, tmp_path / "b")


def test_copy_takes_exactly_1_gb_and_refuses_one_byte_more(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    with open(src / "big.bin", "wb") as f:   # sparse: no gigabyte is written
        f.truncate(1024 ** 3)
    monkeypatch.setattr(ac.shutil, "copyfile", lambda *a, **k: None)
    ac.copy_tree(src, tmp_path / "a")
    with open(src / "big.bin", "r+b") as f:
        f.truncate(1024 ** 3 + 1)
    with pytest.raises(ws.Refused, match="more than 1 GB"):
        ac.copy_tree(src, tmp_path / "b")


# ---- the Box, with Windows stood in for


class _Profile:
    def __init__(self, api):
        self.folder = api.root / "profile"
        self.folder.mkdir()
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def boxing(monkeypatch, tmp_path):
    api = types.SimpleNamespace(root=tmp_path, k=types.SimpleNamespace(closed=[]))
    api.k.CloseHandle = api.k.closed.append
    granted = []
    monkeypatch.setattr(ac, "_open_boxes", 0)
    monkeypatch.setattr(ac, "_api", lambda: api)
    monkeypatch.setattr(ac, "sweep", lambda a: 0)
    monkeypatch.setattr(ac, "deny_app_packages", lambda paths: None)
    monkeypatch.setattr(ac, "check_sealed_folders_closed", lambda root: None)
    monkeypatch.setattr(ac, "capability_sid", lambda a: ("sid", "S-1"))
    monkeypatch.setattr(ac, "grant_runtime", lambda a, cap, libs, root: granted.append(libs))
    monkeypatch.setattr(ac, "Profile", _Profile)
    work = tmp_path / "work"
    work.mkdir()
    (work / "main.py").write_text("print(1)\n", encoding="utf-8")
    boot = tmp_path / "boot.py"
    boot.write_text("", encoding="utf-8")
    return types.SimpleNamespace(api=api, granted=granted, work=work, boot=boot)


def test_a_box_counts_itself_shares_the_job_once_and_uncounts_on_close(boxing, monkeypatch):
    started = []
    monkeypatch.setattr(ac, "query_handle", lambda api, job: 7)
    monkeypatch.setattr(ac, "LowboxProcess", lambda *a: started.append(a[-1]) or "proc")
    box = ac.Box(boxing.work, boxing.boot, ["/lib"])
    assert ac._open_boxes == 1 and boxing.granted == [[Path("/lib")]]
    assert (box.work / "main.py").is_file()
    assert box.start(["py"], {}) == "proc" and started == [[]]
    assert box.share("job") == 7
    box.start(["py"], {})
    assert started[-1] == [7] and boxing.api.k.closed == [7] and box._shared == 0
    box.close()
    assert ac._open_boxes == 0 and box.profile.closed


def test_a_box_that_cant_be_set_up_counts_nothing(boxing, monkeypatch):
    def broken():
        raise OSError("no AppContainers here")

    monkeypatch.setattr(ac, "_api", broken)
    with pytest.raises(ac.SetupFailed, match="no AppContainers here"):
        ac.Box(boxing.work, boxing.boot)
    assert ac._open_boxes == 0


def test_any_other_setup_error_is_raised_as_it_is(boxing, monkeypatch):
    def broken(a):
        raise RuntimeError("bug")

    monkeypatch.setattr(ac, "capability_sid", broken)
    with pytest.raises(RuntimeError, match="bug"):
        ac.Box(boxing.work, boxing.boot)
    assert ac._open_boxes == 0


def test_a_copy_that_fails_closes_the_profile_and_raises(boxing, monkeypatch):
    closed = []
    monkeypatch.setattr(_Profile, "close", lambda self: closed.append(True))

    def refuse(src, dst, refuse_sealed=False):
        raise ws.Refused("sealed")

    monkeypatch.setattr(ac, "copy_tree", refuse)
    with pytest.raises(ws.Refused, match="sealed"):
        ac.Box(boxing.work, boxing.boot)
    assert closed == [True] and ac._open_boxes == 0

