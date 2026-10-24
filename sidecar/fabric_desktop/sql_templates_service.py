"""Your own SQL templates for the Warehouse page's template gallery (WH-04, templates people add).

A template is a .sql file: a few ``-- key: value`` header lines, then T-SQL with the gallery's
``<name, kind, default>`` fields (app/ui/sqltemplates.js). For example::

    -- template: Load a dimension
    -- group: Team loads
    -- description: Truncate and reload a dimension from staging
    -- endpoint: no
    TRUNCATE TABLE [<schema_name, sysname, dbo>].[<table_name, sysname, DimCustomer>];

Templates live in two kinds of place, and nowhere else:

- ``mine``: the ``sql-templates`` folder in the app's data folder, on this PC only.
- A Git repository opened in Source control: its ``sql-templates`` folder. Saving one there only
  writes the file; the person commits and pushes it in Source control, so the team gets it with Git.

A call names the place and a plain file name, never a path, so it can't reach any other folder.
Sharing by file is Export (a new file in Downloads, never replacing one) and Import (the page sends
a picked file's text). Nothing here runs SQL or calls Fabric.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .filenames import file_stem

METHODS = {"sql_templates_list", "sql_template_save", "sql_template_import", "sql_template_delete",
           "sql_template_export"}

FOLDER = "sql-templates"
MAX_BYTES = 64 * 1024     # one template file
MAX_PER_PLACE = 200       # templates listed from one place
MAX_TITLE = 100
MAX_LINE = 200            # group and description
KINDS = ("sysname", "columns", "string", "int", "datatype", "datetime")
_HEADER = re.compile(r"^--\s*(template|title|group|description|endpoint)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_PARAM = re.compile(r"<([A-Za-z_]\w*)\s*,\s*(\w+)\s*,\s*([^<>\n]*?)\s*>")
_NAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,64}\.sql$", re.IGNORECASE)


def parse(text: str, stem: str = "") -> dict[str, Any]:
    """{"title", "group", "description", "endpoint", "sql"} from a template file's text. Header lines
    come first; the SQL is everything after them. With no title line the file name is the title."""
    meta: dict[str, Any] = {"title": "", "group": "", "description": "", "endpoint": False}
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _HEADER.match(lines[i])
        if not m:
            break
        key = "title" if m.group(1).lower() == "template" else m.group(1).lower()
        value = m.group(2)
        meta[key] = value.lower() in ("yes", "true", "1") if key == "endpoint" else value
        i += 1
    meta["sql"] = "\n".join(lines[i:]).strip("\n")
    meta["title"] = meta["title"] or stem
    return meta


def problem(meta: dict[str, Any]) -> str:
    """Why this template can't be saved, or ""."""
    title = str(meta.get("title") or "").strip()
    if not title:
        return "Give the template a title."
    if len(title) > MAX_TITLE or "\n" in title:
        return f"Use a title of up to {MAX_TITLE} characters on one line."
    for key in ("group", "description"):
        value = str(meta.get(key) or "")
        if len(value) > MAX_LINE or "\n" in value:
            return f"Use a {key} of up to {MAX_LINE} characters on one line."
    sql = str(meta.get("sql") or "")
    if not sql.strip():
        return "Write the template's SQL."
    for name, kind, _ in _PARAM.findall(sql):
        if kind not in KINDS:
            return f"The field {name} has the kind {kind}. Use one of: {', '.join(KINDS)}."
    return ""


def render(meta: dict[str, Any]) -> str:
    """The file's text: the header lines, then the SQL."""
    head = [f"-- template: {meta['title'].strip()}"]
    if str(meta.get("group") or "").strip():
        head.append(f"-- group: {meta['group'].strip()}")
    if str(meta.get("description") or "").strip():
        head.append(f"-- description: {meta['description'].strip()}")
    head.append(f"-- endpoint: {'yes' if meta.get('endpoint') else 'no'}")
    return "\n".join(head) + "\n" + str(meta["sql"]).strip("\n") + "\n"


