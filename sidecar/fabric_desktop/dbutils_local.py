"""Databricks ``dbutils`` for notebooks on this PC.

Databricks notebooks run here unchanged where the same thing exists locally:

- ``fs``: the same calls as ``notebookutils.fs``, over the attached lakehouse and this PC.
  ``dbfs:/`` and ``/Volumes/`` are Databricks storage and raise an error that says so.
- ``notebook``: run another notebook from this PC's notebooks folder, exit with a value.
- ``widgets``: text, dropdown, combobox and multiselect. Values come from the arguments a run
  passes, then the value set in the app, then the default.
- ``secrets``: a secret scope is an Azure Key Vault name (Key Vault backed scopes), read as you.
- ``jobs.taskValues``: kept for the run; runMultiple activities read their upstream values.
- ``library.restartPython``: restarts Python after the cell, as in Databricks.
- ``data.summarize``: a summary of a DataFrame.
"""

from __future__ import annotations

import json
import re
import types
from dataclasses import dataclass
from typing import Any, Callable

from . import http_session
from .paging import Pages
from .notebookutils_local import Host, NotebookExit, keyvault_error, on_vault, vault_url
from .retrying import send_with_retry


class RestartPython(NotebookExit):
    """Raised by restartPython: the cell stops there and the kernel restarts after it."""

    def __init__(self) -> None:
        super().__init__("")


class InputWidgetNotDefined(Exception):
    """Databricks' error for a widget that was neither created nor passed in."""


_DATABRICKS_STORAGE = ("dbfs:", "/dbfs/", "/volumes/", "/workspace/")
_PATH_ARGS = {"dir", "file", "from_", "to", "path", "src", "dest"}
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _local_path(path: str) -> str:
    """The path, if it means the same place here as notebookutils would read. In Databricks a bare
    absolute path (``/mnt/raw``, ``/tmp/x``, ``/FileStore/...``) is DBFS, so it is refused rather than
    read as this PC's disk: a local path says ``file:``."""
    p = str(path)
    if p.lower().startswith(_DATABRICKS_STORAGE):
        raise ValueError(f"{path} is Databricks storage, which this PC can't reach. Use the attached lakehouse "
                         f"(Files/... or /lakehouse/default/Files/...) or a file: path on this PC.")
    if (p.startswith("/") and not p.startswith("/lakehouse/")) or _DRIVE.match(p):
        raise ValueError(f"In Databricks, {path} is a DBFS path, so it isn't read as this PC's disk. "
                         f"Write file:{p} for a folder on this PC, or Files/... for the attached lakehouse.")
    return p


def _make_fs(nbu_fs: types.SimpleNamespace) -> types.SimpleNamespace:
    def wrap(fn: Callable[..., Any], paths: int = 1) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            for a in [*args[:paths], *(v for k, v in kwargs.items() if k in _PATH_ARGS)]:
                _local_path(a)
            return fn(*args, **kwargs)
        call.__name__ = fn.__name__
        return call

    def mv(src: str, dest: str, recurse: bool = False) -> bool:  # noqa: ARG001 - folders move whole
        return nbu_fs.mv(_local_path(src), _local_path(dest))

    def mount(*_a: Any, **_kw: Any) -> None:
        raise NotImplementedError("Mounting storage runs in Databricks. On this PC, read the attached lakehouse.")

    def help(*_a: Any) -> None:  # noqa: A001
        print("dbutils.fs on this PC: ls, head, put, cp, mv, rm, mkdirs")

    return types.SimpleNamespace(ls=wrap(nbu_fs.ls), head=wrap(nbu_fs.head), put=wrap(nbu_fs.put),
                                 cp=wrap(nbu_fs.cp, 2), mv=mv, rm=wrap(nbu_fs.rm), mkdirs=wrap(nbu_fs.mkdirs),
                                 mount=mount, unmount=mount, updateMount=mount, mounts=lambda: [],
                                 refreshMounts=lambda: True, help=help)


