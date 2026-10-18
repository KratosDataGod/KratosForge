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
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from . import envprofile
from .onelake import check_name
from .redact import secrets

FORMAT = "fabric-desktop-replay"
VERSION = 1
SAMPLE_ROWS = 1000
# A bundle holds code and a few samples. Anything bigger was not written by Fabric Desktop.
MAX_BUNDLE_JSON = 64 * 1024 * 1024
MAX_SAMPLE_FILE = 256 * 1024 * 1024
_SAMPLE_FILE = re.compile(r"^samples/\d+\.parquet$")


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


_GUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# OneLake addresses carry the workspace and lakehouse by id or by name.
_ABFSS = re.compile(r"(abfss://)[^@/\s'\"]+(@onelake\.[a-z.]*fabric\.microsoft\.com/)[^/\s'\"]+", re.IGNORECASE)
_HTTPS = re.compile(r"(https://onelake\.[a-z.]*fabric\.microsoft\.com/)[^/\s'\"]+/[^/\s'\"]+", re.IGNORECASE)


def scrub_ids(text: str, known: tuple[str, ...] = ()) -> str:
    """``text`` without workspace or lakehouse ids: the ``known`` ones, any OneLake address's
    workspace and lakehouse, and any other GUID."""
    for value in known:
        text = text.replace(value, "<id>")
    text = _ABFSS.sub(r"\1<workspace>\2<lakehouse>", text)
    text = _HTTPS.sub(r"\1<workspace>/<lakehouse>", text)
    return _GUID.sub("<id>", text)


def _scrub_strings(value: Any, ids: tuple[str, ...]) -> Any:
    """``value`` with scrub_ids applied to each string in it (keys included), never to JSON text."""
    if isinstance(value, str):
        return scrub_ids(value, ids)
    if isinstance(value, dict):
        return {_scrub_strings(k, ids): _scrub_strings(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_strings(v, ids) for v in value]
    return value


def strip_ids(bundle: dict[str, Any]) -> dict[str, Any]:
    """The bundle without workspace and lakehouse ids or the workspace name, to share outside the
    organization; replay then asks which lakehouse to use. Ids are taken out of the inputs and also
    out of the error, the code and the notes, where OneLake addresses carry them."""
    out = json.loads(json.dumps(bundle))
    lakehouse = out.get("lakehouse") or {}
    # Names aren't replaced in text (a workspace called "Sales" would change every "Sales"); the
    # OneLake address patterns take them out where they're part of an address.
    known = {str(v) for v in (lakehouse.get("workspace_id"), lakehouse.get("lakehouse_id")) if v}
    for rows in out["inputs"].values():
        for row in rows:
            known.update(str(row.pop(k)) for k in ("workspace_id", "lakehouse_id") if row.get(k))
            row.pop("workspace_id", None)
            row.pop("lakehouse_id", None)
    out["lakehouse"] = {k: v for k, v in lakehouse.items() if k == "name"}
    out["ids_included"] = False
    ids = tuple(sorted(known, key=len, reverse=True))
    code = [c.get("source") for c in out.get("cells") or []]
    out = _scrub_strings(out, ids)
    if code != [c.get("source") for c in out.get("cells") or []]:
        out.setdefault("limitations", []).append(
            "IDs in the cells' code were replaced with <id>, <workspace> and <lakehouse>. Put your own back "
            "before the replay reaches those cells.")
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
            info = z.getinfo("bundle.json")
            if info.file_size > MAX_BUNDLE_JSON:  # checked before reading: a small zip can unpack to gigabytes
                raise ValueError(f"{path.name} is too large to be a replay bundle.")
            bundle = json.loads(z.read(info))
            members = {i.filename: i.file_size for i in z.infolist()}
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"{path.name} isn't a replay bundle.") from exc
    except ValueError as exc:
        if "too large" in str(exc):
            raise
        raise ValueError(f"{path.name} isn't a replay bundle.") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ValueError(f"{path.name} isn't a replay bundle.")
    if int(bundle.get("version", 0)) > VERSION:
        raise ValueError(f"{path.name} was saved by a newer Fabric Desktop. Update the app to open it.")
    try:
        _check_names(bundle, members)
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise ValueError(f"{path.name} isn't a replay bundle: {exc}") from exc
    return bundle


def _check_table(table: Any) -> None:
    """A bundle's table name is schema.table or table, each a name Fabric allows: never a path."""
    schema, _, name = str(table).rpartition(".")
    if schema:
        check_name("schema", schema)
    check_name("table", name)


