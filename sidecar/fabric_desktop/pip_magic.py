"""``%pip`` in a notebook cell on this PC: installs go to the notebook's own environment.

The kernel runs in its own process (Settings > Preview features), and the engine owns installs:
``install_packages`` puts the packages in the notebook's environment (made for it, and attached,
when it has none) and returns a job, which the cell follows until pip finishes. Nothing is
installed into the engine's own Python, and the organization's policy can turn installs off.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import magics

POLL_SECONDS = 1.0
GIVE_UP_SECONDS = 15 * 60 + 60  # the engine stops pip at 15 minutes (environments.INSTALL_SECONDS)

# Flags that only change pip's output or let it upgrade: safe to drop.
_QUIET = {"-q", "--quiet", "-U", "--upgrade", "--no-cache-dir", "--disable-pip-version-check", "--user", "-v",
          "--verbose"}
_SHORT_QUIET = re.compile(r"^-[qUv]+$")  # -qq, -Uq: pip's short flags combine
_REQUIREMENT = re.compile(r"^(?:-r|--requirement=)(.+)$")  # -rreq.txt and --requirement=req.txt
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def specs(args: list[str], read_file: Callable[[str], str]) -> list[str]:
    """The requirement specifiers of ``%pip install`` arguments; ``-r file`` is read line by line."""
    out: list[str] = []
    it = iter(args)
    for arg in it:
        if arg in _QUIET or _SHORT_QUIET.match(arg):
            continue
        if m := _REQUIREMENT.match(arg):
            out += _requirements(read_file(m[1]))
            continue
        if arg in ("-r", "--requirement"):
            path = next(it, None)
            if path is None:
                raise ValueError("%pip install -r needs a file, like: %pip install -r builtin/requirements.txt")
            out += _requirements(read_file(path))
            continue
        if arg.startswith("-"):
            raise ValueError(f"%pip install {arg} isn't supported on this PC: packages come from PyPI by name, "
                             "like %pip install polars==1.9.0.")
        out.append(arg)
    if not out:
        raise ValueError("%pip install needs packages, like: %pip install openpyxl==3.1.5")
    return out


def _requirements(text: str) -> list[str]:
    """Package lines of a requirements file. As in pip, a line ending in a backslash goes on to the
    next, and # starts a comment at the start of a line or after a space (so a#b is kept)."""
    joined = re.sub(r"\\\r?\n", "", text)
    lines = [re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip() for line in joined.splitlines()]
    for line in lines:
        if any(word.startswith("-") for word in line.split()):  # -r, --index-url, or --hash after a package
            raise ValueError(f"The requirements file's line {line!r} isn't supported on this PC; list packages by name.")
    return [line for line in lines if line]


def run(line: str, engine_call: Callable[..., Any] | None, read_file: Callable[[str], str]) -> None:
    """``%pip <command> ...`` in a cell."""
    try:
        args = magics.words(line)
    except ValueError as exc:
        raise ValueError(f"%pip couldn't read {line!r}: {exc}.") from None
    command, rest = (args[0], args[1:]) if args else ("", [])
    if command in ("list", "freeze"):
        for dist in sorted(importlib.metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
            name = dist.metadata["Name"]
            print(f"{name}=={dist.version}" if command == "freeze" else f"{name:<40} {dist.version}")
        return
    if command == "show":
        for name in rest:
            try:
                dist = importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                print(f"WARNING: Package(s) not found: {name}")
                continue
            print(f"Name: {dist.metadata['Name']}\nVersion: {dist.version}\nSummary: {dist.metadata['Summary'] or ''}")
        return
    if command == "uninstall":
        raise ValueError("%pip uninstall isn't run from a cell on this PC. Remove packages on the notebook's "
                         "Environment page; the session restarts with the change.")
    if command != "install":
        raise ValueError("%pip runs install, list, freeze and show on this PC.")
    wanted = specs(rest, read_file)
    if engine_call is None:
        print("Note: %pip install needs Run notebooks in their own process (Settings > Preview features), so "
              "packages go to the notebook's own environment, not the app's. This line was skipped.")
        return
    job = engine_call("install_packages", wanted)
    print(f"Installing {', '.join(wanted)} into environment {job['environment']}...")
    deadline = time.monotonic() + GIVE_UP_SECONDS
    while job["state"] not in ("done", "failed"):
        if time.monotonic() > deadline:
            raise TimeoutError("pip didn't finish in time. The Environment page shows the install.")
        time.sleep(POLL_SECONDS)
        job = engine_call("package_job", job["id"])
    if job["state"] == "failed":
        raise RuntimeError(f"pip couldn't install {', '.join(wanted)}:\n{(job.get('log') or '').strip()[-4000:]}")
    importlib.invalidate_caches()
    missing = [s for s in wanted if not _visible(s)]
    print(f"Installed {', '.join(wanted)} in environment {job['environment']}.")
    if missing:
        print("Restart the session to use them: this session started before the notebook had its environment.")
    else:
        print("A package this session already imported needs a restart to use the new version.")


def _visible(spec: str) -> bool:
    m = _NAME.match(spec)
    if m is None:
        return False
    try:
        importlib.metadata.distribution(m.group(1))
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def read_text(path: str, resources_dir: str) -> str:
    """A requirements file: ``builtin/...`` in the notebook's resources, or a path on this PC."""
    from . import notebook_resources
    resolved = notebook_resources.real_path(path, resources_dir) if resources_dir else None
    return Path(resolved or path).expanduser().read_text(encoding="utf-8-sig")  # Notepad may save a BOM
