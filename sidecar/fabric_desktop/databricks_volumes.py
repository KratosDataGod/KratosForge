"""Unity Catalog volumes on this PC (Preview): managed volumes for Databricks notebooks and SQL cells that run here.

A volume is a folder this engine owns, ``%LOCALAPPDATA%\\FabricDesktop\\volumes\\main\\<schema>\\<volume>`` (the
app's data folder, service.data_dir, which is kept out of OneDrive), named in code as Databricks names it:
``/Volumes/<catalog>/<schema>/<volume>/<path>`` or ``dbfs:/Volumes/...``. The catalog is the session's, main, as
in WR-316 (workspace, hive_metastore and spark_catalog name it too); any other catalog is refused by name. A
volume's files are never uploaded anywhere, and an external volume (LOCATION 's3://...') is refused by name, so
nothing here reaches cloud storage. docs/product/databricks-volumes-local.md has the design and its sources.

Who uses it:

- SQL cells (engines.local_sql): CREATE, DROP, SHOW, DESCRIBE, ALTER ... RENAME TO and COMMENT ON VOLUME, and
  LIST, written by ``statements`` as DuckDB SQL that calls this module's functions on the session's connection
  (``register``), so each runs in its place in the cell; read_files and csv.`/Volumes/...` by ``reads``.
- dbutils.fs and %fs (dbutils_local): ``local_path`` and ``uc_path``.
- spark.read and df.write (spark_local): ``local_path``.
- Workspaces > Databricks > Volumes on this PC (databricks_volumes_service.py): ``volumes``, ``describe``, ``files``,
  ``create``, ``drop``, ``rename``, ``set_comment``, ``local_path`` and ``check_tree``.

Security: every path is read by ``parse`` before anything touches the disk. It takes ``/`` as the only separator
and refuses ``.`` and ``..``, a backslash, a colon (a drive, a stream), the characters and device names Windows
can't hold in a name, and a name ending in a dot or a space (which Windows drops, so it would name another file).
``local_path`` then refuses a path whose real location (links and junctions followed) is outside the volume's
folder, and a volume folder that is itself a link; ``check_tree`` does the same for every entry below a folder
before a whole folder is copied, moved, removed or read.
"""

from __future__ import annotations

import datetime
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, NamedTuple

from . import jsonfile, known_folders, trees

CATALOG = "main"
SESSION_CATALOGS = ("main", "workspace", "hive_metastore", "spark_catalog")  # databricks_builtins_sql's (WR-316)
_DUCKDB_CATALOGS = {"system", "temp", "memory"}
_PREFIXES = ("dbfs:/Volumes/", "/Volumes/")
_WAREHOUSE = "Run it on Databricks"
MANAGED = "MANAGED"
_NAME_LIMIT = 255  # "Object names cannot exceed 255 characters." (Databricks, Names)
# Unity Catalog refuses a period, a space, a forward slash, control characters and DEL in a name (Names); a
# folder on Windows can't hold the rest, so a volume or schema named with one is refused by name too.
_UC_FORBIDDEN = set("./ ") | {chr(c) for c in range(32)} | {chr(127)}
_WINDOWS_FORBIDDEN = set('\\:*?"<>|')
_DEVICE = re.compile(r"(?i)(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]) *(\..*)?")  # "con .txt" is CON too
# What a refusal or error from here starts with, so local_sql shows it without DuckDB's "Invalid Input Error: ".
MESSAGE_STARTS = ("[VOLUME_ALREADY_EXISTS]", "[SCHEMA_NOT_FOUND]", "The volume ", "Volumes on this PC:")


def root() -> Path:
    """Where volumes are kept: the app's data folder (service.data_dir) + ``volumes``. Read each time, so a test
    that points LOCALAPPDATA elsewhere gets its own."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "FabricDesktop" / "volumes"


def _refused(text: str) -> ValueError:
    return ValueError(f"Volumes on this PC: {text}")


def _is_link(path: str | os.PathLike[str]) -> bool:
    return os.path.islink(path) or bool(getattr(os.path, "isjunction", lambda _p: False)(path))


def _inside(base: str, path: str) -> bool:
    b, p = os.path.normcase(base), os.path.normcase(path)
    return p == b or p.startswith(b.rstrip(os.sep) + os.sep)


# ---- names and paths

def check_name(kind: str, name: str) -> str:
    """A schema's or volume's name as Unity Catalog stores it (lowercase), or a refusal that names what's wrong."""
    text = str(name)
    if not text:
        raise _refused(f"a {kind} needs a name.")
    if len(text) > _NAME_LIMIT:
        raise _refused(f"the {kind} name {text[:40]}... is longer than the {_NAME_LIMIT} characters Unity Catalog "
                       "allows.")
    bad = sorted({c for c in text if c in _UC_FORBIDDEN | _WINDOWS_FORBIDDEN})
    if bad:
        shown = ", ".join("a space" if c == " " else repr(c) if c.isprintable() else f"U+{ord(c):04X}" for c in bad)
        raise _refused(f"the {kind} name {text!r} holds {shown}, which a {kind} name can't hold on this PC "
                       "(Unity Catalog refuses . / space and control characters; Windows refuses \\ : * ? \" < > | "
                       "in a folder name). Pick another name.")
    if _DEVICE.fullmatch(text):
        raise _refused(f"the {kind} name {text!r} is a device name on Windows, so it can't be a folder. Pick another "
                       "name.")
    return text.lower()


class VolumePath(NamedTuple):
    """A path in a volume: its schema and volume as stored, and the parts below the volume's folder."""

    schema: str
    volume: str
    parts: tuple[str, ...]

    @property
    def full_name(self) -> str:
        return f"{CATALOG}.{self.schema}.{self.volume}"

    def uc_path(self, scheme: bool = False) -> str:
        """``/Volumes/main/<schema>/<volume>[/<parts>]``, with ``dbfs:`` in front when ``scheme``."""
        tail = "".join("/" + p for p in self.parts)
        return f"{'dbfs:' if scheme else ''}/Volumes/{CATALOG}/{self.schema}/{self.volume}{tail}"


def is_volume_path(path: Any) -> bool:
    p = str(path)
    return isinstance(path, (str, os.PathLike)) and (p.startswith(_PREFIXES) or p in ("/Volumes", "dbfs:/Volumes"))


