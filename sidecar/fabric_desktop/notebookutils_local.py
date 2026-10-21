"""``notebookutils`` (and ``mssparkutils``) for notebooks on this PC.

The calls Fabric notebooks use most work the same here:

- ``fs``: ls, head, cp, mv, put, rm, mkdirs, exists, over the attached lakehouse
  (``Files/...``, ``/lakehouse/default/Files/...`` or abfss paths) and this PC (``file:/...``).
- ``notebook``: run another notebook from this PC's notebooks folder, exit with a value.
- ``credentials``: getToken for storage and Fabric, getSecret from Azure Key Vault as you.
- ``runtime.context``: the notebook, lakehouse and workspace, with ``isLocal`` set.
- ``lakehouse``: get, getWithProperties, list and listTables, read as you. Creating, changing or
  deleting a lakehouse is done in Fabric or the app, where it is confirmed first.
- ``nbResPath``: the notebook's resources folder on this PC; its files are in ``builtin/``
  (notebook_resources).
- ``variableLibrary``: VL_Sources from the metadata project, with the values of the environment
  whose workspace the attached lakehouse is in (Dev's when it is in none); any other library is
  read from the attached lakehouse's workspace with its active value set.

What only Fabric has (mounting other storage, session settings) raises an error that says so.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import shutil
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from . import http_session, lakehouse
from .onelake import FileStore
from .retrying import send_with_retry


class NotebookExit(Exception):
    """Raised by notebookutils.notebook.exit; the kernel ends the run quietly with the value."""

    def __init__(self, value: Any):
        super().__init__(str(value))
        self.value = value


@dataclass
class FileInfo:
    name: str
    path: str
    size: int
    isDir: bool  # noqa: N815 - Fabric's name
    modifyTime: int = 0  # noqa: N815 - Fabric's name: last change, in milliseconds since 1970 (UTC)

    @property
    def isFile(self) -> bool:  # noqa: N802
        return not self.isDir

    @property
    def modificationTime(self) -> int:  # noqa: N802 - Databricks' name for modifyTime
        return self.modifyTime

    def __repr__(self) -> str:
        return f"FileInfo(path={self.path}, name={self.name}, size={self.size})"


class Host:
    """What the utilities need from the app: tokens, the notebook store and the kernel."""

    def __init__(self, token: Callable[[str], str] | None = None,
                 run_notebook: Callable[[str, int, dict[str, Any]], Any] | None = None,
                 notebook_name: Callable[[], str] | None = None,
                 variables: Callable[[str], dict[str, Any] | None] | None = None,
                 resources: Callable[[], str] | None = None):
        self.token = token
        self.run_notebook = run_notebook
        self.notebook_name = notebook_name or (lambda: "")
        self.variables = variables
        self.resources = resources or (lambda: "")


def _store() -> FileStore:
    return FileStore(lakehouse._current().storage_token)


def _split(path: str) -> tuple[str, str]:
    """("onelake", "Files/...") for the lakehouse, or ("local", path) for this PC."""
    p = str(path)
    if p.startswith("file:"):
        return "local", p[len("file:"):] if not p.startswith("file://") else p[len("file://"):]
    rel = lakehouse.relative_path(p)
    if rel is not None:
        return "onelake", rel
    if p.startswith(("abfss://", "wasbs://", "https://")):
        raise ValueError(f"{path}: on this PC only the attached lakehouse and local files are reachable.")
    return "local", p


TABLES_REFUSED = "{path}: Tables/ holds managed Delta tables; change them through a table write, not notebookutils.fs."


def _writable(path: str, p: str) -> lakehouse.Attached:
    """The attached lakehouse, once ``p`` (a OneLake path) may be changed: under Files/, allowed by
    the write guard and not during a proof. Raises before any request is sent otherwise."""
    if p == "Tables" or p.startswith("Tables/"):
        raise PermissionError(TABLES_REFUSED.format(path=path))
    if not p.startswith("Files/") or not p[len("Files/"):].strip("/"):
        raise ValueError(f"{path}: change files and folders under Files/, not Files/ itself.")
    lh = lakehouse._current()
    # The same check table writes go through; runs for external tools refuse every write.
    reason = lakehouse.write_guard(lh.name, "Files", p) if lakehouse.write_guard else None
    if reason:
        raise PermissionError(reason)
    if lakehouse._proof() is not None:
        raise PermissionError(f"{path}: a proof keeps its writes in its sandbox, so it can't change lakehouse files.")
    return lh


def _millis(iso: str | None) -> int:
    """An ISO 8601 time as milliseconds since 1970, or 0 when there isn't one."""
    try:
        return int(dt.datetime.fromisoformat(iso).timestamp() * 1000) if iso else 0
    except ValueError:
        return 0


