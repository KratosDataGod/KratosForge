"""Thin client for the Fabric REST API (workspaces, lakehouses and notebooks)."""

from __future__ import annotations

import base64
import re
import time
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from urllib3.util import parse_url

from . import http_session
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


class FabricError(RuntimeError):
    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status  # the HTTP status, when the error came from a response


def _error(method: str, url: str, resp: requests.Response) -> FabricError:
    try:
        code = resp.json().get("errorCode")
    except ValueError:
        code = None
    if code == "CapacityNotActive":
        return FabricError("This workspace's Fabric capacity is paused or inactive. Resume the capacity, "
                           "or use a workspace on an active capacity.", code, resp.status_code)
    return FabricError(f"{method} {url} failed with {resp.status_code}: {resp.text}", code, resp.status_code)


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
                 sleep: Callable[[float], None] = time.sleep, poll_timeout: float = 300):
        self._token = token_provider
        self._http = session or http_session.session()
        self._sleep = sleep
        self._poll_timeout = poll_timeout

    def _request(self, method: str, url: str, headers: dict[str, str] | None = None,
                 **kwargs) -> requests.Response:
        if not url.startswith("http"):
            url = f"{API}{url}"
        headers = {**(headers or {}), "Authorization": f"Bearer {self._token()}"}
        resp = send_with_retry(lambda: self._http.request(method, url, headers=headers, timeout=60, **kwargs),
                               method, sleep=self._sleep, attempts=5, default_wait=5)
        if resp.status_code >= 400:
            raise _error(method, url, resp)
        return resp

    def _paged(self, url: str, key: str = "value") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        url = url if url.startswith("http") else f"{API}{url}"
        while url:
            body = self._request("GET", url).json()
            items.extend(body.get(key, []))
            nxt = body.get("continuationUri")
            url = followable_url(nxt, url) if nxt else ""
        return items

    def _wait(self, resp: requests.Response) -> dict[str, Any]:
        """Follow a 202 long-running operation to its result."""
        if resp.status_code != 202:
            return resp.json() if resp.content else {}
        came_from = getattr(resp, "url", None)  # the request's own URL; Fabric's operations start at API
        location = followable_url(resp.headers["Location"], came_from if isinstance(came_from, str) and came_from else API)
        deadline = time.monotonic() + self._poll_timeout
        while True:
            self._sleep(retry_after(resp.headers, 2))
            resp = self._request("GET", location)
            status = resp.json().get("status")
            if status == "Succeeded":
                result = self._request("GET", f"{location.rstrip('/')}/result")
                return result.json() if result.content else {}
            if status in ("Failed", "Undefined"):
                raise FabricError(f"Operation failed: {resp.text}")
            if time.monotonic() > deadline:
                raise FabricError(f"Operation timed out: {location}")

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
        """Create a schema-enabled lakehouse."""
        problem = lakehouse_name_problem(name)
        if problem:
            raise ValueError(problem)
        body = {"displayName": name, "creationPayload": {"enableSchemas": True}}
        if description:
            body["description"] = description
        return self._wait(self._request("POST", f"/workspaces/{workspace_id}/lakehouses", json=body))

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

    def find_lakehouse(self, workspace_id: str, name: str) -> dict[str, Any] | None:
        return next((lh for lh in self.list_lakehouses(workspace_id) if lh["displayName"] == name), None)

    def get_item_definition(self, workspace_id: str, item_id: str, fmt: str | None = None) -> dict[str, Any]:
        """An item's definition parts (base64), e.g. a notebook as ipynb. Needs Item.ReadWrite.All, which sign-in already asks for."""
        query = f"?format={fmt}" if fmt else ""
        return self._wait(self._request("POST", f"/workspaces/{workspace_id}/items/{item_id}/getDefinition{query}"))

    def list_notebooks(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/workspaces/{workspace_id}/notebooks")

    def save_notebook(self, workspace_id: str, name: str, ipynb: bytes) -> dict[str, Any]:
        """Create the notebook item, or replace its definition if one with this name exists."""
        definition = {"format": "ipynb", "parts": [{
            "path": "notebook-content.ipynb",
            "payload": base64.b64encode(ipynb).decode(),
            "payloadType": "InlineBase64",
        }]}
        existing = next((n for n in self.list_notebooks(workspace_id) if n["displayName"] == name), None)
        if existing is None:
            body = {"displayName": name, "definition": definition}
            return self._wait(self._request("POST", f"/workspaces/{workspace_id}/notebooks", json=body))
        url = f"/workspaces/{workspace_id}/notebooks/{existing['id']}/updateDefinition"
        self._wait(self._request("POST", url, json={"definition": definition}))
        return existing

    # ---- Fabric's job scheduler: run an item on Fabric capacity, now or on a schedule

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
                    raise FabricError(f"Fabric's tenant scan failed: {status.get('error') or 'no reason given'}")
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
        url: str | None = f"/workspaces/{workspace_id}/items/{item_id}/dataAccessRoles"
        items: list[dict[str, Any]] = []
        etag = None
        while url:
            resp = self._request("GET", url)
            etag = etag or resp.headers.get("ETag")
            body = resp.json()
            items.extend(body.get("value", []))
            url = body.get("continuationUri")
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