def catalog_refusal(name: str, said: str) -> ValueError:
    kind = ("a catalog of this PC's own engine" if name.lower() in _DUCKDB_CATALOGS else "a Unity Catalog catalog")
    others = SESSION_CATALOGS[1:]
    return ValueError(f"{said} can't run on this PC the way it runs in Databricks, because {name} is {kind}, and "
                      f"this PC keeps volumes only in the catalog main ({', '.join(others[:-1])} and {others[-1]} "
                      f"name it too), so nothing ran. {_WAREHOUSE}, or use the catalog main.")


def main_schema_refusal(said: str) -> ValueError:
    return ValueError(f"{said} can't run on this PC the way it runs in Databricks, because this PC's engine already "
                      "uses the name main for the schema shown as default, so nothing ran. Use default or another "
                      "schema name.")


def names(catalog: str | None, schema: str, volume: str, said: str) -> tuple[str, str]:
    """``(schema, volume)`` as stored, from a name's parts as written; another catalog or the schema main refused."""
    if catalog is not None and catalog.lower() not in SESSION_CATALOGS:
        raise catalog_refusal(catalog, said)
    if schema.lower() == "main":
        raise main_schema_refusal(said)
    return check_name("schema", schema), check_name("volume", volume)


def parse(path: str) -> VolumePath:
    """``/Volumes/<catalog>/<schema>/<volume>[/<path>]`` (or ``dbfs:/Volumes/...``) read without touching the disk.
    Refused by name: another catalog, a path that stops above a volume, and any part that could lead somewhere
    else on this PC (``..``, ``.``, a backslash, a drive or stream colon, a name Windows changes or can't hold)."""
    text = str(path)
    if "\x00" in text:
        raise _refused("a path can't hold a NUL character.")
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix):]
            break
    else:
        rest = "" if text in ("/Volumes", "dbfs:/Volumes") else None  # type: ignore[assignment]
        if rest is None:
            raise _refused(f"{text} isn't a /Volumes path. Write /Volumes/<catalog>/<schema>/<volume>/<path>.")
    if "\\" in rest:
        raise _refused(f"{text} holds a backslash. A volume path uses / only, so it can't name another folder on "
                       "this PC.")
    parts = [p for p in rest.split("/") if p != ""]
    if len(parts) < 3:
        raise _refused(f"{text} stops above a volume; this PC reads and lists files inside one. Write "
                       "/Volumes/<catalog>/<schema>/<volume>/<path>.")
    catalog, schema, volume, *below = parts
    schema, volume = names(catalog, schema, volume, f"The path {text}")
    for part in below:
        if part in (".", ".."):
            raise _refused(f"{text} holds {part!r}. A volume path can't step out of its folder or name the same one "
                           "twice; write the path without it.")
        bad = sorted({c for c in part if c in _WINDOWS_FORBIDDEN or ord(c) < 32})
        if bad:
            shown = ", ".join(repr(c) if c.isprintable() else f"U+{ord(c):04X}" for c in bad)
            raise _refused(f"{text} holds {shown} in {part!r}, which a file name on this PC can't hold.")
        if part.endswith((".", " ")) or _DEVICE.fullmatch(part):
            raise _refused(f"{text}: {part!r} isn't a name Windows keeps as written (it drops a final dot or space, "
                           "and keeps device names such as CON or NUL for itself). Rename it.")
    return VolumePath(schema, volume, tuple(below))


def _schema_folder(schema: str) -> Path:
    return root() / CATALOG / schema


def _meta_file(schema: str, volume: str) -> Path:
    """A volume's comment, owner and creation time; beside its folder (a volume name can't start with a dot)."""
    return _schema_folder(schema) / f".{volume}.volume.json"


def not_found(full_name: str) -> ValueError:
    # Databricks' pages say only that an error is raised for a volume that isn't there, and name no error class
    # for it, so this message names none.
    return ValueError(f"The volume {full_name} cannot be found. Verify the spelling of the catalog, schema and "
                      "volume names, or create it with CREATE VOLUME.")


def already_exists(full_name: str) -> ValueError:
    # VOLUME_ALREADY_EXISTS is the error class CREATE VOLUME names; its page gives no message text, so this is ours.
    return ValueError(f"[VOLUME_ALREADY_EXISTS] The volume {full_name} already exists, so nothing was changed. Use "
                      "another name, drop that volume first, or write CREATE VOLUME IF NOT EXISTS.")


def volume_folder(schema: str, volume: str) -> Path:
    """A volume's folder, once it is there and is a folder of its own inside the volume root (not a link)."""
    base = root()
    folder = base / CATALOG / schema / volume
    for step in (base / CATALOG, base / CATALOG / schema, folder):
        if _is_link(step):
            raise _refused(f"{step} is a link, so the volume {CATALOG}.{schema}.{volume} isn't used: a volume's "
                           "folder must be a folder of its own on this PC.")
    if not folder.is_dir():
        raise not_found(f"{CATALOG}.{schema}.{volume}")
    if base.exists() and not _inside(os.path.realpath(base), os.path.realpath(folder)):
        raise _refused(f"the folder of {CATALOG}.{schema}.{volume} leads outside {base}, so it isn't used.")
    return folder


def local_path(path: str) -> Path:
    """The file or folder on this PC that a volume path names (it need not be there yet). The volume must exist.
    Refused when its real location, links and junctions followed, is outside the volume's folder."""
    where = parse(path)
    folder = volume_folder(where.schema, where.volume)
    target = folder.joinpath(*where.parts)
    if not _inside(os.path.realpath(folder), os.path.realpath(target)):
        raise _refused(f"{path} leads outside its volume's folder (through a link or junction), so it isn't read "
                       "or written.")
    return target


def check_tree(folder: str | os.PathLike[str], shown: str) -> None:
    """Refuse a folder that holds a link or junction, before all of it is copied, moved, removed or read: what a
    link points at may be outside the volume."""
    for dirpath, dirs, files in os.walk(folder):
        for name in dirs + files:
            if _is_link(os.path.join(dirpath, name)):
                rel = os.path.relpath(os.path.join(dirpath, name), folder).replace(os.sep, "/")
                raise _refused(f"{shown} holds a link or junction ({rel}), so nothing was done with it: what it "
                               "points at may be outside the volume. Remove the link, or work on the files one by "
                               "one.")


