"""Startup component check, run by the Fabric Desktop shell with the user's Python.

Uses only the standard library, because it must work before the sidecar is installed.

    check            print one JSON object describing Python, the sidecar (and whether it starts) and the newest
                     release on this PC's update channel;
                     a build already seen to start is not started again, since loading its packages is slow
    install-sidecar  pip install the sidecar from the given commit, streaming pip's output
    download-app     download the latest release exe to a path, printing "progress <percent>" lines;
                     with a commit after the path, refuse a download that is another build
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "KratosDataGod/KratosForge"
MIN_PYTHON = (3, 12)
# Each update channel is a GitHub release: "latest" is replaced by every green build of main,
# "stable" only when someone promotes a build (the Promote to stable workflow).
CHANNEL_TAGS = {"stable": "stable", "preview": "latest"}
COMMIT = re.compile(r"/archive/([0-9a-f]{40})\.zip")
SHA = re.compile(r"[0-9a-f]{40}")


def _settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _policy(policy: Path, saved: Path) -> dict:
    """The organization's policy file; when it exists but can't be read as a JSON object, the last
    copy the sidecar could read (policy-last-good.json). A missing file is no policy."""
    try:
        data = json.loads(policy.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, dict) else _settings(saved)


def channel() -> str:
    """This PC's update channel: the organization's policy file (or its last good copy) wins over
    the Settings page. The same rule as the sidecar's app_settings.py; this file can't import it."""
    policy = Path(os.environ.get("ProgramData") or "/etc") / "FabricDesktop" / "policy.json"
    user = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "FabricDesktop" / "app-settings.json"
    for settings in (_policy(policy, user.with_name("policy-last-good.json")), _settings(user)):
        value = settings.get("channel")
        if value in CHANNEL_TAGS:
            return value
    return "stable"


def release_url(name: str = "") -> str:
    return f"https://github.com/{REPO}/releases/download/{CHANNEL_TAGS[name or channel()]}"


def sidecar_url(commit: str) -> str:
    return f"https://github.com/{REPO}/archive/{commit}.zip#subdirectory=sidecar"


def download_sidecar(commit: str, folder: str) -> Path:
    """That commit's sidecar/ folder, downloaded once and unpacked into folder. Its constraints.txt
    holds the exact package versions that commit was tested with (SH-9)."""
    import zipfile
    archive = Path(folder) / "source.zip"
    with _get(sidecar_url(commit).split("#", 1)[0], timeout=120) as resp, open(archive, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    with zipfile.ZipFile(archive) as zf:
        # GitHub's archives hold one top folder, <repo>-<commit>/.
        members = [m for m in zf.namelist() if m.split("/")[1:2] == ["sidecar"]]
        if not members:
            raise ValueError("the download has no sidecar folder")
        zf.extractall(folder, members)  # zipfile drops ".." and absolute parts of names
    source = Path(folder) / members[0].split("/", 1)[0] / "sidecar"
    if not (source / "constraints.txt").is_file():
        raise ValueError("the download has no sidecar/constraints.txt")
    return source


def installed_sidecar() -> dict:
    """Version of the installed sidecar and the commit it was installed from, when known."""
    from importlib import metadata

    try:
        dist = metadata.distribution("fabric-desktop")
    except metadata.PackageNotFoundError:
        return {"version": None, "commit": None, "editable": False}
    direct = json.loads(dist.read_text("direct_url.json") or "null") or {}
    commit = (direct.get("vcs_info") or {}).get("commit_id")
    if not commit:
        match = COMMIT.search(direct.get("url", ""))
        commit = match.group(1) if match else None
    editable = bool((direct.get("dir_info") or {}).get("editable"))
    # The bundled engine is installed from a wheel CI built, and says its build here.
    engine = Path(sys.executable).with_name("engine.json")
    if engine.is_file():
        commit = json.loads(engine.read_text()).get("commit") or commit
    return {"version": dist.version, "commit": commit, "editable": editable}


def verified_path() -> Path:
    """Where the check remembers the engine build it last saw start (the app's data folder)."""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "FabricDesktop" / "engine-verified.json"


def _verified_key(sidecar: dict) -> dict:
    return {"commit": sidecar.get("commit"), "version": sidecar.get("version"), "python": sys.executable}


def already_verified(sidecar: dict) -> bool:
    """True when this exact build, in this Python, started last time. The shell forgets it
    whenever the sidecar exits unexpectedly, so a newly blocked or broken engine is checked again."""
    if not sidecar.get("commit"):
        return False
    try:
        return json.loads(verified_path().read_text()) == _verified_key(sidecar)
    except (OSError, ValueError):
        return False


def remember_verified(sidecar: dict) -> None:
    if not sidecar.get("commit"):
        return
    try:
        path = verified_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_verified_key(sidecar)))
    except OSError:
        pass  # only a shortcut for next time


