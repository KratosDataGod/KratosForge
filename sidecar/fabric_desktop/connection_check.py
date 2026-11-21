"""One answer to "test this connection" for Fabric, Databricks and Snowflake (PAR-003, Preview).

Every platform keeps its own check. This module only maps each check's result into one small type,
``ConnectionCheck``, that says the same four things everywhere:

    identity   who the check signed in as
    scope      the role or workspace it ran as
    can        what that identity may do, as plain sentences the check found evidence for
    problems   each problem in the platform's own words, with the next step

The rest stays native, in ``native``: Snowflake's account, role and warehouse, the Databricks
workspace host, the Fabric tenant. Nothing here is a new abstraction over the platforms (the parity
assessment, section D item 3): the adapters wrap

    Fabric       readiness.checks (Home's Ready to work list), with the exception behind its
                 "Fabric answers" row kept, so a refused sign-in names Entra's or Fabric's reason
    Databricks   engines/databricks_discovery.Discovery (read-only GETs)
    Snowflake    engines/snowflake_remote.check_connection (no warehouse resumed)

and change none of them. Profiles stay in connprofiles.py. No secret reaches a result: credentials
are never copied into one, and every string goes through redact before it leaves (``as_dict``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from . import redact

SCHEMA = "connection-check@1"
PLATFORMS = ("fabric", "databricks", "snowflake")

SIGN_IN = "sign-in"


@dataclass(frozen=True)
class Problem:
    """One problem: ``field`` is what was checked, ``reason`` the platform's own words, ``fix`` the next
    step, and ``code`` the platform's code when it gave one (AADSTS700082, HTTP 401, 390114)."""

    field: str
    reason: str
    fix: str
    code: str = ""


@dataclass(frozen=True)
class ConnectionCheck:
    platform: str
    signed_in: bool
    identity: str = ""
    scope: str = ""
    can: tuple[str, ...] = ()
    problems: tuple[Problem, ...] = ()
    native: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise ValueError(f"unknown platform {self.platform!r}")
        bad = [p.field for p in self.problems if not p.reason or not p.fix]
        if bad:
            raise ValueError(f"every problem needs a reason and a next step: {bad}")

    @property
    def ok(self) -> bool:
        return self.signed_in and not self.problems

    def as_dict(self) -> dict[str, Any]:
        """The result as JSON-ready data, with anything credential-shaped removed."""
        out = {"schema": SCHEMA, "platform": self.platform, "ok": self.ok, "signed_in": self.signed_in,
               "identity": self.identity, "scope": self.scope, "can": list(self.can),
               "problems": [{"field": p.field, "reason": p.reason, "fix": p.fix, "code": p.code} for p in self.problems],
               "native": dict(self.native)}
        return dict(redact.walk(out))


def _line(text: Any) -> str:
    return redact.one_line(text, 600)


def _problem(field_: str, reason: Any, fix: str, code: str = "") -> Problem:
    return Problem(field_, _line(reason) or "The check failed without a reason.", fix, code)


# --- Snowflake -------------------------------------------------------------------------------------

_SF_CODE = re.compile(r"\(HTTP (\d{3})(?:, code (\w+))?\)")
SF_SIGN_IN_FIX = "Fix the sign-in in Settings > Connections, then check again."
SF_RETRY_FIX = "Check again in a minute. If it keeps failing, fix the sign-in in Settings > Connections."


def _sf_code(message: str) -> str:
    found = _SF_CODE.search(message)
    if not found:
        return ""
    return found.group(2) or f"HTTP {found.group(1)}"


