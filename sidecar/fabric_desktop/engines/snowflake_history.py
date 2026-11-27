"""Your recent Snowflake queries (SF-005, Preview; tested against fakes, not a live account).

Reads the signed-in user's own queries from the INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER table function,
through Snowflake's SQL API with engines/snowflake_remote.py's client:

    SELECT ... FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(
        END_TIME_RANGE_START => DATEADD('hour', -<hours>, CURRENT_TIMESTAMP()), RESULT_LIMIT => <limit>))

The function defaults to the current user, so no user name is sent, and it needs no grant on the
SNOWFLAKE database's ACCOUNT_USAGE. Its time range must fall within the last 7 days, so the window is
1 to MAX_HOURS hours; no end is given, so queries still running are included. RESULT_LIMIT keeps the
queries that ended most recently, at most MAX_ROWS here. Each row gives the query id, status, run time,
warehouse and rows produced. Its text is cut to at most TEXT_MAX characters, after anything
credential-shaped is removed (redact.secrets), and so is its error message.

**It uses credits.** A table function is a query, so it runs on the connection's warehouse: a suspended
warehouse resumes, and a running one uses Snowflake credits until its own auto-suspend. So, as for the
SQL warehouse engine (snowflake_warehouse.py), nothing is sent without a ``RemoteAuthorization`` for
that engine and ``account/warehouse`` (ADR-003); an approval to run cells on that warehouse covers this
read, and nothing else does. The session's database is the connection's, because an INFORMATION_SCHEMA
table function is read through a database in use. The query that reads the history is left out of it.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .. import redact
from .authorization import require
from .contract import Unsupported
from .snowflake_auth import provider_for
from .snowflake_objects import _int, _text, read_rows
from .snowflake_remote import Client
from .snowflake_warehouse import SNOWFLAKE_SQL, target_for

SCHEMA = "kratosforge.snowflake-history/1"
HOURS = 24
MAX_HOURS = 7 * 24 - 1   # the function refuses a range outside the last 7 days; an hour's margin
ROWS = 100
MAX_ROWS = 1000          # the function allows 10,000; a person reads far fewer
TEXT = 500
TEXT_MAX = 2000
_FETCH = 4000            # characters of query text read from Snowflake, before redaction and the cut
STATEMENT_SECONDS = 60
_ERROR_MAX = 300

_SQL = ("SELECT query_id, LEFT(query_text, {fetch}) AS query_text, query_type, execution_status, error_code, "
        "LEFT(error_message, {fetch}) AS error_message, warehouse_name, warehouse_size, role_name, database_name, "
        "schema_name, start_time, end_time, total_elapsed_time, rows_produced, bytes_scanned "
        "FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER("
        "END_TIME_RANGE_START => DATEADD('hour', -{hours}, CURRENT_TIMESTAMP()), RESULT_LIMIT => {limit})) "
        "ORDER BY start_time DESC")


def _bounded(value: int, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{what} is {low:,} to {high:,}. Ask for a number in that range.")
    return value


def _when(value: Any) -> str:
    """Snowflake's ``seconds.fraction`` (with a TIMESTAMP_TZ's offset after a space) as UTC ISO 8601, or ""."""
    if value is None or value == "":
        return ""
    try:
        seconds = Decimal(str(value).split(" ", 1)[0])
        when = dt.datetime.fromtimestamp(float(seconds), dt.timezone.utc)
    except (InvalidOperation, ValueError, OverflowError, OSError):
        return _text(value, 40)
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


_TAIL = 256  # longer than any credential redact.secrets looks for

# Snowflake parameters whose value is a credential: CREATE STAGE, COPY, CREATE SECRET, CREATE USER, ALTER
# USER, CREATE STORAGE or SECURITY INTEGRATION, and the connector options a script may set. redact.secrets
# doesn't know them by name, and a person's query history holds exactly these statements. The value goes,
# whether quoted with '...' (doubled or backslash-escaped quotes inside), "...", $$...$$, or bare, after
# =, => or :=, with comments or line breaks around the operator; a value in brackets, IDENTIFIER(...), any
# other call (CONCAT(...)) or joined with || goes whole; a quote cut off by Snowflake's own limit runs to
# the end. The name may be in double quotes. A name with
# words before it counts too (EXTERNAL_OAUTH_CLIENT_SECRET, RSA_PRIVATE_KEY_PASSPHRASE), except
# MUST_CHANGE_PASSWORD, which is true or false. A public key, a role or an ARN isn't a secret, so they stay.
_SF_SECRET_PARAMS = ("AWS_KEY_ID", "AWS_SECRET_KEY", "AWS_TOKEN", "AZURE_SAS_TOKEN", "MASTER_KEY",
                     "OAUTH_CLIENT_SECRET", "OAUTH_REFRESH_TOKEN", "OAUTH_ACCESS_TOKEN", "PRIVATE_KEY",
                     "SECRET_STRING", "PASSWORD", "PASSPHRASE", "PRIVATE_KEY_PASSPHRASE", "TOKEN",
                     "PWD", "PRIVATE_KEY_FILE_PWD", "RSA_PRIVATE_KEY_PASSPHRASE", "CLIENT_SECRET", "API_KEY",
                     "ACCESS_TOKEN", "REFRESH_TOKEN", "SAS_TOKEN")
_GAP = r"(?:\s|/\*.*?(?:\*/|$)|--[^\n]*(?:\n|$))*"  # spaces, line breaks and comments
_LIT = r"(?:'(?:[^'\\]|\\.|'')*(?:'|$)|\"(?:[^\"]|\"\")*(?:\"|$)|\$\$.*?(?:\$\$|$))"
_OPERATOR = r"(" + _GAP + r"(?:=>?|:=)" + _GAP + r")"
_SF_SECRET = re.compile(
    r"(?<![A-Z0-9_$])((?!MUST_CHANGE_PASSWORD\b)(?:[A-Z0-9$]+_)*(?:"
    + "|".join(sorted(_SF_SECRET_PARAMS, key=len, reverse=True)) + r")\"?)(?![A-Z0-9_$])" + _OPERATOR,
    re.IGNORECASE | re.DOTALL)
# A session or scripting variable, or a SET parameter, one of whose underscore-separated words says it holds
# a credential: SET pw = '...', LET db_api_token := '...'. MUST_CHANGE_PASSWORD (true or false), a public
# key and a policy's name (PASSWORD_POLICY) aren't secrets; a word that only contains one (keyword_list) isn't one.
_CREDENTIAL_NAME = (r"(?!MUST_CHANGE_PASSWORD\b|RSA_PUBLIC_KEY|[A-Z0-9_$]*_POLICY\b)(?:[A-Z0-9$]+_)*"
                    r"(?:PW|PWD|PASS|PASSWORD|PASSPHRASE|SECRET|TOKEN|KEY|APIKEY)(?:_[A-Z0-9$]+)*")
_SF_VARIABLE = re.compile(r"(?<![A-Z0-9_$])((?:SET|LET)\s+" + _CREDENTIAL_NAME + r")(?![A-Z0-9_$])"
                          + _OPERATOR, re.IGNORECASE | re.DOTALL)
# SET (a, b) = (x, y) sets several at once: when any of the names holds a credential, every value goes.
_SF_VARIABLES = re.compile(r"(?<![A-Z0-9_$])(SET" + _GAP + r"\(([A-Z0-9_$,\s\"]*)\))(" + _GAP + r"=" + _GAP + r")",
                           re.IGNORECASE | re.DOTALL)
_ONE_NAME = re.compile(_CREDENTIAL_NAME, re.IGNORECASE)
_GAP_AT = re.compile(_GAP, re.DOTALL)
_LIT_AT = re.compile(_LIT, re.DOTALL)
_JOIN_AT = re.compile(_GAP + r"\|\|" + _GAP, re.DOTALL)
_CALL_AT = re.compile(r"[A-Z_$][A-Z0-9_$.]*" + _GAP + r"\(", re.IGNORECASE | re.DOTALL)  # IDENTIFIER(, CONCAT(
_BARE_AT = re.compile(r"[^\s,();|'\"]+")


def _closed(text: str, at: int) -> int:
    """Where the bracket opened just before ``at`` closes, skipping literals and comments, or the end."""
    depth = 1
    while at < len(text):
        quoted = _LIT_AT.match(text, at)
        if quoted and quoted.end() > at:
            at = quoted.end()
        elif text.startswith(("/*", "--"), at):
            at = _GAP_AT.match(text, at).end()
        else:
            depth += {"(": 1, ")": -1}.get(text[at], 0)
            at += 1
            if depth == 0:
                return at
    return at


def _item(text: str, at: int) -> tuple[int, bool] | None:
    """The end of one value at ``at`` (a literal, a bracket, a call, or a bare word) and whether it's a literal."""
    quoted = _LIT_AT.match(text, at)
    if quoted and quoted.end() > at:
        return quoted.end(), True
    if text.startswith("(", at):
        return _closed(text, at + 1), False
    call = _CALL_AT.match(text, at)
    if call:
        return _closed(text, call.end()), False
    bare = _BARE_AT.match(text, at)
    return (bare.end(), False) if bare else None