def _make_fs() -> types.SimpleNamespace:
    def ls(path: str) -> list[FileInfo]:
        where, p = _split(path)
        if where == "local":
            base = Path(p)
            return [FileInfo(e.name, "file:" + str(e), 0 if e.is_dir() else e.stat().st_size, e.is_dir(),
                             int(e.stat().st_mtime * 1000))
                    for e in sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))]
        lh = lakehouse._current()
        return [FileInfo(e["name"], e["path"], e["size"], e["is_dir"], _millis(e.get("modified")))
                for e in _store().list(lh.workspace_id, lh.lakehouse_id, p)]

    def _read(path: str) -> bytes:
        where, p = _split(path)
        if where == "local":
            return Path(p).read_bytes()
        lh = lakehouse._current()
        data = _store().read_bytes(lh.workspace_id, lh.lakehouse_id, p)
        if data is None:
            raise FileNotFoundError(path)
        return data

    def _write(path: str, data: bytes) -> None:
        where, p = _split(path)
        if where == "local":
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Path(p).write_bytes(data)
            return
        lh = _writable(path, p)
        _store().upload(lh.workspace_id, lh.lakehouse_id, p, data)

    def _check_writable(path: str) -> None:
        """Refuse a change to a OneLake path before anything is read or written for it."""
        where, p = _split(path)
        if where == "onelake":
            _writable(path, p)

    def _entry(path: str) -> dict[str, Any] | None:
        """``{"is_dir", "size"}`` for a file or folder, or None when it isn't there."""
        where, p = _split(path)
        if where == "local":
            t = Path(p)
            if not t.exists():
                return None
            return {"is_dir": t.is_dir(), "size": 0 if t.is_dir() else t.stat().st_size}
        lh = lakehouse._current()
        parent, _, name = p.rstrip("/").rpartition("/")
        try:
            listed = _store().list(lh.workspace_id, lh.lakehouse_id, parent or "Files")
        except requests.HTTPError as exc:
            # Only a missing folder means it isn't there. A refusal (401, 403) or a server error
            # raises, as in Fabric: read as "not there", put() without overwrite would replace it.
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        return next((e for e in listed if e["name"] == name), None)

    def _tree(path: str) -> dict[str, int]:
        """Every file under a folder, by path relative to it, with its size."""
        where, p = _split(path)
        if where == "local":
            return {f.relative_to(p).as_posix(): f.stat().st_size for f in Path(p).rglob("*") if f.is_file()}
        lh = lakehouse._current()
        base = p.rstrip("/") + "/"
        return {e["path"][len(base):]: e["size"]
                for e in _store().list(lh.workspace_id, lh.lakehouse_id, p, recursive=True) if not e["is_dir"]}

    def exists(path: str) -> bool:
        return _entry(path) is not None

    def head(path: str, maxBytes: int = 65536) -> str:  # noqa: N803
        return _read(path)[:maxBytes].decode("utf-8", errors="replace")

    def put(path: str, content: str, overwrite: bool = False) -> bool:
        _check_writable(path)
        if not overwrite and exists(path):
            raise FileExistsError(f"{path} exists. Pass overwrite=True to replace it.")
        _write(path, content.encode("utf-8"))
        return True

    def cp(src: str, dest: str, recurse: bool = False) -> bool:
        _check_writable(dest)
        where, p = _split(src)
        if where == "local" and Path(p).is_dir():
            if not recurse:
                raise IsADirectoryError(f"{src} is a folder. Pass recurse=True to copy it.")
            for f in Path(p).rglob("*"):
                if f.is_file():
                    _write(f"{dest.rstrip('/')}/{f.relative_to(p).as_posix()}", f.read_bytes())
            return True
        if where == "onelake" and recurse and (_entry(src) or {}).get("is_dir"):
            for e in ls(src):
                cp(e.path, f"{dest.rstrip('/')}/{e.name}", recurse=e.isDir)
            return True
        _write(dest, _read(src))
        return True

    def rm(path: str, recurse: bool = False) -> bool:
        where, p = _split(path)
        if where == "local":
            target = Path(p)
            if target.is_dir():
                if not recurse:
                    raise IsADirectoryError(f"{path} is a folder. Pass recurse=True to remove it.")
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            return True
        lh = _writable(path, p)
        # As in Fabric, a folder that isn't empty goes only with recurse=True.
        not_empty = f"{path} is a folder that isn't empty. Pass recurse=True to remove it."
        if not recurse and (_entry(path) or {}).get("is_dir") and \
                _store().list(lh.workspace_id, lh.lakehouse_id, p, max_results=1):
            raise IsADirectoryError(not_empty)
        try:
            _store().delete(lh.workspace_id, lh.lakehouse_id, p, recursive=recurse)
        except requests.HTTPError as exc:
            if not recurse and exc.response is not None and exc.response.status_code == 409:
                raise IsADirectoryError(not_empty) from exc
            raise
        return True

    def mv(src: str, dest: str, create_path: bool = False, overwrite: bool = False) -> bool:  # noqa: ARG001
        _check_writable(src)
        _check_writable(dest)
        if not overwrite and exists(dest):
            raise FileExistsError(f"{dest} exists. Pass overwrite=True to replace it.")
        before = _entry(src)
        if before is None:
            raise FileNotFoundError(src)
        files = _tree(src) if before["is_dir"] else {}
        cp(src, dest, recurse=before["is_dir"])
        # The source goes only once its copy is all there: every file, at its size.
        after = _entry(dest)
        if before["is_dir"]:
            copied = _tree(dest) if after is not None and after["is_dir"] else {}
            landed = all(copied.get(name) == size for name, size in files.items())
        else:
            landed = after is not None and not after["is_dir"] and after["size"] == before["size"]
        if not landed:
            raise OSError(f"Moving {src} to {dest} stopped: the copy at {dest} doesn't match {src}, "
                          f"so {src} was kept.")
        rm(src, recurse=True)
        return True

    def mkdirs(path: str) -> bool:
        where, p = _split(path)
        if where == "local":
            Path(p).mkdir(parents=True, exist_ok=True)
        # OneLake folders appear when a file is written into them.
        return True

    def append(path: str, content: str, createFileIfNotExists: bool = False) -> bool:  # noqa: N803
        _check_writable(path)
        old = b""
        if exists(path):
            old = _read(path)
        elif not createFileIfNotExists:
            raise FileNotFoundError(path)
        _write(path, old + content.encode("utf-8"))
        return True

    def getMountPath(mountPoint: str = "/default") -> str:  # noqa: N802, N803
        if mountPoint.strip("/") != "default":
            raise ValueError("On this PC only the attached lakehouse is mounted, as /lakehouse/default.")
        return "/lakehouse/default"

    def mount(*_a: Any, **_kw: Any) -> None:
        raise NotImplementedError("Mounting other storage runs in Fabric. On this PC, read the attached lakehouse.")

    def help(*_a: Any) -> None:  # noqa: A001
        print("notebookutils.fs on this PC: ls, head, put, append, cp, mv, rm, mkdirs, exists, getMountPath")

    return types.SimpleNamespace(ls=ls, head=head, put=put, append=append, cp=cp, fastcp=cp, mv=mv, rm=rm,
                                 mkdirs=mkdirs, exists=exists, getMountPath=getMountPath, mount=mount,
                                 unmount=mount, mounts=lambda: [], help=help)


