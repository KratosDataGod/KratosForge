"""Tests for the startup updater the shell embeds (app/src-tauri/src/updater.py)."""

import importlib.util
import json
from importlib import metadata
from pathlib import Path

import pytest

SHA = "0123456789abcdef0123456789abcdef01234567"

spec = importlib.util.spec_from_file_location(
    "updater", Path(__file__).resolve().parents[2] / "app" / "src-tauri" / "src" / "updater.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


@pytest.fixture(autouse=True)
def verified_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "verified_path", lambda: tmp_path / "engine-verified.json")


class FakeDist:
    def __init__(self, direct_url):
        self.version = "0.2.0"
        self._direct = direct_url

    def read_text(self, name):
        assert name == "direct_url.json"
        return None if self._direct is None else json.dumps(self._direct)


@pytest.mark.parametrize("direct, commit, editable", [
    ({"url": f"https://github.com/KratosDataGod/KratosForge/archive/{SHA}.zip", "archive_info": {}}, SHA, False),
    # Copies installed before the repository was renamed recorded the old address.
    ({"url": f"https://github.com/KratosDataGod/fabric-desktop/archive/{SHA}.zip", "archive_info": {}}, SHA, False),
    ({"url": "https://github.com/KratosDataGod/KratosForge/archive/refs/heads/main.zip", "archive_info": {}}, None, False),
    ({"url": "https://github.com/KratosDataGod/KratosForge.git", "vcs_info": {"vcs": "git", "commit_id": SHA}}, SHA, False),
    ({"url": "file:///C:/src/fabric-desktop/sidecar", "dir_info": {"editable": True}}, None, True),
    (None, None, False),
])
def test_installed_sidecar_reads_commit(monkeypatch, direct, commit, editable):
    monkeypatch.setattr(metadata, "distribution", lambda name: FakeDist(direct))
    assert updater.installed_sidecar() == {"version": "0.2.0", "commit": commit, "editable": editable}


def test_installed_sidecar_missing(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "distribution", missing)
    assert updater.installed_sidecar()["version"] is None


class FakeResponse:
    def __init__(self, body: bytes):
        self.body, self.headers = body, {"Content-Length": str(len(body))}

    def read(self, n=-1):
        out, self.body = (self.body, b"") if n < 0 else (self.body[:n], self.body[n:])
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(monkeypatch, routes):
    """Fake urlopen: the first route whose key is in the URL answers; an exception is raised."""
    seen = []

    def urlopen(req, timeout=None):
        url = req.full_url
        seen.append((url, req.get_header("Cache-control")))
        for key, answer in routes.items():
            if key in url:
                if isinstance(answer, Exception):
                    raise answer
                return FakeResponse(answer() if callable(answer) else answer)
        raise OSError(f"no route for {url}")
    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    return seen


def test_latest_release_offline(monkeypatch):
    serve(monkeypatch, {"github": OSError("no network")})
    result = updater.latest_release()
    assert result["commit"] is None and "no network" in result["error"]


@pytest.fixture
def settings_dirs(monkeypatch, tmp_path):
    """Empty policy and user settings folders, returned as (policy file, user file)."""
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    policy, user = tmp_path / "pd" / "FabricDesktop" / "policy.json", tmp_path / "la" / "FabricDesktop" / "app-settings.json"
    policy.parent.mkdir(parents=True)
    user.parent.mkdir(parents=True)
    return policy, user


def test_channel_defaults_to_stable_and_policy_wins(settings_dirs):
    policy, user = settings_dirs
    assert updater.channel() == "stable"
    user.write_text(json.dumps({"channel": "preview"}))
    assert updater.channel() == "preview"
    policy.write_text(json.dumps({"channel": "stable"}))
    assert updater.channel() == "stable"
    policy.write_text(json.dumps({"channel": "nightly"}))  # not a channel: ignored
    assert updater.channel() == "preview"


