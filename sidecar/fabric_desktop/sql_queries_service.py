"""Saved and shared queries for the Warehouse page (WH-06).

A saved query is a .sql file: a few ``-- key: value`` header lines, then the T-SQL. For example::

    -- query: Revenue by region
    -- warehouse: WH_Gold
    -- description: Last 12 months, for the monthly review
    SELECT ...

They live in the same kinds of place as your own templates (sql_templates_service.py), each in a
folder of its own: ``sql-queries`` in the app's data folder (My queries, this PC only), or in a Git
repository opened in Source control, where the team gets them by committing and pushing. The same
rules hold: a call names a place and a plain file name, never a path; links out of a place are
refused; a new file never replaces one. ``warehouse`` is the warehouse the query was saved from,
shown in the list; a query can be opened on any warehouse. Nothing here runs SQL or calls Fabric.

A query saved on a lakehouse's SQL page (S37) has ``-- lakehouse:`` and ``-- lakehouse_id:`` lines in
place of ``-- warehouse:``. That page lists a query only on the lakehouse whose id it names, and the
Warehouse page leaves it out. A file without those lines reads as before.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .sql_templates_service import MAX_BYTES

METHODS = {"sql_queries_list", "sql_query_save", "sql_query_delete"}

FOLDER = "sql-queries"
MINE = "My queries"
MAX_TITLE = 100
MAX_LINE = 200
_HEADER = re.compile(r"^--\s*(query|title|warehouse|lakehouse|lakehouse_id|description)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_LAKEHOUSE_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def parse(text: str, stem: str = "") -> dict[str, Any]:
    """{"title", "warehouse", "description", "sql"}, plus "lakehouse" and "lakehouse_id" when the file
    has those lines. Header lines come first; with no title line the file name is the title, so any .sql
    file dropped in the folder is a saved query."""
    meta: dict[str, Any] = {"title": "", "warehouse": "", "description": ""}
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _HEADER.fullmatch(lines[i])
        if not m:
            break
        meta["title" if m.group(1).lower() == "query" else m.group(1).lower()] = m.group(2)
        i += 1
    if i and i < len(lines) and not lines[i].strip():
        i += 1  # the blank line render() puts after the header, so the SQL's own comments stay the SQL's
    meta["sql"] = "\n".join(lines[i:]).strip("\n")
    meta["title"] = meta["title"] or stem
    return meta


def problem(meta: dict[str, Any]) -> str:
    title = str(meta.get("title") or "").strip()
    if not title:
        return "Give the query a name."
    if len(title) > MAX_TITLE or "\n" in title:
        return f"Use a name of up to {MAX_TITLE} characters on one line."
    for key in ("warehouse", "lakehouse", "description"):
        value = str(meta.get(key) or "")
        if len(value) > MAX_LINE or "\n" in value:
            return f"Use a {key} of up to {MAX_LINE} characters on one line."
    lakehouse_id = str(meta.get("lakehouse_id") or "")
    if lakehouse_id and not _LAKEHOUSE_ID.fullmatch(lakehouse_id):
        return "That lakehouse id isn't one Fabric gives."
    if lakehouse_id and str(meta.get("warehouse") or "").strip():
        return "A saved query belongs to a warehouse or a lakehouse, not both."
    if str(meta.get("lakehouse") or "").strip() and not lakehouse_id:
        return "A query saved on a lakehouse needs the lakehouse's id."
    if not str(meta.get("sql") or "").strip():
        return "There's no SQL to save."
    return ""


def render(meta: dict[str, Any]) -> str:
    head = [f"-- query: {meta['title'].strip()}"]
    for key in ("warehouse", "lakehouse", "lakehouse_id", "description"):
        if str(meta.get(key) or "").strip():
            head.append(f"-- {key}: {meta[key].strip()}")
    return "\n".join(head) + "\n\n" + str(meta["sql"]).strip("\n") + "\n"


def version(data: bytes) -> str:
    """A short fingerprint of a file's bytes: Save checks it, so a file changed since it was opened
    (a Git pull, another editor) isn't overwritten."""
    return hashlib.sha256(data).hexdigest()[:16]


class SqlQueriesMixin:
    def sql_queries_list(self) -> dict[str, Any]:
        """Every saved query in every place, the places a query can be saved to, and notes on any
        place that was skipped or cut short. A file that can't be read is listed with why."""
        places, found, notes = self._sql_files(FOLDER, MINE, "queries")  # type: ignore[attr-defined]
        out: list[dict[str, Any]] = []
        for p, f in found:
            item: dict[str, Any] = {"place": p["id"], "place_label": p["label"], "name": f.name}
            try:
                if f.stat().st_size > MAX_BYTES:
                    raise ValueError(f"It is larger than {MAX_BYTES // 1024} KB.")
                data = f.read_bytes()
                meta = parse(data.decode("utf-8"), f.stem)
                item.update(meta, version=version(data), problem="" if meta["sql"].strip() else "The file has no SQL.")
            except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
                why = ("It couldn't be read as UTF-8 text." if isinstance(exc, UnicodeDecodeError)
                       else str(exc) if isinstance(exc, ValueError) else "It couldn't be read.")
                item.update(title=f.stem, warehouse="", description="", sql="", version="", problem=why)
            out.append(item)
        return {"queries": out, "notes": notes,
                "places": [{"id": p["id"], "label": p["label"], "folder": str(p["folder"])} for p in places]}

    def sql_query_save(self, place: str, title: str, sql: str, warehouse: str = "", description: str = "",
                       name: str | None = None, expect: str = "", lakehouse: str = "",
                       lakehouse_id: str = "") -> dict[str, Any]:
        """Writes a saved query. ``name`` replaces that file in the place (Save on a tab opened from
        it); without it the file is new, named after the title, and a name that is taken gets a number.
        ``expect`` is the version the tab opened: a file that has changed since is left alone.
        ``lakehouse`` and ``lakehouse_id`` name the lakehouse a query saved on its SQL page is for."""
        meta = {"title": title, "warehouse": warehouse, "description": description, "sql": sql,
                "lakehouse": lakehouse, "lakehouse_id": lakehouse_id}
        why = problem(meta)
        if why:
            raise ValueError(why)
        text = render(meta)
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError(f"A saved query can be up to {MAX_BYTES // 1024} KB.")
        if name:
            path = self._template_file(place, name, FOLDER, MINE, "query")  # type: ignore[attr-defined]
            if not path.is_file():
                raise ValueError("That query's file isn't there any more. Save it as a new one.")
            if expect and version(path.read_bytes()) != expect:
                raise ValueError("That query's file changed since you opened it, perhaps from a Git pull. "
                                 "Open it again to see the change, or use Save as new to keep yours.")
            self._write_over(path, text)  # type: ignore[attr-defined]
        else:
            path = self._write_new(self._template_place(place, FOLDER, MINE), title, text, "Query")  # type: ignore[attr-defined]
        return {"place": place, "name": path.name, "path": str(path), "version": version(text.encode("utf-8"))}

    def sql_query_delete(self, place: str, name: str) -> dict[str, Any]:
        path = self._template_file(place, name, FOLDER, MINE, "query")  # type: ignore[attr-defined]
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return {"place": place, "name": name, "deleted": True}