_AUDIENCES = {
    "storage": ["https://storage.azure.com/user_impersonation"],
    "pbi": ["https://analysis.windows.net/powerbi/api/.default"],
    "keyvault": ["https://vault.azure.net/user_impersonation"],
    "kusto": ["https://kusto.kusto.windows.net/.default"],
}


def _make_credentials(host: Host) -> types.SimpleNamespace:
    def getToken(audience: str) -> str:  # noqa: N802
        if host.token is None:
            raise RuntimeError("Sign in first: tokens come from your Fabric sign-in.")
        key = audience.lower()
        if key not in _AUDIENCES and not key.startswith("https://"):
            raise ValueError(f"Unknown audience {audience!r}. Use storage, pbi, keyvault, kusto or a resource URL.")
        return host.token(key)

    def getSecret(akvName: str, secret: str, linkedService: str | None = None) -> str:  # noqa: N802, N803, ARG001
        vault = akvName if akvName.startswith("https://") else f"https://{akvName}.vault.azure.net"
        headers = {"Authorization": f"Bearer {getToken('keyvault')}"}
        resp = send_with_retry(lambda: http_session.session().get(f"{vault.rstrip('/')}/secrets/{secret}",
                                                    params={"api-version": "7.4"}, headers=headers, timeout=30),
                               "GET")
        if resp.status_code in (401, 403):
            raise PermissionError(f"Key Vault {vault} refused access to {secret!r} for your account. "
                                  f"You need the Key Vault Secrets User role on it.")
        resp.raise_for_status()
        return resp.json()["value"]

    def isValidToken(token: str) -> bool:  # noqa: N802
        """Whether ``token`` is a token that hasn't expired. Only its expiry is read; the signature
        is checked by the service it is sent to."""
        try:
            payload = str(token).split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            return float(claims["exp"]) > time.time()
        except (IndexError, KeyError, TypeError, ValueError):
            return False

    return types.SimpleNamespace(getToken=getToken, getSecret=getSecret, isValidToken=isValidToken,
                                 help=lambda *_a: print("notebookutils.credentials on this PC: getToken, getSecret, "
                                                        "isValidToken"))


