"""OneLake addressing for schema-enabled lakehouses."""

from __future__ import annotations

import email.utils
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

import requests

from . import http_session, redact
from .paging import Pages
from .retrying import send_with_retry

DFS_HOST = "onelake.dfs.fabric.microsoft.com"
TABLE_API = "https://onelake.table.fabric.microsoft.com/delta"

# Characters Fabric does not allow in table names.
_BAD_NAME = re.compile(r"[\"'#%+:?`/\\]")


def check_name(kind: str, name: str) -> str:
    if not name or _BAD_NAME.search(name) or name != name.strip():
        raise ValueError(f"Invalid {kind} name {name!r}: avoid quotes, # % + : ? ` / \\ and edge spaces.")
    return name


@dataclass(frozen=True)
class TableRef:
    """A Delta table in a schema-enabled lakehouse, addressed by GUIDs."""

    workspace_id: str
    lakehouse_id: str
    schema: str
    table: str
    # A lakehouse created without schemas keeps its tables directly under Tables/.
    flat: bool = False

    def __post_init__(self):
        check_name("schema", self.schema)
        check_name("table", self.table)

    @property
    def uri(self) -> str:
        # GUIDs avoid ABFS failures on workspace names with spaces.
        folder = self.table if self.flat else f"{self.schema}/{self.table}"
        return f"abfss://{self.workspace_id}@{DFS_HOST}/{self.lakehouse_id}/Tables/{folder}"


# Uploads on a slow line. delta-rs sends each data file as 5 MB parts, 10 at a time, with several
# files in flight, and object_store gives each request 30 s and all its retries 180 s. On a 42 Mbps
# upload, tens of parts share about 5 MB/s, so a part can take longer than 30 s: it times out and is
# sent again, and the table write fails after 3 minutes ("error sending request", issue #785).
# Fewer parts at once (still enough to fill a fast line), and time limits that let a slow line
# finish a part, instead of retrying it from the start. Read once, at the process's first write,
# so set here, before any; a value set in the environment wins.
os.environ.setdefault("DELTARS_MAX_CONCURRENCY_TASKS", "4")  # parts in flight for one file
os.environ.setdefault("DELTARS_MAX_IN_FLIGHT_UPLOAD_BYTES", str(256 * 1024 * 1024))  # files in flight, by size
REQUEST_TIMEOUT = "600s"  # one part: 5 MB in 10 minutes is 8 KB/s
RETRY_TIMEOUT = "1800s"  # a part's tries, all together: longer than one try, so a timed-out part is tried again


def storage_options(token: str) -> dict[str, str]:
    """delta-rs / object_store options for OneLake with a user bearer token."""
    return {"bearer_token": token, "use_fabric_endpoint": "true",
            "timeout": REQUEST_TIMEOUT, "connect_timeout": "30s", "retry_timeout": RETRY_TIMEOUT}


# A short read the person waits on, such as a replay bundle's sample: storage_options' upload-tuned
# limits would keep it retrying for up to 30 minutes on a PC that's offline (#1303).
READ_TIMEOUT = "60s"
READ_CONNECT_TIMEOUT = "10s"
READ_RETRY_TIMEOUT = "60s"
READ_MAX_RETRIES = "2"


def read_storage_options(token: str) -> dict[str, str]:
    """storage_options for a small read the person waits on: it fails within about a minute when
    OneLake can't be reached, instead of retrying for up to 30 minutes."""
    return storage_options(token) | {"timeout": READ_TIMEOUT, "connect_timeout": READ_CONNECT_TIMEOUT,
                                     "retry_timeout": READ_RETRY_TIMEOUT, "max_retries": READ_MAX_RETRIES}


_CODE = re.compile(r"[A-Za-z0-9_.]{1,64}")


def error_code(value: object) -> str:
    """A service's error code fit for a one-line message: letters, digits, "_" and "." only, at most
    64 of them. Anything else (an odd or hostile reply) gives "", so it can't grow or bend the line."""
    text = str(value or "")
    return text if _CODE.fullmatch(text) else ""