def uc_path(local: str | os.PathLike[str], scheme: bool = True) -> str | None:
    """The ``dbfs:/Volumes/...`` path (``/Volumes/...`` without ``scheme``) of a local path inside a volume's
    folder, or None when it isn't in one."""
    base = os.path.normcase(os.path.abspath(root() / CATALOG))
    here = os.path.abspath(local)
    if not _inside(base, os.path.normcase(here)) or os.path.normcase(here) == base:
        return None
    parts = Path(here).relative_to(os.path.abspath(root() / CATALOG)).parts
    if len(parts) < 2:
        return None
    return VolumePath(parts[0], parts[1], tuple(parts[2:])).uc_path(scheme)


# ---- the volumes themselves (SQL statements and a screen)

class VolumeInfo(NamedTuple):
    """One volume: DESCRIBE VOLUME's columns (name, catalog, database, owner, storageLocation, volumeType,
    comment) and what a screen shows beside them."""

    name: str
    catalog: str
    database: str
    owner: str
    storage_location: str
    volume_type: str
    comment: str | None
    path: str        # /Volumes/main/<schema>/<volume>
    folder: str      # the folder on this PC
    items: int       # entries directly in the volume's folder
    created: int     # milliseconds since 1970

    def as_dict(self) -> dict[str, Any]:
        return self._asdict()


class FileEntry(NamedTuple):
    """One entry in a volume, as LIST answers (path, name, size, modification_time) plus whether it's a folder."""

    path: str              # dbfs:/Volumes/main/<schema>/<volume>/<path>
    name: str
    size: int              # bytes; 0 for a folder
    modification_time: int  # milliseconds since 1970
    is_dir: bool

    def as_dict(self) -> dict[str, Any]:
        return self._asdict()


def _owner() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def _millis(seconds: float) -> int:
    return int(seconds * 1000)


def _storage_location(folder: Path) -> str:
    return "file:" + str(folder).replace("\\", "/")


def _info(schema: str, volume: str, folder: Path) -> VolumeInfo:
    meta = jsonfile.read_json(_meta_file(schema, volume), {})
    meta = meta if isinstance(meta, dict) else {}
    try:
        items = sum(1 for _ in os.scandir(folder))
    except OSError:
        items = 0
    created = meta.get("created")
    if not isinstance(created, int):
        created = _millis(folder.stat().st_mtime)
    comment = meta.get("comment")
    return VolumeInfo(volume, CATALOG, schema, str(meta.get("owner") or _owner()), _storage_location(folder), MANAGED,
                      comment if isinstance(comment, str) else None, VolumePath(schema, volume, ()).uc_path(),
                      str(folder), items, created)


def schema_has_volumes(schema: str) -> bool:
    folder = _schema_folder(schema)
    return folder.is_dir() and not _is_link(folder) and any(e.is_dir() for e in os.scandir(folder))


def volumes(schema: str | None = None) -> list[VolumeInfo]:
    """The volumes on this PC, by schema then name; only ``schema``'s when given. Links are left out."""
    base = root() / CATALOG
    if not base.is_dir() or _is_link(base):
        return []
    schemas = [schema] if schema is not None else sorted(e.name for e in os.scandir(base) if e.is_dir()
                                                            and not _is_link(e.path))
    found: list[VolumeInfo] = []
    for s in schemas:
        folder = base / s
        if not folder.is_dir() or _is_link(folder):
            continue
        for e in sorted(os.scandir(folder), key=lambda e: e.name):
            if e.is_dir(follow_symlinks=False) and not _is_link(e.path) and not e.name.startswith("."):
                found.append(_info(s, e.name, Path(e.path)))
    return found


def describe(schema: str, volume: str) -> VolumeInfo:
    return _info(schema, volume, volume_folder(schema, volume))


_lock = threading.Lock()  # one change to the volumes at a time in this process


def create(schema: str, volume: str, comment: str | None = None, if_not_exists: bool = False) -> bool:
    """Make a managed volume: its folder, and its comment and owner beside it. False when it was there and
    ``if_not_exists``; VOLUME_ALREADY_EXISTS without it (the error class CREATE VOLUME names)."""
    schema, volume = check_name("schema", schema), check_name("volume", volume)
    if schema == "main":
        raise main_schema_refusal(f"CREATE VOLUME {schema}.{volume}")
    refusal = known_folders.onedrive_refusal(root(), "a volume's files")
    if refusal:
        raise _refused(refusal)
    with _lock:
        folder = _schema_folder(schema) / volume
        if folder.exists() or _is_link(folder):
            if if_not_exists:
                return False
            raise already_exists(f"{CATALOG}.{schema}.{volume}")
        for step in (root() / CATALOG, _schema_folder(schema)):
            if _is_link(step):
                raise _refused(f"{step} is a link, so no volume was made there.")
        folder.mkdir(parents=True)
        jsonfile.write_json(_meta_file(schema, volume), {"comment": comment, "owner": _owner(),
                                                         "created": _millis(datetime.datetime.now().timestamp())})
    return True


def drop(schema: str, volume: str, if_exists: bool = False) -> bool:
    """Remove a volume and every file in it, at once: this PC keeps no copy to undrop. False when it wasn't
    there and ``if_exists``."""
    with _lock:
        try:
            folder = volume_folder(schema, volume)
        except ValueError:
            if if_exists and not (_schema_folder(schema) / volume).exists():
                return False
            raise
        trees.remove(folder)  # rmtree doesn't follow a link or junction inside: it removes the link only
        jsonfile.unlink(_meta_file(schema, volume))
    return True


def rename(schema: str, volume: str, new: str) -> None:
    """ALTER VOLUME ... RENAME TO: the folder and its comment move; the schema stays (Databricks: "the schema must
    not change")."""
    new = check_name("volume", new)
    with _lock:
        folder = volume_folder(schema, volume)
        target = _schema_folder(schema) / new
        if target.exists() or _is_link(target):
            raise already_exists(f"{CATALOG}.{schema}.{new}")
        jsonfile.replace(folder, target)  # waits out a reader holding the folder, as every swap here does
        meta = _meta_file(schema, volume)
        if meta.exists():
            jsonfile.replace(meta, _meta_file(schema, new))


