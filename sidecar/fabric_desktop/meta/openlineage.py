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
from . import lineage
from .project import Project

PRODUCER = "https://github.com/KratosDataGod/KratosForge"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
SCHEMA_FACET = "https://openlineage.io/spec/facets/1-2-0/SchemaDatasetFacet.json#/$defs/SchemaDatasetFacet"
COLUMN_FACET = "https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json#/$defs/ColumnLineageDatasetFacet"
JOB_TYPE_FACET = "https://openlineage.io/spec/facets/2-0-4/JobTypeJobFacet.json#/$defs/JobTypeJobFacet"
ENGINE_FACET = "https://openlineage.io/spec/facets/1-1-1/ProcessingEngineRunFacet.json#/$defs/ProcessingEngineRunFacet"
ONELAKE = "onelake.dfs.fabric.microsoft.com"
_STATES = {"ok": "COMPLETE", "failed": "FAIL"}
# OpenLineage transformation types for each way lineage.py says a column is made.
_TRANSFORM = {"source": ("DIRECT", "IDENTITY"), "derived": ("DIRECT", "TRANSFORMATION"),
              "lookup": ("DIRECT", "TRANSFORMATION"), "key": ("DIRECT", "TRANSFORMATION")}
_STORAGE_SCHEMES = {"abfs", "abfss", "wasb", "wasbs"}
_FILE_URL = re.compile(r"(?i)^file:")
_DRIVE = re.compile(r"^[A-Za-z]:")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-#]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
# A literal in an expression is read as a path only when it plainly is one (C:\\..., \\\\server, /home/..., ~/...)
# and has none of a pattern's special characters ('..\\d+' is a regex, not a path).
_TEXT_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/[\w.-]+/|~[\\/])")
_REGEXY = re.compile(r"[*+?^$()\[\]{}|]")
_QUOTED = re.compile(r"(['\"])(.*?)\1")
# User names in a connection string (user=sa; User ID=a; uid=a; username=a).
_USER_KEY = re.compile(r"(?i)\b(user\s*id|user\s*name|username|user|uid)(\s*=\s*)[^;,\s]+")
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
            return f"abfss://{self.workspace_id}@{ONELAKE}"
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
    return "<this PC>/" + ntpath.basename(path.replace("/", "\\").rstrip("\\"))


def _is_local_path(text: str) -> bool:
    """A path that names a place on this PC: absolute, home-relative, drive-relative (C:data), or one that
    climbs out of the project (..). A plain relative path (landing/orders/*.csv) stays inside the project."""
    parts = re.split(r"[\\/]+", text)
    return (ntpath.isabs(text) or posixpath.isabs(text) or text.startswith("~") or bool(_DRIVE.match(text))
            or ".." in parts)


def _clean_one(text: str) -> str:
    text = text.strip()
    if _FILE_URL.match(text):  # file:///C:/Users/... is a path on this PC, in any case
        return _on_this_pc(re.sub(r"(?i)^file:/*", "", text))
    if "://" in text:
        try:
            u = urlsplit(text)
            # abfss://<container>@<account>: the part before @ is the container, not a user name
            container = f"{u.username}@" if u.username and u.scheme.lower() in _STORAGE_SCHEMES and not u.password else ""
            url = urlunsplit((u.scheme, container + (u.hostname or ""), _EMAIL.sub("[email]", u.path), "", ""))
            return _USER_KEY.sub(r"\1\2" + redact.REMOVED, url)  # jdbc:sqlserver://host;user=sa
        except ValueError:
            return redact.REMOVED
    if _is_local_path(text):
        return _on_this_pc(text)
    return _EMAIL.sub("[email]", _USER_KEY.sub(r"\1\2" + redact.REMOVED, text))


def safe_origin(value: Any) -> str:
    """A source's name as it can leave the PC: a URL keeps its scheme, host and path (no user, password, query or
    fragment, where a SAS token or key would sit); a path on this PC, or a file: URL, keeps only its last part;
    a connection string loses its user name; anything credential-shaped that is left is removed. A list is
    cleaned item by item."""
    items = value if isinstance(value, (list, tuple)) else [value]
    return redact.secrets(", ".join(_clean_one(str(v)) for v in items))


def safe_text(text: str) -> str:
    """An expression as it can leave the PC: quoted paths keep only their last part, and secrets, e-mail
    addresses and user names are removed (redact.for_support)."""
    def one(m: re.Match[str]) -> str:
        lit = m.group(2)
        looks = _FILE_URL.match(lit) or "://" in lit or (_TEXT_PATH.match(lit) and not _REGEXY.search(lit))
        return m.group(1) + (_clean_one(lit) if looks else lit) + m.group(1)

    text = _QUOTED.sub(one, text)
    return redact.for_support(text)


def _run_uuid(run_id: str, entity: str) -> str:
    """OpenLineage wants a UUID: the run's own when it is one, else one derived from it, the same every export."""
    try:
        return str(uuid.UUID(run_id))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid5(_ID_NAMESPACE, f"{run_id}/{entity}"))


def _event_time(run: dict[str, Any]) -> str | None:
    try:
        start = dt.datetime.fromisoformat(str(run.get("started_at")))
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    seconds = run.get("seconds")
    if isinstance(seconds, (int, float)) and seconds >= 0:
        start += dt.timedelta(seconds=float(seconds))
    return start.isoformat()


def _dataset_key(names: Names, inp: dict[str, Any]) -> tuple[str, str] | None:
    if "entity" in inp:
        return names.table(inp["entity"]) if inp["entity"] in names.project.entities else None
    if "origin" in inp:
        return names.origin(inp["origin"], inp["value"])
    return None


def _own_sources(cols: dict[str, Any], entity: str, col: str, seen: set[str]) -> list[dict[str, Any]]:
    """The inputs from outside the entity that one of its own columns is built from, however many steps in."""
    if col in seen or col not in cols:
        return []
    seen.add(col)
    out: list[dict[str, Any]] = []
    for i in cols[col]["inputs"]:
        out += _own_sources(cols, entity, i["column"], seen) if i.get("entity") == entity else [i]
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
            ups = _own_sources(cols, entity, inp["column"], set()) if inp.get("entity") == entity else [inp]
            for up in ups:
                key = _dataset_key(names, up)
                if key is None or key == names.table(entity):
                    continue
                inputs.setdefault(key, set()).add(up["column"])
                if not kind:
                    continue
                t, sub = kind
                if up is not inp and sub == "IDENTITY":
                    sub = "TRANSFORMATION"
                if rec["how"] == "lookup" and n > 0:  # the dimension's key comes first; the rest are what it matches on
                    t, sub = "INDIRECT", "JOIN"
                in_fields.append({"namespace": key[0], "name": key[1], "field": up["column"],
                                  "transformations": [{"type": t, "subtype": sub, "masking": False,
                                                       **({"description": safe_text(str(rec["expr"]))} if rec.get("expr") else {})}]})
        if in_fields:
            fields[col] = {"inputFields": in_fields}
    out_ns, out_name = names.table(entity)
    output = {"namespace": out_ns, "name": out_name, "facets": {
        "schema": _facet(SCHEMA_FACET, fields=[{"name": c, **({"type": str(types[c])} if c in types else {})} for c in cols]),
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
        if ent in project.entities and str(r.get("started_at") or "") >= str((last.get(ent) or {}).get("started_at") or ""):
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
