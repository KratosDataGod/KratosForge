"""Failure replay bundles: a file someone can open to run a notebook again, up to the cell that
failed, against the same inputs (plan item 47, docs/notebooks.md).

A bundle is a zip:

    bundle.json   the code of the cells up to the failed one; the inputs as the cells read them
                  (each table's Delta version, each file's SHA-256), recorded by lakehouse.reads;
                  the environment; and the error, with secrets removed
    samples/      only when the person ticks it (and policy allows it, allow_replay_samples): up to
                  SAMPLE_ROWS rows of each table, read at the bundle's version, as Parquet

What a bundle can't promise is written into it as ``limitations``: cells that hadn't run, ran out
of order or ran again after the failure; a file that changed on this PC after it was read; code
that looked like it held a secret, which is removed (so replay may fail there). Replay reads tables
at the recorded versions and writes to a sandbox, as a proof does.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from . import envprofile
from .redact import secrets

FORMAT = "fabric-desktop-replay"
VERSION = 1
SAMPLE_ROWS = 1000


def _private(text: str) -> str:
    """``text`` without secrets or this PC's home folder (which names the person)."""
    home = str(Path.home())
    text = secrets(text or "")
    return text.replace(home, "~") if len(home) > 1 else text


def code(cells: list[dict[str, Any]], failed: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Cells 1 to ``failed`` (an index), with anything credential-shaped removed from their code,
    and what that, and how they ran in this session, means for a replay."""
    if not 0 <= failed < len(cells) or cells[failed].get("type") == "markdown":
        raise ValueError("Pick the code cell that failed.")
    out, notes, counts = [], [], []
    for n, cell in enumerate(cells[:failed + 1], 1):
        source = cell.get("source") or ""
        clean = secrets(source)
        if clean != source:
            notes.append(f"Cell {n} looked like it held a secret, which isn't in the bundle; a replay may fail there.")
        tags = (cell.get("meta") or {}).get("tags") or []
        row = {"type": "markdown" if cell.get("type") == "markdown" else "code", "source": clean}
        if "parameters" in tags:
            row["parameters"] = True
        if (cell.get("meta") or {}).get("frozen"):
            row["frozen"] = True
        out.append(row)
        if row["type"] == "code" and source.strip() and not (cell.get("meta") or {}).get("frozen"):
            count = cell.get("count")
            if count is None:
                notes.append(f"Cell {n} hadn't run in this session when cell {failed + 1} failed, so a replay runs code the failure didn't.")
            else:
                counts.append((n, count))
    ran = [c for _, c in counts]
    if any(b <= a for a, b in zip(ran, ran[1:])):
        notes.append("Cells ran in a different order from the notebook's, or ran again after the failure, so a "
                     "replay in order may not end the same way.")
    return out, notes


def error(cells: list[dict[str, Any]], failed: int) -> dict[str, Any] | None:
    """The failed cell's error, with secrets and this PC's home folder removed."""
    for o in cells[failed].get("outputs") or []:
        if o.get("type") == "error":
            return {"ename": str(o.get("ename") or ""), "evalue": _private(str(o.get("evalue") or "")),
                    "traceback": _private(str(o.get("traceback") or ""))}
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inputs(reads: dict[str, dict[str, dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """The tables and files the cells read (lakehouse.reads), each file hashed now if its local
    copy is still the one the cell read."""
    tables = sorted((dict(t) for t in (reads.get("tables") or {}).values()),
                    key=lambda t: (t.get("lakehouse", ""), t["table"].lower()))
    files, notes = [], []
    for f in sorted((reads.get("files") or {}).values(), key=lambda f: (f.get("lakehouse", ""), f["path"])):
        row = {k: v for k, v in f.items() if k not in ("local", "mtime_ns")}
        local = Path(f.get("local") or "")
        try:
            st = local.stat()
            same = st.st_size == f.get("bytes") and st.st_mtime_ns == f.get("mtime_ns")
        except OSError:
            same = False
        row["sha256"] = _sha256(local) if same else None
        if not same:
            notes.append(f"{f['path']} changed on this PC after a cell read it, so its SHA-256 isn't recorded.")
        files.append(row)
    for t in tables:
        if t.get("first") == "write" and t.get("version") is None:
            t["created"] = True  # the notebook created it: a replay starts without it
    return {"tables": tables, "files": files}, notes


def strip_ids(bundle: dict[str, Any]) -> dict[str, Any]:
    """The bundle without workspace and lakehouse ids or the workspace name, to share outside the
    organization; replay then asks which lakehouse to use."""
    out = json.loads(json.dumps(bundle))
    for rows in out["inputs"].values():
        for row in rows:
            row.pop("workspace_id", None)
            row.pop("lakehouse_id", None)
    out["lakehouse"] = {k: v for k, v in (out.get("lakehouse") or {}).items() if k == "name"}
    out["ids_included"] = False
    return out


def build(name: str, cells: list[dict[str, Any]], failed: int, reads: dict[str, Any],
          lakehouse_ref: dict[str, str] | None = None, kernel: dict[str, Any] | None = None) -> dict[str, Any]:
    """The bundle's bundle.json (with ids; strip_ids takes them out)."""
    cells_out, notes = code(cells, failed)
    found, more = inputs(reads)
    modules = sorted({m["module"] for c in cells_out if c["type"] == "code"
                      for m in envprofile.imports(c["source"])["modules"]})
    env = envprofile.runtime(modules)
    env.pop("machine", None)
    if kernel:
        env["kernel"] = kernel
    notes += more
    notes.append("Inputs are what the cells read through lakehouse (read_table, sql, read_csv and the like) since "
                 "the kernel started; data read another way isn't recorded.")
    return {"format": FORMAT, "version": VERSION, "notebook": name,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "failed_cell": failed + 1, "cells": cells_out, "error": error(cells, failed),
            "inputs": found, "environment": env, "ids_included": True,
            "lakehouse": {k: (lakehouse_ref or {}).get(k, "") for k in ("name", "workspace_id", "workspace_name", "lakehouse_id")},
            "samples": [], "limitations": notes}


def write(path: Path, bundle: dict[str, Any], samples: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save ``bundle`` and its ``samples`` ({table: pyarrow table}) as a zip at ``path``."""
    import pyarrow.parquet as pq
    bundle = dict(bundle)
    bundle["samples"] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (table, arrow) in enumerate(sorted((samples or {}).items())):
            buf = io.BytesIO()
            pq.write_table(arrow.slice(0, SAMPLE_ROWS), buf)
            member = f"samples/{i + 1}.parquet"
            z.writestr(member, buf.getvalue())
            bundle["samples"].append({"table": table, "file": member, "rows": min(arrow.num_rows, SAMPLE_ROWS)})
        z.writestr("bundle.json", json.dumps(bundle, indent=1, default=str))
    return {"path": str(path), "bytes": path.stat().st_size, "samples": bundle["samples"]}


def read(path: Path) -> dict[str, Any]:
    """bundle.json from a bundle file, checked to be one."""
    try:
        with zipfile.ZipFile(path) as z:
            bundle = json.loads(z.read("bundle.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ValueError(f"{path.name} isn't a replay bundle.") from exc
    if bundle.get("format") != FORMAT:
        raise ValueError(f"{path.name} isn't a replay bundle.")
    if int(bundle.get("version", 0)) > VERSION:
        raise ValueError(f"{path.name} was saved by a newer Fabric Desktop. Update the app to open it.")
    return bundle


def sample_tables(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """The tables a sample would hold: those read at a recorded version (not ones the notebook created)."""
    return [t for t in bundle["inputs"]["tables"] if t.get("version") is not None]


def samples(bundle: dict[str, Any], read_sample: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """{table: up to SAMPLE_ROWS rows at its version}, read with ``read_sample(row)``."""
    return {f"{t.get('lakehouse', '')}/{t['table']}".lstrip("/"): read_sample(t) for t in sample_tables(bundle)}
