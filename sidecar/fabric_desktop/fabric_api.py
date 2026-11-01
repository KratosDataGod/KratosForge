"""Thin client for the Fabric REST API (workspaces, lakehouses and notebooks)."""

from __future__ import annotations

import base64
import re
import time
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from urllib3.util import parse_url

from . import http_session, redact
from .onelake import error_code
from .paging import Pages, PagingError
from .retrying import retry_after, send_with_retry

API = "https://api.fabric.microsoft.com/v1"
POWERBI_ADMIN = "https://api.powerbi.com/v1.0/myorg/admin"


# Fabric's rule for lakehouse names: letters, numbers and underscores, starting with a letter.
_LAKEHOUSE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,255}")


def lakehouse_name_problem(name: str) -> str:
    """Why Fabric would refuse this lakehouse name, or "" if it's fine."""
    if _LAKEHOUSE_NAME.fullmatch(name):
        return ""
    if len(name) > 256:
        return "A lakehouse name can be at most 256 characters."
    suggestion = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not suggestion[:1].isalpha():
        suggestion = f"LH_{suggestion}"
    return (f"Fabric doesn't accept the lakehouse name {name!r}: use only letters, numbers and underscores, "
            f"starting with a letter. For example {suggestion}.")


def _notebook_definition(ipynb: bytes) -> dict[str, Any]:
    return {"format": "ipynb", "parts": [{"path": "notebook-content.ipynb",
                                          "payload": base64.b64encode(ipynb).decode(), "payloadType": "InlineBase64"}]}


class FabricError(RuntimeError):
    def __init__(self, message: str, code: str | None = None, status: int | None = None, request_id: str = ""):
        super().__init__(message)
        self.code = code
        self.status = status  # the HTTP status, when the error came from a response
        self.request_id = request_id  # Fabric's request id, for support


# What a person can do about the errors Fabric gives most often (by error code, then by status).
_HINTS = {
    "InsufficientPrivileges": "Your account doesn't have permission for this in Fabric. Ask a workspace admin for access",
    "ItemDisplayNameAlreadyInUse": "Pick a name that isn't used in this workspace yet",
    "TooManyRequests": "Fabric is limiting requests. Try again in a few minutes",
    "UnknownError": "Fabric had a problem on its side. Try again, and give the request id to support if it keeps happening",
    401: "Fabric didn't accept your sign-in. Sign out and in again",
    403: "Your account doesn't have permission for this in Fabric. Ask a workspace admin for access",
    429: "Fabric is limiting requests. Try again in a few minutes",
    500: "Fabric had a problem on its side. Try again, and give the request id to support if it keeps happening",
}


_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_PART_MAX = 300  # characters of one message part Fabric sent


def _part(value: Any) -> str:
    """One message part from Fabric as part of one line: credentials removed, control characters and
    newlines folded to spaces, cut to _PART_MAX, without a trailing full stop (the caller adds one)."""
    return redact.one_line(value or "", _PART_MAX).rstrip(".")


def _request_id(value: Any) -> str:
    text = str(value or "")
    return text if _REQUEST_ID.fullmatch(text) else ""


def _error_body(resp: requests.Response) -> dict[str, Any]:
    """Fabric's error fields, from its own shape ({"errorCode", "message", ...}) or Power BI's ({"error": {...}})."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    raw = body.get("error")
    inner: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return {"code": body.get("errorCode") or inner.get("code") or inner.get("errorCode"),
            "message": body.get("message") or inner.get("message"),
            "details": body.get("moreDetails") or inner.get("details") or [],
            "request_id": body.get("requestId") or inner.get("requestId")}


def _error(method: str, url: str, resp: requests.Response) -> FabricError:
    """A Fabric error a person can read: what was asked (the path, without its query), the status and
    Fabric's code, Fabric's own message, what to do, and the request id. Never the raw body."""
    fields = _error_body(resp)
    code = error_code(fields.get("code")) or None
    headers = getattr(resp, "headers", None) or {}
    request_id = _request_id(fields.get("request_id") or headers.get("x-ms-request-id") or headers.get("RequestId"))
    status = resp.status_code
    if code == "CapacityNotActive":
        return FabricError("This workspace's Fabric capacity is paused or inactive. Resume the capacity, "
                           "or use a workspace on an active capacity.", code, status, request_id)
    path = url.split("?", 1)[0].split("#", 1)[0]
    path = path[len(API):] if path.startswith(API) else path
    parts = [_part(fields.get("message"))]
    parts += [_part(d.get("message")) for d in fields.get("details") or []
              if isinstance(d, dict) and d.get("message")][:3]
    hint = _HINTS.get(code or "") or _HINTS.get(status) or (_HINTS[500] if status >= 500 else "")
    parts = [p for p in parts if p] + ([hint] if hint else [])
    text = f"{method} {path} failed with {status}" + (f" {code}" if code else "")
    text += (": " + ". ".join(parts) + "." if parts else ".") + (f" Request id {request_id}." if request_id else "")
    return FabricError(text, code, status, request_id)


def _operation_failed(operation: str, state: dict[str, Any]) -> FabricError:
    """A failed long-running operation, in Fabric's words: its error code and message, never the raw body."""
    raw = state.get("error")
    error: dict[str, Any] = raw if isinstance(raw, dict) else {}
    code = error_code(error.get("errorCode")) or None
    message = _part(error.get("message"))
    raw_more = error.get("moreDetails")
    more: list[Any] = raw_more if isinstance(raw_more, list) else []
    details = [_part(d.get("message")) for d in more if isinstance(d, dict) and d.get("message")]
    if code == "CapacityNotActive":
        message = ("This workspace's Fabric capacity is paused or inactive. Resume the capacity, "
                   "or use a workspace on an active capacity")
    text = f"Fabric's operation {operation} {'failed' if state.get('status') == 'Failed' else 'ended in an unknown state'}"
    text += f": {message}" if message else " and gave no reason"
    text += "".join(f". {d}" for d in details[:3]) + (f" ({code})" if code else "")
    request_id = _request_id(error.get("requestId"))
    return FabricError(text + (f". Request id {request_id}." if request_id else "."), code)


