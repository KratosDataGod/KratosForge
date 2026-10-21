"""What a SQL query page run is allowed to do (SQL-003).

Every statement is put in one of four classes before anything runs:

    read_only        SELECT, WITH ... SELECT, UNION, VALUES: reads the lakehouse's tables.
    local_mutation   changes only this run's own DuckDB session on this PC: temp views, tables
                     created and filled in the run, SET, DROP of something the run made. Nothing
                     outside the run changes, but it isn't a plain read, so the page asks first.
    onelake_write    would change a lakehouse table (INSERT, UPDATE, DELETE, MERGE, DROP, ALTER or
                     TRUNCATE of a table the run didn't create). The SQL page never does this:
                     Save as table, notebooks and the metadata framework write to OneLake, each
                     with its own confirmation.
    fabric_mutation  would change something outside the run: Fabric itself (GRANT, OPTIMIZE, ...),
                     files on this PC (COPY, EXPORT) or extensions (INSTALL, LOAD, ATTACH). The SQL
                     page never runs SQL in Fabric, so this is always refused.

On the local target (a local data project, KF-DE-027) there is a fifth class:

    local_project_write  changes the project's own tables and views (CREATE TABLE, INSERT, UPDATE,
                         DELETE, ALTER, CREATE OR REPLACE, INSERT OVERWRITE, DROP). Allowed without
                         asking, because the project's database is the person's own on this PC. A
                         statement that removes a table or every row (DROP, TRUNCATE, DELETE or
                         UPDATE without WHERE, CREATE OR REPLACE, INSERT OVERWRITE) is marked
                         ``confirm``: it runs only when the run is started again with confirm.
                         Reading files or other databases (read_csv, FROM 'file', glob, ...) and
                         SET/RESET/PRAGMA/LOAD/INSTALL are refused on the local target.

The lakehouse target never allows local_project_write (its writes stay onelake_write), and the
local target never allows fabric_mutation (COPY, ATTACH, INSTALL, ...), so neither target's
refusals depend on the other.

A run states the classes it allows (read_only, or read_only and local_mutation after the person
confirmed); anything beyond them is refused before any statement runs. On top of that the run's
DuckDB has external access turned off once the lakehouse tables are bound, so no statement can
read or write local files, load extensions or reach the network (sql_worker.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlglot import exp

CLASSES = ("read_only", "local_mutation", "local_project_write", "onelake_write", "fabric_mutation")
TARGETS = ("lakehouse", "local")
LABELS = {
    "read_only": "reads tables",
    "local_mutation": "changes this run's own DuckDB session on this PC",
    "local_project_write": "changes the local data project's own tables",
    "onelake_write": "would change a lakehouse table in OneLake",
    "fabric_mutation": "would change something outside this run: Fabric, files on this PC or extensions",
}
_READS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.Values)
_WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Alter, exp.TruncateTable)
# Commands that touch only the session (SET, CACHE TABLE, ...); anything else unrecognized is refused.
_SESSION_COMMANDS = {"SET", "RESET", "CACHE", "UNCACHE", "PRAGMA"}
_FABRIC_COMMANDS = {"GRANT", "REVOKE", "DENY", "OPTIMIZE", "VACUUM", "REFRESH", "MSCK", "RESTORE", "COPY", "ATTACH",
                    "DETACH", "INSTALL", "LOAD", "EXPORT", "IMPORT"}


@dataclass(frozen=True)
class Verdict:
    statement: int  # 1-based
    kind: str  # one of CLASSES
    reason: str
    confirm: bool = False  # local target: removes a table or every row, so it runs only when confirmed


def classify(statements: list[exp.Expression], target: str = "lakehouse") -> list[Verdict]:
    """The class of each statement, in order. Names created earlier in the same run are local.

    ``target="local"``: the statements run against a local data project, whose own tables they may
    change (local_project_write)."""
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}, got {target!r}")
    made: set[str] = set()
    out = []
    for i, st in enumerate(statements, 1):
        local = _local(st, made) if target == "local" else None
        kind, reason, confirm = local or (*_one(st, made), False)
        out.append(Verdict(i, kind, reason, confirm))
    return out


def strongest(verdicts: list[Verdict]) -> str:
    return max((v.kind for v in verdicts), key=CLASSES.index, default="read_only")


def refused(verdicts: list[Verdict], allow: tuple[str, ...], confirmed: bool = False) -> Verdict | None:
    """The first statement whose class isn't allowed, or that needs confirming and wasn't, or None."""
    return next((v for v in verdicts if v.kind not in allow or (v.confirm and not confirmed)), None)


_LOCAL_CREATES = {"TABLE", "VIEW", "SCHEMA", "DATABASE", "INDEX", "SEQUENCE", "MACRO", "FUNCTION", "TYPE"}
# Table functions that read files or other databases (DuckDB's read_csv, parquet_scan, glob, ...).
_FILE_FUNCS = re.compile(r"^(read_?\w*|parquet_\w+|sniff_csv|glob|st_read\w*|delta_scan|iceberg_\w+|sqlite_\w+"
                         r"|postgres_\w+|mysql_\w+|query_table"
                         # SQL text run inside the statement, which this check can't see into
                         r"|query|json_execute_serialized_sql|json_deserialize_sql"
                         # settings and environment that show this PC's paths
                         r"|current_setting|duckdb_settings|getenv)$")
_SPARK_FILE_FORMATS = {"csv", "parquet", "json", "text", "delta", "orc", "avro", "binaryfile"}
# Settings of the project's database (profiling_output, extension and file access, ...) stay as the engine set them.
_LOCAL_SETTINGS_COMMANDS = {"SET", "RESET", "PRAGMA", "LOAD", "INSTALL"}


def _reads_files(st: exp.Expression) -> str:
    """What in the statement reads a file or another database on this PC, runs SQL text this check
    can't see into, or shows the PC's settings and paths, or ''."""
    for node in st.walk():
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.key).lower()
            if _FILE_FUNCS.match(name):
                return name
        if isinstance(node, exp.Table):
            ident = node.this
            if isinstance(ident, exp.Identifier) and ident.quoted and any(c in ident.name for c in "/\\:."):
                return f"'{ident.name}'"  # FROM 'file.csv'
            if node.db and node.db.lower() in _SPARK_FILE_FORMATS:
                return f"{node.db}.`...`"  # Spark's SELECT * FROM csv.`path`
    return ""


def _local(st: exp.Expression, made: set[str]) -> tuple[str, str, bool] | None:
    """The class of a statement that changes a local data project's own tables, or None for one
    classed as on a lakehouse (reads, temporary objects, SET, COPY, ATTACH, ...).

    DROP, TRUNCATE, CREATE OR REPLACE, INSERT OVERWRITE, and DELETE or UPDATE without WHERE need
    confirming, unless the table was made earlier in the same run, so nothing that was there before
    is lost. Reading files or other databases on this PC, and changing the database's settings, are
    refused: a query reads and writes only the project's own tables."""
    source = _reads_files(st)
    if source:
        return ("fabric_mutation", f"{source} reads a file on this PC outside the project; "
                "add the file to the project first", False)
    if isinstance(st, (exp.Set, exp.Pragma)) or (
            isinstance(st, exp.Command) and str(st.this or "").upper() in _LOCAL_SETTINGS_COMMANDS):
        return "fabric_mutation", "changes the project database's settings, which a query can't do", False
    if isinstance(st, exp.Create):
        kind = str(st.args.get("kind") or "").upper()
        if kind not in _LOCAL_CREATES or _temporary(st):
            return None
        name = _name(st.this)
        if st.args.get("replace") and kind in ("TABLE", "VIEW") and name not in made:
            made.add(name)
            return ("local_project_write",
                    f"CREATE OR REPLACE {kind} {name} replaces it in the project if it exists", True)
        made.add(name)
        return "local_project_write", f"creates {kind.lower()} {name} in the project", False
    if not isinstance(st, _WRITES):
        return None
    name = _name(st)
    ours = bool(name) and name in made
    label = name or "a table"
    if isinstance(st, exp.Drop):
        made.discard(name)
        what = str(st.args.get("kind") or "TABLE").upper()
        return "local_project_write", f"DROP {what} {label} removes it from the project", not ours
    if isinstance(st, exp.TruncateTable):
        return "local_project_write", f"TRUNCATE {label} deletes every row", not ours
    if isinstance(st, (exp.Delete, exp.Update)) and st.args.get("where") is None:
        verb = type(st).__name__.upper()
        return "local_project_write", f"{verb} on {label} has no WHERE, so it changes every row", not ours
    if isinstance(st, exp.Insert) and st.args.get("overwrite"):
        return "local_project_write", f"INSERT OVERWRITE replaces every row of {label}", not ours
    verb = type(st).__name__.upper()
    return "local_project_write", f"{verb} on {label} changes the project's own table", False