class Widgets:
    """dbutils.widgets. Every value is a string, as in Databricks."""

    def __init__(self) -> None:
        self.defined: dict[str, dict[str, Any]] = {}
        self.passed: dict[str, str] = {}  # arguments from notebook.run or runMultiple
        self.set_in_app: dict[str, str] = {}

    def _define(self, kind: str, name: str, defaultValue: str, label: str | None,  # noqa: N803
                choices: list[str] | None = None) -> None:
        name, default = str(name), str(defaultValue)
        if choices is not None:
            choices = [str(c) for c in choices]
            picked = default.split(",") if kind == "multiselect" else [default]
            if any(p not in choices for p in picked):
                raise ValueError(f"Widget {name}: the default {default!r} isn't one of the choices.")
        self.defined[name] = {"name": name, "kind": kind, "default": default, "label": label or name,
                              "choices": choices}

    def text(self, name: str, defaultValue: str = "", label: str | None = None) -> None:  # noqa: N803
        self._define("text", name, defaultValue, label)

    def dropdown(self, name: str, defaultValue: str, choices: list[str], label: str | None = None) -> None:  # noqa: N803
        self._define("dropdown", name, defaultValue, label, choices)

    def combobox(self, name: str, defaultValue: str, choices: list[str], label: str | None = None) -> None:  # noqa: N803
        self.defined[str(name)] = {"name": str(name), "kind": "combobox", "default": str(defaultValue),
                                   "label": label or str(name), "choices": [str(c) for c in choices]}

    def multiselect(self, name: str, defaultValue: str, choices: list[str], label: str | None = None) -> None:  # noqa: N803
        self._define("multiselect", name, defaultValue, label, choices)

    def get(self, name: str) -> str:
        name = str(name)
        if name in self.passed:
            return self.passed[name]
        if name not in self.defined:
            raise InputWidgetNotDefined(f"No widget named {name}. Create it first, for example "
                                        f"dbutils.widgets.text({name!r}, \"\").")
        widget = self.defined[name]
        value = self.set_in_app.get(name)
        picked = (value.split(",") if value else []) if widget["kind"] == "multiselect" else [value]
        if value is None or (widget["kind"] in ("dropdown", "multiselect") and widget["choices"] is not None
                             and any(v not in widget["choices"] for v in picked)):
            return widget["default"]  # a bar value that isn't one of the choices (the bar says so)
        return value

    def getArgument(self, name: str, optional: str | None = None) -> str | None:  # noqa: N802
        try:
            return self.get(name)
        except InputWidgetNotDefined:
            return optional

    def getAll(self) -> dict[str, str]:  # noqa: N802
        return {n: self.get(n) for n in {*self.defined, *self.passed}}

    def remove(self, name: str) -> None:
        if self.defined.pop(str(name), None) is None:
            raise InputWidgetNotDefined(f"No widget named {name}.")
        self.set_in_app.pop(str(name), None)

    def removeAll(self) -> None:  # noqa: N802
        self.defined.clear()
        self.set_in_app.clear()

    def bind(self, arguments: dict[str, Any]) -> None:
        self.passed = {str(k): "" if v is None else str(v) for k, v in arguments.items()}

    def describe(self) -> list[dict[str, Any]]:
        """The widgets, in the order they were created, with their current values (for the app)."""
        return [{**w, "value": self.get(n)} for n, w in self.defined.items()]

    def help(self, *_a: Any) -> None:
        print("dbutils.widgets on this PC: text, dropdown, combobox, multiselect, get, getAll, getArgument, "
              "remove, removeAll")


@dataclass
class SecretMetadata:
    key: str


@dataclass
class SecretScope:
    name: str


def _make_secrets(get_secret: Callable[..., str], token: Callable[[str], str]) -> types.SimpleNamespace:
    def vault(scope: str) -> str:
        return vault_url(scope)

    def get(scope: str, key: str) -> str:
        return get_secret(scope, key)

    def getBytes(scope: str, key: str) -> bytes:  # noqa: N802
        return get(scope, key).encode("utf-8")

    def list(scope: str) -> list[SecretMetadata]:  # noqa: A001
        url = f"{vault(scope).rstrip('/')}/secrets"
        headers = {"Authorization": f"Bearer {token('keyvault')}"}
        names: list[SecretMetadata] = []
        params: dict[str, str] | None = {"api-version": "7.4"}
        pages = Pages(f"the secrets in Key Vault {vault(scope)}", start=url)
        while url:
            resp = send_with_retry(lambda u=url, p=params: http_session.session().get(u, params=p, headers=headers,
                                                                                      timeout=30), "GET")
            if resp.status_code in (401, 403):
                raise PermissionError(f"Key Vault {vault(scope)} refused to list secrets for your account. "
                                      f"You need the Key Vault Secrets User role on it.")
            if resp.status_code >= 400:
                raise keyvault_error(resp, vault(scope), "list its secrets")
            body = resp.json()
            names += [SecretMetadata(item["id"].rstrip("/").rsplit("/", 1)[-1]) for item in body.get("value", [])]
            url, params = body.get("nextLink"), None
            if url and not on_vault(url, vault(scope)):
                raise ValueError(f"Key Vault {vault(scope)} answered with a next page on another host; stopped listing.")
            url = pages.follow(url) if url else url  # a repeated page or too many stops it, never a short list
        return names

    def listScopes() -> list[SecretScope]:  # noqa: N802
        print("Note: on this PC a secret scope is an Azure Key Vault name. Use it as the scope, "
              "for example dbutils.secrets.get(\"my-vault\", \"key\").")
        return []

    return types.SimpleNamespace(get=get, getBytes=getBytes, list=list, listScopes=listScopes,
                                 help=lambda *_a: print("dbutils.secrets on this PC: get, getBytes, list, listScopes "
                                                        "(a scope is a Key Vault name)"))