def from_snowflake(result: Mapping[str, Any], connection: Mapping[str, Any]) -> ConnectionCheck:
    """``result`` is what snowflake_remote.check_connection returned for ``connection``."""
    ident = result.get("identity") or {}
    checks = {c.get("field"): c for c in result.get("checks") or []}
    role = str(connection.get("role") or "")
    roles = [str(r) for r in ident.get("available_roles") or []]
    default_role = str(ident.get("default_role") or "")
    signed_in = bool(result.get("signed_in"))
    scope = ""
    if role and (checks.get("role") or {}).get("ok"):   # the check ran as the role, by its name in Snowflake
        scope = "role " + (next((r for r in roles if r == role), None)
                           or next((r for r in roles if r.lower() == role.lower()), role))
    elif signed_in and default_role:                     # no role, or one not granted: the user's default
        scope = f"default role {default_role}"
    can = tuple(_line(c.get("detail")) for c in result.get("checks") or [] if c.get("ok") and c.get("field") != SIGN_IN)
    problems = tuple(_problem(p.get("field") or SIGN_IN, p.get("message"), p.get("fix") or SF_RETRY_FIX,
                              _sf_code(str(p.get("message") or "")))
                     for p in result.get("problems") or [])
    native = {"account": str(ident.get("account") or result.get("account") or ""),
              "role": role, "default_role": default_role, "available_roles": roles,
              "database": str(connection.get("database") or ""), "schema": str(connection.get("schema") or ""),
              "warehouse": str(connection.get("warehouse") or "")}
    return ConnectionCheck("snowflake", signed_in, str(ident.get("user") or ""), scope, can, problems, native)


def check_snowflake(connection: dict[str, Any], client: Any = None, **kwargs: Any) -> ConnectionCheck:
    """Snowflake's own check (no warehouse resumed), in the shared result."""
    from .engines import snowflake_remote
    return from_snowflake(snowflake_remote.check_connection(connection, client, **kwargs), connection)


# --- Databricks ------------------------------------------------------------------------------------

_DBX_STATUS = re.compile(r"\(HTTP (\d{3})\)")
DBX_SIGN_IN_FIX = ("Sign in again (databricks auth login for a CLI profile), or replace the token the "
                   "connection names in Settings > Connections, then check again.")
_DBX_FIX = {
    "identity": "Ask a workspace admin whether this identity may read its own user details, then check again.",
    "warehouses": "Ask a workspace admin for access to SQL warehouses, then check again.",
    "clusters": "Ask a workspace admin for access to clusters, then check again.",
    "metastore": "Ask a Databricks account admin to assign a Unity Catalog metastore to this workspace.",
    "catalogs": "Ask a Unity Catalog admin for USE CATALOG on a catalog, then check again.",
}


def _dbx_code(message: str) -> str:
    found = _DBX_STATUS.search(message)
    return f"HTTP {found.group(1)}" if found else ""


