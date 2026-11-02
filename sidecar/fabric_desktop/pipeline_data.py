"""Lookup and Get Metadata in local pipeline runs (pipelines plan P1): the runner's DataReader.

Both read a lakehouse in OneLake, named by the activity's dataset (a ``Lakehouse`` linked service
with ``workspaceId`` and ``artifactId``), and write nothing:

* **Lookup** reads a lakehouse table (``LakehouseTable`` dataset). The table is read at the version
  it had when the run first used that lakehouse, the same pin notebook steps use, and a table a
  notebook step wrote earlier in the run is read from the run's own folder. Like Fabric, it returns
  ``{"firstRow": {...}}`` with ``firstRowOnly`` (the default) or ``{"count": n, "value": [...]}``,
  at most 5,000 rows, and fails when the output is over 4 MB.
* **Get Metadata** reports on a lakehouse table (``exists``, ``itemName``, ``structure``,
  ``columnCount``) or on a file or folder under Files (``exists``, ``itemName``, ``itemType``,
  ``size``, ``lastModified``, ``childItems``). Files are read as they are now, not pinned.

Queries (``sqlReaderQuery``), other connections and other fields need a mock. A dataset that
doesn't name its workspace (Fabric's Git export leaves it out, or all zeros, for the pipeline's own
workspace) reads the run's ``workspace_id``. Only runs the person starts with lakehouse reads on get
this reader; runs external AI tools start don't, so these activities still need a mock there.
No Fabric job is started; reading OneLake uses some capacity.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import math
import threading
from typing import Any

from .orchestration.adapters import ActivityFailed
from .orchestration.definition import Activity
from .pipeline_script import LocalScripts
from .onelake import read_storage_options
from .redact import describe

LOOKUP_ROWS = 5000                 # Fabric's Lookup returns at most this many rows
LOOKUP_BYTES = 4 * 1024 * 1024     # and fails when its output is larger than this
ZERO_ID = "00000000-0000-0000-0000-000000000000"
FILE_DATASETS = ("Binary", "DelimitedText", "Parquet", "Json", "Avro", "Orc", "Xml", "Excel")
TABLE_FIELDS = ("exists", "itemName", "structure", "columnCount")
FILE_FIELDS = ("exists", "itemName", "itemType", "size", "lastModified", "childItems")


def _problem(code: str, message: str, a: Activity) -> dict[str, Any]:
    return {"code": code, "message": message, "activity": a.name, "path": a.path}


def _expr(value: Any) -> bool:
    """A value only known when the activity runs."""
    return (isinstance(value, str) and value.startswith("@") and not value.startswith("@@")) or (
        isinstance(value, dict) and value.get("type") == "Expression")


def _dict(value: Any) -> dict[str, Any]:
    """``value`` when it is a JSON object, else an empty one: pipeline JSON can hold anything."""
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dataset(settings: Any) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(dataset type, its typeProperties, the linked service's type and typeProperties as one dict)."""
    ds = _dict(settings)
    ls = _dict(ds.get("linkedService"))
    lsp = _dict(ls.get("properties"))
    service = {"type": lsp.get("type"), "name": ls.get("name"),
               **_dict(lsp.get("typeProperties"))}
    tp = _dict(ds.get("typeProperties"))
    return ds.get("type"), tp, service, ds


def _json_value(v: Any) -> Any:
    """A table value as Fabric's Lookup output gives it in JSON."""
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, bytes):
        return base64.b64encode(v).decode()
    if isinstance(v, dict):
        return {k: _json_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_value(x) for x in v]
    return v


