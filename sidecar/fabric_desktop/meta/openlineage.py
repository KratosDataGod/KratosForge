"""OpenLineage run events for a metadata project's runs, so DataHub, OpenMetadata or Marquez can read its lineage.

One RunEvent (OpenLineage 2-0-2) per entity, for that entity's last run in an environment: the job is the
entity, the output is its table with a schema facet and a column lineage facet built from ``lineage.py``,
and the inputs are the entities it reads (or its files, landing folder, table or link). Only runs that were
recorded are exported: an entity that hasn't run, or whose last run neither finished nor failed, is listed
in ``left_out`` with the reason, never made up.

Events carry names and how they link: no data rows, no run messages, no paths on this PC.
"""

from __future__ import annotations

import datetime as dt
import ntpath
import re
import posixpath
import uuid
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from .. import __version__
from .. import redact
from ..onelake import workspace_root
from . import lineage
from .project import Project

PRODUCER = "https://github.com/KratosDataGod/KratosForge"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
SCHEMA_FACET = "https://openlineage.io/spec/facets/1-2-0/SchemaDatasetFacet.json#/$defs/SchemaDatasetFacet"
COLUMN_FACET = "https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json#/$defs/ColumnLineageDatasetFacet"
JOB_TYPE_FACET = "https://openlineage.io/spec/facets/2-0-4/JobTypeJobFacet.json#/$defs/JobTypeJobFacet"
ENGINE_FACET = "https://openlineage.io/spec/facets/1-1-1/ProcessingEngineRunFacet.json#/$defs/ProcessingEngineRunFacet"
_STATES = {"ok": "COMPLETE", "failed": "FAIL"}
# OpenLineage transformation types for each way lineage.py says a column is made.
_TRANSFORM = {"source": ("DIRECT", "IDENTITY"), "derived": ("DIRECT", "TRANSFORMATION"),
              "lookup": ("DIRECT", "TRANSFORMATION"), "key": ("DIRECT", "TRANSFORMATION")}
_STORAGE_SCHEMES = {"abfs", "abfss", "wasb", "wasbs"}
_FILE_URL = re.compile(r"(?i)^file:")
_DRIVE = re.compile(r"^[A-Za-z]:")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-#]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]+)+")
# A literal in an expression is read as a path only when it plainly is one (C:\\..., \\\\server, /home/..., ~/...)
# and has none of a pattern's special characters ('..\\d+' is a regex, not a path).
_TEXT_PATH = re.compile(r"^(?:[A-Za-z]:[\w .-]*[\\/]|\\\\|/[\w.-]+/|~[\\/]|\.\.[\\/])")
_REGEXY = re.compile(r"[*+?^$()\[\]{}|]")
# A quoted URL is read as one only when the part before its query has none of a pattern's characters
# ('https://([^/]+)/(.*)' and 'file:(\\d+)' are regexes); a query's ? is the URL's own.
_URL_REGEXY = re.compile(r"[*+^()\[\]{}|]|\$$")
_IPV6_HOST = re.compile(r"(?<=[/@])\[[0-9A-Fa-f:.]+\](?=[:/]|$)")  # http://[2001:db8::1]:80/: brackets, not a pattern
_QUOTED = re.compile(r"(['\"])(.*?)\1")
_PATH_PARAMS = re.compile(r";[^/]*")
# User names and secrets in a connection string (user=sa; User ID=a b; uid=a; Pwd={p;w}; AccountKey=...):
# a value runs to the next ; (ADO.NET and ODBC values hold spaces), or is a whole {braced} or quoted one.
# A secret's key may carry a prefix (wallet_password, oracle.net.wallet_password, private_key_pwd), so the key
# starts after anything but a letter or digit, and any key ending in password, pwd, secret, token or key counts.
_USER_KEY = re.compile(r"(?i)(?<![A-Za-z0-9])(user\s*id|user\s*name|username|user|uid"
                       r"|[\w.]{0,64}(?:password|passwd|pwd|secret|token|key|sharedaccesssignature))(\s*=\s*)(?:\{(?:[^}]|\}\})*\}|\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'|[^;\r\n]+)")