def load_error() -> str | None:
    """Start the sidecar's modules in a fresh Python, the way the app will. None when they load."""
    proc = subprocess.run([sys.executable, "-c", "import fabric_desktop.server"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode == 0:
        return None
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    return lines[-1] if lines else f"exited with code {proc.returncode}"


# Windows refuses to load a DLL that Smart App Control or an organization's App Control
# (WDAC) policy does not trust: ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION, or for an
# unverifiable signature ERROR_INVALID_IMAGE_HASH. Python only shows the message text,
# which is translated, so the files are probed one by one for these codes instead.
BLOCKED_CODES = {4551, 577}
DONT_RESOLVE_DLL_REFERENCES = 0x1  # map the image (which runs the policy check) without running its code


def _sidecar_packages() -> list:
    """The installed sidecar and everything it depends on, as importlib distributions."""
    from importlib import metadata

    seen, todo, dists = set(), ["fabric-desktop"], []
    while todo:
        name = re.split(r"[\s;\[<>=!~(]", todo.pop(), maxsplit=1)[0].lower().replace("_", "-")
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        dists.append(dist)
        todo += [r for r in dist.requires or [] if "extra ==" not in r]
    return dists


def _load_blocked(path: str) -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.restype = ctypes.c_void_p
    kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
    handle = kernel32.LoadLibraryExW(path, None, DONT_RESOLVE_DLL_REFERENCES)
    if handle:
        kernel32.FreeLibrary(handle)
        return False
    return ctypes.get_last_error() in BLOCKED_CODES


def smart_app_control() -> str | None:
    """"on", "evaluation" or "off" for Smart App Control; None where Windows has no such setting."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as key:
            state = winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")[0]
    except (ImportError, OSError):
        return None
    return {0: "off", 1: "on", 2: "evaluation"}.get(state)


def blocked_files(probe=_load_blocked) -> dict | None:
    """The sidecar's DLLs that Windows' application control refuses to load, by package.

    None when nothing is blocked (or not on Windows). Every package is checked, so one
    answer names all of them rather than only the first import that fails.
    """
    if sys.platform != "win32":
        return None
    try:
        return _blocked_files(probe)
    except Exception:  # a diagnosis only: never let it break the startup check
        return None


def _blocked_files(probe) -> dict | None:
    dists, packages = _sidecar_packages(), {}
    for dist in dists:
        for file in dist.files or []:
            if file.suffix.lower() in (".pyd", ".dll") and probe(str(dist.locate_file(file))):
                packages.setdefault(dist.metadata["Name"], []).append(str(file))
    if not packages:
        return None
    sac = smart_app_control()
    return {"packages": packages, "by": "smart_app_control" if sac == "on" else "policy",
            "smart_app_control": sac, "location": str(dists[0].locate_file(""))}


def _get(url: str, timeout: int = 5):
    # Ask every cache on the way for a fresh copy: "latest" is replaced in place on each build.
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache",
                                               "User-Agent": "fabric-desktop-updater"})
    return urllib.request.urlopen(req, timeout=timeout)


def release_channels() -> list:
    """Releases to try, in order. Stable falls back to Preview while no build has been promoted
    yet, so a new install never ends on a missing release."""
    name = channel()
    return [name, "preview"] if name == "stable" else [name]


NO_STABLE = "There is no stable release yet, so this PC follows Preview until there is one."


def release_history(raw) -> list:
    """What reached main in the month before a release's build (version.json "history"), newest
    first. Releases from before it have none; anything malformed is dropped, not shown."""
    items = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip():
            continue
        pr = item.get("pr")
        commit = item.get("commit") if isinstance(item.get("commit"), str) and SHA.fullmatch(item["commit"]) else None
        date = item.get("date") if isinstance(item.get("date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["date"]) else None
        items.append({"date": date, "title": item["title"].strip()[:300],
                      "pr": pr if isinstance(pr, int) and not isinstance(pr, bool) and pr > 0 else None, "commit": commit})
    return items[:200]


def latest_release() -> dict:
    """Commit and notes of the newest exe on this PC's channel, from the release's version.json.

    A release download, unlike GitHub's API, has no hourly limit per network address, which
    matters when a whole office shares one.
    """
    name, note = channel(), ""
    for tag in release_channels():
        try:
            with _get(f"{release_url(tag)}/version.json?nocache={int(time.time())}") as resp:
                info = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and tag == "stable":
                note = NO_STABLE
                continue
            return {"commit": None, "error": f"HTTPError: {exc}", "channel": name, "notes": "", "note": note}
        except Exception as exc:  # offline
            return {"commit": None, "error": f"{type(exc).__name__}: {exc}", "channel": name, "notes": "", "note": note}
        commit = info.get("commit") or ""
        if not SHA.fullmatch(commit):
            return {"commit": None, "error": f"the release names {commit!r}, not a commit", "channel": name, "notes": "", "note": note}
        # The shell checks these against what this PC last accepted from the channel (HD-022):
        # the release it came from, when it was published, and the exe's checksum.
        published = info.get("published")
        return {"commit": commit, "error": None, "channel": name, "notes": str(info.get("notes") or ""),
                "history": release_history(info.get("history")), "note": note, "tag": tag,
                "published": published if isinstance(published, int) and not isinstance(published, bool) else None,
                "exe_sha256": str(info.get("exe_sha256") or "")}
    return {"commit": None, "error": "No release was found.", "channel": name, "notes": "", "note": note}


def check() -> dict:
    sidecar = installed_sidecar()
    if sidecar["version"]:
        # Starting the engine's packages is the slow part of the check; skip it for a build
        # that already started in this Python.
        cached = already_verified(sidecar)
        sidecar["error"] = None if cached else load_error()
        sidecar["blocked"] = blocked_files() if sidecar["error"] else None
        sidecar["cached"] = cached
        if not cached and not sidecar["error"]:
            remember_verified(sidecar)
    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        "python_ok": sys.version_info >= MIN_PYTHON,
        "sidecar": sidecar,
        "latest": latest_release(),
    }


def latest() -> dict:
    """The channel's newest build for "Check for updates", without the engine check that only
    startup needs, plus the release page to download it from by hand."""
    info = latest_release()
    info["page"] = f"https://github.com/{REPO}/releases/tag/{CHANNEL_TAGS[info.get('tag') or channel()]}"
    return info


def install_sidecar(commit: str) -> int:
    print(f"== Downloading engine build {commit[:7]}", flush=True)
    with tempfile.TemporaryDirectory() as folder:
        try:
            source = str(download_sidecar(commit, folder))
        except Exception as exc:  # noqa: BLE001 - shown to the person as the reason
            print(f"Couldn't download engine build {commit[:7]}: {exc}", flush=True)
            return 1
        pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off"]
        # The first install brings in any new dependencies, at the versions in that commit's
        # constraints.txt, which CI tested (SH-9). The second replaces the package itself even when
        # its version string has not changed; it is quiet, because it would print the same lines again.
        steps = [("Installing the engine's packages", ["-c", str(Path(source) / "constraints.txt"), source]),
                 (f"Installing engine build {commit[:7]}", ["--quiet", "--force-reinstall", "--no-deps", source])]
        for title, args in steps:
            print(f"== {title}", flush=True)
            code = subprocess.call(pip + args, stdout=sys.stdout, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if code:
                return code
    error = load_error()
    if error:
        blocked = blocked_files()
        if blocked:
            # The shell shows this as an explanation rather than as pip output.
            print("blocked " + json.dumps(blocked), flush=True)
        print(f"The local engine installed but does not start: {error}", flush=True)
        return 1
    remember_verified(installed_sidecar())
    print("The local engine starts.", flush=True)
    return 0


def release_info(tag: str) -> dict | None:
    """A release's version.json as it is now, or None when it can't be read."""
    try:
        with _get(f"{release_url(tag)}/version.json?nocache={int(time.time())}", timeout=10) as resp:
            info = json.load(resp)
    except Exception:  # noqa: BLE001 - reported by the caller as "can't check yet"
        return None
    return info if isinstance(info, dict) else None


def release_sha256(tag: str, commit: str) -> str | None:
    """The exe's SHA-256 that a release's version.json gives for this build; "" when it names this
    build but gives no valid checksum, which is refused; None when it can't be read or names
    another build (one is being published), so the download can't be checked yet."""
    info = release_info(tag)
    if info is None or info.get("commit") != commit:
        return None
    sha = str(info.get("exe_sha256") or "").lower()
    return sha if re.fullmatch(r"[0-9a-f]{64}", sha) else ""


# download-app's exit code when the release now names another build than the one asked for. The
# shell checks the channel again and downloads the build it names (main.rs, update_app).
MOVED = 3


def _moved(tag: str, commit: str) -> str | None:
    """The build a release names now, when it is a build other than commit."""
    named = (release_info(tag) or {}).get("commit")
    return named if isinstance(named, str) and SHA.fullmatch(named) and named != commit else None


# The exe's release asset names, newest first. Releases publish both (exe_name.rs); a release
# from before kratosforge.exe, such as an earlier build Stable was rolled back to, has only the second.
APP_ASSETS = ("kratosforge.exe", "fabric-desktop.exe")


def _open_app(tag: str, nocache: bool):
    """The exe's download from a release, under whichever name it has."""
    for i, name in enumerate(APP_ASSETS):
        url = f"{release_url(tag)}/{name}" + (f"?nocache={int(time.time())}" if nocache else "")
        try:
            return _get(url, timeout=30)
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or i == len(APP_ASSETS) - 1:
                raise


def download_app(dest: str, commit: str = "") -> int:
    """Download the latest exe. With a commit, check the file is that build, since each
    exe carries its commit; a stale cached copy is fetched once more, then refused. When the
    release names another build, before or after the download, prints "moved <commit>" and
    returns MOVED: the channel was republished since the shell last checked it."""
    tags = release_channels()
    for attempt in range(2):
        if commit and attempt == 0 and (named := _moved(tags[0], commit)):
            print(f"moved {named}", flush=True)
            return MOVED
        try:
            resp = _open_app(tags[0], bool(attempt))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and len(tags) > 1:  # no stable release yet: Preview
                tags.pop(0)
                resp = _open_app(tags[0], True)
            else:
                raise
        digest = hashlib.sha256()
        with resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done, last = 0, -1
            while chunk := resp.read(1 << 16):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                percent = done * 100 // total if total else 0
                if percent != last:
                    print(f"progress {percent}", flush=True)
                    last = percent
        if total and done != total:  # the connection dropped part way: never install half an exe
            print(f"The download stopped at {done} of {total} bytes; trying again", flush=True)
            continue
        if not commit:
            return 0
        expected = release_sha256(tags[0], commit)
        if expected is None and (named := _moved(tags[0], commit)):
            print(f"moved {named}", flush=True)
            return MOVED
        if expected is None:
            print(f"Could not read build {commit[:7]}'s checksum from its release; trying again", flush=True)
            continue
        if not expected:
            # Every release CI publishes gives one; without it the download can't be checked (HD-022).
            print(f"Build {commit[:7]}'s release gives no checksum for the app, so it wasn't installed.", flush=True)
            return 1
        if digest.hexdigest() != expected:
            print(f"The download does not match build {commit[:7]}'s checksum; trying again", flush=True)
            continue
        with open(dest, "rb") as f:
            if commit.encode() in f.read():
                return 0
        print(f"The download was not build {commit[:7]}; trying again", flush=True)
    print(f"The release download is not build {commit[:7]} yet. Try again in a few minutes.", flush=True)
    return 1


def main(argv: list[str]) -> int:
    command, args = argv[0], argv[1:]
    if command == "check":
        print(json.dumps(check()))
        return 0
    if command == "latest":
        print(json.dumps(latest()))
        return 0
    if command == "install-sidecar":
        return install_sidecar(args[0])
    if command == "download-app":
        try:
            return download_app(*args[:2])
        except (OSError, urllib.error.URLError) as exc:  # offline, or the release has no exe
            print(f"The download failed: {exc}", flush=True)
            return 1
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
