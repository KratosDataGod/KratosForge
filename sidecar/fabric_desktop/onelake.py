"""OneLake addressing for schema-enabled lakehouses."""

from __future__ import annotations

import email.utils
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import quote, unquote, urlsplit

import requests

from . import http_session, redact
from .paging import Pages
from .retrying import retry_call, send_with_retry

_log = logging.getLogger(__name__)

# FileStore.upload's new file beside its target, until it's renamed over it: never listed.
UPLOADING_SUFFIX = ".uploading"
UPLOADING = re.compile(r"(^|/)\.[^/]+\.[0-9a-f]{12}" + re.escape(UPLOADING_SUFFIX) + "$")

DFS_HOST = "onelake.dfs.fabric.microsoft.com"
TABLE_API = "https://onelake.table.fabric.microsoft.com/delta"

# Characters Fabric does not allow in table names.
_BAD_NAME = re.compile(r"[\"'#%+:?`/\\]")


def check_name(kind: str, name: str) -> str:
    if not name or _BAD_NAME.search(name) or name != name.strip() or name in (".", ".."):
        raise ValueError(f"Invalid {kind} name {name!r}: avoid quotes, # % + : ? ` / \\ and edge spaces.")
    return name


_BAD_ID = re.compile(r"[/@\\?#%:\s]")


def workspace_root(workspace_id: str) -> str:
    """A workspace's OneLake container, ``abfss://<workspace>@onelake...``: what OpenLineage calls a
    dataset namespace. The id is checked as lakehouse_root checks it."""
    return lakehouse_root(workspace_id, "_").rsplit("/", 1)[0]


def lakehouse_root(workspace_id: str, lakehouse_id: str) -> str:
    """The OneLake address of a lakehouse, ``abfss://<workspace>@onelake.../<lakehouse>``: the one place it
    is built (tests/test_table_uris.py fails on another). The ids are GUIDs, which ABFS reads where a
    name with spaces fails; one that would change the address's shape (a slash, @, ?, #, %, :, or . or ..
    alone) is refused."""
    for kind, value in (("workspace", workspace_id), ("lakehouse", lakehouse_id)):
        if not value or _BAD_ID.search(value) or value in (".", ".."):
            raise ValueError(f"Invalid {kind} id {value!r} for a OneLake address.")
    return f"abfss://{workspace_id}@{DFS_HOST}/{lakehouse_id}"


def table_uri(root: str, schema: str, table: str, flat: bool = False) -> str:
    """A Delta table's folder under a lakehouse ``root`` (lakehouse_root, or a local folder in tests and
    runs on this PC): ``<root>/Tables/<schema>/<table>``, or ``<root>/Tables/<table>`` in a lakehouse
    without schemas. Names are checked as Fabric checks them, so none can step into another folder."""
    check_name("table", table)
    if flat:
        return f"{root}/Tables/{table}"
    return f"{root}/Tables/{check_name('schema', schema)}/{table}"


@dataclass(frozen=True)
class TableRef:
    """A Delta table in a schema-enabled lakehouse, addressed by GUIDs."""

    workspace_id: str
    lakehouse_id: str
    schema: str
    table: str
    # A lakehouse created without schemas keeps its tables directly under Tables/.
    flat: bool = False

    def __post_init__(self) -> None:
        check_name("schema", self.schema)
        check_name("table", self.table)

    @property
    def uri(self) -> str:
        # GUIDs avoid ABFS failures on workspace names with spaces.
        return table_uri(lakehouse_root(self.workspace_id, self.lakehouse_id), self.schema, self.table, self.flat)


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


_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
# What a person can do about a refused OneLake request, by status, so every OneLake error says what to do.
_NEXT_STEPS = {
    401: "Sign out and in again",
    403: "Your account needs access to this workspace in Fabric (Contributor to write). Ask a workspace admin",
    404: "Check the workspace, lakehouse and path: it may have been deleted or moved",
    409: "Refresh, then try again: something else changed it at the same time",
    412: "Refresh, then try again: something else changed it at the same time",
    429: "OneLake is limiting requests. Try again in a few minutes",
}
# The fallback steps point at the request id only when OneLake sent one to give.
_SERVER_NEXT_STEP = "OneLake had a problem on its side. Try again, and give the request id to support if it keeps happening"
_SERVER_NEXT_STEP_NO_ID = "OneLake had a problem on its side. Try again, and contact support if it keeps happening"
_NEXT_STEP = "Check the path and try again, or give the request id to support"
_NEXT_STEP_NO_ID = "Check the path and try again"