def set_comment(schema: str, volume: str, comment: str | None) -> None:
    """COMMENT ON VOLUME ... IS 'text' (or NULL, which removes it)."""
    with _lock:
        folder = volume_folder(schema, volume)
        meta = jsonfile.read_json(_meta_file(schema, volume), {})
        meta = meta if isinstance(meta, dict) else {}
        meta.setdefault("owner", _owner())
        meta.setdefault("created", _millis(folder.stat().st_mtime))
        meta["comment"] = comment
        jsonfile.write_json(_meta_file(schema, volume), meta)


def files(path: str, limit: int | None = None) -> list[FileEntry]:
    """What a volume path holds, folders first then files, each by name; a file lists as itself. LIST and a
    screen read this. A link inside is listed (its name only); reading through it is refused by local_path."""
    where = parse(path)
    target = local_path(path)
    if not target.exists():
        raise FileNotFoundError(f"{where.uc_path()} doesn't exist in the volume {where.full_name}.")

    def entry(p: Path, parts: tuple[str, ...]) -> FileEntry:
        st = p.lstat()
        is_dir = p.is_dir() and not _is_link(p)
        return FileEntry(VolumePath(where.schema, where.volume, parts).uc_path(scheme=True), p.name,
                         0 if is_dir else st.st_size, _millis(st.st_mtime), is_dir)

    if not target.is_dir():
        return [entry(target, where.parts)]
    found = sorted(os.scandir(target), key=lambda e: (not (e.is_dir() and not _is_link(e.path)), e.name.lower()))
    listed = [entry(Path(e.path), (*where.parts, e.name)) for e in found]
    return listed[:limit] if limit is not None else listed


# ---- SQL: the statements, written as calls to this module on the session's connection

class _Lexer:
    """The tokens of a volume statement (and where a cell's statements end): words, `quoted` names, strings
    (adjacent ones joined, as Spark joins them), numbers and punctuation. Comments are left out."""

    _TOKEN = re.compile(r"""\s+|--[^\n]*|/\*.*?\*/|`(?:[^`]|``)*`|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[A-Za-z0-9_]+|
                        [.;,()=]|\S""", re.S | re.X)

    def __init__(self, text: str) -> None:
        self.tokens: list[tuple[str, str, int, int]] = []  # kind, value, start, end
        pos = 0
        while pos < len(text):
            m = self._TOKEN.match(text, pos)
            if m is None:
                raise ValueError("unreadable")
            t = m.group(0)
            if t[0] in "'\"" and (len(t) < 2 or t[-1] != t[0]):
                raise ValueError("unterminated")
            if t[0] == "`" and (len(t) < 2 or t[-1] != "`"):
                raise ValueError("unterminated")
            if t.startswith("/*") and not t.endswith("*/"):
                raise ValueError("unterminated")
            if not (t.isspace() or t.startswith(("--", "/*"))):
                if t[0] in "'\"":
                    kind, value = "string", _unescape(t[1:-1])
                elif t[0] == "`":
                    kind, value = "name", t[1:-1].replace("``", "`")
                elif re.fullmatch(r"[0-9]+", t):
                    kind, value = "number", t
                elif re.fullmatch(r"[A-Za-z0-9_]+", t):
                    kind, value = "word", t
                else:
                    kind, value = "punct", t
                if kind == "string" and self.tokens and self.tokens[-1][0] == "string":
                    k, v, s, _e = self.tokens[-1]
                    self.tokens[-1] = (k, v + value, s, m.end())
                else:
                    self.tokens.append((kind, value, m.start(), m.end()))
            pos = m.end()


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\x00", "b": "\b", "Z": "\x1a"}


def _unescape(body: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), body, flags=re.S)


def _statement_spans(code: str) -> list[tuple[int, int, list[tuple[str, str, int, int]]]] | None:
    """Each statement of a cell as (start, end, tokens), split at semicolons; None when the cell can't be read
    (sqlglot then says why)."""
    try:
        tokens = _Lexer(code).tokens
    except ValueError:
        return None
    spans: list[tuple[int, int, list[tuple[str, str, int, int]]]] = []
    current: list[tuple[str, str, int, int]] = []
    for t in [*tokens, ("punct", ";", len(code), len(code))]:
        if t[0] == "punct" and t[1] == ";":
            if current:
                spans.append((current[0][2], current[-1][3], current))
            current = []
        else:
            current.append(t)
    return spans


class _Reader:
    """Reads one volume statement's tokens."""

    def __init__(self, tokens: list[tuple[str, str, int, int]], said: str) -> None:
        self.tokens, self.i, self.said = tokens, 0, said

    def peek(self, *words: str) -> bool:
        t = self.tokens[self.i] if self.i < len(self.tokens) else None
        return t is not None and t[0] == "word" and t[1].upper() in words

    def take(self, *words: str) -> bool:
        if self.peek(*words):
            self.i += 1
            return True
        return False

    def expect(self, *words: str) -> None:
        if not self.take(*words):
            raise self.syntax(f"expected {' or '.join(words)}")

    def done(self) -> bool:
        return self.i >= len(self.tokens)

    def syntax(self, what: str) -> ValueError:
        near = self.tokens[self.i][1] if self.i < len(self.tokens) else "the end of the statement"
        return ValueError(f"[PARSE_SYNTAX_ERROR] {self.said}: {what}, near {near!r}.")

    def string(self) -> str | None:
        """A string literal, or NULL (None)."""
        t = self.tokens[self.i] if self.i < len(self.tokens) else None
        if t is not None and t[0] == "string":
            self.i += 1
            return t[1]
        if self.take("NULL"):
            return None
        raise self.syntax("expected a string")

    def name(self) -> list[str]:
        parts = [self._part()]
        while self.i < len(self.tokens) and self.tokens[self.i][:2] == ("punct", "."):
            self.i += 1
            parts.append(self._part())
        return parts

    def _part(self) -> str:
        t = self.tokens[self.i] if self.i < len(self.tokens) else None
        if t is None or t[0] not in ("word", "name", "number"):
            raise self.syntax("expected a name")
        self.i += 1
        return t[1]

    def end(self) -> None:
        if not self.done():
            raise self.syntax("unexpected text")


def _q(text: str | None) -> str:
    return "NULL" if text is None else "'" + text.replace("'", "''") + "'"


_CURRENT = "lower(CASE current_schema() WHEN 'main' THEN 'default' ELSE current_schema() END)"