def from_databricks(result: Mapping[str, Any]) -> ConnectionCheck:
    """``result`` is what databricks_discovery.Discovery.run returned, or the engine_discover answer for a
    connection whose sign-in couldn't be built (``error`` with no parts)."""
    host = str(result.get("host") or "")
    parts: Mapping[str, Any] = result.get("parts") or {}
    problems: list[Problem] = []
    if result.get("error"):
        problems.append(_problem(SIGN_IN, result["error"], DBX_SIGN_IN_FIX, _dbx_code(str(result["error"]))))
    who = parts.get("identity") or {}
    identity = ""
    if who.get("ok"):
        me = who.get("value") or {}
        identity = str(me.get("user") or me.get("name") or "")
    # A sign-in problem is a part refused with 401, or the part discovery stopped at (the last one that
    # failed before the skipped ones). The first one's words are the reason: a 401 refuses the token for
    # every later part too, which then only says it isn't sent again.
    failed = [n for n in ("identity", "warehouses", "clusters", "metastore", "catalogs")
              if parts.get(n) and not parts[n].get("ok") and not parts[n].get("skipped")]
    signed_out = {n for n in failed if _dbx_code(str(parts[n].get("error") or "")) == "HTTP 401"}
    if failed and not result.get("signed_in"):
        signed_out.add(failed[-1])
    if signed_out:
        error = str(parts[next(n for n in failed if n in signed_out)].get("error") or "")
        problems.append(_problem(SIGN_IN, error, DBX_SIGN_IN_FIX, _dbx_code(error)))
    failed = [n for n in failed if n not in signed_out]
    signed_in = bool(result.get("signed_in")) and not signed_out and not result.get("error")
    can: list[str] = []
    for name in ("identity", "warehouses", "clusters", "metastore", "catalogs"):
        part = parts.get(name) or {}
        if name == "identity" and part.get("ok"):
            continue
        if part.get("ok"):
            value: Any = part.get("value") or []
            if name == "warehouses":
                can.append(f"See {len(value)} SQL warehouse{'s' if len(value) != 1 else ''}.")
            elif name == "clusters":
                usable = sum(1 for c in value if c["connect"]["usable"])
                can.append(f"See {len(value)} cluster{'s' if len(value) != 1 else ''}, "
                           f"{usable} of which Databricks Connect can use.")
            elif name == "metastore":
                can.append(f"Read Unity Catalog metastore {value.get('name') or value.get('id')}.")
            else:
                can.append(f"See {len(value)} catalog{'s' if len(value) != 1 else ''}.")
        elif name in failed:
            problems.append(_problem(name, part.get("error"), _DBX_FIX[name], _dbx_code(str(part.get("error") or ""))))
    metastore = (parts.get("metastore") or {}).get("value") or {}
    native = {"host": host, "metastore": str(metastore.get("name") or ""),
              "suggested": dict(result.get("suggested") or {})}
    scope = f"workspace {host.removeprefix('https://')}" if host and signed_in else ""
    return ConnectionCheck("databricks", signed_in, identity, scope, tuple(can), tuple(problems), native)


def check_databricks(connection: dict[str, Any], client: Any = None, catalogs: bool = True) -> ConnectionCheck:
    """Databricks discovery (every request a GET, nothing starts), in the shared result. ``client`` is a
    databricks_sql.Client; without one, it is built from ``connection`` as engine_discover does."""
    from .engines import databricks_auth, databricks_discovery, databricks_sql
    if client is None:
        try:
            client = databricks_sql.Client(databricks_auth.provider_for(connection))
        except databricks_auth.DatabricksAuthError as exc:
            return from_databricks({"host": str(connection.get("host") or ""), "signed_in": False, "parts": {},
                                    "error": str(exc)})
    return from_databricks(databricks_discovery.Discovery(client, catalogs=catalogs).run())


# --- Fabric ----------------------------------------------------------------------------------------

FABRIC_SIGN_IN_FIX = "Sign in to Fabric again from KratosForge, then check again."
FABRIC_ENDED_FIX = ("Sign in to Fabric again from KratosForge. The saved sign-in has ended, so only a new "
                    "sign-in fixes it.")
FABRIC_BLOCKED_FIX = ("Ask your IT admin which sign-in rule refused this PC or account, then sign in again "
                      "from KratosForge.")
FABRIC_ACCESS_FIX = "Ask a Fabric workspace admin for access, then check again."
FABRIC_RETRY_FIX = "Check this PC's network, then check again."
# readiness.checks names a fix by the action Home offers for it.
_FABRIC_ACTION_FIX = {
    "sign_in": "Sign in to Fabric from KratosForge, then check again.",
    "retry": FABRIC_RETRY_FIX,
    "workspaces": "Ask to be added to a Fabric workspace, or create one, then check again.",
    "capacity": "Ask a Fabric admin to assign a running capacity to one of your workspaces, then check again.",
}