def _onelake_error(resp: requests.Response, what: str) -> requests.HTTPError:
    """A refused OneLake request as one line: what didn't happen, the status and OneLake's error code
    (the DFS API's ``error.code``, the table API's ``error_code``, or ``x-ms-error-code``). The path
    is the one inside the lakehouse, never the whole URL. Still a requests.HTTPError, with the
    response, for callers that read its status."""
    code = resp.headers.get("x-ms-error-code") if resp.headers else None
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        code = (err.get("code") if isinstance(err, dict) else None) or body.get("error_code") or code
    code = error_code(code)
    return requests.HTTPError(f"OneLake didn't {what} ({resp.status_code}{': ' + code if code else ''}).",
                              response=resp)


class TableCatalog:
    """Read-only listing through the OneLake Delta table API (Unity Catalog compatible)."""

    def __init__(self, token_provider: Callable[[], str], session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._token = token_provider
        self._http = session or http_session.session()
        self._sleep = sleep

    def _get(self, url: str, what: str, **params) -> dict[str, Any]:
        resp = send_with_retry(lambda: self._http.get(url, params=params, timeout=60,
                                                      headers={"Authorization": f"Bearer {self._token()}"}),
                               "GET", sleep=self._sleep)
        if resp.status_code >= 400:
            raise _onelake_error(resp, what)
        return resp.json()

    def list_schemas(self, workspace_id: str, lakehouse_id: str) -> list[str]:
        url = f"{TABLE_API}/{workspace_id}/{lakehouse_id}/api/2.1/unity-catalog/schemas"
        return [s["name"] for s in self._get(url, "list the lakehouse's schemas", catalog_name=lakehouse_id).get("schemas", [])]

    def list_tables(self, workspace_id: str, lakehouse_id: str, schema: str) -> list[str]:
        url = f"{TABLE_API}/{workspace_id}/{lakehouse_id}/api/2.1/unity-catalog/tables"
        body = self._get(url, f"list the tables in {schema}", catalog_name=lakehouse_id, schema_name=schema)
        return [t["name"] for t in body.get("tables", [])]


def _iso(http_date: str | None) -> str:
    """An HTTP date (as the DFS API gives lastModified) as ISO 8601, or "" when there isn't one."""
    if not http_date:
        return ""
    try:
        return email.utils.parsedate_to_datetime(http_date).isoformat()
    except (TypeError, ValueError):
        return ""


def item_path(path: str, empty_ok: bool = False) -> str:
    """``path`` inside a lakehouse, without its edge slashes. Refuses a ``.`` or ``..`` segment
    (also percent-encoded or with backslashes): the HTTP client resolves them before sending, so
    the request would land in another lakehouse or workspace. An empty path names the lakehouse's
    own root, which only a read may do (``empty_ok``): a delete there removes everything."""
    clean = path.strip("/")
    for segment in re.split(r"[/\\]", unquote(clean)):
        if segment in (".", ".."):
            raise ValueError(f"{path!r} isn't a path inside the lakehouse.")
    if not clean and not empty_ok:
        raise ValueError("A file path is needed; an empty one would name the whole lakehouse.")
    return clean



class FileTooBig(ValueError):
    """A download stopped at its size limit (FileStore.download's max_bytes)."""

    def __init__(self, path: str, max_bytes: int):
        super().__init__(f"{path} is over {max_bytes // (1024 * 1024)} MB, so it wasn't downloaded.")
        self.path, self.max_bytes = path, max_bytes

# How many times a shortcut check asks OneLake before giving its last answer. The Shortcuts page
# and the agent's watch run 8 checks at once, so each one gives up sooner than a file read does.
PROBE_ATTEMPTS = 3


class FileStore:
    """The lakehouse Files area, through the OneLake DFS (ADLS Gen2) API.

    Reads and folder creates are retried on throttling, 5xx and dropped connections
    (retrying.send_with_retry). Uploads and deletes wait out throttling only; any other failure is
    sent once, since a write that failed part way may have happened."""

    def __init__(self, token_provider: Callable[[], str], session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._token = token_provider
        self._http = session or http_session.session()
        self._sleep = sleep

    def _read(self, url: str, extra_headers: dict[str, str] | None = None, **kwargs: Any) -> requests.Response:
        return send_with_retry(lambda: self._http.get(url, headers={**self._headers(), **(extra_headers or {})},
                                                      **kwargs), "GET", sleep=self._sleep)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}", "x-ms-version": "2023-11-03"}

    def list(self, workspace_id: str, lakehouse_id: str, directory: str = "Files",
             recursive: bool = False, max_results: int | None = None) -> list[dict[str, Any]]:
        """Children of ``directory`` (all the way down with ``recursive``) as ``{"name", "path", "is_dir", "size", "modified"}``.
        With ``max_results``, only the first page of at most that many."""
        url = f"https://{DFS_HOST}/{workspace_id}"
        params = {"resource": "filesystem", "recursive": "true" if recursive else "false",
                  "directory": f"{lakehouse_id}/{item_path(directory, empty_ok=True)}"}
        if max_results:
            params["maxResults"] = str(max_results)
        entries: list[dict[str, Any]] = []
        pages = Pages(f"{directory or 'Files'} in OneLake")
        while True:
            resp = self._read(url, params=dict(params), timeout=60)
            if resp.status_code >= 400:
                raise _onelake_error(resp, f"list {directory or 'Files'}")
            for p in resp.json().get("paths", []):
                path = p["name"].split("/", 1)[1]  # drop the lakehouse GUID
                entries.append({"name": path.rsplit("/", 1)[-1], "path": path,
                                "is_dir": str(p.get("isDirectory", "")).lower() == "true",
                                "size": int(p.get("contentLength") or 0), "modified": _iso(p.get("lastModified"))})
            token = resp.headers.get("x-ms-continuation")
            if not token or max_results:
                break
            params["continuation"] = pages.follow(token)
        return sorted(entries, key=lambda e: (not e["is_dir"], e["name"].lower()))

    def probe(self, workspace_id: str, lakehouse_id: str, directory: str) -> tuple[int | None, str]:
        """List at most one entry of ``directory``: (HTTP status, message). Reads nothing else and
        writes nothing; through a shortcut it proves Fabric can reach the target. Status None: the
        request didn't get an answer. Throttling and 5xx are tried again (PROBE_ATTEMPTS in all)
        before the last answer is returned."""
        url = f"https://{DFS_HOST}/{workspace_id}"
        params = {"resource": "filesystem", "recursive": "false", "maxResults": "1",
                  "directory": f"{lakehouse_id}/{item_path(directory, empty_ok=True)}"}
        try:  # a read: OneLake throttling, a 5xx or a dropped connection is waited out, a few times
            resp = send_with_retry(lambda: self._http.get(url, params=params, headers=self._headers(), timeout=60),
                                   "GET", sleep=self._sleep, attempts=PROBE_ATTEMPTS)
        except requests.RequestException as exc:
            return None, redact.one_line(exc)
        if resp.status_code < 400:
            return resp.status_code, ""
        try:
            body = resp.json()
            err = (body.get("error") if isinstance(body, dict) else None) or {}
            if not isinstance(err, dict):
                err = {"message": err}
            message = f"{err.get('code', '')} {err.get('message', '')}"
        except ValueError:
            message = resp.text
        return resp.status_code, redact.one_line(message)

    def download(self, workspace_id: str, lakehouse_id: str, path: str, dest: Path,
                 max_bytes: int | None = None) -> Path:
        """Download a file to ``dest``. With ``max_bytes``, a file bigger than that stops downloading
        there and raises FileTooBig, and the part written is removed."""
        url = f"https://{DFS_HOST}/{workspace_id}/{lakehouse_id}/{item_path(path)}"
        with self._read(url, stream=True, timeout=300) as resp:
            if resp.status_code >= 400:
                raise _onelake_error(resp, f"read {path}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with dest.open("wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        break
                    f.write(chunk)
            if max_bytes is not None and written > max_bytes:
                dest.unlink(missing_ok=True)
                raise FileTooBig(path, max_bytes)
        return dest

    def read_bytes(self, workspace_id: str, lakehouse_id: str, path: str, length: int | None = None) -> bytes | None:
        """A small file's content, or None when it doesn't exist. With ``length``, only its first
        ``length`` bytes are asked for (a Range request), so peeking at a large file is cheap."""
        url = f"https://{DFS_HOST}/{workspace_id}/{lakehouse_id}/{item_path(path)}"
        ranged = {"Range": f"bytes=0-{length - 1}"} if length else None
        resp = self._read(url, extra_headers=ranged, timeout=60)
        if resp.status_code == 404:
            return None
        if ranged and resp.status_code == 416:  # an empty file has no byte 0
            return b""
        if resp.status_code >= 400:
            raise _onelake_error(resp, f"read {path}")
        return resp.content[:length] if length else resp.content

    def upload(self, workspace_id: str, lakehouse_id: str, path: str, data: bytes) -> None:
        """Create or replace a file: create, append, then flush (the ADLS Gen2 way)."""
        url = f"https://{DFS_HOST}/{workspace_id}/{lakehouse_id}/{item_path(path)}"
        steps = [("PUT", {"resource": "file"}, None),
                 ("PATCH", {"action": "append", "position": "0"}, data),
                 ("PATCH", {"action": "flush", "position": str(len(data))}, None)]
        for (method, params, body), step in zip(steps, ("create", "write", "commit")):
            if body is not None and not body:
                continue  # an empty file needs no append
            # Throttling (429) is waited out: OneLake did nothing. Anything else is sent once, since
            # the step may have happened; a failed append is never flushed, so no half file is seen.
            resp = send_with_retry(lambda: self._http.request(method, url, params=params, data=body,
                                                              headers=self._headers(), timeout=300),
                                   method, sleep=self._sleep, safe=False)
            if resp.status_code >= 400:
                raise _onelake_error(resp, f"{step} {path}")

    def mkdir(self, workspace_id: str, lakehouse_id: str, path: str) -> None:
        """Create a folder (and any missing parents). One that is already there is left as it is:
        If-None-Match makes OneLake answer 409 instead of touching it."""
        url = f"https://{DFS_HOST}/{workspace_id}/{lakehouse_id}/{item_path(path)}"
        # If-None-Match can't change a folder that's there, so sending it again is safe: throttling,
        # a 5xx and a dropped connection are tried again.
        resp = send_with_retry(lambda: self._http.request("PUT", url, params={"resource": "directory"},
                                                          headers={**self._headers(), "If-None-Match": "*"},
                                                          timeout=60), "PUT", sleep=self._sleep, safe=True)
        if resp.status_code >= 400 and resp.status_code != 409:
            raise _onelake_error(resp, f"create the folder {path}")

    def delete(self, workspace_id: str, lakehouse_id: str, path: str, recursive: bool = True) -> None:
        """Delete a file or a whole folder; a missing one is fine. Without ``recursive``, OneLake
        refuses (409) a folder that isn't empty."""
        url = f"https://{DFS_HOST}/{workspace_id}/{lakehouse_id}/{item_path(path)}"
        # Throttling (429) is waited out; anything else is sent once (HD-053).
        resp = send_with_retry(lambda: self._http.request("DELETE", url,
                                                          params={"recursive": "true" if recursive else "false"},
                                                          headers=self._headers(), timeout=300),
                               "DELETE", sleep=self._sleep, safe=False)
        if resp.status_code >= 400 and resp.status_code != 404:
            raise _onelake_error(resp, f"delete {path}")