def _target(parts: list[str], said: str) -> tuple[str, str]:
    """A [catalog.][schema.]volume name as (SQL for its schema, the volume as stored)."""
    if len(parts) > 3:
        raise ValueError(f"[PARSE_SYNTAX_ERROR] {said}: a volume is named as volume, schema.volume or "
                         "catalog.schema.volume.")
    catalog = parts[0] if len(parts) == 3 else None
    if len(parts) == 1:
        return _CURRENT, check_name("volume", parts[0])
    schema, volume = names(catalog, parts[-2], parts[-1], said)
    return _q(schema), volume


def _schema_check(schema_sql: str, schema_shown: str) -> str:
    """SQL that stops the cell with SCHEMA_NOT_FOUND (as WR-316's USE does) when the schema is neither in the
    session nor holds volumes on this PC. default is always there."""
    duck = f"CASE {schema_sql} WHEN 'default' THEN 'main' ELSE {schema_sql} END"
    message = (f"[SCHEMA_NOT_FOUND] The schema `main`.`{schema_shown}` cannot be found. Verify the spelling and "
               "correctness of the schema and catalog.")
    return (f"SELECT error({_q(message)}) WHERE NOT EXISTS (SELECT 1 FROM duckdb_schemas() WHERE database_name = "
            f"current_database() AND lower(schema_name) = {duck}) AND __kf_volume('schema', '', {schema_sql}, '', "
            "NULL, false) = ''")


def _change(op: str, schema_sql: str, volume: str, arg: str | None = None, flag: bool = False) -> list[str]:
    """A change, run once in its place in the cell; it gives no rows, as a DDL statement gives none."""
    call = (f"__kf_volume({_q(op)}, {_q(uuid.uuid4().hex)}, {schema_sql}, {_q(volume)}, {_q(arg)}, "
            f"{'true' if flag else 'false'})")
    return [f"SET VARIABLE __kf_volume_done = (SELECT CASE WHEN m <> '' THEN error(m) END FROM (SELECT {call} AS m))",
            "RESET VARIABLE __kf_volume_done"]


def _exists_check(schema_sql: str, volume: str) -> str:
    return (f"SELECT error(m) FROM (SELECT __kf_volume('exists', '', {schema_sql}, {_q(volume)}, NULL, false) AS m) "
            "WHERE m <> ''")


def _create(r: _Reader, said: str) -> list[str]:
    if r.take("EXTERNAL"):
        raise ValueError(f"CREATE EXTERNAL VOLUME can't run on this PC the way it runs in Databricks, because an "
                         "external volume's files are in cloud storage, which this PC never reaches, so nothing ran. "
                         f"{_WAREHOUSE}, or leave out EXTERNAL and LOCATION to make a managed volume here.")
    r.expect("VOLUME")
    if_not_exists = False
    if r.take("IF"):
        r.expect("NOT")
        r.expect("EXISTS")
        if_not_exists = True
    parts = r.name()
    comment = None
    while not r.done():
        if r.take("LOCATION"):
            raise ValueError("CREATE VOLUME ... LOCATION can't run on this PC the way it runs in Databricks, because "
                             "a location is in cloud storage, which this PC never reaches, so nothing ran. "
                             f"{_WAREHOUSE}, or leave out LOCATION to make a managed volume here.")
        if r.take("COMMENT"):
            comment = r.string()
            continue
        raise r.syntax("expected LOCATION or COMMENT")
    schema_sql, volume = _target(parts, said)
    shown = parts[-2] if len(parts) > 1 else ""
    checks = [_schema_check(schema_sql, shown.lower())] if len(parts) > 1 else []
    return [*checks, *_change("create", schema_sql, volume, comment, if_not_exists)]


def _drop(r: _Reader, said: str) -> list[str]:
    r.expect("VOLUME")
    if_exists = False
    if r.take("IF"):
        r.expect("EXISTS")
        if_exists = True
    schema_sql, volume = _target(r.name(), said)
    r.end()
    return _change("drop", schema_sql, volume, None, if_exists)


def _alter(r: _Reader, said: str) -> list[str]:
    r.expect("VOLUME")
    schema_sql, volume = _target(r.name(), said)
    if r.take("RENAME"):
        r.expect("TO")
        new = r.name()
        r.end()
        if len(new) > 1:  # Databricks: "the schema must not change"
            target_schema, new_volume = _target(new, said)
            moved = ("ALTER VOLUME ... RENAME TO can't move a volume to another schema, so nothing ran. Databricks "
                     "stops it too, because the schema must not change. Give the new name without its schema.")
            return [f"SELECT error({_q(moved)}) WHERE {target_schema} <> {schema_sql}",
                    *_change("rename", schema_sql, volume, new_volume)]
        return _change("rename", schema_sql, volume, check_name("volume", new[0]))
    if r.take("SET"):
        clause = "SET OWNER TO" if r.peek("OWNER") else "SET TAGS" if r.peek("TAGS") else None
    elif r.peek("OWNER"):
        clause = "OWNER TO"
    else:
        clause = "UNSET TAGS" if r.take("UNSET") and r.peek("TAGS") else None
    if clause is not None:
        why = ("this PC keeps no tags on a volume" if clause.endswith("TAGS") else
               "a volume here belongs to the person signed in to this PC, the only principal it has")
        raise ValueError(f"ALTER VOLUME ... {clause} can't run on this PC the way it runs in Databricks, because "
                         f"{why}, so nothing ran. {_WAREHOUSE}, or leave the statement out.")
    raise r.syntax("expected RENAME TO, OWNER TO, SET TAGS or UNSET TAGS")


def _comment(r: _Reader, said: str) -> list[str]:
    r.expect("ON")
    r.expect("VOLUME")
    schema_sql, volume = _target(r.name(), said)
    r.expect("IS")
    text = r.string()
    r.end()
    return _change("comment", schema_sql, volume, text)