def test_channel_uses_the_last_good_policy_when_the_policy_is_unreadable(settings_dirs):
    # As the sidecar does (app_settings._policy_file), so the channel matches what Settings shows (#419).
    policy, user = settings_dirs
    saved = user.with_name("policy-last-good.json")
    user.write_text(json.dumps({"channel": "preview"}))
    saved.write_text(json.dumps({"channel": "stable"}))
    assert updater.channel() == "preview"  # no policy file: the copy left over doesn't apply
    for broken in ('{"channel": "preview",}', "[]", ""):
        policy.write_text(broken)
        assert updater.channel() == "stable", broken
    policy.write_bytes(b"\xff\xfe{}")  # not UTF-8
    assert updater.channel() == "stable"
    saved.write_text("not json")  # no usable copy either: the user's channel
    assert updater.channel() == "preview"
    policy.write_text(json.dumps({"channel": "stable"}))
    assert updater.channel() == "stable"


def test_latest_release_reads_the_channels_version_json(monkeypatch, settings_dirs):
    seen = serve(monkeypatch, {"/stable/version.json": json.dumps({"commit": SHA, "notes": "Settings page"}).encode()})
    assert updater.latest_release() == {"commit": SHA, "error": None, "channel": "stable", "notes": "Settings page", "history": [],
                                        "note": "", "tag": "stable", "published": None, "exe_sha256": ""}
    assert "nocache=" in seen[0][0] and seen[0][1] == "no-cache"
    assert not any("api.github.com" in url for url, _ in seen)


def test_latest_release_passes_on_what_the_shell_checks(monkeypatch, settings_dirs):
    # The shell refuses a version.json older than one it already accepted (HD-022, update_guard.rs).
    info = {"commit": SHA, "published": 1760800000, "exe_sha256": "ab" * 32}
    serve(monkeypatch, {"/stable/version.json": json.dumps(info).encode()})
    result = updater.latest_release()
    assert (result["tag"], result["published"], result["exe_sha256"]) == ("stable", 1760800000, "ab" * 32)
    for bad in ("2026-10-18", True, 1.5):
        serve(monkeypatch, {"/stable/version.json": json.dumps({**info, "published": bad}).encode()})
        assert updater.latest_release()["published"] is None


def test_latest_release_passes_on_the_months_history(monkeypatch, settings_dirs):
    history = [{"date": "2026-10-17", "title": "SQL editor", "pr": 118, "commit": SHA},
               {"date": "2026-10-16", "title": "  Direct push  ", "pr": None, "commit": SHA},
               {"date": "yesterday", "title": "Bad date and PR", "pr": True, "commit": "abc"},
               {"title": ""}, "not an item", {"pr": 5}]
    serve(monkeypatch, {"/stable/version.json": json.dumps({"commit": SHA, "notes": "n", "history": history}).encode()})
    assert updater.latest_release()["history"] == [
        {"date": "2026-10-17", "title": "SQL editor", "pr": 118, "commit": SHA},
        {"date": "2026-10-16", "title": "Direct push", "pr": None, "commit": SHA},
        {"date": None, "title": "Bad date and PR", "pr": None, "commit": None}]


def test_release_history_ignores_anything_but_a_list():
    assert updater.release_history(None) == [] and updater.release_history({"title": "x"}) == []


def test_preview_channel_follows_latest(monkeypatch, settings_dirs):
    settings_dirs[1].write_text(json.dumps({"channel": "preview"}))
    seen = serve(monkeypatch, {"/latest/version.json": json.dumps({"commit": SHA}).encode()})
    assert updater.latest_release()["commit"] == SHA
    assert "/releases/download/latest/" in seen[0][0]


def test_no_stable_release_yet_follows_preview(monkeypatch, settings_dirs):
    import urllib.error
    seen = serve(monkeypatch, {"/stable/": urllib.error.HTTPError("u", 404, "Not Found", {}, None),
                               "/latest/version.json": json.dumps({"commit": SHA, "notes": "n"}).encode()})
    result = updater.latest_release()
    assert result["commit"] == SHA and result["error"] is None and result["note"] == updater.NO_STABLE
    assert "/stable/" in seen[0][0] and "/latest/" in seen[1][0]