def _onelake_error(resp: requests.Response, what: str) -> requests.HTTPError:
    """A refused OneLake request as one line: what didn't happen, the status and OneLake's error code
    (the DFS API's ``error.code``, the table API's ``error_code``, or ``x-ms-error-code``), one next
    step, and OneLake's request id (``x-ms-request-id``) for support. The path is the one inside the
    lakehouse, never the whole URL. Still a requests.HTTPError, with the response, for callers that
    read its status."""
    code = resp.headers.get("x-ms-error-code") if resp.headers else None
    request_id = str((resp.headers.get("x-ms-request-id") if resp.headers else None) or "")
    request_id = request_id if _REQUEST_ID.fullmatch(request_id) else ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        code = (err.get("code") if isinstance(err, dict) else None) or body.get("error_code") or code
    code = error_code(code)
    status = resp.status_code
    if status >= 500 and status not in _NEXT_STEPS:
        step = _SERVER_NEXT_STEP if request_id else _SERVER_NEXT_STEP_NO_ID
    else:
        step = _NEXT_STEPS.get(status) or (_NEXT_STEP if request_id else _NEXT_STEP_NO_ID)
    return requests.HTTPError(f"OneLake didn't {what} ({status}{': ' + code if code else ''}). {step}."
                              + (f" Request id {request_id}." if request_id else ""), response=resp)


class OneLakeBadReply(requests.HTTPError):
    """A OneLake answer that isn't what OneLake sends (an outage page, a cut-off or wrongly shaped
    body): refused, so nothing in it is listed. Still a requests.HTTPError, with the response."""


def _bad_reply(resp: requests.Response, what: str) -> OneLakeBadReply:
    return OneLakeBadReply(f"OneLake didn't {what} because its answer wasn't one Fabric Desktop by KratosBI can "
                           "read, so nothing from it was used. Try again in a minute.", response=resp)


def _reply(resp: requests.Response, what: str, key: str) -> list[dict[str, Any]]:
    """The records under ``key`` in a OneLake JSON answer (none when the key is missing)."""
    try:
        body = resp.json()
    except (ValueError, RecursionError):
        body = None
    records = body.get(key, []) if isinstance(body, dict) else None
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise _bad_reply(resp, what)
    return records


def _names(resp: requests.Response, what: str, key: str) -> list[str]:
    names = [r.get("name") for r in _reply(resp, what, key)]
    if not all(isinstance(n, str) and n for n in names):
        raise _bad_reply(resp, what)
    return cast(list[str], names)


