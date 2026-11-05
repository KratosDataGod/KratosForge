"""A warehouse as files in Git, in the layout Fabric's Git integration uses (WH-11).

Fabric commits a warehouse as a SQL database project: a ``<name>.Warehouse`` folder holding
``.platform``, ``<name>.sqlproj`` and one ``.sql`` file per object, by schema and object type::

    WH_Gold.Warehouse/
        .platform
        WH_Gold.sqlproj
        dbo/Tables/DimCustomer.sql
        dbo/Views/vw_SalesBySegment.sql
        dbo/StoredProcedures/usp_RefreshSegments.sql
        dbo/Functions/fn_FiscalYear.sql
        Security/staging.sql            (CREATE SCHEMA)

The folders are found in the Git repositories opened in Source control. Each file says which
object it defines by its first CREATE, so a project laid out another way (``Stored Procedures``,
one folder per schema only) is read the same; a new file goes where a file for that kind of object
already is, else in the layout above. Nothing here runs SQL, calls Fabric or commits: files change
on this PC, Source control shows them as changes, and you commit them. Import (the page scripts the
warehouse from its catalog and sends the objects) writes new files and, only when asked, replaces
files whose text differs; it never removes a file. Calls name a repository and paths inside the
project folder, never a path outside it, and links are refused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from xml.sax.saxutils import escape

from . import jsonfile, warehouse_compare
from .redact import describe as describe_error
from .git.repo import Repository

METHODS = {"wh_projects_list", "wh_project_files", "wh_project_read", "wh_project_save", "wh_project_write",
           "wh_project_compare", "wh_project_diff", "wh_project_deploy_plan"}

SUFFIX = ".Warehouse"
MAX_FILE = 1024 * 1024        # one object's file
MAX_FILES = 2000              # files listed from one project
MAX_OBJECTS = 5000            # objects in one import
MAX_DEPTH = 6                 # folders below the project folder
FIND_DEPTH = 4                # folders below a repository's root where projects are looked for
KINDS = ("schema", "table", "view", "procedure", "function")
# Schemas Fabric makes and keeps itself in a warehouse: never the project's, so Import, Compare and
# Deploy leave them out (#807). The page leaves them out too (WH_FABRIC_SCHEMAS in whproject.js).
FABRIC_SCHEMAS = {"queryinsights", "_rsc", "sys", "information_schema"}
FABRIC_OWN = "Fabric makes and keeps this schema itself, so it isn't part of the project."
FOLDERS = {"table": "Tables", "view": "Views", "procedure": "StoredProcedures", "function": "Functions"}
SKIP_DIRS = {"bin", "obj", "node_modules"}
PLATFORM_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json"
SQLPROJ = """<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build">
  <Sdk Name="Microsoft.Build.Sql" Version="2.2.0" />
  <PropertyGroup>
    <Name>{name}</Name>
    <ProjectGuid>{{{guid}}}</ProjectGuid>
    <DSP>Microsoft.Data.Tools.Schema.Sql.SqlDwUnifiedDatabaseSchemaProvider</DSP>
    <ModelCollation>1033, CI</ModelCollation>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.SqlServer.Dacpacs.FabricDw" Version="170.0.2" />
  </ItemGroup>
  <Target Name="BeforeBuild">
    <Delete Files="$(BaseIntermediateOutputPath)\\project.assets.json" />
  </Target>
</Project>
"""

_SEGMENT = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,128}$")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])$", re.IGNORECASE)  # Windows device names
_BLANKED = re.compile(r"--[^\n]*|/\*.*?\*/|N?'(?:[^']|'')*'", re.DOTALL)
_PART = r"(\[(?:[^\]]|\]\])+\]|\"(?:[^\"]|\"\")+\"|[A-Za-z_@#][\w@#$]*)"
_CREATE = re.compile(r"\bCREATE\s+(?:OR\s+ALTER\s+)?(TABLE|VIEW|PROC|PROCEDURE|FUNCTION|SCHEMA)\s+"
                     + _PART + r"(?:\s*\.\s*" + _PART + r")?", re.IGNORECASE)
_KIND_WORD = {"TABLE": "table", "VIEW": "view", "PROC": "procedure", "PROCEDURE": "procedure",
              "FUNCTION": "function", "SCHEMA": "schema"}


def version(data: bytes) -> str:
    """A short fingerprint of a file's bytes: Save checks it, so a file changed since it was opened
    (a Git pull, another editor) isn't overwritten."""
    return hashlib.sha256(data).hexdigest()[:16]