def _name(node: exp.Expression | None) -> str:
    table = node if isinstance(node, exp.Table) else (node.find(exp.Table) if node is not None else None)
    if table is None:
        return ""
    return ".".join(p for p in (table.catalog, table.db, table.name) if p).lower()


def _one(st: exp.Expression, made: set[str]) -> tuple[str, str]:
    if isinstance(st, _READS) and not any(isinstance(n, _WRITES + (exp.Create, exp.Command)) for n in st.walk()):
        return "read_only", "reads tables"
    if isinstance(st, exp.Create):
        kind = str(st.args.get("kind") or "").upper()
        name = _name(st.this)
        if kind in ("TABLE", "VIEW"):
            if st.args.get("replace") and name not in made and not _temporary(st):
                # CREATE OR REPLACE TABLE x would replace a lakehouse table in Fabric Spark
                return "onelake_write", f"CREATE OR REPLACE {kind} {name} would replace a lakehouse {kind.lower()}"
            made.add(name)
            return "local_mutation", f"creates {kind.lower()} {name} for this run only"
        if kind in ("SCHEMA", "DATABASE"):
            return "onelake_write", f"CREATE {kind} would change the lakehouse"
        return "fabric_mutation", f"CREATE {kind or 'statement'} isn't run here"
    if isinstance(st, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Alter, exp.TruncateTable)):
        name = _name(st)
        verb = type(st).__name__.upper().replace("TRUNCATETABLE", "TRUNCATE")
        if name and name in made:
            if isinstance(st, exp.Drop):
                made.discard(name)
            return "local_mutation", f"{verb} on {name}, which this run created"
        return "onelake_write", f"{verb} on {name or 'a table'} would change the lakehouse"
    if isinstance(st, exp.Cache) or type(st).__name__ == "Uncache":
        return "local_mutation", "caches a table for this run only"
    if isinstance(st, exp.Copy):
        return "fabric_mutation", "COPY reads or writes files on this PC, which the SQL page doesn't allow"
    if isinstance(st, exp.Command):
        word = str(st.this or "").upper()
        if word in _SESSION_COMMANDS:
            return "local_mutation", f"{word} changes this run's session"
        if word in _FABRIC_COMMANDS:
            return "fabric_mutation", f"{word} isn't run here"
        return "fabric_mutation", f"{word or 'This statement'} isn't run here"
    if isinstance(st, (exp.Set, exp.Pragma)):
        return "local_mutation", "changes this run's session settings"
    if isinstance(st, exp.Describe):
        return "read_only", "describes a table"
    if isinstance(st, exp.Use):
        return "local_mutation", "USE changes this run's session"
    return "fabric_mutation", f"{type(st).__name__} isn't run here"


def _temporary(st: exp.Create) -> bool:
    props = st.args.get("properties")
    return bool(props and any(isinstance(p, exp.TemporaryProperty) for p in props.expressions))