class TableCatalog:
    """Read-only listing through the OneLake Delta table API (Unity Catalog compatible)."""

    def __init__(self, token_provider: Callable[[], str], session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._token = token_provider
        self._http = session or http_session.session()
        self._sleep = sleep

    def _get(self, url: str, what: str, **params: Any) -> requests.Response:
        resp = send_with_retry(lambda: self._http.get(url, params=params, timeout=60,
                                                      headers={"Authorization": f"Bearer {self._token()}"}),
                               "GET", sleep=self._sleep)
        if resp.status_code >= 400:
            raise _onelake_error(resp, what)
        return resp

    def list_schemas(self, workspace_id: str, lakehouse_id: str) -> list[str]:
        url = f"{TABLE_API}/{workspace_id}/{lakehouse_id}/api/2.1/unity-catalog/schemas"
        what = "list the lakehouse's schemas"
        return _names(self._get(url, what, catalog_name=lakehouse_id), what, "schemas")

    def list_tables(self, workspace_id: str, lakehouse_id: str, schema: str) -> list[str]:
        url = f"{TABLE_API}/{workspace_id}/{lakehouse_id}/api/2.1/unity-catalog/tables"
        what = f"list the tables in {schema}"
        return _names(self._get(url, what, catalog_name=lakehouse_id, schema_name=schema), what, "tables")


def _iso(http_date: str | None) -> str:
    """An HTTP date (as the DFS API gives lastModified) as ISO 8601, or "" when there isn't one."""
    if not http_date or not isinstance(http_date, str):
        return ""
    try:
        return email.utils.parsedate_to_datetime(http_date).isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return ""


def item_url(workspace_id: str, lakehouse_id: str, path: str) -> str:
    """The DFS URL of ``path`` in a lakehouse, with the path percent-encoded: a "#" or "?" in a
    name would otherwise end the path there, so a delete of Files/a#b.csv would remove Files/a, and
    a "%" would be read as an escape."""
    return f"https://{DFS_HOST}/{workspace_id}/{lakehouse_id}/{quote(item_path(path), safe='/')}"


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


# How many times an upload step on its new file (create, write, commit) is sent when the connection
# drops (Wi-Fi, a VPN reconnecting, a proxy reset): each names the same new file, at the same position,
# so sending it again changes nothing more. Waits of 2, 4 and 8 s, for a line that comes back.
UPLOAD_STEP_ATTEMPTS = 4
UPLOAD_STEP_WAIT = 2.0


def _dropped(exc: BaseException) -> bool:
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


class UploadDropped(requests.ConnectionError):
    """A file upload stopped by a dropped connection, in words that say what happened to the file."""


def _upload_dropped(path: str, step: str) -> UploadDropped:
    if step == "replace":
        return UploadDropped(f"Uploading {path} to OneLake: the connection dropped just as the new file was being put "
                             f"in place, so {path} may have its old content or its new one. Upload it again once "
                             "you're connected. That replaces the file, and never makes a second copy.")
    return UploadDropped(f"Uploading {path} to OneLake stopped because the connection dropped (Wi-Fi, a VPN or a "
                         f"proxy), even after trying again. {path} wasn't changed. It still has its old content, or "
                         "isn't there if it's new. Upload it again once you're connected.")


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
            what = f"list {directory or 'Files'}"
            for p in _reply(resp, what, "paths"):
                name, size = p.get("name"), str(p.get("contentLength") or 0)
                if not isinstance(name, str) or "/" not in name or not (size.isascii() and size.isdigit()
                                                                         and len(size) <= 19):
                    raise _bad_reply(resp, what)
                path = name.split("/", 1)[1]  # drop the lakehouse GUID
                if UPLOADING.search(path):
                    continue  # an upload's new file before its rename, or one a crash left (EA-007)
                entries.append({"name": path.rsplit("/", 1)[-1], "path": path,
                                "is_dir": str(p.get("isDirectory", "")).lower() == "true",
                                "size": int(size), "modified": _iso(p.get("lastModified"))})
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
        url = item_url(workspace_id, lakehouse_id, path)
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
        url = item_url(workspace_id, lakehouse_id, path)
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
        """Create or replace a file. The bytes go to a new file beside it (create, append, flush: the
        ADLS Gen2 way), which is then renamed over ``path`` in one step. Creating ``path`` itself would
        empty a file that is there before its new bytes arrive, so a failed upload would leave it
        empty (EA-007); this way it keeps its old content until the rename."""
        target = item_path(path)
        folder, _, name = target.rpartition("/")
        temp = f"{folder}/" * bool(folder) + f".{name}.{uuid.uuid4().hex[:12]}{UPLOADING_SUFFIX}"
        url = item_url(workspace_id, lakehouse_id, temp)
        # The source exactly as the create's URL names it, so the rename can't miss the new file.
        rename = {"x-ms-rename-source": urlsplit(url).path}
        steps = [("PUT", url, {"resource": "file"}, None, {}, "create"),
                 ("PATCH", url, {"action": "append", "position": "0"}, data, {}, "write"),
                 ("PATCH", url, {"action": "flush", "position": str(len(data))}, None, {}, "commit"),
                 ("PUT", item_url(workspace_id, lakehouse_id, target), {}, None, rename, "replace")]
        done, step = 0, "create"
        try:
            for method, at, params, body, extra, step in steps:
                if body is not None and not body:
                    continue  # an empty file needs no append
                # Throttling (429) is waited out: OneLake did nothing. Anything else is sent once, since
                # the step may have happened; a failed append is never flushed, so no half file is seen.
                # A dropped connection on the new file is sent again (UPLOAD_STEP_ATTEMPTS); the rename isn't.
                resp = retry_call(
                    lambda: send_with_retry(lambda: self._http.request(method, at, params=params, data=body,  # noqa: B023 - called before the loop moves on
                                                                       headers={**self._headers(), **extra}, timeout=300),  # noqa: B023
                                            method, sleep=self._sleep, safe=False),  # noqa: B023
                    retry_on=_dropped if step != "replace" else lambda exc: False, attempts=UPLOAD_STEP_ATTEMPTS,
                    wait=lambda n: UPLOAD_STEP_WAIT * 2 ** (n - 1), sleep=self._sleep)
                if resp.status_code >= 400:
                    raise _onelake_error(resp, f"{step} {path}")
                done += 1
        except Exception as failed:
            # The new file was created, or may have been when no create's reply came back: remove it by
            # its own name, so only the old file is left (a 404 for one never made is only logged).
            if done or _dropped(failed):
                try:
                    self.delete(workspace_id, lakehouse_id, temp, recursive=False)
                except Exception as exc:  # noqa: BLE001 - the upload's own error is the one to report
                    _log.warning("Couldn't remove %s after a failed upload: %s", temp, redact.describe(exc))
            if _dropped(failed):  # requests' words name a URL and a pool; say what happened to the file
                _log.warning("Upload of %s dropped at its %s step: %s", path, step, redact.describe(failed))
                raise _upload_dropped(path, step) from failed
            raise

    def mkdir(self, workspace_id: str, lakehouse_id: str, path: str) -> None:
        """Create a folder (and any missing parents). One that is already there is left as it is:
        If-None-Match makes OneLake answer 409 instead of touching it."""
        url = item_url(workspace_id, lakehouse_id, path)
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
        url = item_url(workspace_id, lakehouse_id, path)
        # Throttling (429) is waited out; anything else is sent once (HD-053).
        resp = send_with_retry(lambda: self._http.request("DELETE", url,
                                                          params={"recursive": "true" if recursive else "false"},
                                                          headers=self._headers(), timeout=300),
                               "DELETE", sleep=self._sleep, safe=False)
        if resp.status_code >= 400 and resp.status_code != 404:
            raise _onelake_error(resp, f"delete {path}")