def _show(r: _Reader, said: str) -> list[str]:
    r.expect("VOLUMES")
    schema_sql, checks = _CURRENT, []
    if r.take("IN", "FROM"):
        parts = r.name()
        if len(parts) > 2:
            raise ValueError(f"[PARSE_SYNTAX_ERROR] {said}: a schema is named as schema or catalog.schema.")
        if len(parts) == 2 and parts[0].lower() not in SESSION_CATALOGS:
            raise catalog_refusal(parts[0], said)
        if parts[-1].lower() == "main":
            raise main_schema_refusal(said)
        schema_sql = _q(check_name("schema", parts[-1]))
        checks = [_schema_check(schema_sql, parts[-1].lower())]
    r.take("LIKE")
    pattern = r.string() if not r.done() else None
    r.end()
    if pattern is not None:
        _pattern(pattern, said)  # refused now when it isn't a pattern Databricks reads
    return [*checks, (f"SELECT r.database AS database, r.volumeName AS volumeName FROM (SELECT unnest("
                     f"__kf_show_volumes({schema_sql}, {_q(pattern)})) AS r)")]


def _describe(r: _Reader, said: str) -> list[str]:
    r.expect("VOLUME")
    schema_sql, volume = _target(r.name(), said)
    r.end()
    columns = ", ".join(f"r.{c} AS {c}" for c in ("name", "catalog", "database", "owner", "storageLocation",
                                                   "volumeType", "comment"))
    return [_exists_check(schema_sql, volume),
            f"SELECT {columns} FROM (SELECT unnest(__kf_describe_volume({schema_sql}, {_q(volume)})) AS r)"]


def _list(r: _Reader, said: str) -> list[str]:
    url = r.string()
    if url is None:
        raise r.syntax("expected the URL to list")
    if r.take("WITH"):
        raise ValueError("LIST ... WITH (CREDENTIAL ...) can't run on this PC the way it runs in Databricks, "
                         "because a credential reaches cloud storage, which this PC never does, so nothing ran. "
                         f"{_WAREHOUSE}, or LIST a /Volumes path on this PC.")
    limit = None
    if r.take("LIMIT"):
        t = r.tokens[r.i] if not r.done() else None
        if t is None or t[0] != "number" or int(t[1]) < 1:
            raise r.syntax("LIMIT takes a whole number of 1 or more")
        r.i += 1
        limit = int(t[1])
    r.end()
    if not is_volume_path(url):
        raise ValueError(f"LIST {url!r} can't run on this PC the way it runs in Databricks, because it lists cloud "
                         f"storage, which this PC never reaches, so nothing ran. {_WAREHOUSE}, or LIST a /Volumes "
                         "path on this PC.")
    parse(url)  # refused now when the path can't name a file in a volume
    return [f"SELECT error(m) FROM (SELECT __kf_volume('list', '', '', '', {_q(url)}, false) AS m) WHERE m <> ''",
            ("SELECT r.path AS path, r.name AS name, r.size AS size, r.modification_time AS modification_time FROM "
            f"(SELECT unnest(__kf_list({_q(url)}, {limit if limit is not None else -1})) AS r)")]


def _connector_only(word: str) -> list[str]:
    raise ValueError(f"{word} can't run here, so nothing ran. Databricks stops it too in notebooks and the SQL "
                     "editor, because PUT INTO, GET and REMOVE run only through its SQL connectors and drivers. "
                     "Use dbutils.fs.cp in a notebook on this PC to copy a file into or out of a volume.")


def _undrop(_r: _Reader, _said: str) -> list[str]:
    raise ValueError("UNDROP VOLUME can't run on this PC the way it runs in Databricks, because DROP VOLUME here "
                     "removes a volume's files at once and keeps no copy, so nothing ran. Make the volume again "
                     "with CREATE VOLUME.")


_STATEMENTS = {"CREATE": _create, "DROP": _drop, "ALTER": _alter, "COMMENT": _comment, "SHOW": _show,
               "DESCRIBE": _describe, "DESC": _describe, "LIST": _list, "UNDROP": _undrop}


def _is_volume_statement(tokens: list[tuple[str, str, int, int]]) -> str | None:
    words = [t[1].upper() if t[0] == "word" else "" for t in tokens[:3]]
    first = words[0]
    if first in ("PUT", "GET", "REMOVE") and len(tokens) > 1 and tokens[1][0] == "string":
        return first
    if first == "LIST" and len(tokens) > 1 and tokens[1][0] == "string":
        return first
    if first == "SHOW" and words[1:2] == ["VOLUMES"]:
        return first
    if first == "COMMENT" and words[1:3] == ["ON", "VOLUME"]:
        return first
    if first == "CREATE" and ("VOLUME" in words[1:2] or words[1:3] == ["EXTERNAL", "VOLUME"]):
        return first
    if first in ("DROP", "ALTER", "DESCRIBE", "DESC", "UNDROP") and words[1:2] == ["VOLUME"]:
        return first
    return None


PLACEHOLDER = "__kf_volume_statement_"


def prepare(code: str) -> tuple[str, dict[str, list[str]]]:
    """``code`` with each volume statement in it (which sqlglot can't read, or reads as text only) replaced by a
    placeholder query, and the DuckDB SQL that runs in its place, by placeholder. A statement this PC can't run
    as Databricks does is refused by name here, before anything runs."""
    spans = _statement_spans(code)
    if spans is None:
        return code, {}
    out: list[str] = []
    written: dict[str, list[str]] = {}
    last = 0
    for start, end, tokens in spans:
        word = _is_volume_statement(tokens)
        if word is None:
            continue
        said = " ".join(code[start:end].split())
        said = said if len(said) <= 80 else said[:77] + "..."
        if word in ("PUT", "GET", "REMOVE"):
            _connector_only(word)
        r = _Reader(tokens, said)
        r.i = 1
        key = f"{PLACEHOLDER}{len(written)}_{uuid.uuid4().hex[:8]}"
        written[key] = _STATEMENTS[word](r, said)
        out += [code[last:start], f"SELECT '{key}'"]
        last = end
    if not written:
        return code, {}
    out.append(code[last:])
    return "".join(out), written


def placeholder(statement: Any) -> str | None:
    """The key of a placeholder query ``prepare`` wrote, or None."""
    from sqlglot import exp
    if isinstance(statement, exp.Select) and len(statement.expressions) == 1 and not statement.args.get("from_"):
        only = statement.expressions[0]
        only = only.this if isinstance(only, exp.Alias) else only  # Spark's column name, which spark_fixes gives
        if isinstance(only, exp.Literal) and only.is_string and only.name.startswith(PLACEHOLDER):
            return only.name
    return None


