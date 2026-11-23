"""Reading and writing an environment's manifest, and this PC's cached copy (CP-002).

The manifest is one file in the environment's analytical store: ``Files/_kratosforge/environment.json``
in Fabric. Every write is conditional, like the lease files (leases.py): ``create`` only when no
manifest exists (If-None-Match: *), ``replace`` only when it is still the copy that was read
(If-Match: <etag>). Two installations writing at once can't overwrite each other: the second one is
told the manifest changed, rereads it and decides again.

The cache is a JSON file on this PC listing the environments it connected to, so the app knows its
environment while offline. Forgetting one never touches the environment.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import jsonfile
from ..leases import Backend, LocalBackend, OneLakeBackend, Stored
from ..onelake import _onelake_error, item_url
from ..retrying import send_with_retry
from . import manifest as mf

FOLDER = "Files/_kratosforge"
NAME = "environment"
PATH = f"{FOLDER}/{NAME}.json"


class ManifestExists(RuntimeError):
    """Create was refused: this store already holds an environment. Connect to it instead."""


class ManifestChanged(RuntimeError):
    """A replace was refused: another installation changed the manifest since it was read. Reread it."""


@dataclass(frozen=True)
class Read:
    manifest: dict[str, Any]
    etag: str


class OneLakeManifestBackend(OneLakeBackend):
    """The lease backend's conditional OneLake writes, pointed at ``Files/_kratosforge/``."""

    WHAT = "the manifest of the KratosForge"  # errors read "write the manifest of the KratosForge environment"

    def _url(self, key: str) -> str:
        return item_url(self.workspace_id, self.lakehouse_id, f"{FOLDER}/{key}.json")  # key is NAME

    def read(self, key: str) -> Stored | None:
        """The file, read at most ``manifest.MAX_BYTES``: anyone who can write to the workspace can put a
        file here, and discovery reads one per lakehouse, so a huge one is refused, not loaded."""
        resp = send_with_retry(lambda: self._http.get(self._url(key), headers=self._headers(**{"Accept-Encoding": "identity"}),
                                                      timeout=self.timeout, stream=True),
                               "GET", sleep=self._sleep)  # identity: the cap counts the real bytes
        try:
            if resp.status_code == 404:
                return None
            if resp.status_code >= 400:
                raise _onelake_error(resp, f"read {self.WHAT} {key}")
            too_big = mf.ManifestError(f"The manifest is larger than {mf.MAX_BYTES // 1024} KB, so it isn't one "
                                       "KratosForge wrote")
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > mf.MAX_BYTES:
                raise too_big
            data = bytearray()
            for chunk in resp.iter_content(16 * 1024):
                data += chunk
                if len(data) > mf.MAX_BYTES:
                    raise too_big
            return Stored(bytes(data), resp.headers.get("ETag", ""), time.time())
        finally:
            resp.close()


class ManifestStore:
    """One store's manifest. ``backend`` is a leases.Backend: OneLake, or a folder on this PC."""

    def __init__(self, backend: Backend):
        self.backend = backend

    def read(self) -> Read | None:
        """The manifest and its etag, or None when the store holds none. A manifest that breaks a
        rule raises ManifestError: it is shown as a problem, never silently replaced."""
        stored = self.backend.read(NAME)
        if stored is None:
            return None
        return Read(mf.from_json(stored.data), stored.etag)

    def create(self, manifest: dict[str, Any]) -> Read:
        data = mf.to_json(manifest)
        etag = self.backend.create(NAME, data)
        if etag is None:
            raise ManifestExists("This store already holds a KratosForge Environment. Connect to it instead "
                                 "of creating another.")
        return Read(mf.from_json(data), etag)

    def replace(self, current: Read, manifest: dict[str, Any]) -> Read:
        if current.manifest["environment_id"] != manifest.get("environment_id"):
            raise mf.ManifestError("A manifest can't be replaced by another environment's")
        if not mf.writable(current.manifest):
            raise mf.ManifestError("A newer version of KratosForge wrote this manifest; it's read-only here.")
        data = mf.to_json(manifest)
        etag = self.backend.replace(NAME, data, current.etag)
        if etag is None:
            raise ManifestChanged("Another installation changed this environment's manifest since it was read. "
                                  "Read it again and retry.")
        return Read(mf.from_json(data), etag)


def local_store(folder: Path | str) -> ManifestStore:
    """A manifest in a folder on this PC: a local environment, and the tests."""
    return ManifestStore(LocalBackend(Path(folder) / FOLDER))


def onelake_store(workspace_id: str, lakehouse_id: str, token: Callable[[], str], **kwargs: Any) -> ManifestStore:
    return ManifestStore(OneLakeManifestBackend(workspace_id, lakehouse_id, token, **kwargs))


class Cache:
    """The environments this PC connected to (``<data>/controlplane/environments.json``), and the active one.

    Holds ``manifest.summary`` entries only: ids and labels, never anything credential-shaped."""

    def __init__(self, folder: Path | str):
        self.path = Path(folder) / "environments.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            data = jsonfile.read_json(self.path, {})
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        envs = data.get("environments")
        envs = {k: v for k, v in envs.items() if isinstance(v, dict)} if isinstance(envs, dict) else {}
        active = data.get("active")
        return {"active": active if isinstance(active, str) else "", "environments": envs}

    def all(self) -> list[dict[str, Any]]:
        return sorted(self._load()["environments"].values(), key=lambda e: (e.get("name") or "").lower())

    def active(self) -> dict[str, Any] | None:
        data = self._load()
        return data["environments"].get(data["active"]) if data["active"] else None

    def remember(self, manifest: dict[str, Any], activate: bool = True) -> dict[str, Any]:
        entry = mf.summary(mf.validate(manifest))
        with self._lock:
            data = self._load()
            data["environments"][entry["environment_id"]] = entry
            if activate or not data["active"]:
                data["active"] = entry["environment_id"]
            jsonfile.write_json(self.path, data)
        return entry

    def activate(self, environment_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            if not isinstance(environment_id, str) or environment_id not in data["environments"]:
                raise KeyError("This PC isn't connected to that environment")
            data["active"] = environment_id
            jsonfile.write_json(self.path, data)
            return data["environments"][environment_id]

    def forget(self, environment_id: str) -> bool:
        """Forget an environment on this PC. Nothing in the environment changes."""
        with self._lock:
            data = self._load()
            if not isinstance(environment_id, str) or data["environments"].pop(environment_id, None) is None:
                return False
            if data["active"] == environment_id:
                data["active"] = ""
            jsonfile.write_json(self.path, data)
            return True