# Hosts a URL Fabric hands back (continuationUri, an operation's Location) may name before the
# token goes with it: Fabric, OneLake, Power BI, and Power BI's regional wabi-* hosts. Only the
# public cloud: every base URL in this app is a public-cloud one.
FOLLOW_DOMAINS = ("fabric.microsoft.com", "powerbi.com", "analysis.windows.net")
UNTRUSTED_URL = "UntrustedUrl"


def _sent_to(url: str) -> Any:
    """The URL as requests will send it, parsed the way urllib3 will connect to it. Checking the
    standard library's reading instead lets "https://evil.example\\.fabric.microsoft.com/" pass as a
    Fabric host while requests connects to evil.example."""
    return parse_url(requests.Request("GET", url).prepare().url)


def followable_url(url: str, came_from: str) -> str:
    """``url`` (relative or absolute) resolved against the request it came from, if it's https on
    that request's host or under FOLLOW_DOMAINS. Anything else raises, so the token isn't sent.
    Returns the URL exactly as it will be sent, so the host checked is the host contacted."""
    unreadable = FabricError("Fabric pointed to a page with an address Fabric Desktop by KratosBI can't check; it "
                             "didn't follow it, so your sign-in stays private.", UNTRUSTED_URL)
    try:
        full = urljoin(came_from, url)
        if "\\" in full or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in full):
            raise unreadable
        parts, origin = _sent_to(full), _sent_to(came_from)
    except (requests.RequestException, ValueError) as exc:
        raise unreadable from exc
    host = (parts.host or "").lower()
    same = host == (origin.host or "").lower()
    known = any(host == d or host.endswith(f".{d}") for d in FOLLOW_DOMAINS)
    if parts.scheme != "https" or parts.auth:
        raise FabricError(f"Fabric pointed to a page without a secure connection ({host or full}); Fabric Desktop "
                          "by KratosBI didn't follow it, so your sign-in stays private.", UNTRUSTED_URL)
    if not host or not (same or known):
        raise FabricError(f"Fabric pointed to a page on another site ({host or full}); Fabric Desktop by KratosBI "
                          "didn't follow it, so your sign-in stays private.", UNTRUSTED_URL)
    return parts.url


POLICY_BLOCKED = "PolicyBlocked"


def _check_fabric_runs_allowed(what: str) -> None:
    """The organization's policy can keep work off Fabric capacity (allow_fabric_runs: false)."""
    from . import app_settings
    if not app_settings.value("allow_fabric_runs"):
        raise FabricError(f"Your organization doesn't allow {what} from KratosForge, so it wasn't "
                          "started. Run it on this PC instead, or ask your IT team.", POLICY_BLOCKED)