# jdbc:oracle:thin:scott/tiger@host:1521:orcl carries the user and password before the @.
_ORACLE_USER = re.compile(r"(?i)(\bjdbc:oracle:\w+:)[^@\s;,]+@")
# The URL inside a jdbc: one (jdbc:postgresql://...) has user info of its own, before an @.
_INNER_USERINFO = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*://)[^/?#@]*@")
_DRIVER_URL = re.compile(r"(?i)^(?:jdbc|odbc):")
_ID_NAMESPACE = uuid.UUID("6b0f1a52-6f1d-4d8e-9b0e-2f1c9f6c4a11")


def _facet(url: str, **body: Any) -> dict[str, Any]:
    return {"_producer": PRODUCER, "_schemaURL": url, **body}


class Names:
    """Where each dataset lives, in OpenLineage's naming: OneLake paths for an environment in Fabric (so
    events line up with what Spark in Fabric reports), and the project and environment for one on this PC."""

    def __init__(self, project: Project, env: str, workspace_id: str | None):
        self.project, self.env, self.workspace_id = project, env, workspace_id
        info = getattr(project, "info", None)
        self.project_name = (info.get("name") if isinstance(info, dict) else None) or project.root.name

    @property
    def namespace(self) -> str:
        if self.workspace_id:
            return workspace_root(self.workspace_id)
        return f"kratosforge://{self.project_name}/{self.env}"

    def table(self, entity: str) -> tuple[str, str]:
        e = self.project.entities[entity]
        lakehouse = self.project.layer(e.layer).lakehouse
        if self.workspace_id:
            return self.namespace, f"{lakehouse}.Lakehouse/Tables/{e.schema}/{e.table}"
        return self.namespace, f"{lakehouse}.{e.schema}.{e.table}"

    def origin(self, kind: str, value: Any) -> tuple[str, str]:
        """A source that isn't an entity: landing files, a table, a link. Never with credentials or a path on this PC."""
        return f"kratosforge://{self.project_name}/{kind}", safe_origin(value)

    @property
    def job_namespace(self) -> str:
        return f"kratosforge://{self.project_name}"


def _on_this_pc(path: str) -> str:
    return "<this PC>/" + _EMAIL.sub("[email]", ntpath.basename(path.replace("/", "\\").rstrip("\\")))


def _is_local_path(text: str) -> bool:
    """A path that names a place on this PC: absolute, home-relative, drive-relative (C:data), or one that
    climbs out of the project (..). A plain relative path (landing/orders/*.csv) stays inside the project."""
    parts = re.split(r"[\\/]+", text)
    return (ntpath.isabs(text) or posixpath.isabs(text) or text.startswith("~") or bool(_DRIVE.match(text))
            or ".." in parts)


def _clean_one(text: str) -> str:
    text = _ORACLE_USER.sub(r"\1" + redact.REMOVED + "@", text.strip())
    if _FILE_URL.match(text):  # file:///C:/Users/... is a path on this PC, in any case; no query or fragment
        return _on_this_pc(re.split(r"[?#]", re.sub(r"(?i)^file:/*", "", text), maxsplit=1)[0])
    if _DRIVER_URL.match(text) and "://" not in text:
        # jdbc:oracle:thin:@//h:1521/svc?wallet_password=...: not a URL urlsplit reads, but its query is one
        text = re.split(r"[?#]", text, maxsplit=1)[0]
    if "://" in text:
        try:
            u = urlsplit(text)
            # abfss://<container>@<account>: the part before @ is the container, not a user name
            container = f"{u.username}@" if u.username and u.scheme.lower() in _STORAGE_SCHEMES and not u.password else ""
            host = u.hostname or ""
            if ":" in host:  # an IPv6 address goes back in brackets
                host = f"[{host}]"
            try:
                port = f":{u.port}" if u.port is not None else ""
            except ValueError:  # host:1433;database=x: not a port urlsplit can read, so leave it off
                port = ""
            # ;jsessionid=... and other path parameters carry session ids, never where the data is
            path = _PATH_PARAMS.sub("", u.path) if u.scheme.lower() in ("http", "https") else u.path
            path = _INNER_USERINFO.sub(r"\1", path)
            url = urlunsplit((u.scheme, container + host + port, _EMAIL.sub("[email]", path), "", ""))
            return _USER_KEY.sub(r"\1\2" + redact.REMOVED, url)  # jdbc:sqlserver://host;user=sa
        except ValueError:
            return redact.REMOVED
    if _is_local_path(text):
        return _on_this_pc(text)
    return _EMAIL.sub("[email]", _USER_KEY.sub(r"\1\2" + redact.REMOVED, text))