def _unquote(part: str) -> str:
    if part.startswith("[") and part.endswith("]"):
        return part[1:-1].replace("]]", "]")
    if part.startswith('"') and part.endswith('"'):
        return part[1:-1].replace('""', '"')
    return part


def defines(text: str) -> dict[str, str] | None:
    """{"kind", "schema", "name"} of the first object a file creates, or None. Comments and strings
    are blanked first, so a CREATE in either doesn't count; a name without a schema is in dbo."""
    blanked = _BLANKED.sub(lambda m: " " * len(m.group(0)), text)
    m = _CREATE.search(blanked)
    if not m:
        return None
    kind = _KIND_WORD[m.group(1).upper()]
    first, second = text[m.start(2):m.end(2)], m.group(3) and text[m.start(3):m.end(3)]
    if kind == "schema":
        return {"kind": "schema", "schema": _unquote(first), "name": _unquote(first)}
    schema, name = (first, second) if second else ("dbo", first)
    return {"kind": kind, "schema": _unquote(schema), "name": _unquote(name)}


def _key(kind: str, schema: str, name: str) -> tuple[str, str, str]:
    return kind, schema, name  # names are case-sensitive in a Fabric warehouse (BIN2 collation)


def _segment_ok(s: str) -> bool:
    return (bool(_SEGMENT.match(s)) and s not in (".", "..") and not s.startswith(".") and not s.endswith((" ", "."))
            and not _RESERVED.match(s.split(".")[0].strip()))


def default_path(kind: str, schema: str, name: str) -> str:
    """Where Fabric's layout puts an object: <schema>/<Tables|Views|StoredProcedures|Functions>/<name>.sql,
    and a schema in Security/<schema>.sql. Raises for a name that can't be a file name."""
    for part in (schema, name):
        if not _segment_ok(part):
            raise ValueError(f"{part!r} can't be a file name, so {kind} {schema}.{name} isn't written.")
    if kind == "schema":
        return f"Security/{name}.sql"
    return f"{schema}/{FOLDERS[kind]}/{name}.sql"


def _normal(text: str) -> str:
    return text.replace("\r\n", "\n").strip() + "\n"


_log = logging.getLogger(__name__)