def _pattern(pattern: str, said: str) -> re.Pattern[str]:
    """SHOW VOLUMES' LIKE pattern as Databricks documents it: a regular expression, except that * alone matches
    any characters and | separates patterns any one of which may match; blanks at the ends are trimmed, and case
    is ignored."""
    pieces = [p.replace("*", ".*") for p in pattern.strip().split("|")]
    try:
        return re.compile("|".join(f"(?:{p})" for p in pieces), re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"[PARSE_SYNTAX_ERROR] {said}: the pattern {pattern!r} isn't a regular expression "
                         f"({exc}).") from None


# ---- the functions the statements call, on a session's connection

_done: dict[str, str] = {}  # a change's token -> its answer, so a change runs once however often DuckDB calls


def _answer(op: str, token: str, schema: str | None, volume: str | None, arg: str | None, flag: bool | None) -> str:
    if token and token in _done:
        return _done[token]
    try:
        schema = check_name("schema", schema or "")
        if op == "schema":
            return "" if schema == "default" else ("x" if schema_has_volumes(schema) else "")
        volume = check_name("volume", volume or "")
        if op == "exists":
            volume_folder(schema, volume)
        elif op == "create":
            create(schema, volume, arg, bool(flag))
        elif op == "drop":
            drop(schema, volume, bool(flag))
        elif op == "rename":
            rename(schema, volume, arg or "")
        elif op == "comment":
            set_comment(schema, volume, arg)
        result = ""
    except (ValueError, OSError) as exc:
        result = str(exc) or type(exc).__name__
    if token:
        if len(_done) > 1000:
            _done.clear()
        _done[token] = result
    return result


def _answer_list(op: str, token: str, schema: str | None, volume: str | None, arg: str | None,
                 flag: bool | None) -> str:
    if op == "list":  # schema and volume are empty: the path says them
        try:
            target = local_path(arg or "")
            where = parse(arg or "")
            return "" if target.exists() else f"The volume {where.full_name} has no {where.uc_path()}."
        except (ValueError, OSError) as exc:
            return str(exc)
    return _answer(op, token, schema, volume, arg, flag)


def _show_rows(schema: str | None, pattern: str | None) -> list[dict[str, str]]:
    found = volumes(check_name("schema", schema or ""))
    if pattern is not None:
        rx = _pattern(pattern, "SHOW VOLUMES")
        found = [v for v in found if rx.fullmatch(v.name)]
    return [{"database": v.database, "volumeName": v.name} for v in found]


def _describe_rows(schema: str | None, volume: str | None) -> list[dict[str, Any]]:
    try:
        v = describe(check_name("schema", schema or ""), check_name("volume", volume or ""))
    except ValueError:
        return []  # the check before it said why
    return [{"name": v.name, "catalog": v.catalog, "database": v.database, "owner": v.owner,
             "storageLocation": v.storage_location, "volumeType": v.volume_type, "comment": v.comment}]


def _list_rows(url: str | None, limit: int | None) -> list[dict[str, Any]]:
    try:
        listed = files(url or "", None if limit is None or limit < 0 else limit)
    except (ValueError, OSError):
        return []  # the check before it said why
    return [{"path": f.path, "name": f.name, "size": f.size, "modification_time": f.modification_time}
            for f in listed]


def register(con: Any) -> None:
    """The functions the volume statements call, on a DuckDB connection (a SQL session's)."""
    text, flag = "VARCHAR", "BOOLEAN"
    con.create_function("__kf_volume", _answer_list, [text, text, text, text, text, flag], text,
                        null_handling="special", side_effects=True)
    con.create_function("__kf_show_volumes", _show_rows, [text, text],
                        'STRUCT("database" VARCHAR, "volumeName" VARCHAR)[]', null_handling="special",
                        side_effects=True)
    con.create_function("__kf_describe_volume", _describe_rows, [text, text],
                        'STRUCT("name" VARCHAR, "catalog" VARCHAR, "database" VARCHAR, "owner" VARCHAR, '
                        '"storageLocation" VARCHAR, "volumeType" VARCHAR, "comment" VARCHAR)[]',
                        null_handling="special", side_effects=True)
    con.create_function("__kf_list", _list_rows, [text, "BIGINT"],
                        'STRUCT("path" VARCHAR, "name" VARCHAR, "size" BIGINT, "modification_time" BIGINT)[]',
                        null_handling="special", side_effects=True)


# ---- read_files and format.`/Volumes/...` in a query

_READ_FORMATS = ("csv", "json", "parquet")
_EXTENSIONS = {".csv": "csv", ".json": "json", ".jsonl": "json", ".parquet": "parquet"}
_OPTIONS = {"csv": ("format", "header", "sep", "delimiter", "inferColumnTypes", "schemaEvolutionMode"),
            "json": ("format", "multiLine", "schemaEvolutionMode"),
            "parquet": ("format", "schemaEvolutionMode")}
RESCUED = "_rescued_data"  # the rescued data column's default name (Auto Loader, which read_files uses)


def _truth(value: Any, name: str) -> bool:
    text = str(value).strip().lower()
    if text in ("true", "false"):
        return text == "true"
    raise ValueError(f"read_files option {name} takes true or false, not {value!r}.")


def _data_files(target: Path, shown: str) -> list[str]:
    """A file, or every data file below a folder (read_files reads a folder's files recursively); names that start
    with _ or . (_SUCCESS, .crc) are left out, as Spark leaves them out."""
    if not target.exists():
        raise FileNotFoundError(f"[PATH_NOT_FOUND] Path does not exist: {shown}.")
    if not target.is_dir():
        return [str(target)]
    check_tree(target, shown)
    found: list[str] = []
    for dirpath, dirs, names_ in os.walk(target):
        dirs[:] = sorted(d for d in dirs if not d.startswith(("_", ".")))
        found += [os.path.join(dirpath, n) for n in sorted(names_) if not n.startswith(("_", "."))]
    if not found:
        raise ValueError(f"[UNABLE_TO_INFER_SCHEMA] {shown} is a folder with no data files, so there's no schema "
                         "to read. Write data there first, or give a file.")
    return found