_ACTIVITY_KEYS = {"name", "path", "args", "dependencies", "retry", "retryIntervalInSeconds", "timeoutPerCellInSeconds"}


def _activities(dag: Any) -> list[dict[str, Any]]:
    """runMultiple's activities in an order that runs each after its dependencies. Anything the
    PC can't honor (an unknown key, a missing or circular dependency) is an error before any run."""
    if isinstance(dag, (list, tuple)):
        return [{"name": p, "path": p, "args": {}, "dependencies": [], "retry": 0, "retry_interval": 0} for p in dag]
    if not isinstance(dag, dict) or not isinstance(dag.get("activities"), list):
        raise TypeError("runMultiple takes a list of notebook names or a DAG: {\"activities\": [...]}.")
    unknown = set(dag) - {"activities", "timeoutInSeconds", "concurrency"}
    if unknown:
        raise NotImplementedError(f"runMultiple on this PC doesn't support {', '.join(sorted(unknown))}.")
    out: dict[str, dict[str, Any]] = {}
    for a in dag["activities"]:
        extra = set(a) - _ACTIVITY_KEYS
        if extra:
            raise NotImplementedError(f"runMultiple on this PC doesn't support {', '.join(sorted(extra))} in an activity.")
        name = a.get("name") or a.get("path")
        if not a.get("path") or name in out:
            raise ValueError(f"Every runMultiple activity needs a path and a unique name ({name!r}).")
        out[name] = {"name": name, "path": a["path"], "args": dict(a.get("args") or {}),
                     "dependencies": list(a.get("dependencies") or []), "retry": int(a.get("retry") or 0),
                     "retry_interval": float(a.get("retryIntervalInSeconds") or 0)}
    ordered: list[dict[str, Any]] = []
    done: set[str] = set()
    while len(ordered) < len(out):
        ready = [a for a in out.values() if a["name"] not in done and all(d in done for d in a["dependencies"])]
        if not ready:
            missing = sorted({d for a in out.values() for d in a["dependencies"] if d not in out})
            raise ValueError(f"runMultiple: unknown dependency {', '.join(missing)}." if missing
                             else "runMultiple: the dependencies form a cycle.")
        for a in ready:
            ordered.append(a)
            done.add(a["name"])
    return ordered