class SqlTemplatesMixin:
    def _template_places(self) -> list[dict[str, Any]]:
        """[{"id", "label", "folder": Path}]: this PC, then each Git repository opened here."""
        places = [{"id": "mine", "label": "My templates", "folder": self.root / FOLDER}]  # type: ignore[attr-defined]
        for repo in self.git_repos()["repos"]:  # type: ignore[attr-defined]
            if repo.get("exists"):
                places.append({"id": repo["path"], "label": repo.get("name") or Path(repo["path"]).name,
                               "folder": Path(repo["path"]) / FOLDER})
        return places

    def _template_place(self, place: str) -> dict[str, Any]:
        for p in self._template_places():
            if p["id"] == place:
                return p
        raise ValueError("Pick My templates or a repository opened in Source control.")

    def _template_file(self, place: str, name: str) -> Path:
        if not isinstance(name, str) or not _NAME.match(name) or name.startswith((".", " ")) or ".." in name:
            raise ValueError("That isn't a template file name.")
        return self._template_place(place)["folder"] / name

    def sql_templates_list(self) -> dict[str, Any]:
        """Every template in every place, and the places a template can be saved to. A file that
        can't be read, or is too large, is listed with why, so it isn't silently left out."""
        out: list[dict[str, Any]] = []
        places = self._template_places()
        for p in places:
            folder: Path = p["folder"]
            try:
                files = sorted(f for f in folder.iterdir() if f.suffix.lower() == ".sql" and f.is_file())
            except OSError:
                continue  # no folder yet
            for f in files[:MAX_PER_PLACE]:
                item: dict[str, Any] = {"place": p["id"], "place_label": p["label"], "name": f.name}
                try:
                    if f.stat().st_size > MAX_BYTES:
                        raise ValueError(f"It is larger than {MAX_BYTES // 1024} KB.")
                    meta = parse(f.read_text(encoding="utf-8"), f.stem)
                    item.update(meta, problem=problem(meta))
                except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
                    why = ("It couldn't be read as UTF-8 text." if isinstance(exc, UnicodeDecodeError)
                           else str(exc) if isinstance(exc, ValueError) else "It couldn't be read.")
                    item.update(title=f.stem, group="", description="", endpoint=False, sql="", problem=why)
                out.append(item)
        return {"templates": out, "kinds": list(KINDS),
                "places": [{"id": p["id"], "label": p["label"], "folder": str(p["folder"])} for p in places]}

    def sql_template_save(self, place: str, title: str, sql: str, group: str = "", description: str = "",
                          endpoint: bool = False, name: str | None = None) -> dict[str, Any]:
        """Writes a template. ``name`` replaces that file in the place (Edit); without it the file is
        new, named after the title, and a name that is taken gets a number."""
        meta = {"title": title, "group": group, "description": description, "endpoint": bool(endpoint), "sql": sql}
        why = problem(meta)
        if why:
            raise ValueError(why)
        text = render(meta)
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError(f"A template can be up to {MAX_BYTES // 1024} KB.")
        if name:
            path = self._template_file(place, name)
            if not path.is_file():
                raise ValueError("That template isn't there any more. Save it as a new one.")
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp, path)
            return {"place": place, "name": path.name, "path": str(path)}
        folder: Path = self._template_place(place)["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        base = file_stem(title, "Template")
        for n in range(1, 1000):
            path = folder / (f"{base}.sql" if n == 1 else f"{base} ({n}).sql")
            try:
                with open(path, "x", encoding="utf-8", newline="\n") as f:
                    f.write(text)
            except FileExistsError:
                continue
            return {"place": place, "name": path.name, "path": str(path)}
        raise ValueError("Couldn't find a free file name for the template.")

    def sql_template_import(self, place: str, file_name: str, text: str) -> dict[str, Any]:
        """Saves a picked .sql file's text (sent by the page) as a new template in ``place``."""
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError(f"Pick a .sql file of up to {MAX_BYTES // 1024} KB.")
        meta = parse(text, Path(str(file_name).replace("\\", "/")).stem)
        return self.sql_template_save(place, meta["title"], meta["sql"], meta["group"], meta["description"],
                                      meta["endpoint"])

    def sql_template_delete(self, place: str, name: str) -> dict[str, Any]:
        path = self._template_file(place, name)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return {"place": place, "name": name, "deleted": True}

    def sql_template_export(self, place: str, name: str) -> dict[str, Any]:
        """Copies a template to a new file in Downloads; an existing file is never replaced."""
        src = self._template_file(place, name)
        text = src.read_text(encoding="utf-8")
        folder = Path.home() / "Downloads"
        folder.mkdir(parents=True, exist_ok=True)
        stem = file_stem(src.stem, "Template")
        for n in range(1, 1000):
            path = folder / (f"{stem}.sql" if n == 1 else f"{stem} ({n}).sql")
            try:
                with open(path, "x", encoding="utf-8", newline="\n") as f:
                    f.write(text)
            except FileExistsError:
                continue
            return {"path": str(path), "at": time.time()}
        raise ValueError("Couldn't find a free file name in Downloads for the template.")