def _fabric_failure(exc: BaseException) -> Problem:
    """The platform's own reason behind a failed Fabric call: Entra's code for a refused sign-in,
    Fabric's status and code for a refused request."""
    from . import auth
    from .fabric_api import FabricError
    if isinstance(exc, auth.SignInError):
        fix = (FABRIC_ENDED_FIX if exc.code in auth.ENDED_CODES else
               FABRIC_BLOCKED_FIX if exc.code in auth.BLOCKED_CODES else FABRIC_SIGN_IN_FIX)
        reason = f"Microsoft Entra ID refused the sign-in ({exc.code})." + (f" {exc.description}" if exc.description else "")
        return _problem(SIGN_IN, reason, fix, exc.code)
    if isinstance(exc, auth.SignInRequired):
        return _problem(SIGN_IN, exc, FABRIC_SIGN_IN_FIX)
    if isinstance(exc, FabricError):
        code = " ".join(x for x in (f"HTTP {exc.status}" if exc.status else "", exc.code or "") if x)
        if exc.status == 401:
            return _problem(SIGN_IN, exc, FABRIC_SIGN_IN_FIX, code)
        if exc.status == 403:
            return _problem("workspaces", exc, FABRIC_ACCESS_FIX, code)
        return _problem("fabric", exc, FABRIC_RETRY_FIX, code)
    return _problem("fabric", f"Fabric didn't answer ({type(exc).__name__}). {redact.secrets(str(exc))}",
                    FABRIC_RETRY_FIX)


def from_fabric(rows: list[dict[str, Any]], user: str | None, tenant: str | None,
                failure: BaseException | None = None) -> ConnectionCheck:
    """``rows`` are readiness.checks' rows; ``failure`` is the exception behind a failed "Fabric answers"
    row, when there was one. Only the sign-in, Fabric and capacity rows are about the connection."""
    by_key = {r["key"]: r for r in rows}
    problems: list[Problem] = []
    can: list[str] = []
    signed_in = bool(user)
    for key, problem_field in (("sign_in", SIGN_IN), ("fabric", "workspaces"), ("capacity", "capacity")):
        row = by_key.get(key)
        if not row or row["status"] == "wait":
            continue
        if row["status"] == "ok":
            if key != "sign_in":
                can.append(_line(row["text"]))
        elif key == "fabric" and row["status"] == "fail" and failure is not None:
            problem = _fabric_failure(failure)
            problems.append(problem)
            signed_in = signed_in and problem.field != SIGN_IN
        else:
            problems.append(_problem(problem_field, row["text"],
                                     _FABRIC_ACTION_FIX.get(str(row.get("fix") or ""), FABRIC_RETRY_FIX)))
    spaces = _WORKSPACES.search(str((by_key.get("fabric") or {}).get("text") or ""))
    count = int(spaces.group(1)) if spaces else 0
    scope = f"{count} Fabric workspace{'s' if count != 1 else ''}" if signed_in and spaces else ""
    return ConnectionCheck("fabric", signed_in, str(user or "") if signed_in else "", scope, tuple(can),
                           tuple(problems), {"tenant": str(tenant or ""), "workspaces": count})


_WORKSPACES = re.compile(r"You can see (\d+) workspace")


def _no_capacities() -> list[dict[str, Any]]:
    raise PermissionError("Capacities weren't asked for.")


def check_fabric(user: str | None, tenant: str | None, list_workspaces: Callable[[], list[dict[str, Any]]],
                 list_capacities: Callable[[], list[dict[str, Any]]] | None = None) -> ConnectionCheck:
    """Fabric's sign-in and workspace rows of Home's Ready to work list, in the shared result. Reads
    only. ``list_workspaces`` is FabricClient.list_workspaces (or a fake); it is called only when signed in."""
    from . import readiness
    failure: list[BaseException] = []

    def workspaces() -> list[dict[str, Any]]:
        try:
            return list_workspaces()
        except Exception as exc:  # noqa: BLE001 - kept for its own reason, then raised for readiness's row
            failure.append(exc)
            raise

    rows = readiness.checks(user, workspaces, list_capacities or _no_capacities, {})
    return from_fabric(rows, user, tenant, failure[0] if failure else None)