def _make_notebook(host: Host) -> types.SimpleNamespace:
    def run(path: str, timeout_seconds: int = 90, arguments: dict[str, Any] | None = None, **_kw: Any) -> Any:
        if host.run_notebook is None:
            raise RuntimeError("Running other notebooks isn't available here.")
        return host.run_notebook(path, timeout_seconds, arguments or {})

    def exit(value: Any = "") -> None:  # noqa: A001
        raise NotebookExit(value)

    def runMultiple(dag: Any, timeout_seconds: int | None = None, **_kw: Any) -> dict[str, Any]:  # noqa: N802
        """Fabric's runMultiple, one notebook at a time: each activity gets its ``args``, runs after
        its ``dependencies``, and is retried ``retry`` times. An activity whose dependency failed is
        skipped. Every result is kept; if any activity failed, a RuntimeError lists them after all
        have run, with the results on its ``results`` attribute."""
        activities = _activities(dag)
        notes = sorted({k for a in activities for k in ("timeoutPerCellInSeconds",) if a.get(k)}
                       | {k for k in ("timeoutInSeconds", "concurrency") if isinstance(dag, dict) and dag.get(k)}
                       | ({"timeout_seconds"} if timeout_seconds else set()))
        if notes:
            print(f"Note: {', '.join(notes)} isn't enforced on this PC; the notebooks run one at a time.")
        results: dict[str, Any] = {}
        failed: list[str] = []
        for a in activities:
            blocked = [d for d in a["dependencies"] if d in failed]
            if blocked:
                results[a["name"]] = {"exitVal": None, "exception": f"Skipped: {', '.join(blocked)} failed."}
                failed.append(a["name"])
                continue
            for attempt in range(a["retry"] + 1):
                try:
                    results[a["name"]] = {"exitVal": run(a["path"], arguments=a["args"]), "exception": None}
                    break
                except Exception as exc:  # noqa: BLE001 - recorded per activity, as Fabric does
                    results[a["name"]] = {"exitVal": None, "exception": f"{type(exc).__name__}: {exc}"}
                    if attempt < a["retry"]:
                        time.sleep(a["retry_interval"])
            if results[a["name"]]["exception"]:
                failed.append(a["name"])
        if failed:
            error = RuntimeError("runMultiple: " + "; ".join(f"{n}: {results[n]['exception']}" for n in failed))
            error.results = results  # type: ignore[attr-defined]
            raise error
        return results

    def find(name: str, workspaceId: str) -> dict[str, Any]:  # noqa: N803
        ws = _workspace(workspaceId)
        found = next((n for n in _client(host).list_notebooks(ws) if n.get("displayName") == name), None)
        if found is None:
            raise LookupError(f"No notebook named {name} in workspace {ws}.")
        return found

    def get(name: str, workspaceId: str = "") -> Item:  # noqa: N803
        """A Fabric notebook item, read as you (not a notebook on this PC)."""
        return Item(find(name, workspaceId))

    def list(workspaceId: str = "", maxResults: int = 1000) -> list[Item]:  # noqa: A001, N803
        return [Item(n) for n in _limit(_client(host).list_notebooks(_workspace(workspaceId)), maxResults)]

    def getDefinition(name: str, workspaceId: str = "", format: str = "ipynb") -> str:  # noqa: N802, N803, A002
        """The notebook's content as Fabric stores it (ipynb JSON by default), as text."""
        item = find(name, workspaceId)
        parts = _client(host).get_item_definition(item["workspaceId"], item["id"], format).get("definition", {}).get("parts", [])
        part = next((p for p in parts if p.get("path", "").startswith("notebook-content")), None)
        if part is None:
            raise LookupError(f"Fabric returned no content for the notebook {name}.")
        return base64.b64decode(part["payload"]).decode("utf-8")

    def item_change(*_a: Any, **_kw: Any) -> None:
        raise NotImplementedError("Creating, changing or deleting Fabric notebook items from a notebook runs in "
                                  "Fabric. On this PC, use Publish to OneLake, which shows what it replaces first.")

    return types.SimpleNamespace(run=run, exit=exit, runMultiple=runMultiple, get=get, list=list,
                                 getDefinition=getDefinition, create=item_change, update=item_change,
                                 updateDefinition=item_change, delete=item_change,
                                 help=lambda *_a: print("notebookutils.notebook on this PC: run, exit, runMultiple, "
                                                        "get, list, getDefinition (Fabric items, read only)"))