def test_download_app_follows_preview_without_a_stable_release(monkeypatch, settings_dirs, tmp_path):
    import hashlib
    import urllib.error
    exe = b"MZ..." + SHA.encode()
    seen = serve(monkeypatch, {"/stable/": urllib.error.HTTPError("u", 404, "Not Found", {}, None),
                               "/latest/kratosforge.exe": exe,
                               "/latest/version.json": json.dumps({"commit": SHA, "exe_sha256": hashlib.sha256(exe).hexdigest()}).encode()})
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 0
    # Stable is missing (its version.json, then the exe under both names) before Preview is tried.
    assert all("/stable/" in url for url, _ in seen[:3]) and all("/latest/" in url for url, _ in seen[3:])


def test_preview_channel_does_not_fall_back(monkeypatch, settings_dirs):
    import urllib.error
    settings_dirs[1].write_text(json.dumps({"channel": "preview"}))
    serve(monkeypatch, {"version.json": urllib.error.HTTPError("u", 404, "Not Found", {}, None)})
    assert updater.latest_release()["commit"] is None


def test_download_app_refuses_a_release_without_a_checksum(monkeypatch, tmp_path, capsys):
    # A version.json that names the build but gives no checksum: the download can't be checked (HD-022).
    for info in ({"commit": SHA}, {"commit": SHA, "exe_sha256": "not-a-checksum"}):
        serve(monkeypatch, {"kratosforge.exe": b"MZ..." + SHA.encode() + b"...", "version.json": json.dumps(info).encode()})
        assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 1
        assert "gives no checksum" in capsys.readouterr().out


def test_download_app_refuses_when_the_checksum_cant_be_read(monkeypatch, tmp_path, capsys):
    serve(monkeypatch, {"kratosforge.exe": b"MZ..." + SHA.encode()})  # no version.json
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 1
    assert "Could not read build 0123456's checksum" in capsys.readouterr().out


def test_download_app_says_when_the_release_names_another_build(monkeypatch, tmp_path, capsys):
    # The channel was republished since the shell checked it: say which build it names now,
    # without downloading, so the shell checks again and installs that one.
    other = json.dumps({"commit": "f" * 40, "exe_sha256": "0" * 64}).encode()
    seen = serve(monkeypatch, {"kratosforge.exe": b"MZ..." + SHA.encode(), "version.json": other})
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == updater.MOVED
    assert capsys.readouterr().out.splitlines()[-1] == "moved " + "f" * 40
    assert not any("kratosforge.exe" in url for url, _ in seen)


def test_download_app_says_when_the_release_moves_during_the_download(monkeypatch, tmp_path, capsys):
    import hashlib
    exe = b"MZ..." + SHA.encode()
    infos = iter([{"commit": SHA, "exe_sha256": hashlib.sha256(exe).hexdigest()}, {"commit": "f" * 40, "exe_sha256": "0" * 64}])
    last = {}

    def version():
        last.update(next(infos, last))
        return json.dumps(last).encode()

    serve(monkeypatch, {"kratosforge.exe": exe, "version.json": version})
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == updater.MOVED
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "moved " + "f" * 40 and "trying again" not in out


def test_download_app_offline_says_so_in_one_line(monkeypatch, tmp_path, capsys):
    serve(monkeypatch, {"github": OSError("no network")})
    assert updater.main(["download-app", str(tmp_path / "new.exe"), SHA]) == 1
    assert capsys.readouterr().out.strip() == "The download failed: no network"


def test_download_app_refuses_a_stale_copy(monkeypatch, tmp_path, capsys):
    import hashlib
    stale = b"MZ...old build..."
    # Even with a matching checksum, the file must carry the build's commit.
    info = {"commit": SHA, "exe_sha256": hashlib.sha256(stale).hexdigest()}
    seen = serve(monkeypatch, {"kratosforge.exe": stale, "version.json": json.dumps(info).encode()})
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 1
    exe = [url for url, _ in seen if "kratosforge.exe" in url]
    assert len(exe) == 2 and "nocache=" in exe[1]
    assert "not build 0123456 yet" in capsys.readouterr().out


def test_download_app_checks_the_checksum(monkeypatch, tmp_path, capsys):
    import hashlib
    good = b"MZ..." + SHA.encode()
    info = {"commit": SHA, "exe_sha256": hashlib.sha256(good).hexdigest()}
    serve(monkeypatch, {"version.json": json.dumps(info).encode(), "kratosforge.exe": good})
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 0
    serve(monkeypatch, {"version.json": json.dumps(info).encode(), "kratosforge.exe": good + b"tampered"})
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 1
    assert "checksum" in capsys.readouterr().out