class FabricClient:
    def __init__(self, token_provider: Callable[[], str], session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, poll_timeout: float = 300,
                 refresh: Callable[[], str] | None = None):
        self._token = token_provider
        # A new token after a 401 (revoked, or expired early); without one, asking the provider again.
        self._refresh = refresh or token_provider
        self._http = session or http_session.session()
        self._sleep = sleep
        self._poll_timeout = poll_timeout

    def _request(self, method: str, url: str, headers: dict[str, str] | None = None,
                 **kwargs) -> requests.Response:
        if not url.startswith("http"):
            url = f"{API}{url}"
        token = self._token()

        def send(token: str) -> requests.Response:
            sent = {**(headers or {}), "Authorization": f"Bearer {token}"}
            return send_with_retry(lambda: self._http.request(method, url, headers=sent, timeout=60, **kwargs),
                                   method, sleep=self._sleep, attempts=5, default_wait=5)

        resp = send(token)
        if resp.status_code == 401:
            # Fabric refused the token before reading the request, so even a write is safe to send
            # again: once, and only with a token that is actually new.
            fresh = self._refresh()
            if fresh != token:
                close = getattr(resp, "close", None)
                if callable(close):
                    close()
                resp = send(fresh)
        if resp.status_code >= 400:
            raise _error(method, url, resp)
        return resp

    def _paged(self, url: str, key: str = "value") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        url = url if url.startswith("http") else f"{API}{url}"
        pages = Pages(url.split("?", 1)[0][len(API):] or "Fabric", start=url)
        while url:
            body = self._request("GET", url).json()
            items.extend(body.get(key, []))
            nxt = body.get("continuationUri")
            try:
                url = pages.follow(followable_url(nxt, url)) if nxt and nxt != "null" else ""  # some APIs send the text "null"
            except PagingError as exc:  # a FabricError, so callers that report Fabric errors report this one
                raise FabricError(str(exc), "PagingStopped") from None
        return items

    def _wait(self, resp: requests.Response) -> dict[str, Any]:
        """Follow a 202 long-running operation to its result. Once it has succeeded, the state's
        ``Location`` names the result; an operation that has none (Fabric says some just finish)
        returns {}. A failure raises with Fabric's own code and message, not the raw response."""
        if resp.status_code != 202:
            return resp.json() if resp.content else {}
        came_from = getattr(resp, "url", None)  # the request's own URL; Fabric's operations start at API
        location = followable_url(resp.headers["Location"], came_from if isinstance(came_from, str) and came_from else API)
        operation = resp.headers.get("x-ms-operation-id") or location.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        deadline = time.monotonic() + self._poll_timeout
        while True:
            self._sleep(retry_after(resp.headers, 2))
            resp = self._request("GET", location)
            state = resp.json() if resp.content else {}
            status = state.get("status")
            if status == "Succeeded":
                return self._operation_result(location, resp.headers.get("Location"))
            if status in ("Failed", "Undefined"):
                raise _operation_failed(operation, state)
            if time.monotonic() > deadline:
                raise FabricError(f"Fabric's operation {operation} didn't finish within {self._poll_timeout:g} seconds "
                                  f"(last status: {status or 'none'}). It may still finish in Fabric.", "OperationTimedOut")

    def _operation_result(self, location: str, result_location: str | None) -> dict[str, Any]:
        if result_location:
            url = followable_url(result_location, location)
            if url.rstrip("/") != location.rstrip("/"):
                result = self._request("GET", url)
                return result.json() if result.content else {}
        try:  # no result link: build it from the operation, as Fabric's docs allow
            result = self._request("GET", f"{location.rstrip('/')}/result")
        except FabricError as exc:
            if exc.status in (400, 404):
                return {}  # it succeeded and has no result
            raise
        return result.json() if result.content else {}

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self._paged("/workspaces")

    def list_capacities(self) -> list[dict[str, Any]]:
        """Capacities the signed-in user can assign workspaces to."""
        return self._paged("/capacities")

    def create_workspace(self, name: str, capacity_id: str = "", description: str = "") -> dict[str, Any]:
        """Create a workspace as the signed-in user, on a capacity so it can hold lakehouses."""
        body: dict[str, Any] = {"displayName": name}
        if capacity_id:
            body["capacityId"] = capacity_id
        if description:
            body["description"] = description
        try:
            return self._request("POST", "/workspaces", json=body).json()
        except FabricError as exc:
            if exc.code == "WorkspaceNameAlreadyExists":
                raise FabricError(f"A workspace named {name} already exists. Pick it from the list, "
                                  f"or use another name.", exc.code) from exc
            raise

    def assign_to_capacity(self, workspace_id: str, capacity_id: str) -> None:
        """Put a workspace on a capacity. Needs the workspace's Admin role and Contributor on the capacity;
        the same permission (Workspace.ReadWrite.All) as creating it."""
        self._request("POST", f"/workspaces/{workspace_id}/assignToCapacity", json={"capacityId": capacity_id})

    def list_lakehouses(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/lakehouses")

    def list_items(self, workspace_id: str) -> list[dict[str, Any]]:
        """Every item in the workspace: lakehouses, warehouses, notebooks, semantic models, ..."""
        return self._paged(f"/workspaces/{workspace_id}/items")

    def list_tables(self, workspace_id: str, lakehouse_id: str) -> list[dict[str, Any]]:
        """A lakehouse's tables, as Fabric lists them (lakehouses with schemas aren't listed by this API)."""
        return self._paged(f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables", key="data")

    def get_lakehouse(self, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
        return self._request("GET", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}").json()

    def create_lakehouse(self, workspace_id: str, name: str, description: str = "") -> dict[str, Any]:
        """Create a schema-enabled lakehouse. Always returns it with its ``id``: when the operation gives
        no result, the lakehouse is looked up by name."""
        problem = lakehouse_name_problem(name)
        if problem:
            raise ValueError(problem)
        body = {"displayName": name, "creationPayload": {"enableSchemas": True}}
        if description:
            body["description"] = description
        made = self._wait(self._request("POST", f"/workspaces/{workspace_id}/lakehouses", json=body))
        if made.get("id"):
            return made
        found = self.find_lakehouse(workspace_id, name)
        if found and found.get("id"):
            return found
        raise FabricError(f"Fabric said it created the lakehouse {name}, but it isn't in the workspace yet. "
                          "Refresh the workspace in a minute, then try again.", "LakehouseNotFoundAfterCreate")

    def environment_libraries(self, workspace_id: str, environment_id: str, staged: bool = False) -> list[dict[str, Any]]:
        """The libraries published to an Environment item (or, with ``staged``, the ones saved but not
        yet published): [{"name", "libraryType": External|Custom, "version"}]. Read only (``beta=False``
        is the GA contract; the preview one has another shape). An empty list answers 404
        EnvironmentLibrariesNotFound, which is an empty list, not an error."""
        where = "staging/libraries" if staged else "libraries"
        try:
            return self._paged(f"/workspaces/{workspace_id}/environments/{environment_id}/{where}?beta=False", key="libraries")
        except FabricError as exc:
            if exc.code == "EnvironmentLibrariesNotFound":
                return []
            raise

    def environment_spark_compute(self, workspace_id: str, environment_id: str, staged: bool = False) -> dict[str, Any]:
        """The Environment item's published (or, with ``staged``, unpublished) Spark settings, with
        ``runtimeVersion``. Read only."""
        where = "staging/sparkcompute" if staged else "sparkcompute"
        return self._request("GET", f"/workspaces/{workspace_id}/environments/{environment_id}/{where}?beta=False").json()

    def delete_lakehouse(self, workspace_id: str, lakehouse_id: str) -> None:
        """Delete a lakehouse with its tables and files (shortcut targets stay). Only the live test
        harness calls this, for lakehouses it created itself."""
        self._request("DELETE", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}")

    def delete_item(self, workspace_id: str, item_id: str) -> None:
        """Delete an item (a notebook or pipeline). Only the live test harness calls this, for
        items it created itself and recorded in its ledger."""
        self._request("DELETE", f"/workspaces/{workspace_id}/items/{item_id}")

    def get_warehouse(self, workspace_id: str, warehouse_id: str) -> dict[str, Any]:
        """A warehouse, with its SQL connection string (``properties.connectionString``)."""
        return self._request("GET", f"/workspaces/{workspace_id}/warehouses/{warehouse_id}").json()

    def list_kql_databases(self, workspace_id: str) -> list[dict[str, Any]]:
        """A workspace's KQL databases, each with ``properties.queryServiceUri`` (WH-19)."""
        return self._paged(f"/workspaces/{workspace_id}/kqlDatabases")

    def get_kql_database(self, workspace_id: str, database_id: str) -> dict[str, Any]:
        return self._request("GET", f"/workspaces/{workspace_id}/kqlDatabases/{database_id}").json()

    def list_kql_querysets(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/kqlQuerysets")

    def create_kql_queryset(self, workspace_id: str, name: str, definition: dict[str, Any],
                            description: str = "") -> dict[str, Any]:
        """Create a KQL queryset from its definition (RealTimeQueryset.json)."""
        body: dict[str, Any] = {"displayName": name, "definition": definition}
        if description:
            body["description"] = description
        return self._wait(self._request("POST", f"/workspaces/{workspace_id}/kqlQuerysets", json=body))

    def update_item_definition(self, workspace_id: str, item_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        """Replace an item's definition (all its parts)."""
        return self._wait(self._request("POST", f"/workspaces/{workspace_id}/items/{item_id}/updateDefinition",
                                        json={"definition": definition}))

    def find_lakehouse(self, workspace_id: str, name: str) -> dict[str, Any] | None:
        return next((lh for lh in self.list_lakehouses(workspace_id) if lh["displayName"] == name), None)

    def get_item_definition(self, workspace_id: str, item_id: str, fmt: str | None = None) -> dict[str, Any]:
        """An item's definition parts (base64), e.g. a notebook as ipynb. Needs Item.ReadWrite.All, which sign-in already asks for."""
        query = f"?format={fmt}" if fmt else ""
        got = self._wait(self._request("POST", f"/workspaces/{workspace_id}/items/{item_id}/getDefinition{query}"))
        if not isinstance(got.get("definition"), dict):
            raise FabricError("Fabric didn't send the item's definition. Try again; if it keeps happening, "
                              "open the item in Fabric to check it still loads.", "NoDefinition")
        return got

    def list_notebooks(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/notebooks")

    def save_notebook(self, workspace_id: str, name: str, ipynb: bytes) -> dict[str, Any]:
        """Create the notebook item, or replace its definition if one with this name exists."""
        existing = next((n for n in self.list_notebooks(workspace_id) if n["displayName"] == name), None)
        if existing is None:
            body = {"displayName": name, "definition": _notebook_definition(ipynb)}
            return self._wait(self._request("POST", f"/workspaces/{workspace_id}/notebooks", json=body))
        return self.update_notebook(workspace_id, existing, name, ipynb)

    def get_notebook(self, workspace_id: str, item_id: str) -> dict[str, Any] | None:
        """The notebook item ``item_id`` in that workspace, or None when it isn't there (deleted)."""
        try:
            item = self._request("GET", f"/workspaces/{workspace_id}/notebooks/{item_id}").json()
        except FabricError as exc:
            if exc.status == 404:
                return None
            raise
        return item if item.get("workspaceId", workspace_id) == workspace_id else None

    def update_notebook(self, workspace_id: str, item: dict[str, Any], name: str, ipynb: bytes) -> dict[str, Any]:
        """Replace ``item``'s definition, renaming it to ``name`` first when that isn't its name."""
        if item.get("displayName") != name:
            self._request("PATCH", f"/workspaces/{workspace_id}/notebooks/{item['id']}", json={"displayName": name})
            item = item | {"displayName": name}
        url = f"/workspaces/{workspace_id}/notebooks/{item['id']}/updateDefinition"
        self._wait(self._request("POST", url, json={"definition": _notebook_definition(ipynb)}))
        return item

    # ---- Fabric's job scheduler: run an item on Fabric capacity, now or on a schedule

    # Items Fabric runs through their own "execute" job, at a path of their own type (the job type is
    # in the path, not the jobType query): copy jobs and Dataflow Gen2.
    EXECUTE_PATHS = {"CopyJob": "copyJobs", "Dataflow": "dataflows"}

    def run_item_execute(self, workspace_id: str, item_id: str, item_type: str) -> str:
        """Start a copy job or a Dataflow Gen2 refresh and return the job instance id."""
        _check_fabric_runs_allowed("starting jobs in Fabric")
        path = self.EXECUTE_PATHS[item_type]
        resp = self._request("POST", f"/workspaces/{workspace_id}/{path}/{item_id}/jobs/execute/instances")
        location = resp.headers.get("Location", "")
        if not location:
            raise FabricError(f"Fabric accepted the {item_type} run but returned no job instance")
        return location.rstrip("/").rsplit("/", 1)[-1]

    def run_item_job(self, workspace_id: str, item_id: str, job_type: str,
                     execution_data: dict[str, Any] | None = None, item_path: str = "items") -> str:
        """Start a job instance and return its id (the last part of the Location header)."""
        _check_fabric_runs_allowed("starting jobs in Fabric")
        body = {"executionData": execution_data} if execution_data else None
        resp = self._request("POST", f"/workspaces/{workspace_id}/{item_path}/{item_id}/jobs/instances",
                             params={"jobType": job_type}, json=body)
        location = resp.headers.get("Location", "")
        if not location:
            raise FabricError(f"Fabric accepted the {job_type} job but returned no job instance")
        return location.rstrip("/").rsplit("/", 1)[-1]

    def job_instance(self, workspace_id: str, item_id: str, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/workspaces/{workspace_id}/items/{item_id}/jobs/instances/{job_id}").json()

    def cancel_job(self, workspace_id: str, item_id: str, job_id: str) -> None:
        self._request("POST", f"/workspaces/{workspace_id}/items/{item_id}/jobs/instances/{job_id}/cancel")

    def list_schedules(self, workspace_id: str, item_id: str, job_type: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/items/{item_id}/jobs/{job_type}/schedules")

    def create_schedule(self, workspace_id: str, item_id: str, job_type: str,
                        configuration: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
        if enabled:
            _check_fabric_runs_allowed("scheduling jobs in Fabric")
        url = f"/workspaces/{workspace_id}/items/{item_id}/jobs/{job_type}/schedules"
        return self._request("POST", url, json={"enabled": enabled, "configuration": configuration}).json()

    def update_schedule(self, workspace_id: str, item_id: str, job_type: str, schedule_id: str,
                        enabled: bool, configuration: dict[str, Any]) -> dict[str, Any]:
        if enabled:  # turning a schedule off is always allowed
            _check_fabric_runs_allowed("scheduling jobs in Fabric")
        url = f"/workspaces/{workspace_id}/items/{item_id}/jobs/{job_type}/schedules/{schedule_id}"
        return self._request("PATCH", url, json={"enabled": enabled, "configuration": configuration}).json()

    def list_job_instances(self, workspace_id: str, item_id: str) -> list[dict[str, Any]]:
        """The item's job runs, newest first (Fabric keeps them for a while)."""
        runs = self._paged(f"/workspaces/{workspace_id}/items/{item_id}/jobs/instances")
        return sorted(runs, key=lambda r: r.get("startTimeUtc") or "", reverse=True)

    def query_activity_runs(self, workspace_id: str, job_id: str, updated_after: str,
                            updated_before: str) -> list[dict[str, Any]]:
        """The activity runs of one pipeline run (its job instance id), newest first. A query, not a job:
        it reads run history and uses no capacity. Fabric needs the time window."""
        body = {"filters": [], "orderBy": [{"orderBy": "ActivityRunStart", "order": "DESC"}],
                "lastUpdatedAfter": updated_after, "lastUpdatedBefore": updated_before}
        data = self._request("POST", f"/workspaces/{workspace_id}/datapipelines/pipelineruns/{job_id}/queryactivityruns",
                             json=body).json()
        return data if isinstance(data, list) else (data or {}).get("value", [])

    def delete_schedule(self, workspace_id: str, item_id: str, job_type: str, schedule_id: str) -> None:
        self._request("DELETE", f"/workspaces/{workspace_id}/items/{item_id}/jobs/{job_type}/schedules/{schedule_id}")

    def run_table_maintenance(self, workspace_id: str, lakehouse_id: str, table: str, schema: str | None = None,
                              v_order: bool = True, z_order_by: list[str] | None = None,
                              vacuum_retention: str | None = "7.01:00:00", optimize: bool = True) -> str:
        """OPTIMIZE (with V-Order) and VACUUM one table on Fabric capacity. Returns the job id.

        ``optimize=False`` runs VACUUM alone; ``vacuum_retention=None`` runs OPTIMIZE alone."""
        data: dict[str, Any] = {"tableName": table}
        if optimize:
            data["optimizeSettings"] = {"vOrder": "true" if v_order else "false"}
        if schema:
            data["schemaName"] = schema
        if z_order_by and optimize:
            data["optimizeSettings"]["zOrderBy"] = list(z_order_by)
        if vacuum_retention:
            data["vacuumSettings"] = {"retentionPeriod": vacuum_retention}
        return self.run_item_job(workspace_id, lakehouse_id, "TableMaintenance", data, item_path="lakehouses")

    # ---- semantic models and the SQL analytics endpoint

    def list_semantic_models(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/semanticModels")

    def save_semantic_model(self, workspace_id: str, name: str, parts: dict[str, str],
                            description: str = "", replace: bool = True) -> dict[str, Any]:
        """Create the semantic model from TMDL files, or replace the definition of the one with this name.
        Without ``replace``, an existing one raises FileExistsError(message, its id) and nothing is written.
        A failed list raises too, so a model that couldn't be looked up is never treated as new."""
        definition = {"parts": [{"path": path, "payload": base64.b64encode(text.encode()).decode(),
                                 "payloadType": "InlineBase64"} for path, text in parts.items()]}
        existing = next((m for m in self.list_semantic_models(workspace_id) if m["displayName"] == name), None)
        if existing is not None and not replace:
            raise FileExistsError(f"{name} already exists; replacing its definition needs confirming", existing["id"])
        if existing is None:
            body: dict[str, Any] = {"displayName": name, "definition": definition}
            if description:
                body["description"] = description
            created = self._wait(self._request("POST", f"/workspaces/{workspace_id}/semanticModels", json=body))
            return created | {"created": True}
        url = f"/workspaces/{workspace_id}/semanticModels/{existing['id']}/updateDefinition"
        self._wait(self._request("POST", url, json={"definition": definition}))
        return existing | {"created": False}

    # ---- any item type (SQL databases, User Data Functions)

    def find_item(self, workspace_id: str, item_type: str, name: str) -> dict[str, Any] | None:
        return next((i for i in self.list_items(workspace_id)
                     if i.get("type") == item_type and i.get("displayName") == name), None)

    def save_item(self, workspace_id: str, item_type: str, name: str, parts: dict[str, str] | None = None,
                  description: str = "") -> dict[str, Any]:
        """Create an item of this type, or replace the definition of the one with this name.
        Without parts, an existing item is left as it is."""
        definition = {"parts": [{"path": path, "payload": base64.b64encode(text.encode()).decode(),
                                 "payloadType": "InlineBase64"} for path, text in parts.items()]} if parts else None
        existing = self.find_item(workspace_id, item_type, name)
        if existing is None:
            body: dict[str, Any] = {"displayName": name, "type": item_type}
            if definition:
                body["definition"] = definition
            if description:
                body["description"] = description
            created = self._wait(self._request("POST", f"/workspaces/{workspace_id}/items", json=body))
            if not created.get("id"):
                created = self.find_item(workspace_id, item_type, name) or created
            return created | {"created": True}
        if definition:
            url = f"/workspaces/{workspace_id}/items/{existing['id']}/updateDefinition"
            self._wait(self._request("POST", url, json={"definition": definition}))
        return existing | {"created": False}

    def refresh_sql_endpoint(self, workspace_id: str, sql_endpoint_id: str) -> dict[str, Any]:
        """Ask the lakehouse's SQL analytics endpoint to pick up tables written from outside Fabric."""
        url = f"/workspaces/{workspace_id}/sqlEndpoints/{sql_endpoint_id}/refreshMetadata"
        return self._wait(self._request("POST", url, json={}))

    # ---- Power BI's admin tenant scan (endorsement)

    SCAN_BATCH = 100  # workspaces per getInfo call: Power BI's limit

    def admin_scan_workspaces(self, workspace_ids: list[str], poll_seconds: float = 2) -> list[dict[str, Any]]:
        """Power BI's admin tenant scan (metadata scanning) of these workspaces: each workspace with its
        items (datasets, reports, dataflows, datamarts and Fabric items), which carry endorsementDetails
        when endorsed. Fabric administrators only (Tenant.Read.All on Power BI); anyone else gets 401/403.
        Read-only: asks for no lineage, data sources, schemas, expressions or users."""
        ids = [w for w in dict.fromkeys(str(w).strip() for w in workspace_ids) if w]
        found: list[dict[str, Any]] = []
        polls = max(1, int(self._poll_timeout / poll_seconds))
        for i in range(0, len(ids), self.SCAN_BATCH):
            started = self._request("POST", f"{POWERBI_ADMIN}/workspaces/getInfo", json={"workspaces": ids[i:i + self.SCAN_BATCH]},
                                    params={"lineage": "false", "datasourceDetails": "false", "datasetSchema": "false",
                                            "datasetExpressions": "false", "getArtifactUsers": "false"}).json()
            scan = str(started.get("id") or "")
            if not scan:
                raise FabricError("Fabric's tenant scan didn't start: no scan id came back")
            for n in range(polls):
                status = self._request("GET", f"{POWERBI_ADMIN}/workspaces/scanStatus/{scan}").json()
                state = str(status.get("status") or "")
                if state == "Succeeded":
                    break
                if state == "Failed":
                    raise FabricError(f"Fabric's tenant scan failed: {_part(status.get('error') or 'no reason given')}")
                if n == polls - 1:
                    raise FabricError("Fabric's tenant scan didn't finish in time. Scan again in a few minutes.")
                self._sleep(poll_seconds)
            found += self._request("GET", f"{POWERBI_ADMIN}/workspaces/scanResult/{scan}").json().get("workspaces", [])
        return found

    # ---- domains

    def list_admin_domains(self) -> list[dict[str, Any]]:
        """Fabric's domains and subdomains: [{id, displayName, description, parentDomainId}].
        Fabric administrators only (Tenant.Read.All); anyone else gets 401/403."""
        body = self._request("GET", "/admin/domains", params={"preview": "false"}).json()
        return body.get("domains", body.get("value", []))

    def create_admin_domain(self, name: str, description: str = "", parent_id: str = "") -> dict[str, Any]:
        """Create a domain, or a subdomain of ``parent_id``, in Fabric: {id, displayName, description,
        parentDomainId}. Fabric administrators only (Tenant.ReadWrite.All); anyone else gets 401/403."""
        body: dict[str, Any] = {"displayName": name}
        if description:
            body["description"] = description
        if parent_id:
            body["parentDomainId"] = parent_id
        return self._request("POST", "/admin/domains", params={"preview": "false"}, json=body).json()

    def list_admin_domain_role_assignments(self, domain_id: str) -> list[dict[str, Any]]:
        """A domain's role assignments (Admins, Contributors): [{id, principal: {id, displayName,
        type}, role}]. Fabric admin API, Tenant.Read.All."""
        return self._paged(f"/admin/domains/{domain_id}/roleAssignments")

    def list_tenant_settings(self) -> list[dict[str, Any]]:
        """Fabric's tenant settings: [{settingName, title, enabled, tenantSettingGroup, ...}]. Fabric
        administrators only (Tenant.Read.All); anyone else gets 401/403. Documented as paged
        (continuationUri) and limited to 25 calls a minute."""
        return self._paged("/admin/tenantsettings")

    def list_capacity_tenant_setting_overrides(self) -> list[dict[str, Any]]:
        """Tenant settings each capacity overrides: [{id (capacity id), tenantSettings: [...]}].
        Fabric administrators only (Tenant.Read.All); documented as paged (continuationUri)."""
        return self._paged("/admin/capacities/delegatedTenantSettingOverrides")

    # ---- workspaces: who has access

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        return self._request("GET", f"/workspaces/{workspace_id}").json()

    def admin_get_workspace(self, workspace_id: str) -> dict[str, Any]:
        """A workspace as Fabric's admin API sees it, member or not: {id, name, state, ...}, where state
        is Active, Deleted or Removing. Fabric administrators only (Tenant.Read.All)."""
        return self._request("GET", f"/admin/workspaces/{workspace_id}").json()

    def list_role_assignments(self, workspace_id: str) -> list[dict[str, Any]]:
        """Workspace roles: [{id, principal: {id, displayName, type, userDetails...}, role}]."""
        return self._paged(f"/workspaces/{workspace_id}/roleAssignments")

    def add_role_assignment(self, workspace_id: str, principal_id: str, principal_type: str, role: str) -> dict[str, Any]:
        body = {"principal": {"id": principal_id, "type": principal_type}, "role": role}
        return self._request("POST", f"/workspaces/{workspace_id}/roleAssignments", json=body).json()

    def update_role_assignment(self, workspace_id: str, assignment_id: str, role: str) -> dict[str, Any]:
        url = f"/workspaces/{workspace_id}/roleAssignments/{assignment_id}"
        resp = self._request("PATCH", url, json={"role": role})
        return resp.json() if resp.content else {}

    def delete_role_assignment(self, workspace_id: str, assignment_id: str) -> None:
        self._request("DELETE", f"/workspaces/{workspace_id}/roleAssignments/{assignment_id}")

    # ---- OneLake security: data access roles on a lakehouse

    def list_data_access_roles(self, workspace_id: str, item_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/items/{item_id}/dataAccessRoles")

    def list_data_access_roles_tagged(self, workspace_id: str, item_id: str) -> tuple[list[dict[str, Any]], str | None]:
        """The roles and the ETag Fabric gave the list, for put_data_access_roles(etag=) to refuse
        the write if someone changed the roles in between."""
        url = f"{API}/workspaces/{workspace_id}/items/{item_id}/dataAccessRoles"
        items: list[dict[str, Any]] = []
        etag = None
        pages = Pages(f"the data access roles of item {item_id}", start=url)
        while url:
            resp = self._request("GET", url)
            etag = etag or resp.headers.get("ETag")
            body = resp.json()
            items.extend(body.get("value", []))
            nxt = body.get("continuationUri")
            try:  # HD-058, as _paged; some APIs send the text "null"
                url = pages.follow(followable_url(nxt, url)) if nxt and nxt != "null" else ""
            except PagingError as exc:
                raise FabricError(str(exc), "PagingStopped") from None
        return items, etag

    def put_data_access_roles(self, workspace_id: str, item_id: str, roles: list[dict[str, Any]],
                              etag: str | None = None) -> None:
        """Replace every data access role on the item with ``roles``. With ``etag`` Fabric refuses
        (PreconditionFailed) if the roles changed since that list was read."""
        self._request("PUT", f"/workspaces/{workspace_id}/items/{item_id}/dataAccessRoles", json={"value": roles},
                      headers={"If-Match": etag} if etag else None)

    # ---- OneLake shortcuts

    def list_shortcuts(self, workspace_id: str, item_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/items/{item_id}/shortcuts")

    def create_shortcut(self, workspace_id: str, item_id: str, path: str, name: str,
                        target: dict[str, Any]) -> dict[str, Any]:
        """``path`` is where it goes in the lakehouse ("Tables/dbo" or "Files/..."); ``target`` is
        {"oneLake": {...}}, {"adlsGen2": {...}} or {"amazonS3": {...}}."""
        body = {"path": path, "name": name, "target": target}
        return self._request("POST", f"/workspaces/{workspace_id}/items/{item_id}/shortcuts",
                             params={"shortcutConflictPolicy": "Abort"}, json=body).json()

    def delete_shortcut(self, workspace_id: str, item_id: str, path: str, name: str) -> None:
        self._request("DELETE", f"/workspaces/{workspace_id}/items/{item_id}/shortcuts/{path.strip('/')}/{name}")

    def list_connections(self) -> list[dict[str, Any]]:
        """Cloud connections the user can use (ADLS Gen2 and S3 shortcuts need one)."""
        return self._paged("/connections")

    def list_gateways(self) -> list[dict[str, Any]]:
        """On-premises and virtual network gateways the user can see. Needs Gateway.Read.All."""
        return self._paged("/gateways")

    # ---- variable libraries

    def list_variable_libraries(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/variableLibraries")

    def save_variable_library(self, workspace_id: str, name: str, parts: dict[str, str],
                              description: str = "") -> dict[str, Any]:
        """Create the variable library from its definition files, or replace the one with this name."""
        definition = {"parts": [{"path": path, "payload": base64.b64encode(text.encode()).decode(),
                                 "payloadType": "InlineBase64"} for path, text in parts.items()]}
        existing = next((v for v in self.list_variable_libraries(workspace_id) if v["displayName"] == name), None)
        if existing is None:
            body: dict[str, Any] = {"displayName": name, "definition": definition}
            if description:
                body["description"] = description
            created = self._wait(self._request("POST", f"/workspaces/{workspace_id}/variableLibraries", json=body))
            if not created.get("id"):
                created = next((v for v in self.list_variable_libraries(workspace_id) if v["displayName"] == name), created)
            return created | {"created": True}
        url = f"/workspaces/{workspace_id}/variableLibraries/{existing['id']}/updateDefinition"
        self._wait(self._request("POST", url, json={"definition": definition}))
        return existing | {"created": False}

    def read_variable_library(self, workspace_id: str, name: str) -> tuple[dict[str, str], str | None] | None:
        """The library's definition files (decoded) and active value set, or None if the workspace
        has no library with this name. Read only: getDefinition is a POST but changes nothing."""
        item = next((v for v in self.list_variable_libraries(workspace_id) if v["displayName"] == name), None)
        if item is None:
            return None
        got = self.get_item_definition(workspace_id, item["id"])
        parts = {p["path"]: base64.b64decode(p["payload"]).decode("utf-8-sig")
                 for p in (got.get("definition") or {}).get("parts", [])}
        return parts, (item.get("properties") or {}).get("activeValueSetName")

    def set_active_value_set(self, workspace_id: str, library_id: str, value_set: str) -> None:
        self._request("PATCH", f"/workspaces/{workspace_id}/variableLibraries/{library_id}",
                      json={"properties": {"activeValueSetName": value_set}})
