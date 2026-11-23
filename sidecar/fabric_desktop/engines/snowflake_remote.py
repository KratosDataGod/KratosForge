"""Snowflake's SQL API from this PC, and the connection check (SF-002, Preview; tested against fakes, not a live account).

This is the cloud side of Snowflake. The local side, Snowflake SQL validated on DuckDB, is
``fabric_desktop/snowflake_sql.py`` and never calls this module, and this module never falls back to it.

Calls go to the documented SQL API only, at ``https://<account>.snowflakecomputing.com``
(connprofiles.snowflake_host), with the headers engines/snowflake_auth.py makes:

    POST /api/v2/statements                    submit one statement
    GET  /api/v2/statements/{handle}           its status and first result partition
    POST /api/v2/statements/{handle}/cancel    cancel it
    GET  /api/v2/statements/{handle}?partition=N   a later result partition (the SQL warehouse engine)

The connection check (``check_connection``) answers "can I work with this connection, and as whom?"
without resuming a warehouse: it sends no warehouse, so its statements are context functions and SHOW
commands, which Snowflake answers from its cloud services layer. Those don't bill warehouse credits;
cloud services use is billed only beyond Snowflake's daily adjustment, which a check's few statements
don't approach. It checks, in turn:

    sign-in     the statement runs at all (the credential is good)
    role        the profile's role is one of the user's available roles (CURRENT_AVAILABLE_ROLES)
    database    SHOW DATABASES as that role finds it
    schema      SHOW SCHEMAS in that database finds it
    warehouse   SHOW WAREHOUSES finds it, with its state and size (never resumed)

Each failed check names the field and why, and each says what to do. Unset fields are skipped, not
failed. A result says what was checked, never more: a passed check is evidence the connection works
for metadata, not that queries on the warehouse will.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Callable

from .. import connprofiles, redact
from ..retrying import PollTimedOut, poll_until, send_remote
from .snowflake_auth import SnowflakeAuthError, TokenProvider, provider_for

SCHEMA = "kratosforge.snowflake-check/1"
STATEMENT_TIMEOUT = 30          # seconds Snowflake may spend on one check statement
POLL_SECONDS = 0.5
POLL_CAP = 5.0              # the longest wait between looks, whatever Retry-After says, so Cancel stays prompt
MAX_WAIT = 60.0                 # a statement still running after this is cancelled and reported
_HANDLE = re.compile(r"^[0-9a-fA-F-]{1,64}$")
CHECK_FIELDS = ("role", "database", "schema", "warehouse")


class SnowflakeError(RuntimeError):
    """Snowflake refused or couldn't be reached. ``code`` and ``sql_state`` come from its error body."""

    def __init__(self, message: str, status: int = 0, code: str = "", sql_state: str = ""):
        super().__init__(message)
        self.status, self.code, self.sql_state = status, code, sql_state


def _hint(status: int) -> str:
    return {401: "Check the connection's user name, the key registered on that user, or the token.",
            403: ("A network policy may block this PC, or your role may not use the SQL API from this network. "
                  "Ask a Snowflake admin to check the network policy."),
            404: "Check the account identifier in the connection.",
            408: "The statement took too long and Snowflake stopped it. Check again in a minute.",
            429: "Snowflake is limiting requests, so try again in a minute."}.get(status, "")