def safe_origin(value: Any) -> str:
    """A source's name as it can leave the PC: a URL keeps its scheme, host, port and path (no user, password,
    query, ;path parameters or fragment, where a SAS token, key or session id would sit); a path on this PC, or a
    file: URL, keeps only its last part; a connection string loses its user name, password, account key and SAS
    ({braced} and quoted ones whole); anything credential-shaped that is left is removed. A list is cleaned item
    by item."""
    items = value if isinstance(value, (list, tuple)) else [value]
    return redact.secrets(", ".join(_clean_one(str(v)) for v in items))


def _clean_literal(lit: str) -> str:
    """A quoted literal: cleaned like a source when it plainly is a path or URL, else left as it is."""
    before_query = _IPV6_HOST.sub("", re.split(r"[?#]", lit, maxsplit=1)[0])
    url = (_FILE_URL.match(lit) or "://" in before_query) and not _URL_REGEXY.search(before_query)
    looks = url or (_TEXT_PATH.match(lit) and not _REGEXY.search(lit))
    return _clean_one(lit) if looks else lit


def safe_text(text: str) -> str:
    """An expression as it can leave the PC: quoted paths keep only their last part, and secrets, e-mail
    addresses and user names are removed (redact.for_support)."""
    text = _QUOTED.sub(lambda m: m.group(1) + _clean_literal(m.group(2)) + m.group(1), text)
    return redact.for_support(text)


def _safe_column(name: str) -> str:
    """A column an expression reads, as it can leave the PC: to SQL a "C:\\Users\\..." in double quotes
    is a column name, so the names an expression gives are cleaned like its text."""
    return redact.for_support(_clean_literal(name))


def _run_uuid(run_id: str, entity: str) -> str:
    """OpenLineage wants a UUID: the run's own when it is one, else one derived from it, the same every export."""
    try:
        return str(uuid.UUID(run_id))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid5(_ID_NAMESPACE, f"{run_id}/{entity}"))


_NEVER = dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def _started(run: dict[str, Any]) -> dt.datetime:
    """When a run started, as a time (so 2026-11-18T09:00+01:00 comes before 2026-11-18T08:30Z); a start
    that can't be read counts as earliest, so a readable run is chosen over it."""
    try:
        start = dt.datetime.fromisoformat(str(run.get("started_at")))
    except ValueError:
        return _NEVER
    return start if start.tzinfo else start.replace(tzinfo=dt.timezone.utc)


def _latest(run: dict[str, Any]) -> tuple[Any, ...]:
    """How runs of one entity are ordered: by start, then (two runs that started at the same time) by
    what the event is made of, so the rows' order never changes which run is exported."""
    return (_started(run), *(str(run.get(k)) for k in ("run_id", "status", "seconds", "started_at")))


def _event_time(run: dict[str, Any]) -> str | None:
    try:
        start = dt.datetime.fromisoformat(str(run.get("started_at")))
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    seconds = run.get("seconds")
    if isinstance(seconds, (int, float)) and seconds >= 0:
        try:
            start += dt.timedelta(seconds=float(seconds))
        except OverflowError:  # an infinite or absurd duration: the run's start is still its time
            pass
    return start.isoformat()


def _dataset_key(names: Names, inp: dict[str, Any]) -> tuple[str, str] | None:
    if "entity" in inp:
        return names.table(inp["entity"]) if inp["entity"] in names.project.entities else None
    if "origin" in inp:
        return names.origin(inp["origin"], inp["value"])
    return None


