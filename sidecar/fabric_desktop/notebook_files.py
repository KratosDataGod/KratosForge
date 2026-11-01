"""Notebook files (NB-001, NB-021): search inside notebooks, open a notebook file from anywhere,
save a copy, delete to Recently deleted and restore, and copy a Fabric notebook to this PC.

Nothing here replaces a notebook that is already there: every new file is reserved with an
exclusive create first, so a name taken in the meantime stops the call instead of being
overwritten. A file someone picks to open is read, never changed or moved.

Deleted notebooks go to ``<data>/notebooks-deleted/`` (outside every notebooks repository, so a
delete never adds files to Git) and are kept ``KEEP_DAYS`` days.

Publishing a notebook keeps its cells as sent under ``<data>/published/<item id>.json``. The next
publish of that item compares them with what Fabric has now, so a change someone made in Fabric is
shown before it would be replaced (``fabric_changes``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from . import jsonfile, notebook_resources
from .fabric_run import SHIM_MARKER, uses_spark
from .meta import diff as diff_mod
from .notebook import NotebookShelf, NotebookStore, check_notebook_name, from_ipynb

KEEP_DAYS = 30
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_NOTEBOOKS_FOUND = 100
MAX_HITS = 20          # per notebook
LINE_CHARS = 160
_PYTHON = {"python", "python3", "pyspark", "synapse_pyspark", "jupyter_python", ""}
_TRASH_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---- search


def _snippet(line: str, start: int, end: int) -> tuple[str, int, int]:
    """The line, cut to LINE_CHARS around the match, and where the match is in what's returned."""
    text = line.rstrip()
    if len(text) <= LINE_CHARS:
        return text, start, end
    lo = max(0, min(start - 40, len(text) - LINE_CHARS))
    cut = text[lo:lo + LINE_CHARS]
    prefix = "…" if lo else ""
    return prefix + cut + ("…" if lo + LINE_CHARS < len(text) else ""), start - lo + len(prefix), end - lo + len(prefix)


def search(shelf: NotebookShelf, query: str, names_only: bool = False, saved_in: str | None = None,
           open_notebook: dict[str, Any] | None = None) -> dict[str, Any]:
    """Notebooks whose name, cell code or Markdown contains ``query`` (any case). ``saved_in`` is
    None for every place, "pc" for the app's own folder, or a repository root. ``open_notebook``
    ({"name", "cells"}) is the open notebook's unsaved state, searched in place of its file."""
    q = (query or "").strip().lower()
    if not q:
        return {"query": "", "notebooks": [], "searched": 0, "problems": []}
    found, problems, searched = [], [], 0
    for row in shelf.list():
        place = row.get("place")
        if saved_in == "pc" and place or saved_in not in (None, "pc") and (place or {}).get("root") != saved_in:
            continue
        searched += 1
        name = row["name"]
        hits: list[dict[str, Any]] = []
        if not names_only:
            if open_notebook and open_notebook.get("name") == name:
                cells = open_notebook.get("cells") or []
            else:
                try:
                    cells = shelf.load(name)["cells"]
                except (OSError, ValueError) as exc:
                    problems.append({"name": name, "text": f"Couldn't read it ({type(exc).__name__})."})
                    continue
            for n, c in enumerate(cells, 1):
                for line_no, line in enumerate((c.get("source") or "").splitlines(), 1):
                    at = line.lower().find(q)
                    if at < 0:
                        continue
                    text, s, e = _snippet(line, at, at + len(q))
                    hits.append({"cell": n, "cell_id": c.get("cell_id"), "type": c.get("type", "code"),
                                 "line": line_no, "text": text, "start": s, "end": e})
                    if len(hits) >= MAX_HITS:
                        break
                if len(hits) >= MAX_HITS:
                    break
        name_hit = q in name.lower()
        if name_hit or hits:
            found.append({"name": name, "place": place, "modified": row["modified"], "name_match": name_hit,
                          "hits": hits, "more": len(hits) >= MAX_HITS})
        if len(found) >= MAX_NOTEBOOKS_FOUND:
            break
    return {"query": query.strip(), "notebooks": found, "searched": searched, "problems": problems,
            "more": len(found) >= MAX_NOTEBOOKS_FOUND}