class WarehouseProjectMixin:
    # ---- where projects are

    def _wh_repo_root(self, repo: str) -> Path:
        """A repository this PC opened in Source control, with links followed."""
        for r in self.git_repos()["repos"]:  # type: ignore[attr-defined]
            if r.get("exists") and r.get("path") == repo:
                return Path(repo).resolve()
        raise ValueError("Open this repository in Source control first.")

    def _wh_project_dir(self, repo: str, folder: str, must_exist: bool = True) -> Path:
        """The project folder ``folder`` (relative to the repository's root, / separated), which
        must end in .Warehouse and be no link, nor inside one."""
        root = self._wh_repo_root(repo)
        parts = PurePosixPath(str(folder or "")).parts
        if (not parts or not all(_segment_ok(p) for p in parts) or not parts[-1].lower().endswith(SUFFIX.lower())
                or len(parts) > FIND_DEPTH + 1):
            raise ValueError("That isn't a warehouse project folder (a folder named <warehouse>.Warehouse).")
        path = root.joinpath(*parts)
        here = root
        for p in parts:
            here = here / p
            if here.is_symlink() or (here.exists() and here.resolve() != here):
                raise ValueError("That project folder is a link to somewhere else, so it isn't used.")
        if must_exist and not path.is_dir():
            raise ValueError("That project folder isn't there any more. Choose the project again.")
        return path

    def _wh_file(self, project: Path, path: str) -> Path:
        parts = PurePosixPath(str(path or "")).parts
        if (not parts or len(parts) > MAX_DEPTH + 1 or not all(_segment_ok(p) for p in parts)
                or not parts[-1].lower().endswith(".sql")):
            raise ValueError("That isn't a .sql file in the project.")
        here = project
        for p in parts:
            here = here / p
            if here.is_symlink() or (here.exists() and here.resolve() != here):
                raise ValueError("That file is a link to somewhere else, so it isn't used.")
        return here

    def _wh_find(self, root: Path, depth: int = 0) -> list[Path]:
        out: list[Path] = []
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return out
        for e in entries:
            if e.name.startswith(".") or e.name.lower() in SKIP_DIRS or e.is_symlink() or not e.is_dir():
                continue
            if e.name.lower().endswith(SUFFIX.lower()):
                out.append(e)
            elif depth < FIND_DEPTH:
                out += self._wh_find(e, depth + 1)
        return out

    def wh_projects_list(self) -> dict[str, Any]:
        """The warehouse projects in every repository opened in Source control, and the repositories
        a new project can go in. A project's name is its .platform displayName, else its folder's."""
        repos, projects = [], []
        for r in self.git_repos()["repos"]:  # type: ignore[attr-defined]
            if not r.get("exists"):
                continue
            label = r.get("name") or Path(r["path"]).name
            repos.append({"repo": r["path"], "label": label})
            root = Path(r["path"]).resolve()
            for d in self._wh_find(root)[:50]:
                name = d.name[: -len(SUFFIX)]
                try:
                    meta = json.loads((d / ".platform").read_text(encoding="utf-8")).get("metadata") or {}
                    name = str(meta.get("displayName") or name)
                except (OSError, ValueError, AttributeError):
                    pass
                projects.append({"repo": r["path"], "repo_label": label, "folder": d.relative_to(root).as_posix(), "name": name})
        return {"repos": repos, "projects": projects}

    # ---- files

    def _wh_walk(self, project: Path) -> tuple[list[Path], bool]:
        files: list[Path] = []
        cut = False
        for here, dirs, names in os.walk(project, followlinks=False):
            depth = len(Path(here).relative_to(project).parts)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS
                             and depth < MAX_DEPTH and not os.path.islink(os.path.join(here, d)))
            for n in sorted(names):
                f = Path(here) / n
                if n.lower().endswith(".sql") and not f.is_symlink() and f.is_file():
                    if len(files) >= MAX_FILES:
                        cut = True
                        break
                    files.append(f)
        return files, cut

    def _wh_git(self, root: Path, project: Path) -> dict[str, Any]:
        """The project's Git state: each changed file's code (A added, M modified) by its path in the
        project, the branch, the commit, the upstream and how many commits aren't pushed."""
        try:
            st = Repository(root).status()
        except Exception:  # noqa: BLE001 - no Git, or not a repository yet: files show without codes
            return {"codes": {}, "branch": None, "commit": None, "upstream": None, "ahead": None}
        prefix = project.relative_to(root).as_posix() + "/"
        codes: dict[str, str] = {}
        for group in ("staged", "unstaged", "untracked", "conflicts"):
            for e in st.get(group) or []:
                p = str(e.get("path") or "")
                if p.startswith(prefix):
                    code = "A" if e.get("code") in ("U", "A", "?") else str(e.get("code") or "M")
                    codes.setdefault(p[len(prefix):], code)
        return {"codes": codes, "branch": st.get("branch"), "commit": st.get("commit"),
                "upstream": st.get("upstream"), "ahead": st.get("ahead")}

    def _wh_committed(self, root: Path, project: Path, paths: list[str]) -> dict[str, bool]:
        """Whether each project file is tracked by Git and the same as in the last commit (HEAD). A file
        Git ignores, or one with changes staged or not, is not; so is every file when there is no commit."""
        try:
            git = Repository(root)
            base = project.resolve().relative_to(Path(git.root).resolve())
        except Exception:  # noqa: BLE001 - no Git, or the project isn't in it: nothing is committed
            return {p: False for p in paths}
        out: dict[str, bool] = {}
        for rel in paths:
            spec = (base / rel).as_posix()  # cli.run reads pathspecs literally
            out[rel] = (git._try("ls-files", "--error-unmatch", "--", spec) is not None
                        and git._try("diff", "--quiet", "HEAD", "--", spec) is not None)
        return out

    def wh_project_files(self, repo: str, folder: str) -> dict[str, Any]:
        """Every .sql file in the project: its path, the object it defines (or a problem), its Git code
        and version. Also the branch, and whether the project has its .sqlproj and .platform."""
        project = self._wh_project_dir(repo, folder)
        files, cut = self._wh_walk(project)
        git = self._wh_git(self._wh_repo_root(repo), project)
        codes, branch = git["codes"], git["branch"]
        out = []
        for f in files:
            rel = f.relative_to(project).as_posix()
            item: dict[str, Any] = {"path": rel, "git": codes.get(rel, "")}
            try:
                if f.stat().st_size > MAX_FILE:
                    raise ValueError(f"It is larger than {MAX_FILE // 1024} KB.")
                data = f.read_bytes()
                obj = defines(data.decode("utf-8-sig"))
                item.update(version=version(data), **(obj or {}),
                            problem="" if obj else "It doesn't create a table, view, procedure, function or schema.")
            except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
                item.update(version="", problem="It couldn't be read as UTF-8 text." if isinstance(exc, UnicodeDecodeError)
                            else str(exc) if isinstance(exc, ValueError) else "It couldn't be read.")
            out.append(item)
        return {"repo": repo, "folder": folder, "branch": branch, "files": out,
                "sqlproj": any(p.suffix.lower() == ".sqlproj" for p in project.iterdir() if p.is_file()),
                "platform": (project / ".platform").is_file(),
                "note": f"The project has more than {MAX_FILES} .sql files; the first {MAX_FILES} are listed." if cut else ""}

    def wh_project_read(self, repo: str, folder: str, path: str) -> dict[str, Any]:
        f = self._wh_file(self._wh_project_dir(repo, folder), path)
        if not f.is_file():
            raise ValueError("That file isn't in the project any more.")
        if f.stat().st_size > MAX_FILE:
            raise ValueError(f"That file is larger than {MAX_FILE // 1024} KB.")
        data = f.read_bytes()
        return {"path": path, "text": data.decode("utf-8-sig"), "version": version(data)}

    @staticmethod
    def _wh_write(f: Path, text: str) -> None:
        """Replaces or creates ``f`` in one step, through a fresh temp file beside it."""
        tmp = f.with_name(f"{f.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(tmp, "x", encoding="utf-8", newline="\n") as out:
                out.write(text)
            jsonfile.replace(tmp, f)
        except BaseException:
            tmp.unlink(missing_ok=True)  # a locked file (Windows) mustn't leave a .tmp for Git to show
            raise

    def _wh_mkdirs(self, project: Path, f: Path) -> None:
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.parent.resolve() != f.parent or not str(f.parent).startswith(str(project)):
            raise ValueError("A folder in that path is a link to somewhere else, so nothing is written there.")

    def wh_project_save(self, repo: str, folder: str, path: str, text: str, expect: str) -> dict[str, Any]:
        """Writes a file the page opened from the project. ``expect`` is the version it opened: a file
        that has changed since is left alone."""
        if not isinstance(expect, str) or not expect:
            raise ValueError("Open the file again before saving it, so a newer version isn't overwritten.")
        if len(str(text).encode("utf-8")) > MAX_FILE:
            raise ValueError(f"A project file can be up to {MAX_FILE // 1024} KB.")
        project = self._wh_project_dir(repo, folder)
        f = self._wh_file(project, path)
        if not f.is_file():
            raise ValueError("That file isn't in the project any more. Import or New object makes it again.")
        if version(f.read_bytes()) != expect:
            raise ValueError("That file changed since you opened it, perhaps from a Git pull. Open it again to see the change.")
        self._wh_write(f, text)
        return {"path": path, "version": version(str(text).encode("utf-8"))}

    # ---- import and new objects

    def _wh_create_project(self, repo: str, name: str) -> tuple[Path, str]:
        if not _segment_ok(name):
            raise ValueError(f"{name!r} can't be a folder name, so the project can't be made.")
        folder = f"{name}{SUFFIX}"
        project = self._wh_project_dir(repo, folder, must_exist=False)
        project.mkdir(exist_ok=True)
        if not (project / ".platform").exists():
            self._wh_write(project / ".platform", json.dumps({
                "$schema": PLATFORM_SCHEMA, "metadata": {"type": "Warehouse", "displayName": name},
                "config": {"version": "2.0", "logicalId": str(uuid.uuid4())}}, indent=2) + "\n")
        if not any(p.suffix.lower() == ".sqlproj" for p in project.iterdir()):
            self._wh_write(project / f"{name}.sqlproj", SQLPROJ.format(name=escape(name), guid=str(uuid.uuid4()).upper()))
        return project, folder

    def wh_project_write(self, repo: str, objects: list[dict[str, Any]], folder: str = "", create: str = "",
                         replace: bool = False, dry_run: bool = False) -> dict[str, Any]:
        """Writes objects ([{kind, schema, name, text}]) into the project as files. Each goes to the
        file that already defines it, else where Fabric's layout puts it. A file with the same text is
        left as it is; one whose text differs is replaced only with ``replace``. No file is removed.
        ``create`` (the warehouse's name) makes <name>.Warehouse with its .platform and .sqlproj when
        ``folder`` isn't given. ``dry_run`` says what would happen and writes nothing."""
        if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
            raise ValueError(f"Send up to {MAX_OBJECTS} objects at a time.")
        if not folder and not create:
            raise ValueError("Choose a project, or the warehouse to make one for.")
        project = self._wh_project_dir(repo, folder) if folder else None
        created = project is None and not dry_run
        if created:
            project, folder = self._wh_create_project(repo, create)
        elif project is None:
            folder = f"{create}{SUFFIX}"
            if not _segment_ok(create):
                raise ValueError(f"{create!r} can't be a folder name, so the project can't be made.")
            existing = self._wh_project_dir(repo, folder, must_exist=False)
            project = existing if existing.is_dir() else None
        where: dict[tuple[str, str, str], str] = {}
        folders: dict[str, str] = {}
        if project is not None:
            for f in self._wh_walk(project)[0]:
                try:
                    obj = defines(f.read_text(encoding="utf-8-sig")) if f.stat().st_size <= MAX_FILE else None
                except (OSError, ValueError):
                    obj = None
                if obj:
                    rel = f.relative_to(project).as_posix()
                    where.setdefault(_key(obj["kind"], obj["schema"], obj["name"]), rel)
                    if obj["kind"] != "schema" and len(PurePosixPath(rel).parts) == 3:
                        folders.setdefault(obj["kind"], PurePosixPath(rel).parts[1])  # this project's own folder name
        # Windows and macOS file names ignore letter case, but warehouse names don't (BIN2): dbo.Sales and
        # dbo.sales would share one file there. Each path, in lower case, belongs to the first object to
        # have it, and another object whose path differs only in case is skipped, never written over it.
        taken = {rel.lower(): key for key, rel in where.items()}
        result: dict[str, list[dict[str, str]]] = {"added": [], "changed": [], "same": [], "skipped": []}
        for o in objects:
            kind, schema, name = str(o.get("kind") or ""), str(o.get("schema") or ""), str(o.get("name") or "")
            text = _normal(str(o.get("text") or ""))
            label = name if kind == "schema" else f"{schema}.{name}"
            row = {"kind": kind, "name": label}
            try:
                if kind not in KINDS or not name or (kind != "schema" and not schema):
                    raise ValueError("It isn't a table, view, procedure, function or schema with a name.")
                if (schema or name).lower() in FABRIC_SCHEMAS:
                    raise ValueError(FABRIC_OWN)
                if len(text.encode("utf-8")) > MAX_FILE:
                    raise ValueError(f"Its script is larger than {MAX_FILE // 1024} KB.")
                rel = where.get(_key(kind, schema, name))
                if rel is None:
                    rel = default_path(kind, schema, name)
                    if kind in folders:
                        rel = f"{schema}/{folders[kind]}/{name}.sql"
                row["path"] = rel
                owner = taken.setdefault(rel.lower(), _key(kind, schema, name))
                if owner != _key(kind, schema, name) and where.get(_key(kind, schema, name)) != rel:
                    other = owner[2] if owner[0] == "schema" else f"{owner[1]}.{owner[2]}"
                    if where.get(owner) == rel:
                        raise ValueError(f"Its file would be {rel}, which already defines {other}.")
                    raise ValueError(f"Its file would be {rel}, which is {other}'s file apart from letter case, and "
                                     "Windows keeps only one of them. Rename one of the two objects.")
                f = self._wh_file(project, rel) if project is not None else None
                if f is None or not f.is_file():
                    if not dry_run:
                        self._wh_mkdirs(project, f)
                        self._wh_write(f, text)
                    result["added"].append(row)
                elif _normal(f.read_text(encoding="utf-8-sig")) == text:
                    result["same"].append(row)
                else:
                    if replace and not dry_run:
                        self._wh_write(f, text)
                    result["changed"].append({**row, "replaced": "yes" if replace else "no"})
            except (OSError, ValueError) as exc:
                result["skipped"].append({**row, "why": str(exc) if isinstance(exc, ValueError) else "It couldn't be written."})
        return {"repo": repo, "folder": folder, "dry_run": dry_run, "created": created,
                **result, "counts": {k: len(v) for k, v in result.items()}}

    # ---- compare (WH-12)

    def wh_project_compare(self, repo: str, folder: str, objects: list[dict[str, Any]]) -> dict[str, Any]:
        """Compares the project's files with the warehouse's objects ([{kind, schema, name, text}],
        scripted by the page from the catalog). Writes nothing. Files that can't be read, or define
        no object, are listed under ``unread``."""
        if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
            raise ValueError(f"Send up to {MAX_OBJECTS} objects at a time.")
        project = self._wh_project_dir(repo, folder)
        mine: dict[tuple[str, str, str], dict[str, str]] = {}
        unread: list[dict[str, str]] = []
        for f in self._wh_walk(project)[0]:
            rel = f.relative_to(project).as_posix()
            try:
                if f.stat().st_size > MAX_FILE:
                    raise ValueError(f"It is larger than {MAX_FILE // 1024} KB.")
                text = f.read_text(encoding="utf-8-sig")
            except (OSError, ValueError) as exc:
                unread.append({"path": rel, "why": str(exc) if isinstance(exc, ValueError) and not isinstance(exc, UnicodeDecodeError)
                               else "It couldn't be read as UTF-8 text."})
                continue
            obj = defines(text)
            if not obj:
                unread.append({"path": rel, "why": "It doesn't create a table, view, procedure, function or schema."})
                continue
            if obj["schema"].lower() in FABRIC_SCHEMAS:
                unread.append({"path": rel, "why": FABRIC_OWN})
                continue
            key = _key(obj["kind"], obj["schema"], obj["name"])
            if key in mine:
                unread.append({"path": rel, "why": f"{obj['schema']}.{obj['name']} is also defined in {mine[key]['path']}."})
                continue
            mine[key] = {**obj, "text": text, "path": rel}
        theirs = {}
        for o in objects:
            kind, schema, name = str(o.get("kind") or ""), str(o.get("schema") or ""), str(o.get("name") or "")
            if kind in KINDS and name and (schema or kind == "schema") and (schema or name).lower() not in FABRIC_SCHEMAS:
                theirs[_key(kind, schema or name, name)] = {"kind": kind, "schema": schema or name, "name": name,
                                                            "text": str(o.get("text") or "")}
        rows = warehouse_compare.compare(mine, theirs)
        counts = {s: sum(1 for r in rows if r["state"] == s) for s in ("changed", "only_project", "only_warehouse", "same")}
        return {"repo": repo, "folder": folder, "objects": rows, "counts": counts, "unread": unread}

    def wh_project_diff(self, warehouse_text: str, project_text: str) -> dict[str, Any]:
        """Side-by-side rows for one object: the warehouse's script left, the project's file right."""
        if len(str(warehouse_text)) + len(str(project_text)) > 2 * MAX_FILE:
            raise ValueError(f"Each side can be up to {MAX_FILE // 1024} KB.")
        return warehouse_compare.side_by_side(str(warehouse_text or ""), str(project_text or ""))

    # ---- deploy (WH-13)

    def _wh_workspace_env(self, workspace_id: str) -> str:
        """The environment the open metadata project gives ``workspace_id``: the strictest one that
        names it (Prod, then Test, then Dev; a Keys workspace counts as its environment's), or
        "none" when none does, there's no project, or it can't be read."""
        from .meta_service import NoMetadataProject, _env_workspaces
        if not workspace_id:
            return "none"
        try:
            envs = self._project().environments  # type: ignore[attr-defined]
        except NoMetadataProject:
            return "none"
        except Exception as exc:  # noqa: BLE001 - an unreadable project gates the deploy, as no project does
            _log.info("Couldn't read the metadata project's environments; the deploy is gated: %s", describe_error(exc))
            return "none"
        wanted = workspace_id.lower()
        for env in ("prod", "test", "dev"):
            cfg = envs.get(env) or {}
            ids = _env_workspaces(cfg) | {cfg.get("keys_workspace_id") or ""}
            if wanted in {i.lower() for i in ids if i}:
                return env
        return "none"

    def wh_project_deploy_plan(self, repo: str, folder: str, objects: list[dict[str, Any]],
                               environment: str = "", workspace_id: str = "") -> dict[str, Any]:
        """What a deploy would run to make the warehouse match the project: the compare, turned into
        changes in the order they'd run (warehouse_compare.deploy_plan), and the project's Git state.
        Each change from a file says whether that file is committed (tracked, and the same as HEAD).
        Outside Dev a deploy takes only what Git has. The environment is the stricter of ``environment``
        (what the page sent) and what the metadata project gives ``workspace_id`` (the warehouse's
        workspace, as the page sends it; see _wh_workspace_env), so a page can't turn the rule off by sending "dev"; without a workspace,
        or one no environment names, the deploy counts as Prod. There,
        a change whose file isn't committed isn't offered, and nothing is while the branch has commits
        that aren't pushed. Writes nothing and runs nothing: the page runs each ticked change as a
        confirmed warehouse change (sql_exec.py)."""
        compared = self.wh_project_compare(repo, folder, objects)
        root = self._wh_repo_root(repo)
        project = self._wh_project_dir(repo, folder)
        git = self._wh_git(root, project)
        changes = warehouse_compare.deploy_plan(compared["objects"])
        committed = self._wh_committed(root, project, sorted({c["path"] for c in changes if c["path"]}))
        rank = {"dev": 0, "test": 1, "prod": 2}
        env = max((environment if environment in rank else "none", self._wh_workspace_env(workspace_id)),
                  key=lambda e: rank.get(e, 3))
        gated = env != "dev"
        unpushed = not git["upstream"] or bool(git["ahead"])
        for c in changes:
            c["committed"] = committed.get(c["path"], True) if c["path"] else True
            c["git"] = (git["codes"].get(c["path"]) or ("" if c["committed"] else "!")) if c["path"] else ""
            if gated and c["offered"] and (not c["committed"] or unpushed):
                c.update(offered=False, ticked=False, held=True,
                         why=f"{c['why']}; held until it's committed and pushed")
        return {"repo": repo, "folder": folder, "changes": changes, "unread": compared["unread"],
                "branch": git["branch"], "commit": git["commit"], "upstream": git["upstream"], "ahead": git["ahead"],
                "gated": gated, "environment": env, "uncommitted": sorted({c["path"] for c in changes if not c["committed"]})}
