"""Prove this notebook: what can be proven, and the cells that make the Fabric run a proof.

``check`` reads a notebook before a proof and sorts what it finds, cell by cell, into:

* **blockers**: things that would make the comparison wrong or touch shared data, so the proof
  doesn't start (a Spark notebook in Fabric reads and writes the lakehouse directly; a direct
  ``deltalake`` or ``notebookutils.fs`` write; SQL that changes tables);
* **warnings**: side effects the proof can't contain or check (network calls, files written on
  the machine, other notebooks run) and sources of nondeterminism (the clock, unseeded
  randomness). They are shown before the run and recorded in the receipt;
* **notes**: what the notebook uses that's worth knowing (your credentials).

It is a reading of the code, not a guarantee: code that builds calls dynamically isn't seen, and
the receipt says so.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import magics, proof_runtime, proof_spark
from .fabric_run import _SPARK_SQL_LITERAL, _in_comment, _literal_sql, compute_check, uses_local_module, uses_spark
from .fabric_step import STEP_REFUSED, is_step

RUNTIME_SOURCE = Path(proof_runtime.__file__).read_text(encoding="utf-8")
SPARK_SOURCE = Path(proof_spark.__file__).read_text(encoding="utf-8")

_BLOCKERS = [
    (re.compile(r"\bwrite_deltalake\s*\("), "writes a Delta table directly with deltalake, outside the proof's folder"),
    (re.compile(r"\bDeltaTable\s*\("),
     "opens a Delta table directly with deltalake, so the proof can't pin or contain it; use lakehouse.read_table or open_table"),
    (re.compile(r"\b(notebookutils|mssparkutils|dbutils)\.fs\.(put|cp|mv|rm|mkdirs|append|fastcp)\b"),
     "changes files in the lakehouse with notebookutils.fs or dbutils.fs, outside the proof's folder"),
    (re.compile(r"\.write_(parquet|csv|delta|ndjson|json|ipc)\s*\(\s*[\"'](abfss://|/lakehouse/|Files/|Tables/)"),
     "writes a file into the lakehouse directly, outside the proof's folder"),
]
_SQL_CHANGE = re.compile(r"\b(CREATE|INSERT|MERGE|UPDATE|DELETE|DROP|ALTER|TRUNCATE|REPLACE)\b", re.I)
_WARNINGS = [
    (re.compile(r"\b(requests|httpx|urllib3?)\.(get|post|put|patch|delete|request|urlopen|Request)\b|\bhttp\.client\b|\bsmtplib\b"),
     "calls a network service; the proof can't contain or compare what that does"),
    (re.compile(r"\bopen\s*\((?:[^,()]|\([^()]*\))*,\s*(?:mode\s*=\s*)?[\"'][rbt]*[wax+]|\bopen\s*\([^)]*\bmode\s*=\s*[\"'][rbt]*[wax+]"
                r"|\.write_(text|bytes)\s*\(|\bos\.(remove|unlink|rename|makedirs)\b|\bshutil\."
                r"|\.to_(csv|parquet|excel|json|pickle|feather)\s*\(\s*(?![A-Za-z_]\w*\s*=)[^)\s]"
                # Spark's df.write...parquet(path) or csv(path) to a path outside the lakehouse.
                r"|\.write(?:\.\w+\([^()]*\))*\.(?:parquet|csv)\s*\(\s*(?![rfu]?[\"'](?:Files/|Tables/|abfss://|/lakehouse/))[^)\s]"),
     "writes or changes files on the machine running it; those aren't compared"),
    (re.compile(r"\b(notebookutils|mssparkutils|dbutils)\.notebook\.(run|runMultiple)\b|^\s*%run\b", re.M),
     "runs another notebook, which isn't checked or contained by the proof"),
]
_NONDETERMINISM = re.compile(r"\b(datetime\.(now|utcnow|today)|date\.today|time\.time|uuid\.uuid[14]|"
                             r"current_timestamp|current_date|now\(\)|random\.(?!seed)\w+|np\.random\.|default_rng)\b", re.I)
# A Spark notebook's Fabric side runs through proof_spark, which keeps table reads and changes in the
# proof; these are the ways to the lakehouse it refuses, found before the run where the code shows them.
_SPARK_BLOCKERS = [
    (re.compile(r"\.insertInto\s*\("), "uses insertInto, which a proof doesn't support; use saveAsTable(mode=\"append\") or INSERT INTO"),
    (re.compile(r"\.writeTo\s*\("), "uses writeTo, which a proof doesn't support; use saveAsTable"),
    (re.compile(r"\.(writeStream|readStream)\b"), "runs a stream, which a proof doesn't take to Fabric yet"),
    (re.compile(r"\bDeltaTable\.(create|createIfNotExists|createOrReplace|replace)\b"),
     "builds a table with DeltaTable.create, which a proof doesn't support; use CREATE TABLE or saveAsTable"),
    (re.compile(r"\bcatalog\.create(External)?Table\b"), "creates a table with spark.catalog, which a proof doesn't support; use saveAsTable"),
]
_SPARK_SQL_UNCONTAINED = re.compile(r"\b(DROP\s+TABLE|TRUNCATE\s+TABLE|ALTER\s+TABLE|OPTIMIZE|VACUUM|RESTORE\s+TABLE|"
                                    r"CREATE\s+(OR\s+REPLACE\s+)?VIEW|COPY\s+INTO)\b|\bLOCATION\s+''|(?:^|;)\s*USE\b", re.I)
# Comments, string literals and quoted names, so words in them (SELECT 'use') aren't read as SQL.
_SQL_QUOTED = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`]|``)*`", re.S)


def _sql_uncontained(sql: str) -> re.Match[str] | None:
    """Where ``sql`` does something a Spark proof can't keep in its folder, outside comments and literals."""
    return _SPARK_SQL_UNCONTAINED.search(_SQL_QUOTED.sub(lambda m: "''" if m[0][0] in "'\"" else " ", sql))