def read_table(path: str, options: dict[str, Any], fmt: str | None = None) -> Any:
    """The rows read_files (or format.`path`) reads from a volume path, as an Arrow table. The files are read on a
    connection of their own that may open only them. Options this PC can't honour as Databricks does are refused
    by name."""
    import duckdb
    import pyarrow as pa
    target = local_path(path)
    files_ = _data_files(target, parse(path).uc_path())
    given = {k.lower(): (k, v) for k, v in options.items()}
    fmt = (fmt or str(given.get("format", ("", ""))[1] or "")).lower() or None
    if fmt is None:
        kinds = {_EXTENSIONS.get(os.path.splitext(f)[1].lower()) for f in files_}
        if len(kinds) != 1 or None in kinds:
            raise ValueError(f"read_files can't tell the format of {path} on this PC from its files' names, so "
                             "nothing was read. Give it, for example format => 'csv'.")
        fmt = kinds.pop()
    if fmt not in _READ_FORMATS:
        raise ValueError(f"read_files with format {fmt!r} can't run on this PC the way it runs in Databricks, "
                         f"because this PC reads only {', '.join(_READ_FORMATS)} files from a volume, so nothing "
                         f"was read. {_WAREHOUSE}, or read a csv, json or parquet file.")
    allowed = {o.lower(): o for o in _OPTIONS[fmt]}
    for key, (name, _v) in given.items():
        if key not in allowed:
            raise ValueError(f"read_files option {name} can't be honoured on this PC the way Databricks honours it, "
                             f"so nothing was read. With format {fmt!r} this PC takes {', '.join(_OPTIONS[fmt])}. "
                             f"{_WAREHOUSE}, or leave the option out.")
    mode = str(given.get("schemaevolutionmode", ("", "addNewColumns"))[1])
    if mode.lower() not in ("none", "addnewcolumns"):
        raise ValueError(f"read_files option schemaEvolutionMode {mode!r} can't be honoured on this PC, which "
                         "reads a volume's files once. Use 'none' or 'addNewColumns'.")
    con = duckdb.connect(":memory:", config={"autoinstall_known_extensions": False,
                                             "autoload_known_extensions": False})
    try:
        con.execute("SET allowed_paths = ?", [files_])  # then nothing else on this PC
        con.execute("SET enable_external_access = false")
        source = files_ if len(files_) > 1 else files_[0]
        if fmt == "csv":
            # Without a schema Databricks reads a CSV file's first line as its header ("the CSV files are assumed
            # to have headers"); a comma unless sep or delimiter says otherwise; every line kept.
            header = _truth(given["header"][1], "header") if "header" in given else True
            sep = str((given.get("sep") or given.get("delimiter") or ("", ","))[1])
            extra: dict[str, Any] = {"sep": sep, "skiprows": 0, "union_by_name": len(files_) > 1}
            if "infercolumntypes" in given and not _truth(given["infercolumntypes"][1], "inferColumnTypes"):
                extra["all_varchar"] = True
            rel = con.read_csv(source, header=header, **extra)  # type: ignore[arg-type]
            if not header:  # Spark names a file's columns _c0, _c1...
                rel = rel.project(", ".join(f'"{c.replace(chr(34), chr(34) * 2)}" AS "_c{i}"'
                                            for i, c in enumerate(rel.columns)))
        elif fmt == "json":
            multi = _truth(given["multiline"][1], "multiLine") if "multiline" in given else False
            rel = con.read_json(source, format="auto" if multi else "newline_delimited")  # type: ignore[arg-type]
        else:
            rel = con.read_parquet(source, union_by_name=len(files_) > 1)
        table = (getattr(rel, "to_arrow_table", None) or rel.arrow)()
    finally:
        con.close()
    if mode.lower() != "none" and RESCUED not in table.column_names:
        # Databricks adds the rescued data column when it infers the schema. Here a value that doesn't fit the
        # schema stops the read instead of being rescued, so the column is always NULL.
        table = table.append_column(RESCUED, pa.nulls(table.num_rows, pa.string()))
    return table


def _kwargs(call: Any) -> tuple[str | None, dict[str, Any]]:
    from sqlglot import exp
    path, options = None, {}
    for i, arg in enumerate(call.expressions):
        if isinstance(arg, exp.Kwarg):
            value = arg.expression
            if isinstance(value, exp.Boolean):
                options[arg.this.name] = "true" if value.this else "false"
            elif isinstance(value, exp.Literal):
                options[arg.this.name] = value.name
            else:
                raise ValueError(f"read_files option {arg.this.name} must be a constant on this PC.")
        elif i == 0 and isinstance(arg, exp.Literal) and arg.is_string:
            path = arg.name
        else:
            raise ValueError("read_files on this PC takes one path, a string, then name => value options.")
    return path, options


def reads(statements: list[Any], con: Any, registered: list[str]) -> list[Any]:
    """``statements`` with each read_files('/Volumes/...') and csv.`/Volumes/...` (json, parquet) replaced by a
    table of the rows read, registered on ``con`` (their names are added to ``registered``, for the session to
    drop). read_files of anything else is refused by name: this PC never reads cloud storage."""
    from sqlglot import exp

    def swap(node: Any, table: Any, alias: Any) -> Any:
        name = f"__kf_read_files_{uuid.uuid4().hex}"
        con.register(name, table)
        registered.append(name)
        return exp.Table(this=exp.to_identifier(name), alias=alias.copy() if alias is not None else None)

    for statement in statements:
        for call in list(statement.find_all(exp.Anonymous)):
            if call.name.lower() != "read_files":
                continue
            path, options = _kwargs(call)
            if path is None or not is_volume_path(path):
                raise ValueError(f"read_files({_q(path) if path else ''}) can't run on this PC the way it runs in "
                                 "Databricks, because it reads cloud storage, which this PC never reaches, so "
                                 f"nothing ran. {_WAREHOUSE}, or read a /Volumes path on this PC.")
            holder = call.parent if isinstance(call.parent, exp.Table) else None
            if holder is None:
                raise ValueError("read_files is a table function: write it after FROM.")
            holder.replace(swap(holder, read_table(path, options), holder.args.get("alias")))
        for table in list(statement.find_all(exp.Table)):
            fmt = table.db.lower() if table.args.get("db") is not None else ""
            if fmt in _READ_FORMATS and not table.args.get("catalog") and is_volume_path(table.name):
                # Spark's own reader defaults: no header and every CSV column a string, no rescued data column
                plain = {"schemaEvolutionMode": "none", **({"header": "false", "inferColumnTypes": "false"}
                                                            if fmt == "csv" else {})}
                table.replace(swap(table, read_table(table.name, plain, fmt), table.args.get("alias")))
    return statements