def _own_sources(cols: dict[str, Any], entity: str, col: str) -> list[dict[str, Any]]:
    """The inputs from outside the entity that one of its own columns is built from, however many steps in
    (depth first, each own column once; a stack, not recursion, so a long chain of derives ends)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    todo: list[Any] = [col]
    while todo:
        item = todo.pop()
        if isinstance(item, dict):  # an input from outside the entity
            out.append(item)
            continue
        if item in seen or item not in cols:
            continue
        seen.add(item)
        todo += [i["column"] if i.get("entity") == entity else i for i in reversed(cols[item]["inputs"])]
    return out


def entity_event(project: Project, names: Names, entity: str, run: dict[str, Any]) -> dict[str, Any]:
    """The RunEvent for one entity's run."""
    e = project.entities[entity]
    cols = lineage.entity_columns(project, entity)
    types = e.columns
    inputs: dict[tuple[str, str], set[str]] = {}
    fields: dict[str, Any] = {}
    for col, rec in cols.items():
        kind = _TRANSFORM.get(rec["how"])
        in_fields = []
        for n, inp in enumerate(rec["inputs"]):
            # A column it makes from its own columns (a derive over a cleaned or renamed one) traces back
            # through them to where they start.
            ups = _own_sources(cols, entity, inp["column"]) if inp.get("entity") == entity else [inp]
            for up in ups:
                key = _dataset_key(names, up)
                if key is None or key == names.table(entity):
                    continue
                field = _safe_column(str(up["column"]))
                inputs.setdefault(key, set()).add(field)
                if not kind:
                    continue
                t, sub = kind
                if up is not inp and sub == "IDENTITY":
                    sub = "TRANSFORMATION"
                if rec["how"] == "lookup" and n > 0:  # the dimension's key comes first; the rest are what it matches on
                    t, sub = "INDIRECT", "JOIN"
                in_fields.append({"namespace": key[0], "name": key[1], "field": field,
                                  "transformations": [{"type": t, "subtype": sub, "masking": False,
                                                       **({"description": safe_text(str(rec["expr"]))} if rec.get("expr") else {})}]})
        if in_fields:  # named as readers' events name it: a column named like a path or URL is cleaned the same way
            fields.setdefault(_safe_column(str(col)), {"inputFields": []})["inputFields"] += in_fields
    out_ns, out_name = names.table(entity)
    output = {"namespace": out_ns, "name": out_name, "facets": {
        "schema": _facet(SCHEMA_FACET, fields=[{"name": _safe_column(str(c)), **({"type": str(types[c])} if c in types else {})}
                                             for c in cols]),
        **({"columnLineage": _facet(COLUMN_FACET, fields=fields)} if fields else {}),
    }}
    return {
        "eventType": _STATES[run["status"]],
        "eventTime": _event_time(run),
        "producer": PRODUCER,
        "schemaURL": SCHEMA_URL,
        "run": {"runId": _run_uuid(str(run.get("run_id") or ""), entity), "facets": {
            "processing_engine": _facet(ENGINE_FACET, version=__version__, name="KratosForge"),
        }},
        "job": {"namespace": names.job_namespace, "name": entity, "facets": {
            "jobType": _facet(JOB_TYPE_FACET, processingType="BATCH", integration="KRATOSFORGE", jobType="ENTITY"),
        }},
        "inputs": [{"namespace": ns, "name": n, "facets": {
            "schema": _facet(SCHEMA_FACET, fields=[{"name": c} for c in sorted(used)])}} for (ns, n), used in sorted(inputs.items())],
        "outputs": [output],
    }


def run_events(project: Project, runs: list[dict[str, Any]], env: str,
               workspace_id: str | None = None) -> dict[str, Any]:
    """Events for each entity's last recorded run in ``env``; ``runs`` are run_log rows, any order."""
    names = Names(project, env, workspace_id)
    last: dict[str, dict[str, Any]] = {}
    for r in runs:
        ent = r.get("entity")
        if ent in project.entities and (ent not in last or _latest(r) >= _latest(last[ent])):
            last[ent] = r
    events, left_out = [], []
    for ent in sorted(project.entities):
        run = last.get(ent)
        if run is None:
            left_out.append({"entity": ent, "why": f"hasn't run in {env.title()}"})
        elif run.get("status") not in _STATES:
            left_out.append({"entity": ent, "why": f"its last run is {run.get('status') or 'not finished'}"})
        elif _event_time(run) is None:
            left_out.append({"entity": ent, "why": "its last run has no start time"})
        else:
            events.append(entity_event(project, names, ent, run))
    return {"env": env, "namespace": names.namespace, "events": events, "left_out": left_out}
