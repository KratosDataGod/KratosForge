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

A run states the classes it allows (read_only, or read_only and local_mutation after the person
confirmed); anything beyond them is refused before any statement runs. On top of that the run's
DuckDB has external access turned off once the lakehouse tables are bound, so no statement can
read or write local files, load extensions or reach the network (sql_worker.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

CLASSES = ("read_only", "local_mutation", "onelake_write", "fabric_mutation")
LABELS = {
    "read_only": "reads tables",
    "local_mutation": "changes this run's own DuckDB session on this PC",
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


def classify(statements: list[exp.Expression]) -> list[Verdict]:
    """The class of each statement, in order. Names created earlier in the same run are local."""
    made: set[str] = set()
    out = []
    for i, st in enumerate(statements, 1):
        kind, reason = _one(st, made)
        out.append(Verdict(i, kind, reason))
    return out


def strongest(verdicts: list[Verdict]) -> str:
    return max((v.kind for v in verdicts), key=CLASSES.index, default="read_only")


def refused(verdicts: list[Verdict], allow: tuple[str, ...]) -> Verdict | None:
    """The first statement whose class isn't allowed, or None."""
    return next((v for v in verdicts if v.kind not in allow), None)


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
