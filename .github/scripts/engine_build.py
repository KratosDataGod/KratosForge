"""Makes the engine zip reproducible: the same commit gives the same fabric-desktop-engine.zip, byte
for byte. CI builds the engine folder twice, in two folders, and compare fails the build when the
two zips differ (ci.yml, engine job). Standard library only, so the engine's own python.exe runs it.

    finish FOLDER       run by the engine's own python.exe after pip has installed every package
                        with --no-compile. Compiles each package's .py files to .pyc in one process,
                        in sorted order, as checked-hash pycs naming the file by its path inside the
                        engine (pip's own compile writes its random temp folder into every .pyc and
                        stamps them with the install time). Removes direct_url.json, which names
                        the runner's folder the engine's own wheel was installed from.
    zip FOLDER OUT [PATH ...]
                        zip FOLDER (or only PATHs inside it, each a file or a folder) with every
                        entry in sorted order, every timestamp SOURCE_DATE_EPOCH (the commit's
                        time), and fixed attributes, so the zip depends only on the files' contents.
    compare A B         exit 1 when zips A and B differ, listing each entry that does (and the
                        changed lines of a small text file), so a failed check says what to fix.
"""

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import os
import py_compile
import sys
import time
import zipfile
from pathlib import Path

DOS_DIRECTORY, DOS_ARCHIVE = 0x10, 0x20
TEXT_LIMIT = 64 * 1024  # compare shows the changed lines of text entries up to this size


def finish(folder: Path) -> int:
    """Compile site-packages deterministically and drop the build machine's paths. Returns the count of
    .py files that don't compile on this Python (some packages ship templates or Python 2 code);
    pip skips those too, and they are left as source."""
    site = folder / "Lib" / "site-packages"
    for record in sorted(site.glob("*.dist-info/direct_url.json")):
        rows = record.with_name("RECORD")
        rows.write_bytes(b"".join(line for line in rows.read_bytes().splitlines(keepends=True)
                                  if not line.startswith(f"{record.parent.name}/direct_url.json,".encode())))
        record.unlink()
    skipped = 0
    for source in sorted(p for p in site.rglob("*.py") if p.is_file()):
        relative = source.relative_to(folder).as_posix()
        try:
            py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)), dfile=relative,
                               doraise=True, invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
        except py_compile.PyCompileError:
            skipped += 1
    return skipped


def _date_time() -> tuple[int, int, int, int, int, int]:
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not epoch.isdigit():
        raise SystemExit("SOURCE_DATE_EPOCH must be set to the commit's time (git log -1 --format=%ct)")
    # A zip can't hold a time before 1980.
    y, mo, d, h, mi, s = time.gmtime(max(int(epoch), 315532800))[:6]
    return y, mo, d, h, mi, s


def _entries(folder: Path, paths: list[str]) -> list[tuple[str, Path]]:
    roots = [folder / p for p in paths] if paths else [folder]
    found: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            raise SystemExit(f"{root} doesn't exist")
        for path in [root, *root.rglob("*")] if root.is_dir() else [root]:
            if path.is_symlink() or path.is_junction():  # a junction is Windows' folder link
                raise SystemExit(f"{path} is a link; the engine zip holds only files and folders")
            if path == folder:
                continue
            name = path.relative_to(folder).as_posix() + ("/" if path.is_dir() else "")
            found[name] = path
    # A path's parent folders get entries too, so the zip has the same shape whichever PATHs it was given.
    for name in list(found):
        parts = name.rstrip("/").split("/")[:-1]
        for i in range(1, len(parts) + 1):
            parent = "/".join(parts[:i]) + "/"
            found.setdefault(parent, folder / parent)
    return sorted(found.items())


def make_zip(folder: Path, out: Path, paths: list[str]) -> None:
    when = _date_time()
    with zipfile.ZipFile(out, "w", allowZip64=True) as archive:
        for name, path in _entries(folder, paths):
            info = zipfile.ZipInfo(name, when)
            info.create_system = 0  # MS-DOS attributes, as on Windows, wherever this runs
            if name.endswith("/"):
                info.external_attr = DOS_DIRECTORY
                archive.writestr(info, b"")
                continue
            info.external_attr = DOS_ARCHIVE
            info.compress_type = zipfile.ZIP_DEFLATED  # zlib's default level, 6
            size = path.stat().st_size
            with path.open("rb") as src, archive.open(info, "w", force_zip64=size >= zipfile.ZIP64_LIMIT) as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def compare(a: Path, b: Path) -> list[str]:
    """What differs between zips a and b, or [] when they are the same bytes."""
    if _sha256(a) == _sha256(b):
        return []
    problems = []
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        ia, ib = {i.filename: i for i in za.infolist()}, {i.filename: i for i in zb.infolist()}
        for name in sorted(set(ia) - set(ib)):
            problems.append(f"only in the first build: {name}")
        for name in sorted(set(ib) - set(ia)):
            problems.append(f"only in the second build: {name}")
        for name in sorted(set(ia) & set(ib)):
            x, y = ia[name], ib[name]
            if (x.CRC, x.file_size) != (y.CRC, y.file_size):
                problems.append(f"differs: {name} ({x.file_size} and {y.file_size} bytes)")
                if max(x.file_size, y.file_size) <= TEXT_LIMIT:
                    try:
                        left, right = za.read(name).decode("utf-8"), zb.read(name).decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    diff = difflib.unified_diff(left.splitlines(), right.splitlines(), lineterm="", n=0)
                    problems += [f"    {line}" for line in list(diff)[2:] if not line.startswith("@@")][:20]
            elif (x.date_time, x.external_attr, x.compress_type, x.extra) != (y.date_time, y.external_attr, y.compress_type, y.extra):
                problems.append(f"same contents, different zip metadata: {name}")
        if not problems:
            problems.append("every entry is the same, but the zips' bytes differ (entry order or compression)")
    return problems


def main(argv: list[str]) -> int:
    if argv[:1] == ["finish"] and len(argv) == 2:
        skipped = finish(Path(argv[1]))
        print(f"Compiled the engine's packages; {skipped} .py file(s) don't compile on this Python and stay as source.")
        return 0
    if argv[:1] == ["zip"] and len(argv) >= 3:
        make_zip(Path(argv[1]), Path(argv[2]), argv[3:])
        return 0
    if argv[:1] == ["compare"] and len(argv) == 3:
        problems = compare(Path(argv[1]), Path(argv[2]))
        for problem in problems:
            print(problem)
        if problems:
            print(f"::error::{argv[1]} and {argv[2]} differ: the engine zip is no longer reproducible. "
                  "docs/audits/SUPPLY_CHAIN.md, Reproducible builds, lists what is fixed and how.")
            return 1
        print(f"Same bytes from both builds: sha256 {_sha256(Path(argv[1]))}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