def _make_runtime(host: Host) -> types.SimpleNamespace:
    class _Context(dict):
        def __getitem__(self, key: str) -> Any:
            self.refresh()
            return super().__getitem__(key)

        def __repr__(self) -> str:
            self.refresh()
            return super().__repr__()

        def get(self, key: str, default: Any = None) -> Any:
            self.refresh()
            return super().get(key, default)

        def refresh(self) -> None:
            lh = lakehouse._attached
            self.update({
                "currentNotebookName": host.notebook_name(),
                "defaultLakehouseId": lh.lakehouse_id if lh else None,
                "defaultLakehouseName": lh.name if lh else None,
                "defaultLakehouseWorkspaceId": lh.workspace_id if lh else None,
                "currentWorkspaceId": lh.workspace_id if lh else None,
                "currentWorkspaceName": lh.workspace_name if lh else None,
                "currentNotebookId": None,  # a notebook on this PC isn't a Fabric item
                "currentRunId": None,
                "isForPipeline": False,
                "isForInteractive": True,
                "isReferenceRun": False,
                "productType": "KratosForge",
                "isLocal": True,
                "runtimeVersion": "Fabric Desktop (this PC)",
                "userName": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            })

    return types.SimpleNamespace(context=_Context())


class Item(dict):
    """A Fabric item as notebookutils returns it: ``item.displayName`` or ``item["displayName"]``."""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


_ITEM_CHANGES = ("Creating, changing or deleting a lakehouse from a notebook runs in Fabric. On this PC, "
                 "do it in Workspaces or Platform setup, where the change is confirmed first.")


def _client(host: Host) -> Any:
    """A Fabric API client that reads as you, with your sign-in."""
    if host.token is None:
        raise RuntimeError("Sign in first: item details come from Fabric as you.")
    from .fabric_api import FabricClient
    return FabricClient(lambda: host.token("pbi"))  # type: ignore[misc]


def _workspace(workspaceId: str) -> str:  # noqa: N803
    """The workspace given, else the attached lakehouse's, as Fabric defaults to the notebook's own."""
    if workspaceId:
        return workspaceId
    lh = lakehouse._attached
    if lh is None:
        raise ValueError("Pass workspaceId, or attach a lakehouse: its workspace is the default, as in Fabric.")
    return lh.workspace_id


def _limit(items: list[Any], max_results: int) -> list[Any]:
    """The first ``max_results`` items; a negative number means all of them, as in Fabric."""
    return items if max_results is None or max_results < 0 else items[:max_results]


def _make_lakehouse(host: Host) -> types.SimpleNamespace:
    def client() -> Any:
        return _client(host)

    workspace = _workspace

    def find(name: str, workspaceId: str) -> dict[str, Any]:  # noqa: N803
        ws = workspace(workspaceId)
        found = client().find_lakehouse(ws, name)
        if found is None:
            raise LookupError(f"No lakehouse named {name} in workspace {ws}.")
        return found

    def get(name: str = "", workspaceId: str = "") -> Item:  # noqa: N803
        if not name:
            lh = lakehouse._attached
            if lh is None:
                raise ValueError("Pass a lakehouse name, or attach one: the attached lakehouse is the default.")
            name, workspaceId = lh.name, workspaceId or lh.workspace_id
        found = find(name, workspaceId)
        return Item({k: v for k, v in found.items() if k != "properties"})

    def getWithProperties(name: str = "", workspaceId: str = "") -> Item:  # noqa: N802, N803
        item = get(name, workspaceId)
        return Item(client().get_lakehouse(item["workspaceId"], item["id"]))

    def list(workspaceId: str = "", maxResults: int = 1000) -> list[Item]:  # noqa: A001, N803
        return [Item(x) for x in _limit(client().list_lakehouses(workspace(workspaceId)), maxResults)]

    def listTables(lakehouse: str = "", workspaceId: str = "", maxResults: int = 1000) -> list[Item]:  # noqa: N802, N803
        item = get(lakehouse, workspaceId)
        return [Item(t) for t in _limit(client().list_tables(item["workspaceId"], item["id"]), maxResults)]

    def refuse(*_a: Any, **_kw: Any) -> None:
        raise NotImplementedError(_ITEM_CHANGES)

    def loadTable(*_a: Any, **_kw: Any) -> None:  # noqa: N802
        raise NotImplementedError("Loading files into a table runs in Fabric. On this PC, read the file and "
                                  "write it with saveAsTable or lakehouse.write_table.")

    return types.SimpleNamespace(get=get, getWithProperties=getWithProperties, list=list, listTables=listTables,
                                 create=refuse, update=refuse, delete=refuse, loadTable=loadTable,
                                 help=lambda *_a: print("notebookutils.lakehouse on this PC: get, getWithProperties, "
                                                        "list, listTables (create, update, delete run in Fabric)"))