class Client:
    """The SQL API calls, with the sign-in headers on each; errors never quote a credential."""

    def __init__(self, tokens: TokenProvider, http: Any = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if http is None:
            from ..http_session import session
            http = session()
        self.host = connprofiles.snowflake_host(tokens.account)
        self._tokens = tokens
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self.stopped: Callable[[], bool] = lambda: False
        self._sent = ""   # the last token sent

    def _send(self, method: str, path: str, repeatable: bool, **kwargs: Any) -> Any:
        headers = self._tokens.headers() | {"Accept": "application/json", "Content-Type": "application/json",
                                            "User-Agent": "KratosForge"}
        token = headers["Authorization"][len("Bearer "):]
        self._sent = token

        def send() -> Any:
            return self._http.request(method, self.host + path, headers=headers, timeout=60, **kwargs)
        try:
            resp, _ = send_remote(send, repeatable, self._sleep,
                                  (lambda: False) if path.endswith("/cancel") else self.stopped)
        except Exception as exc:  # noqa: BLE001 - a network failure becomes the person's message
            raise SnowflakeError(f"Couldn't reach {self.host} ({type(exc).__name__}). "
                                 "Check this PC's network and the account identifier in the connection.") from None
        if resp.status_code == 401:
            self._tokens.drop(token)
        return resp

    def _body(self, resp: Any) -> dict[str, Any]:
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise SnowflakeError(f"{self.host} answered with something other than a JSON object (HTTP {resp.status_code}).",
                                 resp.status_code)
        return body

    def _refused(self, resp: Any) -> SnowflakeError:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        message = str(body.get("message") or "")
        if self._sent:
            message = message.replace(self._sent, "[removed]")
        message = redact.one_line(redact.secrets(message))[:300]
        code, state = str(body.get("code") or "")[:12], str(body.get("sqlState") or "")[:8]
        text = f"Snowflake refused the request (HTTP {resp.status_code}" + (f", code {code}" if code else "") + "). "
        return SnowflakeError((text + (f"{message} " if message else "") + _hint(resp.status_code)).rstrip(),
                              resp.status_code, code, state)

    def statement(self, sql: str, *, role: str = "", database: str = "", schema: str = "",
                  warehouse: str = "", timeout: int = STATEMENT_TIMEOUT, max_wait: float = MAX_WAIT,
                  asynchronous: bool = False) -> dict[str, Any]:
        """Run one statement and return Snowflake's final answer (its result set's first partition).
        ``asynchronous`` asks for the statement handle at once (``async=true``), so a Cancel pressed
        while it runs always has a handle to cancel; it is then followed for up to ``max_wait`` seconds."""
        # One statement per request, whatever the account or user sets: Snowflake's own count check backs
        # up any statement splitting done before the call.
        body: dict[str, Any] = {"statement": sql, "timeout": int(timeout),
                                "parameters": {"MULTI_STATEMENT_COUNT": "1"}}
        for key, value in (("role", role), ("database", database), ("schema", schema), ("warehouse", warehouse)):
            if value:
                body[key] = value
        # A submit is never sent twice on a 5xx: it may have started (retrying.send_remote).
        query = f"requestId={uuid.uuid4()}" + ("&async=true" if asynchronous else "")
        resp = self._send("POST", f"/api/v2/statements?{query}", False, data=json.dumps(body))
        if resp.status_code == 202:
            handle = str(self._body(resp).get("statementHandle") or "")
            if not _HANDLE.fullmatch(handle):
                raise SnowflakeError("Snowflake accepted the statement but gave no statement handle to follow.")

            def look() -> tuple[Any, Any]:
                answer = self._send("GET", f"/api/v2/statements/{handle}", True)
                return answer, getattr(answer, "headers", None)
            try:
                resp = poll_until(look, lambda r: r.status_code != 202 or self.stopped(), max_wait,
                                  getattr(resp, "headers", None), self._sleep, self._clock, POLL_SECONDS,
                                  cap=POLL_CAP)
            except PollTimedOut:
                self.cancel(handle)
                took = f"{int(max_wait)} seconds" if max_wait < 120 else f"{int(max_wait // 60)} minutes"
                raise SnowflakeError(f"The statement was still running after {took}, so it was cancelled. "
                                     "Narrow the query, or run it in Snowsight.") from None
            if self.stopped():  # the person pressed Cancel, even if the last look was refused or throttled
                self.cancel(handle)
                raise SnowflakeError("Cancelled.")
        if resp.status_code != 200:
            raise self._refused(resp)
        return self._body(resp)

    def cancel(self, handle: str) -> None:
        if not _HANDLE.fullmatch(handle or ""):
            raise SnowflakeError("Not a Snowflake statement handle.")
        resp = self._send("POST", f"/api/v2/statements/{handle}/cancel", True)
        if resp.status_code not in (200, 422):  # 422: it had already finished
            raise self._refused(resp)


    def partition(self, handle: str, index: int) -> dict[str, Any]:
        """Result partition ``index`` of a finished statement: its ``data`` only, no metadata."""
        if not _HANDLE.fullmatch(handle or ""):
            raise SnowflakeError("Not a Snowflake statement handle.")
        resp = self._send("GET", f"/api/v2/statements/{handle}?partition={int(index)}", True)
        if resp.status_code != 200:
            raise self._refused(resp)
        return self._body(resp)


def rows(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """The answer's rows as dicts keyed by lower-case column name. Values stay Snowflake's strings."""
    meta = answer.get("resultSetMetaData") or {}
    names = [str(c.get("name") or "").lower() for c in meta.get("rowType") or [] if isinstance(c, dict)]
    data = answer.get("data") or []
    if not isinstance(data, list):
        raise SnowflakeError("Snowflake's answer had no rows where they belong.")
    if any(not isinstance(r, list) or len(r) != len(names) for r in data):
        raise SnowflakeError("Snowflake's answer had rows that don't match its columns.")
    return [dict(zip(names, r, strict=True)) for r in data]


def _literal(text: str) -> str:
    """``text`` as a Snowflake string literal for SHOW ... LIKE: backslashes and quotes escaped. SHOW's LIKE
    has no escape character, so ``_`` and ``%`` in a name still match any character; the rows it lists
    are filtered to the exact name afterwards (``_find``)."""
    escaped = text.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def _ident(name: str) -> str:
    """A name exactly as Snowflake lists it, quoted."""
    return '"' + name.replace('"', '""') + '"'


def _find(found: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    """The row named ``wanted``: exactly, or ignoring case when exactly one matches (an unquoted name
    in the profile is stored upper case by Snowflake)."""
    exact = [r for r in found if r.get("name") == wanted]
    if exact:
        return exact[0]
    folded = [r for r in found if str(r.get("name") or "").lower() == wanted.lower()]
    return folded[0] if len(folded) == 1 else None


def _roles(text: Any) -> list[str]:
    try:
        value = json.loads(text) if isinstance(text, str) else []
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def check_connection(connection: dict[str, Any], client: Client | None = None, *,
                     http: Any = None, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Check a Snowflake connection from a profile, field by field, without resuming a warehouse."""
    out: dict[str, Any] = {"schema": SCHEMA, "platform": "snowflake", "account": str(connection.get("account") or ""),
                           "signed_in": False, "ok": False, "identity": {}, "checks": [], "problems": []}

    def check(field: str, ok: bool, detail: str, fix: str = "") -> None:
        out["checks"].append({"field": field, "ok": ok, "detail": detail})
        if not ok:
            out["problems"].append({"field": field, "message": detail, "fix": fix})

    try:
        client = client or Client(provider_for(connection), http=http, sleep=sleep)
        me = rows(client.statement("SELECT CURRENT_ACCOUNT() AS kf_account, CURRENT_USER() AS kf_user, "
                                   "CURRENT_ROLE() AS kf_role, CURRENT_AVAILABLE_ROLES() AS kf_roles"))
    except (SnowflakeAuthError, SnowflakeError, ValueError) as exc:
        check("sign-in", False, redact.secrets(str(exc)),
              "Fix the sign-in in Settings > Connections, then check again.")
        return out
    first = me[0] if me else {}
    roles = _roles(first.get("kf_roles"))
    out["signed_in"] = True
    out["identity"] = {"account": str(first.get("kf_account") or ""), "user": str(first.get("kf_user") or ""),
                       "default_role": str(first.get("kf_role") or ""), "available_roles": roles}
    check("sign-in", True, f"Signed in as {out['identity']['user'] or 'the connection user'}.")

    role = str(connection.get("role") or "")
    role_ok = True
    if role:
        match = next((r for r in roles if r == role), None) or next((r for r in roles if r.lower() == role.lower()), None)
        role_ok = match is not None
        check("role", role_ok, f"Role {match} is available to you." if match else
              f"Role {role} isn't granted to {out['identity']['user'] or 'this user'}.",
              "Ask a Snowflake admin to grant the role, or pick one of your available roles.")
        role = match or role
    use_role = role if role and role_ok else ""

    def show(sql: str) -> list[dict[str, Any]] | None:
        try:
            return rows(client.statement(sql, role=use_role))
        except (SnowflakeError, SnowflakeAuthError) as exc:  # a token refresh can fail mid-check too
            out["problems"].append({"field": "show", "message": redact.secrets(str(exc)), "fix": ""})
            return None

    as_role = f"role {use_role}" if use_role else "your default role"
    database, schema = str(connection.get("database") or ""), str(connection.get("schema") or "")
    db_row = None
    if database:
        found = show(f"SHOW DATABASES LIKE {_literal(database)}")
        db_row = _find(found or [], database)
        check("database", db_row is not None, f"Database {db_row['name']} is visible to {as_role}." if db_row else
              f"Database {database} doesn't exist, or {as_role} can't see it.",
              "Check the name, or ask for USAGE on the database for your role.")
    if schema:
        if db_row is None:
            check("schema", False, f"Schema {schema} wasn't checked, because its database isn't set or wasn't found.",
                  "Set a database the role can see first.")
        else:
            found = show(f"SHOW SCHEMAS LIKE {_literal(schema)} IN DATABASE {_ident(str(db_row['name']))}")
            row = _find(found or [], schema)
            check("schema", row is not None, f"Schema {row['name']} is visible to {as_role}." if row else
                  f"Schema {schema} doesn't exist in {db_row['name']}, or {as_role} can't see it.",
                  "Check the name, or ask for USAGE on the schema for your role.")
    warehouse = str(connection.get("warehouse") or "")
    if warehouse:
        found = show(f"SHOW WAREHOUSES LIKE {_literal(warehouse)}")
        row = _find(found or [], warehouse)
        if row:
            state, size = str(row.get("state") or "").lower(), str(row.get("size") or "")
            check("warehouse", True, f"Warehouse {row['name']} is visible to {as_role}"
                  + (f" ({state}" + (f", {size}" if size else "") + ")" if state else "")
                  + ". It wasn't resumed. Running queries on it uses credits.")
        else:
            check("warehouse", False, f"Warehouse {warehouse} doesn't exist, or {as_role} can't use it.",
                  "Check the name, or ask for USAGE on the warehouse for your role.")
    out["ok"] = not out["problems"]
    return out