_SPARK_SQL_TEXT = ("is SQL that drops, truncates, alters, optimizes or restores a table, loads one with COPY INTO, gives "
                   "one its own LOCATION, makes a lasting view or switches the database; a proof keeps changes in its "
                   "own folder, so it can't run that")
_CREDENTIALS = re.compile(r"\b(notebookutils|mssparkutils)\.credentials\.(getToken|getSecret)\b"
                          r"|\bdbutils\.secrets\.(get|getBytes)\b")


def _line(source: str, pos: int) -> tuple[int, str]:
    n = source.count("\n", 0, pos) + 1
    text = source.splitlines()[n - 1].strip() if source.splitlines() else ""
    return n, text if len(text) <= 80 else text[:77] + "..."


def check(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None,
          load: Callable[[str], Any] | None = None, resources: str | None = None) -> dict[str, Any]:
    """Whether a proof can run this notebook, and what it should tell the person first. ``load``
    and ``resources`` let the check follow the notebooks and Built-in files it runs (compute_check)."""
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []

    def add(bucket: list[dict[str, Any]], n: int | None, c: dict[str, Any] | None, text: str, source: str = "",
            pos: int | None = None) -> None:
        row: dict[str, Any] = {"cell": n, "cell_id": (c or {}).get("cell_id"), "text": text}
        if pos is not None:
            row["line"], row["code"] = _line(source, pos)
        bucket.append(row)

    step = next(((i, c) for i, c in enumerate(cells, 1) if is_step(c)), None)
    if step:
        add(blockers, step[0], step[1], STEP_REFUSED)
    if not lakehouse:
        add(blockers, None, None, "No lakehouse is attached. A proof pins the attached lakehouse's tables and writes to its own folder there.")
    spark = runs_spark(cells)
    if not spark and not uses_local_module(cells):
        add(blockers, None, None, "The notebook doesn't use `from fabric_desktop import lakehouse` or Spark. A proof "
                                  "compares the tables a notebook writes, so there would be nothing to compare.")
    compat = compute_check(cells, lakehouse, load, resources=resources)
    for side, label in (("local", "this PC"), ("fabric", "Fabric")):
        for r in compat[side]["reasons"]:
            if r.get("cell") is None and side == "fabric" and not lakehouse:
                continue  # already said
            c = cells[r["cell"] - 1] if r.get("cell") else None
            add(blockers, r.get("cell"), c, f"can't run on {label}: {r['text']}")
        for r in compat[side]["unknown"]:  # not a blocker: running both sides is how a proof finds out
            add(warnings, r["cell"], cells[r["cell"] - 1], f"may not run on {label}: it {r['text']}")
    for n, c in enumerate(cells, 1):
        source = c.get("source") or ""
        if c.get("type") == "markdown" or not source.strip():
            continue
        language = magics.cell(source).language
        if language == "sql":
            if spark and _sql_uncontained(magics.cell(source).body):
                add(blockers, n, c, _SPARK_SQL_TEXT)
            elif not spark and (m := _SQL_CHANGE.search(source)):
                add(blockers, n, c, "is SQL that creates or changes tables; in a proof, write with lakehouse.write_table",
                    source, m.start())
            continue
        if language == "shell" or any(m.name in ("system", "sx", "sh") for m in magics.find(source)):
            add(warnings, n, c, "runs shell commands; the proof can't contain or compare what they change")
        if language != "python":
            continue
        for rx, text in _BLOCKERS:
            if m := rx.search(source):
                add(blockers, n, c, text, source, m.start())
        for magic in magics.find(source):
            if magic.name == "sql" and (_sql_uncontained(magic.rest) if spark else _SQL_CHANGE.search(magic.rest)):
                add(blockers, n, c, _SPARK_SQL_TEXT if spark else
                    "is SQL that creates or changes tables; in a proof, write with lakehouse.write_table",
                    source, sum(len(ln) + 1 for ln in source.split("\n")[:magic.line - 1]))
        if spark:
            for rx, text in _SPARK_BLOCKERS:
                if m := rx.search(source):
                    add(blockers, n, c, text, source, m.start())
            for m in _SPARK_SQL_LITERAL.finditer(source):
                if _sql_uncontained(_literal_sql(m)) and not _in_comment(source, m.start()):
                    add(blockers, n, c, _SPARK_SQL_TEXT, source, m.start())
                    break
        for rx, text in _WARNINGS:
            if m := rx.search(source):
                add(warnings, n, c, text, source, m.start())
        if m := _NONDETERMINISM.search(source):
            add(warnings, n, c, "uses the clock, random values or new ids; the proof fixes the random seed and time zone, "
                                "but the clock differs between runs, so outputs built from it can differ", source, m.start())
        if m := _CREDENTIALS.search(source):
            add(notes, n, c, "uses your credentials; the proof runs as you on both sides", source, m.start())
    limits = ["This check reads the code; it can't see calls built at run time."]
    if spark:
        limits += ["In Fabric, Spark reads of a single file under Files/ are checked; files read as a whole folder "
                   "aren't, so a change to them between the runs isn't found.",
                   "In Fabric, Spark writes to Azure storage outside OneLake (abfss:// of another account) aren't "
                   "stopped or compared.",
                   "In Fabric, the copy needs sqlglot in the notebook's environment to read its SQL; it installs nothing."]
    return {"ok": not blockers, "blockers": blockers, "warnings": warnings, "notes": notes,
            "engine": "spark" if spark else "python", "limits": limits}