class LocalData:
    """The DataReader for one pipeline run. ``svc`` is the engine's Service; ``notebooks`` is the
    run's LocalNotebooks when it has notebook steps, so both share the run's pins and folder."""

    def __init__(self, svc: Any, run_id: str, notebooks: Any = None, workspace_id: str | None = None,
                 lakehouse_reads: bool = True, sql_projects: dict[str, str] | None = None):
        self.svc, self.run_id, self.notebooks = svc, run_id, notebooks
        self.workspace_id = workspace_id or None
        self.lakehouse_reads = lakehouse_reads
        self.scripts = LocalScripts(self, sql_projects)
        self._lock = threading.Lock()
        self._lakes: dict[str, dict[str, Any]] = {}

    # ---- before the run

    def check(self, a: Activity) -> list[dict[str, Any]]:
        if a.type == "Script":
            return self.scripts.check(a)
        if not self.lakehouse_reads:
            return [_problem("data_needs_engine", f"{a.name} ({a.type}) reads a lakehouse. It runs when you start "
                             "the run with lakehouse reads on (fabric-desktop pipeline run --read-lakehouse); give "
                             "a mock for it here.", a)]
        tp = a.type_properties
        kind, ds_tp, service, _ = _dataset(tp.get("datasetSettings"))
        what = "Lookup" if a.type == "Lookup" else "Get Metadata"
        if not _expr(service.get("type")) and service.get("type") != "Lakehouse":
            return [_problem("data_not_lakehouse", f"{a.name} reads {service.get('type') or 'a connection'}; {what} "
                             "runs on this PC only on a lakehouse. Give a mock for it.", a)]
        ws = service.get("workspaceId")
        if not _expr(ws) and (not ws or ws == ZERO_ID) and not self.workspace_id:
            return [_problem("data_workspace_unknown", f"{a.name}'s lakehouse doesn't name its workspace (it is the "
                             "pipeline's own). Say which workspace the pipeline is in (workspace_id), or give a mock.",
                             a)]
        if a.type == "Lookup":
            source = _dict(tp.get("source"))
            if any(k in source for k in ("sqlReaderQuery", "query", "sqlReaderStoredProcedureName")):
                return [_problem("data_query", f"{a.name} runs a query; Lookup on this PC reads a whole table. Give "
                                 "a mock for it.", a)]
            if not _expr(kind) and kind != "LakehouseTable":
                return [_problem("data_not_table", f"{a.name} reads a {kind or 'dataset'} dataset; Lookup on this PC "
                                 "reads lakehouse tables only. Give a mock for it.", a)]
            return []
        fields = tp.get("fieldList")
        if not _expr(kind) and kind != "LakehouseTable" and kind not in FILE_DATASETS:
            return [_problem("data_not_table", f"{a.name} reads a {kind or 'dataset'} dataset, which Get Metadata on "
                             "this PC can't read. Give a mock for it.", a)]
        if isinstance(fields, list) and not _expr(kind):
            allowed = TABLE_FIELDS if kind == "LakehouseTable" else FILE_FIELDS
            other = [f for f in fields if isinstance(f, str) and not _expr(f) and f not in allowed]
            if other:
                return [_problem("data_field", f"{a.name} asks for {', '.join(other)}; on this PC Get Metadata gives "
                                 f"{', '.join(allowed)} for this dataset. Give a mock for it.", a)]
        return []

    def lakehouses(self, acts: list[Activity]) -> bool:
        """Whether any activity reads a lakehouse, so the engine can sign in before the run."""
        return any(a.type in ("Lookup", "GetMetadata") or (a.type == "Script" and self.scripts.on_lakehouse(a))
                   for a in acts)

    # ---- the lakehouse an activity names

    def _ref(self, a: Activity, settings: Any) -> tuple[str, dict[str, Any], dict[str, str]]:
        kind, ds_tp, service, _ = _dataset(settings)
        if service.get("type") != "Lakehouse":
            raise ActivityFailed(f"{a.name} reads {service.get('type') or 'a connection'}, not a lakehouse.",
                                 "LocalDataUnsupported")
        ws = service.get("workspaceId")
        ws = ws if isinstance(ws, str) and ws and ws != ZERO_ID else self.workspace_id
        lh = service.get("artifactId")
        if not ws or not isinstance(lh, str) or not lh:
            raise ActivityFailed(f"{a.name}'s lakehouse needs its workspace and artifactId.", "LocalDataUnsupported")
        return kind, ds_tp, {"workspace_id": ws, "lakehouse_id": lh, "name": service.get("name") or lh}

    def _lake(self, ref: dict[str, str]) -> dict[str, Any]:
        if self.notebooks is not None:
            return self.notebooks._lake(ref)
        with self._lock:
            lake = self._lakes.get(ref["lakehouse_id"])
            if lake is None:
                schemas = self.svc._schemas_enabled(ref["workspace_id"], ref["lakehouse_id"])
                lake = {"ref": ref, "pins": self.svc._proof_tables(ref).versions(), "flat": not schemas,
                        "written": set()}
                self._lakes[ref["lakehouse_id"]] = lake
            return lake

    def _table(self, a: Activity, ref: dict[str, str], ds_tp: dict[str, Any]) -> tuple[str, int | None, str] | None:
        """(uri, version, name) of the table to read, or None when it doesn't exist: the run's own
        output when a notebook step wrote it, else the pinned version."""
        table = ds_tp.get("table")
        schema = ds_tp.get("schema") or "dbo"
        if not isinstance(table, str) or not table or not isinstance(schema, str):
            raise ActivityFailed(f"{a.name}'s dataset needs a table name.", "LocalDataUnsupported")
        lake = self._lake(ref)
        root, _ = self.svc._proof_location(ref)
        key = f"{schema.lower()}.{table.lower()}"
        with getattr(self.notebooks, "_lock", self._lock):
            written = key in lake["written"]
        if written:
            folder = getattr(self.notebooks, "folder", "")
            return f"{root}/{folder}/pc/tables/{schema.lower()}/{table.lower()}", None, f"{schema}.{table}"
        for name, version in lake["pins"].items():
            if name.lower() == key:
                s, t = name.split(".", 1)
                uri = f"{root}/Tables/{t}" if lake["flat"] else f"{root}/Tables/{s}/{t}"
                return uri, version, name
        return None

    # ---- the activities

    def lookup(self, a: Activity, props: dict[str, Any]) -> dict[str, Any]:
        return self._read(a, self._lookup, props)

    def metadata(self, a: Activity, props: dict[str, Any]) -> dict[str, Any]:
        return self._read(a, self._metadata, props)

    def script(self, a: Activity, props: dict[str, Any], cancelled: Any, deadline: float | None) -> dict[str, Any]:
        return self.scripts.run(a, props, cancelled, deadline)

    @staticmethod
    def _read(a: Activity, read: Any, props: dict[str, Any]) -> dict[str, Any]:
        """A read that fails (sign-in, network, a damaged table) fails the activity, secrets removed."""
        try:
            return read(a, props)
        except ActivityFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - the activity fails with the reason; the run goes on
            raise ActivityFailed(f"{a.name} couldn't read its lakehouse: {describe(exc)}", "LocalDataReadFailed") from None

    def _lookup(self, a: Activity, props: dict[str, Any]) -> dict[str, Any]:
        _, ds_tp, ref = self._ref(a, props.get("datasetSettings"))
        found = self._table(a, ref, ds_tp)
        if found is None:
            raise ActivityFailed(f"{a.name}: table {ds_tp.get('schema') or 'dbo'}.{ds_tp.get('table')} isn't in "
                                 f"{ref['name']}.", "LocalDataNotFound")
        uri, version, _ = found
        first = props.get("firstRowOnly", True) is not False
        _, options = self.svc._proof_location(ref)
        from deltalake import DeltaTable
        data = DeltaTable(uri, version=version, storage_options=_short(options())).to_pyarrow_dataset()
        rows = [{k: _json_value(v) for k, v in r.items()}
                for r in data.head(1 if first else LOOKUP_ROWS).to_pylist()]
        out: dict[str, Any] = ({"firstRow": rows[0]} if rows else {}) if first else {"count": len(rows), "value": rows}
        if len(json.dumps(out, default=str).encode()) > LOOKUP_BYTES:
            raise ActivityFailed(f"{a.name}: the Lookup output is larger than 4 MB, which Fabric doesn't allow.",
                                 "LocalLookupTooLarge")
        return out

    def _metadata(self, a: Activity, props: dict[str, Any]) -> dict[str, Any]:
        kind, ds_tp, ref = self._ref(a, props.get("datasetSettings"))
        fields = _list(props.get("fieldList"))
        allowed = TABLE_FIELDS if kind == "LakehouseTable" else FILE_FIELDS
        other = [f for f in fields if f not in allowed]
        if other:
            raise ActivityFailed(f"{a.name}: Get Metadata on this PC doesn't give {', '.join(map(str, other))} for this "
                                 "dataset.", "LocalDataUnsupported")
        if kind == "LakehouseTable":
            return self._table_metadata(a, ref, ds_tp, fields)
        if kind in FILE_DATASETS:
            return self._file_metadata(a, ref, ds_tp, fields)
        raise ActivityFailed(f"{a.name} reads a {kind} dataset, which Get Metadata on this PC can't read.",
                             "LocalDataUnsupported")

    def _table_metadata(self, a: Activity, ref: dict[str, str], ds_tp: dict[str, Any],
                        fields: list[str]) -> dict[str, Any]:
        found = self._table(a, ref, ds_tp)
        if found is None:
            if "exists" not in fields:
                raise ActivityFailed(f"{a.name}: table {ds_tp.get('schema') or 'dbo'}.{ds_tp.get('table')} isn't in "
                                     f"{ref['name']}.", "LocalDataNotFound")
            return {"exists": False}
        uri, version, name = found
        out: dict[str, Any] = {}
        if "exists" in fields:
            out["exists"] = True
        if "itemName" in fields:
            out["itemName"] = name.split(".", 1)[1]
        if "structure" in fields or "columnCount" in fields:
            _, options = self.svc._proof_location(ref)
            from deltalake import DeltaTable
            schema = DeltaTable(uri, version=version, storage_options=_short(options())).to_pyarrow_dataset().schema
            if "structure" in fields:
                out["structure"] = [{"name": f.name, "type": _type_name(f.type)} for f in schema]
            if "columnCount" in fields:
                out["columnCount"] = len(schema)
        return out

    def _file_metadata(self, a: Activity, ref: dict[str, str], ds_tp: dict[str, Any],
                       fields: list[str]) -> dict[str, Any]:
        loc = _dict(ds_tp.get("location"))
        folder = str(loc.get("folderPath") or "").strip("/")
        name = str(loc.get("fileName") or "").strip("/")
        if ".." in folder.split("/") or ".." in name.split("/"):
            raise ActivityFailed(f"{a.name}: the path can't go up a folder.", "LocalDataUnsupported")
        parent = "/".join(p for p in ("Files", folder) if p)
        target = f"{parent}/{name}" if name else parent
        item = self.svc._pipeline_file_info(ref, target)
        if item is None:
            if "exists" not in fields:
                raise ActivityFailed(f"{a.name}: {target} isn't in {ref['name']}.", "LocalDataNotFound")
            return {"exists": False}
        out: dict[str, Any] = {}
        if "exists" in fields:
            out["exists"] = True
        if "itemName" in fields:
            out["itemName"] = target.rsplit("/", 1)[-1]
        if "itemType" in fields:
            out["itemType"] = "Folder" if item["is_dir"] else "File"
        if "size" in fields and not item["is_dir"]:
            out["size"] = item["size"]
        if "lastModified" in fields and item.get("modified"):
            out["lastModified"] = item["modified"]
        if "childItems" in fields:
            if not item["is_dir"]:
                raise ActivityFailed(f"{a.name}: childItems needs a folder; {target} is a file.", "LocalDataUnsupported")
            out["childItems"] = [{"name": c["name"], "type": "Folder" if c["is_dir"] else "File"}
                                 for c in self.svc._pipeline_list_files(ref, target)]
        return out


def _type_name(t: Any) -> str:
    """A column type as Get Metadata's structure names it."""
    import pyarrow as pa
    for check, name in ((pa.types.is_boolean, "Boolean"), (pa.types.is_int8, "SByte"), (pa.types.is_int16, "Int16"),
                        (pa.types.is_int32, "Int32"), (pa.types.is_int64, "Int64"), (pa.types.is_float32, "Single"),
                        (pa.types.is_float64, "Double"), (pa.types.is_decimal, "Decimal"),
                        (pa.types.is_timestamp, "DateTime"), (pa.types.is_date, "Date"),
                        (pa.types.is_binary, "Byte[]"), (pa.types.is_large_binary, "Byte[]"),
                        (pa.types.is_string, "String"), (pa.types.is_large_string, "String")):
        if check(t):
            return name
    return str(t)


def _short(options: dict[str, str]) -> dict[str, str]:
    """OneLake's short read limits for a Lookup or Get Metadata (#1303's read_storage_options): an
    offline PC fails the activity within about a minute instead of retrying for up to 30 (#1448)."""
    return read_storage_options(options["bearer_token"]) if "bearer_token" in options else options