def _make_task_values(host: Host) -> types.SimpleNamespace:
    def set(key: str, value: Any) -> None:  # noqa: A001
        json.dumps(value)  # Databricks keeps task values as JSON
        task = host.tasks[-1] if host.tasks else host.notebook_name()
        host.task_values.setdefault(task, {})[str(key)] = value

    def get(taskKey: str, key: str, default: Any = None, debugValue: Any = None) -> Any:  # noqa: N803
        if not host.tasks:
            # Run by hand, Databricks answers with debugValue.
            if debugValue is None:
                raise TypeError("Run by hand, taskValues.get returns debugValue; pass debugValue=... "
                                "or run the notebooks with runMultiple.")
            return debugValue
        values = host.task_values.get(str(taskKey), {})
        if str(key) in values:
            return values[str(key)]
        if default is None:
            raise ValueError(f"Task {taskKey} set no value {key!r}, and no default was given.")
        return default

    return types.SimpleNamespace(set=set, get=get)


class _Opt:
    """Databricks' Scala Option, as entry_point's context returns it."""

    def __init__(self, value: Any):
        self.value = value

    def get(self) -> Any:
        if self.value is None:
            raise ValueError("None.get")
        return self.value

    def isDefined(self) -> bool:  # noqa: N802
        return self.value is not None

    def getOrElse(self, other: Any) -> Any:  # noqa: N802
        return other if self.value is None else self.value


def _make_entry_point(host: Host) -> types.SimpleNamespace:
    import os

    def context() -> types.SimpleNamespace:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        name = host.notebook_name()
        tags = {"notebookPath": f"/{name}", "user": user, "isLocal": "true"}
        return types.SimpleNamespace(notebookPath=lambda: _Opt(f"/{name}"), userName=lambda: _Opt(user),
                                     currentRunId=lambda: _Opt(None), tags=lambda: tags,
                                     toJson=lambda: json.dumps({"tags": tags, "extraContext": {}}))

    notebook = types.SimpleNamespace(getContext=context)
    return types.SimpleNamespace(getDbutils=lambda: types.SimpleNamespace(notebook=lambda: notebook))


def build(host: Host, notebookutils: types.ModuleType) -> types.SimpleNamespace:
    nbu = notebookutils

    def run(path: str, timeout_seconds: int = 0, arguments: dict[str, Any] | None = None, **_kw: Any) -> str | None:
        value = nbu.notebook.run(path, timeout_seconds, arguments or {})
        return None if value is None else str(value)

    def restartPython() -> None:  # noqa: N802
        raise RestartPython()

    def unsupported(what: str) -> Callable[..., Any]:
        def call(*_a: Any, **_kw: Any) -> Any:
            raise NotImplementedError(what)
        return call

    def summarize(df: Any, precise: bool = False) -> None:  # noqa: ARG001
        host.display(df, summary=True)

    widgets = Widgets()
    notebook = types.SimpleNamespace(run=run, exit=nbu.notebook.exit, entry_point=_make_entry_point(host),
                                     help=lambda *_a: print("dbutils.notebook on this PC: run, exit"))
    aws = "AWS role credentials are a Databricks on AWS feature; this PC signs in to Microsoft as you."
    return types.SimpleNamespace(
        fs=_make_fs(nbu.fs), notebook=notebook, widgets=widgets,
        secrets=_make_secrets(nbu.credentials.getSecret, nbu.credentials.getToken),
        jobs=types.SimpleNamespace(taskValues=_make_task_values(host)),
        library=types.SimpleNamespace(restartPython=restartPython, install=unsupported(
            "Installing libraries runs in Databricks. On this PC the engine's packages are fixed.")),
        data=types.SimpleNamespace(summarize=summarize),
        credentials=types.SimpleNamespace(assumeRole=unsupported(aws), showCurrentRole=unsupported(aws),
                                          showRoles=unsupported(aws)),
        help=lambda *_a: print("dbutils on this PC: fs, notebook, widgets, secrets, jobs.taskValues, "
                               "library.restartPython, data.summarize"))