class VariableLibrary:
    """A variable library's values, read as attributes: ``lib.CRM_server_host``."""

    def __init__(self, name: str, values: dict[str, Any]):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_values", dict(values))

    def __getattr__(self, key: str) -> Any:
        values = object.__getattribute__(self, "_values")
        if key in values:
            return values[key]
        raise AttributeError(f"{object.__getattribute__(self, '_name')} has no variable {key}")

    def __dir__(self) -> list[str]:
        return sorted(object.__getattribute__(self, "_values"))

    def __repr__(self) -> str:
        return f"VariableLibrary({object.__getattribute__(self, '_name')}, {len(object.__getattribute__(self, '_values'))} variables)"


_REFERENCE = re.compile(r"^\$\(/\*\*/([^/]+)/([^)]+)\)$")


# Set by the app on a library read from Fabric: the workspace it came from (never a variable name,
# which starts with a letter).
FROM_KEY = "__fd_from__"


def _make_variable_library(host: Host) -> types.SimpleNamespace:
    told: set[str] = set()

    def getLibrary(variableLibraryName: str) -> VariableLibrary:  # noqa: N802, N803
        values = host.variables(variableLibraryName) if host.variables else None
        source = values.pop(FROM_KEY, None) if values else None
        if source and variableLibraryName not in told:
            told.add(variableLibraryName)
            print(f"Read {variableLibraryName} from workspace {source}, the attached lakehouse's. In Fabric a notebook "
                  f"reads its own workspace's library, so attach a lakehouse in that workspace to read the same values.")
        if values is None:
            raise LookupError(f"Attach a lakehouse to read the variable library {variableLibraryName} from its workspace. "
                              "VL_Sources also comes from the metadata project (Data mesh > Design > Source systems).")
        return VariableLibrary(variableLibraryName, values)

    def get(variableReference: str) -> Any:  # noqa: N803
        m = _REFERENCE.match(str(variableReference).strip())
        if not m:
            raise ValueError('Use a reference like "$(/**/VL_Sources/CRM_server_host)"')
        return getattr(getLibrary(m.group(1)), m.group(2))

    return types.SimpleNamespace(getLibrary=getLibrary, get=get)


def build(host: Host) -> types.ModuleType:
    mod = types.ModuleType("notebookutils", __doc__)
    mod.fs = _make_fs()
    mod.credentials = _make_credentials(host)
    mod.notebook = _make_notebook(host)
    mod.runtime = _make_runtime(host)
    mod.lakehouse = _make_lakehouse(host)
    mod.variableLibrary = _make_variable_library(host)
    mod.mssparkutils = mod  # ``from notebookutils import mssparkutils`` still works
    mod.help = lambda *_a: print("notebookutils on this PC: fs, notebook, credentials, runtime, lakehouse, "
                                 "variableLibrary, nbResPath")

    def attribute(name: str) -> Any:
        # nbResPath follows the notebook that is running, so it is looked up on each use.
        if name == "nbResPath":
            folder = host.resources()
            if not folder:
                raise AttributeError("nbResPath is the notebook's resources folder; it is set while a saved notebook runs.")
            return folder
        raise AttributeError(f"notebookutils on this PC has no {name}")
    mod.__getattr__ = attribute  # type: ignore[method-assign]
    return mod