def _check_names(bundle: dict[str, Any], members: dict[str, int]) -> None:
    """Every table a bundle names, and every sample file, is one a replay may use. A replay builds its
    sandbox paths from these names, so a hand-edited bundle must not be able to point outside it."""
    for t in (bundle.get("inputs") or {}).get("tables") or []:
        _check_table(t["table"])
    for s in bundle.get("samples") or []:
        _check_table(str(s["table"]).split("/", 1)[-1])
        if not _SAMPLE_FILE.match(str(s["file"])) or s["file"] not in members:
            raise ValueError(f"the sample file {s['file']!r} isn't in the bundle's samples folder")
        if members[s["file"]] > MAX_SAMPLE_FILE:
            raise ValueError(f"the sample file {s['file']} is too large")


def sample_tables(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """The tables a sample would hold: those read at a recorded version (not ones the notebook created)."""
    return [t for t in bundle["inputs"]["tables"] if t.get("version") is not None]


def samples(bundle: dict[str, Any], read_sample: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """{table: up to SAMPLE_ROWS rows at its version}, read with ``read_sample(row)``."""
    return {f"{t.get('lakehouse', '')}/{t['table']}".lstrip("/"): read_sample(t) for t in sample_tables(bundle)}


# ---- opening a bundle and replaying it (prove_service.replay_*)

def notebook_cells(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """The bundle's cells as a notebook's (frozen cells stay frozen, the parameter cell keeps its tag)."""
    cells = []
    for c in bundle["cells"]:
        meta: dict[str, Any] = {}
        if c.get("parameters"):
            meta["tags"] = ["parameters"]
        if c.get("frozen"):
            meta["frozen"] = True
        cells.append({"type": c["type"], "source": c["source"], "meta": meta})
    return cells


def tables_to_pin(bundle: dict[str, Any], lakehouse_id: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    """The bundle's tables a replay in ``lakehouse_id`` can pin, and notes on the ones it can't."""
    pin, notes = [], []
    for t in bundle["inputs"]["tables"]:
        if t.get("created"):
            continue  # the notebook created it: a replay starts without it, as the failed run did
        if bundle.get("ids_included", True) and t.get("lakehouse_id") and t["lakehouse_id"] != lakehouse_id:
            notes.append(f"{t['table']} was read from another lakehouse ({t.get('lakehouse') or 'not named'}), "
                         "so this replay can't read it.")
            continue
        if t.get("version") is None:
            continue
        pin.append(t)
    return pin, notes


def outcome(bundle: dict[str, Any], stopped_at: int, status: str, outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """How the replay ended against how the bundle's run ended. ``stopped_at`` is the 1-based cell
    the replay stopped at, ``status`` how that cell ended and ``outputs`` its output."""
    failed = bundle["failed_cell"]
    expected = bundle.get("error") or {}
    got = error([{"outputs": outputs}], 0) or {}
    if status != "error":
        if stopped_at < failed:
            return {"verdict": "stopped", "text": f"The replay stopped at cell {stopped_at} before reaching cell {failed}."}
        return {"verdict": "not_reproduced", "text": f"Cell {failed} ran without an error this time. Not reproduced."}
    said = f"{got.get('ename')}: {got.get('evalue')}"
    if stopped_at < failed:
        return {"verdict": "failed_earlier", "text": f"Failed earlier, at cell {stopped_at}: {said}", "error": got}
    same = got.get("evalue") == expected.get("evalue")
    if not same and bundle.get("ids_included") is False:
        # The bundle's error had its ids taken out; this run's names the lakehouse it ran in.
        same = scrub_ids(str(got.get("evalue"))) == expected.get("evalue")
    if got.get("ename") == expected.get("ename") and same:
        return {"verdict": "reproduced", "text": "Failed the same way as in the bundle. Reproduced.", "error": got}
    if got.get("ename") == expected.get("ename"):
        return {"verdict": "same_kind", "text": f"Failed with the same kind of error, but a different message: {said}", "error": got}
    return {"verdict": "different", "text": f"Failed differently: {said} (the bundle has {expected.get('ename')})", "error": got}


def input_differences(bundle: dict[str, Any], log: dict[str, Any], marked: dict[str, str]) -> list[str]:
    """What the replay read that isn't what the bundle's run read: files whose SHA-256 differs, and
    tables read at another version (marked: the latest version or the bundle's sample)."""
    read_as = {"sample": "from the bundle's sample", "latest": "at its latest version"}
    notes = [f"{t} was read {read_as[how]}, not at the bundle's version: not the same input."
             for t, how in sorted(marked.items())]
    files = {f["path"]: f for f in bundle["inputs"]["files"]}
    for rel, now in sorted((log.get("files") or {}).items()):
        then = files.get(rel)
        if then is None:
            notes.append(f"{rel} wasn't read in the bundle's run.")
        elif then.get("sha256") and then["sha256"] != now.get("sha256"):
            notes.append(f"{rel} is different now from the file the bundle's run read (SHA-256 differs).")
        elif not then.get("sha256"):
            notes.append(f"{rel} has no SHA-256 in the bundle, so it can't be checked.")
    return notes