def _value_end(text: str, at: int) -> int:
    """The end of the value that starts at ``at``: items joined with ||, and a literal written right after a
    literal ('a' 'b'). ``at`` itself when there's none."""
    first = _item(text, at)
    if first is None:
        return at
    end, literal = first
    while True:
        joined = _JOIN_AT.match(text, end)
        if joined and (more := _item(text, joined.end())):
            end, literal = more
            continue
        after = _GAP_AT.match(text, end).end()
        quoted = _LIT_AT.match(text, after) if literal else None
        if quoted and quoted.end() > after:
            end = quoted.end()
            continue
        return end


def _sf_secrets(text: str) -> str:
    """``text`` with the value of each Snowflake credential parameter or variable replaced by [removed]."""
    for pattern in (_SF_VARIABLES, _SF_VARIABLE, _SF_SECRET):
        at = 0
        while found := pattern.search(text, at):
            at = found.end()
            if pattern is _SF_VARIABLES and not any(
                    _ONE_NAME.fullmatch(name.strip().strip('"')) for name in found.group(2).split(",")):
                continue
            end = _value_end(text, at)
            if end > at:
                text = f"{text[:at]}'[removed]'{text[end:]}"
                at += len("'[removed]'")
    return text


def _untailed(text: Any) -> str:
    """``text``, less its last ``_TAIL`` characters when it fills Snowflake's ``_FETCH`` cut, where a
    credential may be split too short to be recognised by redaction."""
    raw = str(text or "")
    return raw[:_FETCH - _TAIL] if len(raw) >= _FETCH else raw


