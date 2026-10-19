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
from pathlib import Path
from typing import Any

from . import proof_runtime
from .fabric_run import compute_check, uses_local_module, uses_spark

RUNTIME_SOURCE = Path(proof_runtime.__file__).read_text(encoding="utf-8")

_BLOCKERS = [
    (re.compile(r"\bwrite_deltalake\s*\("), "writes a Delta table directly with deltalake, outside the proof's folder"),
    (re.compile(r"\bDeltaTable\s*\("),
     "opens a Delta table directly with deltalake, so the proof can't pin or contain it; use lakehouse.read_table or open_table"),
    (re.compile(r"\b(notebookutils|mssparkutils)\.fs\.(put|cp|mv|rm|mkdirs|append|fastcp)\b"),
     "changes files in the lakehouse with notebookutils.fs, outside the proof's folder"),
    (re.compile(r"\.write_(parquet|csv|delta|ndjson|json|ipc)\s*\(\s*[\"'](abfss://|/lakehouse/|Files/|Tables/)"),
     "writes a file into the lakehouse directly, outside the proof's folder"),
]
_SQL_CHANGE = re.compile(r"\b(CREATE|INSERT|MERGE|UPDATE|DELETE|DROP|ALTER|TRUNCATE|REPLACE)\b", re.I)
_WARNINGS = [
    (re.compile(r"\b(requests|httpx|urllib3?)\.(get|post|put|patch|delete|request|urlopen|Request)\b|\bhttp\.client\b|\bsmtplib\b"),
     "calls a network service; the proof can't contain or compare what that does"),
    (re.compile(r"\bopen\s*\((?:[^,()]|\([^()]*\))*,\s*(?:mode\s*=\s*)?[\"'][rbt]*[wax+]|\bopen\s*\([^)]*\bmode\s*=\s*[\"'][rbt]*[wax+]"
                r"|\.write_(text|bytes)\s*\(|\bos\.(remove|unlink|rename|makedirs)\b|\bshutil\."
                r"|\.to_(csv|parquet|excel|json|pickle|feather)\s*\(\s*(?![A-Za-z_]\w*\s*=)[^)\s]"),
     "writes or changes files on the machine running it; those aren't compared"),
    (re.compile(r"\b(notebookutils|mssparkutils)\.notebook\.(run|runMultiple)\b|^\s*%run\b", re.M),
     "runs another notebook, which isn't checked or contained by the proof"),
]
_NONDETERMINISM = re.compile(r"\b(datetime\.(now|utcnow|today)|date\.today|time\.time|uuid\.uuid[14]|"
                             r"current_timestamp|current_date|now\(\)|random\.(?!seed)\w+|np\.random\.|default_rng)\b", re.I)
_CREDENTIALS = re.compile(r"\b(notebookutils|mssparkutils)\.credentials\.(getToken|getSecret)\b")


def _line(source: str, pos: int) -> tuple[int, str]:
    n = source.count("\n", 0, pos) + 1
    text = source.splitlines()[n - 1].strip() if source.splitlines() else ""
    return n, text if len(text) <= 80 else text[:77] + "..."


def check(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None) -> dict[str, Any]:
    """Whether a proof can run this notebook, and what it should tell the person first."""
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []

    def add(bucket: list[dict[str, Any]], n: int | None, c: dict[str, Any] | None, text: str, source: str = "",
            pos: int | None = None) -> None:
        row: dict[str, Any] = {"cell": n, "cell_id": (c or {}).get("cell_id"), "text": text}
        if pos is not None:
            row["line"], row["code"] = _line(source, pos)
        bucket.append(row)

    if not lakehouse:
        add(blockers, None, None, "No lakehouse is attached. A proof pins the attached lakehouse's tables and writes to its own folder there.")
    if not uses_local_module(cells):
        add(blockers, None, None, "The notebook doesn't use `from fabric_desktop import lakehouse`. A proof compares what a "
                                  "notebook reads and writes through that module, so there would be nothing to compare.")
    if uses_spark(cells):
        n, c = next((i, c) for i, c in enumerate(cells, 1)
                    if c.get("type") != "markdown" and uses_spark([c]))
        add(blockers, n, c, "uses Spark. In Fabric it would read and write the lakehouse with Fabric's own Spark, which "
                            "a proof can't pin or contain yet. Use the lakehouse module (read_table, write_table, sql), "
                            "or compare with Side by side.")
    compat = compute_check(cells, lakehouse)
    for side, label in (("local", "this PC"), ("fabric", "Fabric")):
        for r in compat[side]["reasons"]:
            if r.get("cell") is None and side == "fabric" and not lakehouse:
                continue  # already said
            c = cells[r["cell"] - 1] if r.get("cell") else None
            add(blockers, r.get("cell"), c, f"can't run on {label}: {r['text']}")
    for n, c in enumerate(cells, 1):
        source = c.get("source") or ""
        if c.get("type") == "markdown" or not source.strip():
            continue
        if source.lstrip().startswith("%%sql"):
            if m := _SQL_CHANGE.search(source):
                add(blockers, n, c, "is SQL that creates or changes tables; in a proof, write with lakehouse.write_table",
                    source, m.start())
            continue
        for rx, text in _BLOCKERS:
            if m := rx.search(source):
                add(blockers, n, c, text, source, m.start())
        for rx, text in _WARNINGS:
            if m := rx.search(source):
                add(warnings, n, c, text, source, m.start())
        if m := _NONDETERMINISM.search(source):
            add(warnings, n, c, "uses the clock, random values or new ids; the proof fixes the random seed and time zone, "
                                "but the clock differs between runs, so outputs built from it can differ", source, m.start())
        if m := _CREDENTIALS.search(source):
            add(notes, n, c, "uses your credentials; the proof runs as you on both sides", source, m.start())
    return {"ok": not blockers, "blockers": blockers, "warnings": warnings, "notes": notes,
            "limits": ["This check reads the code; it can't see calls built at run time."]}


# ---- the cells that make the Fabric run a proof


def fabric_cells(config: dict[str, Any], modules: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The first and last cells added to the copy published for the Fabric side.

    The first loads the proof runtime (this module's sibling, copied verbatim) as its own module,
    so names in the notebook can't break it, and binds it to the lakehouse module. The last saves
    the run's log (reads, file hashes, writes, environment) next to the Fabric outputs."""
    first = {"type": "code", "cell_id": "fd-proof-start", "source": (
        "# Fabric Desktop proof: pinned inputs, outputs in the proof's own folder. Added for a proof run only.\n"
        "import json as _fd_json, sys as _fd_sys, types as _fd_types\n"
        f"_fd_source = {RUNTIME_SOURCE!r}\n"
        "_fd_mod = _fd_types.ModuleType('fabric_desktop_proof')\n"
        "exec(compile(_fd_source, 'fabric_desktop_proof', 'exec'), _fd_mod.__dict__)\n"
        "_fd_sys.modules['fabric_desktop_proof'] = _fd_mod\n"
        f"_fd_proof = _fd_mod.ProofRuntime(_fd_json.loads({json.dumps(config)!r}), 'fabric')\n"
        "_fd_sys.modules['fabric_desktop.lakehouse']._bind_proof(_fd_proof)\n")}
    last = {"type": "code", "cell_id": "fd-proof-end", "source": (
        "# Fabric Desktop proof: save what this run read, wrote and ran on.\n"
        f"_fd_proof.save({sorted(set(modules))!r})\n")}
    return first, last