def test_download_app_refuses_a_cut_off_download(monkeypatch, tmp_path, capsys):
    class Short(FakeResponse):
        def __init__(self, body):
            super().__init__(body)
            self.headers = {"Content-Length": str(len(body) + 1000)}

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=None: Short(b"MZ..." + SHA.encode()))
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 1
    assert "stopped at" in capsys.readouterr().out


def test_download_app_takes_the_legacy_name_from_an_older_release(monkeypatch, tmp_path):
    # A build-* release from before kratosforge.exe (Stable rolled back to it) has only fabric-desktop.exe.
    import hashlib
    import urllib.error
    exe = b"MZ..." + SHA.encode()
    seen = serve(monkeypatch, {"/kratosforge.exe": urllib.error.HTTPError("u", 404, "Not Found", {}, None),
                               "/fabric-desktop.exe": exe,
                               "version.json": json.dumps({"commit": SHA, "exe_sha256": hashlib.sha256(exe).hexdigest()}).encode()})
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 0
    assert (tmp_path / "new.exe").read_bytes() == exe
    exes = [url for url, _ in seen if ".exe" in url][:2]
    assert [url.split("?")[0].rsplit("/", 1)[1] for url in exes] == ["kratosforge.exe", "fabric-desktop.exe"]
    assert "/stable/" in exes[0] and "/stable/" in exes[1], "the same release, not Preview"


def test_download_app_asks_for_kratosforge_exe_first(monkeypatch, tmp_path):
    import hashlib
    exe = b"MZ..." + SHA.encode()
    seen = serve(monkeypatch, {"/kratosforge.exe": exe,
                               "version.json": json.dumps({"commit": SHA, "exe_sha256": hashlib.sha256(exe).hexdigest()}).encode()})
    assert updater.download_app(str(tmp_path / "new.exe"), SHA) == 0
    assert not any("fabric-desktop.exe" in url for url, _ in seen)