def runs_spark(cells: list[dict[str, Any]]) -> bool:
    """Whether the Fabric side runs as a Spark notebook (fabric_run.fabric_ipynb's rule), so its
    proof needs proof_spark: it uses Spark, or it has SQL (%%sql cells or %sql) and not the lakehouse module."""
    return uses_spark(cells) or not uses_local_module(cells) and any(
        c.get("type") != "markdown" and (magics.cell(c.get("source") or "").language == "sql"
                                         or any(m.name == "sql" for m in magics.find(c.get("source") or "")))
        for c in cells)


# ---- the cells that make the Fabric run a proof


def fabric_cells(config: dict[str, Any], modules: list[str],
                 spark: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The first and last cells added to the copy published for the Fabric side.

    The first loads the proof runtime (this module's sibling, copied verbatim) as its own module,
    so names in the notebook can't break it, and binds it to the lakehouse module. The last saves
    the run's log (reads, file hashes, writes, environment) next to the Fabric outputs.

    ``spark`` (the lakehouse's workspace_id, lakehouse_id, name and schemas) is given for a Spark
    notebook: the first cell then also loads proof_spark and binds Fabric's ``spark`` to the same
    runtime, and the last lets it drop tables that were only opened."""
    source = (
        "# Fabric Desktop proof: pinned inputs, outputs in the proof's own folder. Added for a proof run only.\n"
        "import json as _fd_json, sys as _fd_sys, types as _fd_types\n"
        f"_fd_source = {RUNTIME_SOURCE!r}\n"
        "_fd_mod = _fd_types.ModuleType('fabric_desktop_proof')\n"
        "exec(compile(_fd_source, 'fabric_desktop_proof', 'exec'), _fd_mod.__dict__)\n"
        "_fd_sys.modules['fabric_desktop_proof'] = _fd_mod\n"
        f"_fd_proof = _fd_mod.ProofRuntime(_fd_json.loads({json.dumps(config)!r}), 'fabric')\n")
    if spark is None:
        source += "_fd_sys.modules['fabric_desktop.lakehouse']._bind_proof(_fd_proof)\n"
    else:
        root = f"abfss://{spark['workspace_id']}@onelake.dfs.fabric.microsoft.com/{spark['lakehouse_id']}"
        source += (
            "try:\n"
            "    import sqlglot as _fd_sqlglot  # nothing is installed here: a proof brings no code from outside\n"
            "except ImportError:\n"
            "    raise RuntimeError('A Spark proof reads the notebook SQL with sqlglot, which this Fabric runtime '\n"
            "                       'doesn\\'t have. Add sqlglot to the notebook\\'s environment and prove it again.') from None\n"
            f"_fd_spark_where = ({root!r}, '/lakehouse/default')\n"
            "if 'fabric_desktop.lakehouse' in _fd_sys.modules:  # the notebook also uses the lakehouse module\n"
            "    _fd_sys.modules['fabric_desktop.lakehouse']._bind_proof(_fd_proof)\n"
            "else:\n"
            "    _fd_proof.bind(_fd_spark_where[0], None, _fd_spark_where[1])\n"
            f"_fd_spark_source = {SPARK_SOURCE!r}\n"
            "_fd_smod = _fd_types.ModuleType('fabric_desktop_proof_spark')\n"
            "exec(compile(_fd_spark_source, 'fabric_desktop_proof_spark', 'exec'), _fd_smod.__dict__)\n"
            f"_fd_spark = _fd_smod.bind(_fd_proof, spark, {spark.get('name', '')!r}, {bool(spark.get('schemas', True))!r})\n")
    first = {"type": "code", "cell_id": "fd-proof-start", "source": source}
    last = {"type": "code", "cell_id": "fd-proof-end", "source": (
        "# Fabric Desktop proof: save what this run read, wrote and ran on.\n"
        + ("_fd_spark.finish()\n" if spark is not None else "")
        + f"_fd_proof.save({sorted(set(modules))!r})\n")}
    return first, last