def _cut(text: Any, chars: int) -> tuple[str, bool]:
    """``text`` with credentials removed, then cut to ``chars``, and whether it was cut. When Snowflake
    cut the text at ``_FETCH``, a credential may be split at its end, too short to be recognised, so the
    last ``_TAIL`` characters are dropped first. Redaction then can't move part of one into what's shown."""
    raw = str(text or "")
    snowflake_cut = len(raw) >= _FETCH
    clean = redact.secrets(_sf_secrets(_untailed(raw)))
    return (clean, snowflake_cut) if len(clean) <= chars else (clean[:chars], True)


def _row(r: dict[str, Any], chars: int) -> dict[str, Any]:
    text, cut = _cut(r.get("query_text"), chars)
    error = (redact.one_line(_sf_secrets(_untailed(r["error_message"])), _ERROR_MAX)
             if r.get("error_message") else "")
    ms = _int(r.get("total_elapsed_time"))
    return {"query_id": _text(r.get("query_id"), 64), "status": _text(r.get("execution_status"), 40).lower(),
            "query_type": _text(r.get("query_type"), 60), "text": text, "text_cut": cut,
            "error_code": _text(r.get("error_code"), 12), "error": error,
            "warehouse": _text(r.get("warehouse_name")), "warehouse_size": _text(r.get("warehouse_size"), 40),
            "role": _text(r.get("role_name")), "database": _text(r.get("database_name")),
            "schema": _text(r.get("schema_name")), "started": _when(r.get("start_time")),
            "ended": _when(r.get("end_time")), "duration_ms": ms, "rows": _int(r.get("rows_produced")),
            "bytes_scanned": _int(r.get("bytes_scanned"))}


def query_history(connection: dict[str, Any], authorization: Any, *, warehouse: str = "", hours: int = HOURS,
                  limit: int = ROWS, text_chars: int = TEXT, client: Client | None = None, http: Any = None,
                  sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """The signed-in user's queries that ended in the last ``hours`` (or still run), newest first, at most
    ``limit``. Runs on ``warehouse`` (or the connection's), so it needs an authorization for it."""
    hours = _bounded(hours, 1, MAX_HOURS, "The time window in hours")
    limit = _bounded(limit, 1, MAX_ROWS, "The number of queries")
    text_chars = _bounded(text_chars, 1, TEXT_MAX, "The characters of query text")
    warehouse = str(warehouse or connection.get("warehouse") or "")
    if not warehouse:
        raise Unsupported("Query history runs on a warehouse, and this connection has none. "
                          "Pick a warehouse for this Snowflake connection in Settings > Connections.")
    require(SNOWFLAKE_SQL, target_for(connection, warehouse), authorization)
    database = str(connection.get("database") or "")
    if not database:
        raise Unsupported("Query history is read through a database's INFORMATION_SCHEMA, and this connection "
                          "sets none. Set a database for this Snowflake connection in Settings > Connections.")
    client = client or Client(provider_for(connection), http=http, sleep=sleep)
    answer = client.statement(_SQL.format(fetch=_FETCH, hours=hours, limit=limit),
                              role=str(connection.get("role") or ""), database=database, warehouse=warehouse,
                              timeout=STATEMENT_SECONDS, max_wait=STATEMENT_SECONDS + 30)
    own = str(answer.get("statementHandle") or "")
    found = [r for r in read_rows(client, answer, limit + 1) if not own or r.get("query_id") != own]
    return {"schema": SCHEMA, "account": str(connection.get("account") or ""), "warehouse": warehouse,
            "hours": hours, "limit": limit, "queries": [_row(r, text_chars) for r in found[:limit]],
            "note": f"Read on warehouse {warehouse}, which uses Snowflake credits while it runs."}