def source_zip(constraints=True, extra=()):
    """A GitHub source archive: one top folder holding sidecar/."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        top = f"KratosForge-{SHA}/"
        zf.writestr(top + "README.md", "readme")
        zf.writestr(top + "sidecar/pyproject.toml", "[project]\nname = 'fabric-desktop'\n")
        if constraints:
            zf.writestr(top + "sidecar/constraints.txt", "msal==1.39.0\n")
        for name, data in extra:
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def archive(monkeypatch):
    return serve(monkeypatch, {f"/archive/{SHA}.zip": source_zip()})


def test_install_sidecar_installs_the_commit_with_its_own_constraints(monkeypatch, archive):
    """SH-9: the packages are the versions in that commit's constraints.txt, read from the one
    archive the updater downloads, so no other host is needed."""
    calls, seen_files = [], []

    def call(cmd, **kwargs):
        calls.append(cmd)
        seen_files.append((Path(cmd[-1]) / "pyproject.toml").is_file())
        if "-c" in cmd:
            seen_files.append(Path(cmd[cmd.index("-c") + 1]).read_text())
        return 0
    monkeypatch.setattr(updater, "load_error", lambda: None)
    monkeypatch.setattr(updater.subprocess, "call", call)
    assert updater.install_sidecar(SHA) == 0
    assert [url for url, _ in archive] == [f"https://github.com/KratosDataGod/KratosForge/archive/{SHA}.zip"]
    source = calls[0][-1]
    assert Path(source).name == "sidecar" and "--force-reinstall" not in calls[0]
    assert calls[0][-3:-1] == ["-c", str(Path(source) / "constraints.txt")]
    assert calls[1][-4:] == ["--quiet", "--force-reinstall", "--no-deps", source]
    assert seen_files == [True, "msal==1.39.0\n", True]
    assert not Path(source).exists()  # the download is removed afterwards


def test_install_sidecar_refuses_a_download_without_constraints(monkeypatch, capsys):
    serve(monkeypatch, {f"/archive/{SHA}.zip": source_zip(constraints=False)})
    calls = []
    monkeypatch.setattr(updater.subprocess, "call", lambda cmd, **kwargs: calls.append(cmd) or 0)
    assert updater.install_sidecar(SHA) == 1
    assert calls == [] and "constraints.txt" in capsys.readouterr().out


def test_install_sidecar_unpacks_only_the_sidecar_folder(monkeypatch, tmp_path):
    serve(monkeypatch, {f"/archive/{SHA}.zip": source_zip(extra=[("../outside.txt", "x")])})
    (tmp_path / "d").mkdir()
    source = updater.download_sidecar(SHA, str(tmp_path / "d"))
    assert sorted(p.name for p in source.iterdir()) == ["constraints.txt", "pyproject.toml"]
    assert not (tmp_path / "outside.txt").exists() and not (source.parent / "README.md").exists()


def test_install_sidecar_offline_says_so(monkeypatch, capsys):
    serve(monkeypatch, {"github": OSError("no network")})
    assert updater.install_sidecar(SHA) == 1
    assert "no network" in capsys.readouterr().out


def test_install_sidecar_stops_on_failure(monkeypatch, archive):
    calls = []
    monkeypatch.setattr(updater.subprocess, "call", lambda cmd, **kwargs: calls.append(cmd) or 1)
    assert updater.install_sidecar(SHA) == 1
    assert len(calls) == 1


def test_install_sidecar_fails_when_it_does_not_start(monkeypatch, capsys, archive):
    monkeypatch.setattr(updater.subprocess, "call", lambda cmd, **kwargs: 0)
    monkeypatch.setattr(updater, "load_error", lambda: "ModuleNotFoundError: No module named 'fabric_desktop.meta'")
    assert updater.install_sidecar(SHA) == 1
    assert "fabric_desktop.meta" in capsys.readouterr().out


def test_load_error_reports_last_line(monkeypatch):
    class Done:
        returncode = 1
        stderr = "Traceback (most recent call last):\n  ...\nModuleNotFoundError: No module named 'x'\n"
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: Done())
    assert updater.load_error() == "ModuleNotFoundError: No module named 'x'"


def test_load_error_none_when_it_starts():
    assert updater.load_error() is None  # the sidecar under test is installed


def test_check_reports_python(monkeypatch, capsys):
    monkeypatch.setattr(updater, "latest_release", lambda: {"commit": SHA, "error": None})
    assert updater.main(["check"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["python_ok"] is True
    assert report["latest"]["commit"] == SHA
    assert set(report["sidecar"]) == {"version", "commit", "editable", "error", "blocked", "cached"}
    assert report["sidecar"]["error"] is None


def test_latest_reports_the_channels_build_and_release_page(monkeypatch, capsys):
    # "Check for updates": the build and where to download it by hand, without the engine check.
    monkeypatch.setattr(updater, "installed_sidecar", lambda: pytest.fail("the manual check doesn't check the engine"))
    monkeypatch.setattr(updater, "latest_release", lambda: {"commit": SHA, "error": None, "tag": "preview"})
    assert updater.main(["latest"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["commit"] == SHA
    assert report["page"] == "https://github.com/KratosDataGod/KratosForge/releases/tag/latest"


def test_latest_offline_still_names_the_channels_release_page(monkeypatch):
    monkeypatch.setattr(updater, "channel", lambda: "stable")
    monkeypatch.setattr(updater, "latest_release", lambda: {"commit": None, "error": "URLError: offline"})
    assert updater.latest()["page"] == "https://github.com/KratosDataGod/KratosForge/releases/tag/stable"


class FakeFile(str):
    @property
    def suffix(self):
        return Path(self).suffix


class FakeBinaryDist:
    def __init__(self, name, files):
        self.metadata = {"Name": name}
        self.files = [FakeFile(f) for f in files]

    def locate_file(self, file):
        return Path("C:/Users/chris/site-packages") / file


def test_blocked_files_names_every_blocked_package(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "smart_app_control", lambda: "on")
    monkeypatch.setattr(updater, "_sidecar_packages", lambda: [
        FakeBinaryDist("fabric-desktop", ["fabric_desktop/server.py"]),
        FakeBinaryDist("pyarrow", ["pyarrow/lib.cp314-win_amd64.pyd", "pyarrow/arrow.dll", "pyarrow/__init__.py"]),
        FakeBinaryDist("duckdb", ["_duckdb.cp314-win_amd64.pyd"]),
    ])
    blocked = updater.blocked_files(probe=lambda path: "pyarrow" in path)
    assert blocked["packages"] == {"pyarrow": ["pyarrow/lib.cp314-win_amd64.pyd", "pyarrow/arrow.dll"]}
    assert blocked["by"] == "smart_app_control"


def test_blocked_files_blames_a_policy_when_smart_app_control_is_off(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "smart_app_control", lambda: "evaluation")
    monkeypatch.setattr(updater, "_sidecar_packages", lambda: [FakeBinaryDist("cffi", ["_cffi_backend.pyd"])])
    assert updater.blocked_files(probe=lambda path: True)["by"] == "policy"
    assert updater.blocked_files(probe=lambda path: False) is None


def test_blocked_files_only_on_windows(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    assert updater.blocked_files(probe=lambda path: True) is None


def test_sidecar_packages_follows_dependencies():
    names = {d.metadata["Name"].lower() for d in updater._sidecar_packages()}
    assert {"fabric-desktop", "pyarrow", "polars", "cryptography"} <= names


def test_install_sidecar_reports_blocked_files(monkeypatch, capsys, archive):
    blocked = {"packages": {"pyarrow": ["pyarrow/lib.pyd"]}, "by": "policy", "smart_app_control": None, "location": "x"}
    monkeypatch.setattr(updater.subprocess, "call", lambda cmd, **kwargs: 0)
    monkeypatch.setattr(updater, "load_error", lambda: "ImportError: DLL load failed while importing lib")
    monkeypatch.setattr(updater, "blocked_files", lambda: blocked)
    assert updater.install_sidecar(SHA) == 1
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("blocked "))
    assert json.loads(line[8:]) == blocked


def test_installed_sidecar_reads_the_bundled_engines_build(monkeypatch, tmp_path):
    (tmp_path / "engine.json").write_text(json.dumps({"commit": SHA}))
    monkeypatch.setattr(updater.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(metadata, "distribution", lambda name: FakeDist({"url": "file:///D:/a/wheels/x.whl"}))
    assert updater.installed_sidecar()["commit"] == SHA


def test_check_skips_starting_a_build_that_started_before(monkeypatch):
    monkeypatch.setattr(updater, "latest_release", lambda: {"commit": SHA, "error": None})
    monkeypatch.setattr(updater, "installed_sidecar", lambda: {"version": "0.9.0", "commit": SHA, "editable": False})
    starts = []
    monkeypatch.setattr(updater, "load_error", lambda: starts.append(1))
    first, second = updater.check(), updater.check()
    assert len(starts) == 1
    assert first["sidecar"]["cached"] is False and second["sidecar"]["cached"] is True
    assert second["sidecar"]["error"] is None


def test_check_starts_again_after_a_new_build_or_a_failure(monkeypatch):
    monkeypatch.setattr(updater, "latest_release", lambda: {"commit": SHA, "error": None})
    build = {"version": "0.9.0", "commit": SHA, "editable": False}
    monkeypatch.setattr(updater, "installed_sidecar", lambda: dict(build))
    errors = ["ImportError: DLL load failed"]
    monkeypatch.setattr(updater, "load_error", lambda: errors.pop() if errors else None)
    assert updater.check()["sidecar"]["error"]  # a failure is never remembered
    assert updater.check()["sidecar"]["cached"] is False
    build["commit"] = "f" * 40
    assert updater.check()["sidecar"]["cached"] is False


def test_check_always_starts_a_developer_install(monkeypatch):
    monkeypatch.setattr(updater, "latest_release", lambda: {"commit": SHA, "error": None})
    monkeypatch.setattr(updater, "installed_sidecar", lambda: {"version": "0.9.0", "commit": None, "editable": True})
    starts = []
    monkeypatch.setattr(updater, "load_error", lambda: starts.append(1))
    updater.check(), updater.check()
    assert len(starts) == 2