# ---- new files, never over an existing one


def free_name(shelf: NotebookShelf, wanted: str) -> str:
    """``wanted`` (made safe), or the first "<wanted> 2", "<wanted> 3"... that isn't taken."""
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", wanted or "").strip().rstrip(".").strip()[:80] or "Notebook"
    name, i = base, 2
    while shelf._taken(name):  # noqa: SLF001 - the shelf's own rule for names
        name, i = f"{base} {i}", i + 1
    return name


def create(shelf: NotebookShelf, name: str, place: str | None, cells: list[dict[str, Any]],
            lakehouse: dict[str, Any] | None, compute: str | None, workspace: dict[str, Any] | None) -> dict[str, Any]:
    """Saves a new notebook ``name`` in ``place``. Refuses a name any place already holds."""
    name = check_notebook_name(name)
    if shelf._taken(name):  # noqa: SLF001
        raise ValueError(f"A notebook named {name!r} already exists. Pick another name.")
    _, folder = shelf._place(place)  # noqa: SLF001
    path = folder / f"{name}.ipynb"
    path.parent.mkdir(parents=True, exist_ok=True)  # its folder, for a name like "Sales/Daily load"
    try:
        open(path, "xb").close()  # reserve the name: a file made in the meantime stops this
    except FileExistsError:
        raise ValueError(f"A notebook named {name!r} already exists. Pick another name.") from None
    try:
        NotebookStore(folder).save(name, cells, lakehouse, compute or "default", workspace or {})
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return shelf.load(name) | {"place": shelf.place_of(name)}


def save_copy(shelf: NotebookShelf, name: str, new_name: str, place: str | None) -> dict[str, Any]:
    """A copy of ``name`` with the same cells, lakehouse, compute and publish workspace. The copy
    isn't linked to a Fabric item: its first Publish makes one under its own name."""
    nb = shelf.load(name)
    cells = [{k: v for k, v in c.items() if k in ("type", "source", "meta", "cell_id")} for c in nb["cells"]]
    return create(shelf, new_name, place, cells, nb.get("lakehouse"), nb.get("compute"), nb.get("workspace"))


# ---- a notebook file from anywhere, or a Fabric notebook's definition


def _parse(content: str) -> dict[str, Any]:
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"The file is over {MAX_FILE_BYTES // (1024 * 1024)} MB, so it isn't opened.")
    try:
        nb = json.loads(content)
    except (ValueError, RecursionError):  # RecursionError: nested too deep to be a notebook
        raise ValueError("This file isn't a notebook: it isn't JSON.") from None
    return checked(nb)


def checked(nb: Any) -> dict[str, Any]:
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise ValueError("This file isn't a notebook: it has no cells.")
    if not isinstance(nb.get("nbformat"), int) or nb["nbformat"] < 4:
        raise ValueError("Only Jupyter notebook format 4 (.ipynb from Jupyter, VS Code or Fabric) can be opened.")
    if any(not isinstance(c, dict) for c in nb["cells"]):
        raise ValueError("This file isn't a notebook: a cell isn't readable.")
    return nb


def _language(nb: dict[str, Any]) -> str:
    meta = nb.get("metadata") if isinstance(nb.get("metadata"), dict) else {}
    for key in ("kernel_info", "kernelspec", "language_info"):
        v = meta.get(key)
        if isinstance(v, dict) and isinstance(v.get("name"), str):
            return v["name"].lower()
    return ""


def _fabric_lakehouse(nb: dict[str, Any]) -> dict[str, Any] | None:
    """The default lakehouse Fabric saved in the notebook (metadata.dependencies.lakehouse)."""
    meta = nb.get("metadata") if isinstance(nb.get("metadata"), dict) else {}
    lh = ((meta.get("dependencies") or {}).get("lakehouse") or {}) if isinstance(meta.get("dependencies"), dict) else {}
    if not isinstance(lh, dict) or not lh.get("default_lakehouse") or not lh.get("default_lakehouse_workspace_id"):
        return None
    return {"workspace_id": str(lh["default_lakehouse_workspace_id"]), "lakehouse_id": str(lh["default_lakehouse"]),
            "name": str(lh.get("default_lakehouse_name") or "Lakehouse")}


def read(nb: dict[str, Any]) -> dict[str, Any]:
    """Cells, lakehouse and notes from a parsed notebook. KratosForge's setup cell (added when it
    publishes) is dropped; outputs are never kept."""
    parsed = from_ipynb(nb)
    cells = [c for c in parsed["cells"] if not (c.get("type") == "code" and c.get("source", "").startswith(SHIM_MARKER))]
    dropped_setup = len(cells) < len(parsed["cells"])
    lakehouse = parsed.get("lakehouse") or _fabric_lakehouse(nb)
    language = _language(nb)
    code = [c for c in cells if c.get("type") != "markdown"]
    notes = []
    if language not in _PYTHON:
        notes.append(f"It's a {language} notebook. KratosForge runs Python cells only, so run it in Fabric.")
    spark = [n for n, c in enumerate(cells, 1) if c.get("type") != "markdown" and uses_spark([c])]
    if spark:
        notes.append(f"Cell{'s' if len(spark) > 1 else ''} {', '.join(map(str, spark))} use{'' if len(spark) > 1 else 's'} "
                     "Spark. On this PC spark runs on DuckDB's Spark-compatible API, so check them with Validate.")
    if dropped_setup:
        notes.append("The setup cell KratosForge adds when it publishes was left out; Publish adds it again.")
    outputs = any(c.get("outputs") for c in nb["cells"] if isinstance(c, dict))
    return {"cells": cells, "lakehouse": lakehouse, "workspace": parsed.get("workspace"), "compute": parsed.get("compute"),
            "summary": {"cells": len(cells), "code": len(code), "markdown": len(cells) - len(code),
                        "language": language or "python", "outputs_dropped": outputs, "notes": notes}}


def import_check(shelf: NotebookShelf, filename: str, content: str) -> dict[str, Any]:
    """What opening this file would add, without writing anything."""
    got = read(_parse(content))
    stem = Path(str(filename or "Notebook").replace("\\", "/")).name
    stem = stem[:-6] if stem.lower().endswith(".ipynb") else stem
    name = free_name(shelf, stem)
    return {"name": name, "taken": name != stem, "file_name": stem, "lakehouse": got["lakehouse"],
            "workspace": got["workspace"], **got["summary"]}


def import_file(shelf: NotebookShelf, content: str, name: str, place: str | None) -> dict[str, Any]:
    """Adds the notebook in ``content`` as a new notebook ``name``. The picked file isn't touched."""
    got = read(_parse(content))
    return create(shelf, name, place, got["cells"], got["lakehouse"], got["compute"], got["workspace"])


def from_fabric(shelf: NotebookShelf, definition: dict[str, Any], item: dict[str, Any], place: str | None,
                local_name: str | None) -> dict[str, Any]:
    """Saves a Fabric notebook item's cells as a notebook here, named as the item (Publish finds the
    item by name) and set to publish to the item's workspace. ``local_name`` is the notebook here
    already linked to this item, if any: then nothing is written and that one is named."""
    if local_name:
        return {"exists": True, "name": local_name}
    name = check_notebook_name(item["name"])
    if shelf._taken(name):  # noqa: SLF001
        raise ValueError(f"You already have a notebook named {name!r} that isn't this Fabric item. Rename it first, "
                         "since Publish finds the Fabric item by its name.")
    got = read(checked(ipynb_of(definition)))
    ws = {"workspace_id": item["workspace_id"], "workspace_name": item.get("workspace_name", "")}
    out = create(shelf, name, place, got["cells"], got["lakehouse"], got["compute"], ws)
    return out | {"summary": got["summary"]}


def ipynb_of(definition: dict[str, Any]) -> dict[str, Any]:
    """The .ipynb JSON in a getDefinition result."""
    import base64
    parts = (definition.get("definition") or definition).get("parts") or []
    part = next((p for p in parts if str(p.get("path", "")).endswith(".ipynb")), None)
    if part is None:
        raise ValueError("Fabric didn't return the notebook's cells.")
    return json.loads(base64.b64decode(part["payload"]))


# ---- what was published, to see changes made in Fabric since


def _plain(nb: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in nb.get("cells") or []:
        src = c.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        out.append({"id": c.get("id"), "cell_type": c.get("cell_type", "code"), "source": src.rstrip()})
    return out


def cells_hash(nb: dict[str, Any]) -> str:
    plain = [(c["cell_type"], c["source"]) for c in _plain(nb)]
    return hashlib.sha256(json.dumps(plain).encode()).hexdigest()


def remember_published(folder: Path, item_id: str, workspace_id: str, nb: dict[str, Any], name: str = "") -> None:
    """Keeps the cells as they were sent to Fabric item ``item_id``, and the notebook's name then
    (``name``, without its folders), so the next publish can tell a rename here from one in Fabric."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_safe_id(item_id)}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"workspace_id": workspace_id, "item_id": item_id, "at": _now().isoformat(),
                               "hash": cells_hash(nb), "cells": _plain(nb), **({"name": name} if name else {})}),
                   encoding="utf-8")
    jsonfile.replace(tmp, path)


def published(folder: Path, item_id: str) -> dict[str, Any] | None:
    path = folder / f"{_safe_id(item_id)}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe_id(item_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(item_id)):
        raise ValueError(f"{item_id!r} isn't a Fabric item id.")
    return str(item_id)


def fabric_changes(base: dict[str, Any], current: dict[str, Any], mine: dict[str, Any] | None = None) -> dict[str, Any]:
    """Whether the Fabric item's cells (``current``, .ipynb) changed since ``base`` (from
    remember_published), and how; with ``mine``, also how the notebook here changed since."""
    changed = cells_hash(current) != base["hash"]

    def as_nb(cells: list[dict[str, Any]], with_ids: bool) -> dict[str, Any]:
        return {"cells": [{"id": c["id"] if with_ids else None, "cell_type": c["cell_type"], "source": c["source"]}
                          for c in cells]}

    def diff(new: dict[str, Any]) -> list[dict[str, Any]]:
        a, b = base["cells"], _plain(new)
        ids = all(c.get("id") for c in a + b)  # Fabric may drop cell ids: then compare by position
        return diff_mod.notebook_changes(as_nb(a, ids), as_nb(b, ids))

    return {"changed": changed, "fabric": diff(current) if changed else [],
            "mine": diff(mine) if mine is not None else None, "published_at": base.get("at")}


# ---- Recently deleted


def _trash(shelf: NotebookShelf) -> Path:
    return shelf.folder.parent / "notebooks-deleted"


def _purge(trash: Path, now: dt.datetime) -> None:
    if not trash.is_dir():
        return
    for meta in trash.glob("*.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            expired = dt.datetime.fromisoformat(info["deleted_at"]) + dt.timedelta(days=KEEP_DAYS) < now
        except (OSError, ValueError, KeyError, TypeError):
            continue  # unreadable: left for the person, never guessed at
        if expired:
            meta.with_suffix(".ipynb").unlink(missing_ok=True)
            meta.unlink(missing_ok=True)


def delete(shelf: NotebookShelf, name: str, schedules_off: list[str]) -> dict[str, Any]:
    """Moves the notebook's file to Recently deleted. The copy there is written and checked before
    the notebook's file is removed. ``schedules_off`` (this PC's schedules that ran it, already turned
    off by the caller) is kept so Restore can turn them back on."""
    src = shelf.path(name)
    if not src.is_file():
        raise ValueError(f"There's no notebook named {name!r}.")
    trash = _trash(shelf)
    now = _now()
    _purge(trash, now)
    trash.mkdir(parents=True, exist_ok=True)
    tid = f"{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    data = src.read_bytes()
    dest = trash / f"{tid}.ipynb"
    with open(dest, "xb") as f:
        f.write(data)
    if dest.read_bytes() != data:
        dest.unlink(missing_ok=True)
        raise OSError(f"Couldn't keep a copy of {name} in Recently deleted, so it wasn't deleted.")
    place = shelf.place_of(name)
    kept_until = (now + dt.timedelta(days=KEEP_DAYS)).isoformat()
    jsonfile.write_text(trash / f"{tid}.json", json.dumps({"id": tid, "name": name, "place": place, "deleted_at": now.isoformat(),
                                                                "kept_until": kept_until, "schedules": schedules_off}))
    src.unlink()
    # Its resources folder (notebook_resources) isn't moved or deleted: it stays beside where the
    # notebook was, and the notebook restored under its name, or a new one, picks it up again.
    res = notebook_resources.folder_for(src)
    return {"deleted": name, "id": tid, "kept_until": kept_until, "place": place, "schedules_off": schedules_off,
            "resources_kept": str(res) if res.is_dir() else None}


def deleted(shelf: NotebookShelf) -> list[dict[str, Any]]:
    """Recently deleted notebooks still kept, newest first."""
    trash, now, out = _trash(shelf), _now(), []
    if not trash.is_dir():
        return []
    for meta in trash.glob("*.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if dt.datetime.fromisoformat(info["kept_until"]) < now or not meta.with_suffix(".ipynb").is_file():
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out.append({k: info.get(k) for k in ("id", "name", "place", "deleted_at", "kept_until")}
                   | {"schedules": len(info.get("schedules") or [])})
    return sorted(out, key=lambda r: r["deleted_at"], reverse=True)


def restore(shelf: NotebookShelf, tid: str, name: str | None = None) -> dict[str, Any]:
    """Puts a deleted notebook back where it was (the app's own folder when that repository isn't
    open any more), as ``name`` or its old name. Refuses a name that is taken."""
    if not _TRASH_ID.match(str(tid)):
        raise ValueError("That isn't a deleted notebook.")
    trash = _trash(shelf)
    meta, file = trash / f"{tid}.json", trash / f"{tid}.ipynb"
    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
        data = file.read_bytes()
    except (OSError, ValueError):
        raise ValueError("That notebook isn't in Recently deleted any more.") from None
    target = check_notebook_name(name or info["name"])
    if shelf._taken(target):  # noqa: SLF001
        raise ValueError(f"A notebook named {target!r} already exists. Restore it under another name.")
    root = (info.get("place") or {}).get("root")
    try:
        _, folder = shelf._place(root)  # noqa: SLF001
        moved = False
    except ValueError:
        _, folder = shelf._place(None)  # noqa: SLF001
        moved = True
    dest = folder / f"{target}.ipynb"
    dest.parent.mkdir(parents=True, exist_ok=True)  # its folder comes back too, if it was removed
    try:
        with open(dest, "xb") as f:
            f.write(data)
    except FileExistsError:
        raise ValueError(f"A notebook named {target!r} already exists. Restore it under another name.") from None
    file.unlink(missing_ok=True)
    meta.unlink(missing_ok=True)
    return {"name": target, "place": shelf.place_of(target), "moved_to_pc": moved and bool(root),
            "schedules": info.get("schedules") or [], "renamed": target != info["name"], "was": info["name"]}
