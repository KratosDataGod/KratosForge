"""Export to Ossie and Import from Ossie on the Semantic model tab (model_ossie.py does the converting).

Export reads a model's definition from the environment's workspace as TMSL (getDefinition, read only) and
converts it on this PC. Import previews first and writes only when ``confirmed``: the model's definition through
updateDefinition (or a new model with ``mode="new"``). No refresh is started. The Ossie file and the model's
TMDL are saved in the metadata project's folder, so they go to Git with the rest of the project.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, cast

from . import model_ossie, tmdl
from .jsonfile import write_text
from .meta.store import OneLakeStore
from .notebook_files_service import _is_link

METHODS = {"meta_model_ossie_models", "meta_model_ossie_export", "meta_model_ossie_save", "meta_model_ossie_read",
           "meta_model_ossie_preview", "meta_model_ossie_import", "meta_model_tmdl_save",
           "meta_model_ossie_folders", "meta_model_ossie_export_folder", "meta_model_ossie_to_tmdl"}
FOLDER = "semantic"
MODES = ("update", "replace", "new")
_SAFE = re.compile(r"[^A-Za-z0-9 _.-]+")


def _real(path: Path, kind: Any) -> bool:
    """Whether ``path`` is a folder or file (``kind`` is stat.S_ISDIR or S_ISREG) in place, never a symbolic
    link, junction or other Windows link, which could point outside the project or at a network share. Only
    lstat is called, so a link's target is never opened."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return not _is_link(st) and kind(st.st_mode)


MAX_DEPTH = 8  # definition/tables/x.tmdl is two deep; Power BI projects never go near this
MAX_FILES = 20_000


_SAVED = (".tmdl", ".pbism", ".json")  # the files Save TMDL writes, and so may remove


def _tmdl_files(top: Path, depth: int = 0, found: list[Path] | None = None,
                suffixes: tuple[str, ...] = (".tmdl",)) -> list[Path]:
    """The .tmdl files (or files ending in one of ``suffixes``) under ``top``, never going into a link or junction,
    at most MAX_DEPTH folders down and MAX_FILES files (a folder that holds more isn't a semantic model)."""
    found = [] if found is None else found
    if depth > MAX_DEPTH:
        raise ValueError(f"{top.name} is nested too deeply to be a semantic model folder. Pick the model's own folder.")
    with os.scandir(top) as entries:
        for e in entries:
            here = Path(e.path)
            if _real(here, stat.S_ISDIR):
                _tmdl_files(here, depth + 1, found, suffixes)
            elif e.name.endswith(suffixes) and _real(here, stat.S_ISREG):
                found.append(here)
                if len(found) > MAX_FILES:
                    raise ValueError(f"There are more than {MAX_FILES} TMDL files here, so this isn't one semantic model. Pick the model's own folder.")
    return found


def _folder_name(name: str) -> str:
    """semantic/<this>.SemanticModel for a model called ``name``: never the name used raw."""
    return tmdl.file_name(_SAFE.sub("_", name).strip() or "model", set())


def _no_links(root: Path, target: Path) -> None:
    """Refuse when any folder from ``root`` down to ``target`` that exists is a link or junction, or not a folder."""
    rel = target.relative_to(root)
    here = root
    for part in rel.parts:
        here = here / part
        if os.path.lexists(here) and not _real(here, stat.S_ISDIR):
            raise ValueError(f"{here.relative_to(root).as_posix()} is a link, a junction or a file, so nothing is written there")


def _file_key(rel: str) -> str:
    """One key for every spelling of a file name that is one file on Windows: NTFS ignores case (but not Unicode
    normalisation, so NFC and NFD names stay two files). A file already there under another case is written in
    place, under its own name, so Git doesn't see a rename, and it is never taken for a stale file."""
    return rel.casefold()


def _by_key(folder: Path, files: list[Path]) -> dict[str, list[Path]]:
    """``files`` (under ``folder``) grouped by ``_file_key`` of their path in ``folder``."""
    out: dict[str, list[Path]] = {}
    for f in sorted(files):
        out.setdefault(_file_key(f.relative_to(folder).as_posix()), []).append(f)
    return out


def _in_place(folder: Path, rel: str, existing: dict[str, list[Path]]) -> Path:
    """Where ``rel`` is written in ``folder``: the file already there under another spelling of its name if there
    is one (Windows would write into it anyway), so it keeps its name and isn't removed as stale."""
    path = folder.joinpath(*PurePosixPath(rel).parts)
    same = existing.get(_file_key(PurePosixPath(rel).as_posix())) or []
    # Compared as text: WindowsPath equality ignores case, which would pick the new spelling instead.
    return path if not same or any(str(p) == str(path) for p in same) else same[0]


def _same_text(path: Path, content: str) -> bool:
    try:
        return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n") == content
    except (OSError, UnicodeDecodeError):
        return False


def _model_counts(bim: dict[str, Any]) -> dict[str, int]:
    model = bim.get("model") or {}
    tables = [t for t in model.get("tables") or [] if isinstance(t, dict)]
    return {"tables": len(tables), "columns": sum(len(t.get("columns") or []) for t in tables),
            "measures": sum(len(t.get("measures") or []) for t in tables),
            "relationships": len(model.get("relationships") or []), "roles": len(model.get("roles") or []),
            "hierarchies": sum(len(t.get("hierarchies") or []) for t in tables)}


_CULTURE_LINE = re.compile(r"^(?:cultureInfo|culture)[ \t]", re.M)  # only such files can define a culture


def _refuse_unreadable(read_report: list[dict[str, str]]) -> None:
    """Refuse the write when the reader couldn't read part of a TMDL file outside cultures/ (``read_report`` names
    it in ``file``). Rewriting that file from what was read would drop those lines from the user's folder, and
    keeping it as it was would leave it disagreeing with the model written, so nothing is written. The message
    names each file and what in it KratosForge can't read. Culture files are kept instead (``_kept_cultures``)."""
    lost: dict[str, list[str]] = {}
    for r in read_report:
        rel = str(r.get("file") or "")
        if r.get("kind") == "lost" and rel and PurePosixPath(rel).parts[:2] != ("definition", "cultures"):
            lost.setdefault(rel, []).append(str(r.get("object") or ""))
    if not lost:
        return
    parts = []
    for rel in sorted(lost):
        short = PurePosixPath(rel).relative_to("definition").as_posix() if rel.startswith("definition/") else rel
        what = list(dict.fromkeys(lost[rel]))
        more = f" and {len(what) - 3} more" if len(what) > 3 else ""
        parts.append(f"{short} holds lines KratosForge can't read ({', '.join(what[:3])}{more})")
    them = "them" if len(parts) == 1 else "those lines"
    raise ValueError(f"{'; '.join(parts)}, so it would lose {them} if rewritten; nothing was written. "
                     "Take those lines out of the folder by hand, or keep it as it is, then preview again.")


def _kept_cultures(folder: Path, read_report: list[dict[str, str]]) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    """The culture files under ``folder`` kept as they are, each with the cultures it defines, and one report row per
    file saying so. A file the reader couldn't read in full (``read_report`` names it in ``file``) is kept: rewriting
    it from what was read would drop the translations KratosForge can't read. So is every other culture file that
    defines one of those cultures (to a fixed point), or its part of the culture would be dropped with nobody to
    write it. Culture names compare as file names do (``_file_key``). A kept culture also defined outside
    cultures/ can't be kept whole, so that is refused."""
    lost: dict[str, list[str]] = {}
    for r in read_report:
        rel = str(r.get("file") or "")
        if r.get("kind") == "lost" and PurePosixPath(rel).parts[:2] == ("definition", "cultures"):
            lost.setdefault(rel, []).append(r["object"])
    if not lost:
        return {}, []
    defined: dict[str, list[str]] = {}  # every culture file: the cultures it defines
    cultures_dir = folder / "definition" / "cultures"
    for f in sorted(_tmdl_files(cultures_dir) if _real(cultures_dir, stat.S_ISDIR) else []):
        rel = f.relative_to(folder).as_posix()
        _no_links(folder, f.parent)
        if not _real(f, stat.S_ISREG):  # read as a file in place, never through a link
            raise ValueError(f"{rel} is a link or not a file, so it isn't read")
        cultures = tmdl.to_bim({rel: f.read_text(encoding="utf-8-sig")})[0]["model"].get("cultures") or []
        defined[rel] = [c["name"] for c in cultures if isinstance(c, dict) and isinstance(c.get("name"), str)]
    kept = {rel: defined.get(rel, []) for rel in lost}
    names = {_file_key(n) for ns in kept.values() for n in ns}
    grew = True
    while grew:
        grew = False
        for rel, ns in defined.items():
            if rel not in kept and names & {_file_key(n) for n in ns}:
                kept[rel] = ns
                names |= {_file_key(n) for n in ns}
                grew = True
    definition = folder / "definition"
    for f in sorted(_tmdl_files(definition) if _real(definition, stat.S_ISDIR) else []):
        rel = f.relative_to(folder).as_posix()
        if PurePosixPath(rel).parts[:2] == ("definition", "cultures"):
            continue
        text = f.read_text(encoding="utf-8-sig")  # _tmdl_files lists regular files in place only
        if not _CULTURE_LINE.search(text):
            continue
        for c in tmdl.to_bim({rel: text})[0]["model"].get("cultures") or []:
            if isinstance(c, dict) and _file_key(str(c.get("name"))) in names:
                raise ValueError(f"Culture {c.get('name')} is kept as it is in cultures/, and defined outside cultures/ too, "
                                 "so it can't be kept whole and nothing was written. Move it into its culture file.")
    rows = []
    for rel in sorted(kept):
        short = PurePosixPath(rel).relative_to("definition").as_posix()
        if rel in lost:
            message = f"kept as it is, because it holds translations KratosForge can't read: {', '.join(lost[rel])}"
        else:
            shared = sorted(n for n in kept[rel] if any(_file_key(n) in {_file_key(m) for m in kept[o]} for o in lost))
            message = (f"kept as it is, because it defines {', '.join(shared or kept[rel])} with a culture file that is "
                       "kept as it is, so rewriting it would drop that part of the culture")
        rows.append({"object": short, "kind": "lost", "file": rel, "message": message})
    return kept, rows


def _folder_bim(folder: Path, name: str, path: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The model kept in ``folder`` (semantic/<name>.SemanticModel) as a parsed model.bim, read on this PC from its
    TMDL (or its model.bim), and the reader's report. ``path`` names it in messages."""
    definition = folder / "definition"
    if _real(definition, stat.S_ISDIR):
        files: dict[str, str] = {}
        total = 0
        for f in sorted(_tmdl_files(definition)):
            total += f.stat().st_size
            if total > tmdl.MAX_CHARS * 4:
                raise ValueError(f"{name}'s TMDL is too large to read on this PC")
            files[f.relative_to(folder).as_posix()] = f.read_text(encoding="utf-8-sig")
        bim, read_report = tmdl.to_bim(files, name)
    elif _real(folder / "model.bim", stat.S_ISREG):
        if (folder / "model.bim").stat().st_size > tmdl.MAX_CHARS * 4:
            raise ValueError(f"{name}'s model.bim is too large to read on this PC")
        try:
            bim, read_report = json.loads((folder / "model.bim").read_text(encoding="utf-8-sig")), []
        except RecursionError:
            raise ValueError(f"{path}/model.bim is nested too deeply to be a semantic model") from None
        if not isinstance(bim, dict):
            raise ValueError(f"{path}/model.bim isn't a semantic model")
        bim["name"] = name
    else:
        raise ValueError(f"{path} has neither definition/ (TMDL) nor model.bim")
    return bim, read_report


class ModelOssieMixin:
    def _ossie_workspace(self, env: str) -> tuple[Any, dict[str, Any]]:
        project = self._project()  # type: ignore[attr-defined]
        cfg = self._env_cfg(project, env)  # type: ignore[attr-defined]
        if not cfg.get("workspace_id") or cfg.get("target", "onelake" if env != "dev" else "local") == "local":
            raise ValueError("Semantic models live in a Fabric workspace, and this environment keeps its tables on "
                             "this PC. Switch to Test or Prod.")
        return project, cfg

    def _ossie_model(self, workspace_id: str, name: str) -> dict[str, Any] | None:
        return next((m for m in self.fabric.list_semantic_models(workspace_id)  # type: ignore[attr-defined]
                     if m["displayName"] == name), None)

    def _model_bim(self, workspace_id: str, model: dict[str, Any]) -> dict[str, Any]:
        """The model's definition as a parsed model.bim. Fabric converts TMDL models to TMSL on request."""
        try:
            got = self.fabric.get_item_definition(workspace_id, model["id"], "TMSL")  # type: ignore[attr-defined]
        except Exception as exc:  # an empty model here would turn into an update that removes everything
            raise RuntimeError(f"Couldn't read {model.get('displayName') or 'the model'}'s definition "
                               f"({type(exc).__name__}: {exc}). Try again.") from exc
        part = next((p for p in (got.get("definition") or {}).get("parts", []) if p.get("path") == "model.bim"), None)
        if part is None:
            raise RuntimeError(f"Fabric didn't return {model.get('displayName') or 'the model'}'s model.bim")
        return json.loads(base64.b64decode(part.get("payload") or "").decode("utf-8-sig"))

    def meta_model_ossie_models(self, env: str = "test") -> dict[str, Any]:
        """The semantic models Export can read in the environment's workspace."""
        _, cfg = self._ossie_workspace(env)
        models = self.fabric.list_semantic_models(cfg["workspace_id"])  # type: ignore[attr-defined]
        return {"env": env, "workspace_name": cfg.get("workspace_name", ""),
                "models": sorted(({"id": m["id"], "name": m["displayName"]} for m in models), key=lambda m: m["name"].lower())}

    def meta_model_ossie_export(self, env: str = "test", name: str = "", translate_dax: bool = True) -> dict[str, Any]:
        """The named model as Ossie YAML with the conversion report. Reads only; nothing is saved."""
        model_ossie.require()  # before reading from Fabric
        _, cfg = self._ossie_workspace(env)
        model = self._ossie_model(cfg["workspace_id"], name)
        if model is None:
            raise LookupError(f"There's no semantic model named {name} in {cfg.get('workspace_name') or 'the workspace'}")
        out = model_ossie.export(self._model_bim(cfg["workspace_id"], model), translate_dax=translate_dax)
        return out | {"model": name, "path": f"{FOLDER}/{_SAFE.sub('_', name).strip() or 'model'}.ossie.yaml"}

    def _model_folders(self, project: Any) -> list[Path]:
        base = Path(project.root, FOLDER)
        if not _real(base, stat.S_ISDIR):
            return []
        return sorted((Path(e.path) for e in os.scandir(base)
                       if e.name.endswith(".SemanticModel") and _real(Path(e.path), stat.S_ISDIR)), key=lambda p: p.name.lower())

    def meta_model_ossie_folders(self) -> dict[str, Any]:
        """The semantic models kept in the project's folder (semantic/<name>.SemanticModel/, the Power BI
        project layout Save TMDL to Git writes), which Export reads on this PC with no Fabric call. ``unavailable``
        says why Export and Import can't run on this engine (a missing package), or is empty."""
        project = self._project()  # type: ignore[attr-defined]
        return {"folders": [{"path": p.relative_to(project.root).as_posix(), "name": p.name.removesuffix(".SemanticModel")}
                            for p in self._model_folders(project)],
                "unavailable": model_ossie.unavailable()}

    def meta_model_ossie_export_folder(self, path: str, translate_dax: bool = True) -> dict[str, Any]:
        """A model kept in the project's folder as Ossie YAML, read from its TMDL (or model.bim) on this PC.
        Nothing is read from Fabric and nothing is saved. The report also lists anything the TMDL reader left out."""
        model_ossie.require()
        project = self._project()  # type: ignore[attr-defined]
        rel = PurePosixPath(path.replace("\\", "/"))
        folder = next((p for p in self._model_folders(project) if p.relative_to(project.root).as_posix() == rel.as_posix()), None)
        if folder is None:
            raise LookupError(f"There's no model folder {path}; pick one from {FOLDER}/<name>.SemanticModel")
        name = folder.name.removesuffix(".SemanticModel")
        bim, read_report = _folder_bim(folder, name, path)
        out = model_ossie.export(bim, translate_dax=translate_dax)
        return out | {"model": name, "report": read_report + out["report"], "round_trip": model_ossie.round_trip(bim),
                      "path": f"{FOLDER}/{_SAFE.sub('_', name).strip() or 'model'}.ossie.yaml"}

    def _ossie_path(self, project: Any, path: str, suffixes: tuple[str, ...]) -> Path:
        rel = PurePosixPath(path.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or not rel.name.endswith(suffixes):
            raise ValueError(f"Save it as a path inside the project ending {' or '.join(suffixes)}, like {FOLDER}/Sales.ossie.yaml")
        return Path(project.root, *rel.parts)

    def meta_model_ossie_save(self, path: str, text: str) -> dict[str, Any]:
        """Save an Ossie document in the metadata project's folder (it shows in Changes, ready to commit)."""
        project = self._project()  # type: ignore[attr-defined]
        model_ossie.parse(text)
        target = self._ossie_path(project, path, (".ossie.yaml", ".ossie.yml", ".ossie.json"))
        write_text(target, text)
        return {"path": target.relative_to(project.root).as_posix()}

    def meta_model_ossie_read(self, path: str) -> dict[str, Any]:
        """An Ossie file from the project's folder, for Import."""
        project = self._project()  # type: ignore[attr-defined]
        target = self._ossie_path(project, path, (".yaml", ".yml", ".json"))
        if target.stat().st_size > model_ossie.MAX_CHARS * 4:  # UTF-8 is at most 4 bytes a character
            raise ValueError(f"{path} is too large for an Ossie model")
        text = target.read_text(encoding="utf-8")
        model_ossie.parse(text)  # refuse here what Import would refuse, before the UI shows it
        return {"path": target.relative_to(project.root).as_posix(), "text": text}

    def _ossie_import_plan(self, env: str, text: str, name: str, mode: str, remove: list[str] | None,
                           layer: str | None) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError(f"mode is one of {', '.join(MODES)}")
        model_ossie.require()  # before any Fabric call, even when a name is given and the file isn't parsed yet
        project, cfg = self._ossie_workspace(env)
        name = (name or model_ossie.parse(text)["name"]).strip()
        store = self._store(project, env)  # type: ignore[attr-defined]
        lakehouse = project.layer(self._model_layer(project, layer)).lakehouse  # type: ignore[attr-defined]
        source = None
        if isinstance(store, OneLakeStore) and lakehouse in store.lakehouse_ids:
            source = {"workspaceId": store.workspace_of(lakehouse), "itemId": store.lakehouse_ids[lakehouse]}
        incoming, report = model_ossie.to_tmsl(text, source=source)
        if source is None:
            report.append({"object": "Direct Lake", "kind": "lost",
                           "message": f"{lakehouse} isn't in this environment yet, so new tables read from placeholder ids; promote first"})
        existing = self._ossie_model(cfg["workspace_id"], name)
        if mode == "new" and existing is not None:
            raise FileExistsError(f"{name} already exists in {cfg.get('workspace_name') or 'the workspace'}; "
                                  "pick Update or Replace, or another name")
        if mode != "new" and existing is None:
            raise LookupError(f"There's no semantic model named {name} to {mode}; pick New model to create it")
        current = self._model_bim(cfg["workspace_id"], existing) if existing else None
        final = model_ossie.merge(cast(dict[str, Any], current), incoming, remove) if mode == "update" else incoming
        final["name"] = name
        return {"final": final, "existing": existing, "cfg": cfg, "name": name, "report": report,
                "changes": model_ossie.plan(current, final),
                "removable": model_ossie.removable(current, incoming) if mode == "update" else []}

    def meta_model_ossie_preview(self, env: str = "test", text: str = "", name: str = "", mode: str = "update",
                                 remove: list[str] | None = None, layer: str | None = None) -> dict[str, Any]:
        """What importing the Ossie document would change, and what can't be carried over. Writes nothing.

        ``removable`` lists what an update keeps although the file doesn't have it; pass any of them in
        ``remove`` to take them out."""
        p = self._ossie_import_plan(env, text, name, mode, remove, layer)
        return {"env": env, "name": p["name"], "mode": mode, "exists": p["existing"] is not None,
                "workspace_name": p["cfg"].get("workspace_name", ""), "changes": p["changes"],
                "removable": p["removable"], "report": p["report"]}

    def meta_model_ossie_import(self, env: str = "test", text: str = "", name: str = "", mode: str = "update",
                                remove: list[str] | None = None, layer: str | None = None,
                                confirmed: bool = False) -> dict[str, Any]:
        """Write the Ossie document into the model. Without ``confirmed`` the answer is ``{"confirm": {...}}``
        and nothing is written, so the user sees what changes first. No refresh runs."""
        p = self._ossie_import_plan(env, text, name, mode, remove, layer)
        counts: dict[str, int] = {}
        for row in p["changes"]:
            counts[row["change"]] = counts.get(row["change"], 0) + 1
        if not confirmed:
            return {"confirm": {"name": p["name"], "mode": mode, "workspace_name": p["cfg"].get("workspace_name", ""),
                                "counts": counts}}
        ws = p["cfg"]["workspace_id"]
        model = self.fabric.save_semantic_model(  # type: ignore[attr-defined]
            ws, p["name"], model_ossie.bim_parts(p["final"]), "Imported from an Apache Ossie file by KratosForge",
            replace=mode != "new")
        model_id = model.get("id") or (self._ossie_model(ws, p["name"]) or {}).get("id", "")
        return {"name": p["name"], "id": model_id, "created": model.get("created", False), "mode": mode,
                "counts": counts, "changes": p["changes"], "report": p["report"],
                "needs_dax": [r["object"] for r in p["changes"] if r["change"] == "needs_dax"],
                "url": self._model_url(ws, model_id) if model_id else ""}  # type: ignore[attr-defined]

    def meta_model_tmdl_save(self, env: str = "test", name: str = "") -> dict[str, Any]:
        """Save the model's TMDL, as Fabric has it, under semantic/<name>.SemanticModel/ in the project's
        folder (the Power BI project layout), so Git holds what's in Fabric. Files Fabric no longer returns
        are removed from that folder."""
        project, cfg = self._ossie_workspace(env)
        model = self._ossie_model(cfg["workspace_id"], name)
        if model is None:
            raise LookupError(f"There's no semantic model named {name} in {cfg.get('workspace_name') or 'the workspace'}")
        got = self.fabric.get_item_definition(cfg["workspace_id"], model["id"], "TMDL")  # type: ignore[attr-defined]
        parts = {p["path"]: base64.b64decode(p.get("payload") or "").decode("utf-8")
                 for p in (got.get("definition") or {}).get("parts", []) if p.get("path")}
        if not parts:
            raise RuntimeError(f"Fabric returned no definition for {name}")
        folder = Path(project.root, FOLDER, f"{_SAFE.sub('_', name).strip() or 'model'}.SemanticModel")
        for path in parts:
            rel = PurePosixPath(path)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"Fabric returned an unexpected part path: {path}")
        root = Path(project.root)
        _no_links(root, folder)
        # Never through a link or junction (rglob walks into a Windows junction), so nothing outside is ever removed.
        existing = _by_key(folder, _tmdl_files(folder, suffixes=_SAVED) if _real(folder, stat.S_ISDIR) else [])
        targets = {}
        for path, content in parts.items():  # every target is checked before anything is written
            target = _in_place(folder, path, existing)  # never removed below as stale under another spelling
            _no_links(root, target.parent)
            if os.path.lexists(target) and not _real(target, stat.S_ISREG):
                raise ValueError(f"{target.relative_to(root).as_posix()} is a link or a folder, so it isn't overwritten")
            if not target.resolve().is_relative_to(folder.resolve()):
                raise ValueError(f"Fabric returned a part outside the model's folder: {path}")
            targets[target] = content
        written = []
        for target, content in targets.items():
            write_text(target, content)
            written.append(target)
        removed = []
        for old in sorted(f for group in existing.values() for f in group):
            if old in written:
                continue
            _no_links(root, old.parent)  # checked again just before the unlink
            if _real(old, stat.S_ISREG) and old.resolve().is_relative_to(folder.resolve()):
                old.unlink()
                removed.append(old.relative_to(project.root).as_posix())
        return {"folder": folder.relative_to(project.root).as_posix(), "files": len(written), "removed": removed}

    def meta_model_ossie_to_tmdl(self, text: str = "", name: str = "", confirmed: bool = False,
                                 token: str = "", remove: list[str] | None = None) -> dict[str, Any]:
        """An Ossie document saved as TMDL under semantic/<name>.SemanticModel/ in the project's folder (the Power BI
        project layout), converted on this PC with no Fabric call (Preview, PB-110).

        Without ``confirmed`` nothing is written: the answer lists the files, the TMDL files that would be removed
        because this model no longer has them, and the report of anything that can't make the trip, by name. With
        it and the preview's ``token``, every file is written whole (temporary file, then swapped in), and only
        regular .tmdl files under definition/ that this write didn't produce are removed. The token binds the
        confirm to what the preview showed: if the files or the removals changed since, nothing is written.
        Links and junctions are never followed.

        When the folder already holds the model, the file is laid over it like an Import update
        (``model_ossie.merge``): what the file doesn't have stays (roles, hierarchies, measures written by hand)
        unless named in ``remove``, and ``changes`` and ``removable`` say what changes, as Import's preview does.
        Translations (cultures/) are read and written like the rest, so they stay too: a culture the file doesn't
        have is kept, and one it has (from the Power BI section of an Ossie file this app wrote) is laid over.
        A culture file the reader can't read in full (a partition's or a KPI's translation, a property it doesn't
        know) is kept byte for byte: never rewritten or removed, shown as "same", with one report row naming what it
        holds that KratosForge can't read (``_kept_cultures``). Any other TMDL file the reader can't read in full
        (a property or line it doesn't know) is refused, in the preview and the confirm alike, naming the file and
        what it can't read: rewriting it would drop those lines, keeping it would disagree with the model written
        (``_refuse_unreadable``)."""
        model_ossie.require()
        project = self._project()  # type: ignore[attr-defined]
        name = (name or str(model_ossie.parse(text)["name"])).strip()
        root = Path(project.root)
        folder = root / FOLDER / f"{_folder_name(name)}.SemanticModel"
        definition = folder / "definition"
        _no_links(root, definition)
        incoming, report = model_ossie.to_tmsl(text)
        current: dict[str, Any] | None = None
        read_report: list[dict[str, str]] = []
        if (_real(definition, stat.S_ISDIR) and _tmdl_files(definition)) or _real(folder / "model.bim", stat.S_ISREG):
            current, read_report = _folder_bim(folder, name, folder.relative_to(root).as_posix())
        _refuse_unreadable(read_report)  # before the confirm too, so an old token never writes over such a file
        kept, kept_rows = _kept_cultures(folder, read_report)
        report = [r for r in read_report if r.get("file") not in kept] + kept_rows + report
        final = model_ossie.merge(current, incoming, remove) if current else incoming
        final["name"] = name
        kept_names = {_file_key(n) for names in kept.values() for n in names}
        culture_refs = None
        if kept_names:
            # Written by no one but the files kept for them, so nothing else claims those cultures; model.tmdl
            # still lists them where they were, so the cultures' order stays.
            all_cultures = [c for c in (final.get("model") or {}).get("cultures") or [] if isinstance(c, dict)]
            culture_refs = list(dict.fromkeys(str(c.get("name")) for c in all_cultures if isinstance(c.get("name"), str)))
            final["model"]["cultures"] = [c for c in all_cultures if _file_key(str(c.get("name"))) not in kept_names]
            if not final["model"]["cultures"]:
                del final["model"]["cultures"]
        files, written = tmdl.from_bim(final, culture_refs)
        report = report + written
        files["definition.pbism"] = json.dumps({"version": "4.0", "settings": {}}, indent=2) + "\n"
        targets = {}
        kept_keys = {_file_key(rel) for rel in kept}
        existing = _by_key(folder, _tmdl_files(definition) if _real(definition, stat.S_ISDIR) else [])
        for rel, content in files.items():
            if _file_key(rel) in kept_keys:
                raise ValueError(f"{rel} is kept as it is, because it holds translations KratosForge can't read, and another "
                                 "culture would be written to it, so nothing was written. Rename that file.")
            path = _in_place(folder, rel, existing)
            _no_links(root, path.parent)
            if os.path.lexists(path) and not _real(path, stat.S_ISREG):
                raise ValueError(f"{path.relative_to(root).as_posix()} is a link or a folder, so it isn't overwritten")
            if not path.resolve().is_relative_to(folder.resolve()):  # never outside the model's folder
                raise ValueError(f"{rel} would be written outside {folder.relative_to(root).as_posix()}, so nothing was written.")
            targets[path] = content
        stale = sorted(f for group in existing.values() for f in group
                       if f not in targets and _file_key(f.relative_to(folder).as_posix()) not in kept_keys)
        status = {p: ("same" if _same_text(p, c) else "changed") if _real(p, stat.S_ISREG) else "new" for p, c in targets.items()}
        unchanged = sum(1 for v in status.values() if v == "same")
        changes = model_ossie.plan(current, final)
        out = {"name": name, "folder": folder.relative_to(root).as_posix(), "exists": current is not None,
               "files": sorted(p.relative_to(root).as_posix() for p in targets),
               "removed": [p.relative_to(root).as_posix() for p in stale], "report": report,
               "changes": changes if current else [r for r in changes if r["change"] == "needs_dax"],
               "removable": model_ossie.removable(current, incoming) if current else [],
               "unchanged": unchanged, "counts": _model_counts(final),
               "file_changes": sorted([{"path": p.relative_to(folder).as_posix(), "change": status[p]} for p in targets]
                                      + [{"path": rel, "change": "same"} for rel in kept], key=lambda f: f["path"])
               + [{"path": p.relative_to(folder).as_posix(), "change": "removed"} for p in stale]}
        digest = hashlib.sha256(json.dumps([out["folder"], out["removed"], sorted(kept), sorted(
            (p.relative_to(root).as_posix(), c) for p, c in targets.items())], ensure_ascii=False).encode()).hexdigest()
        if not confirmed:
            return {"confirm": out | {"token": digest}}
        if not hmac.compare_digest(str(token).encode(), digest.encode()):
            raise ValueError("The folder or the Ossie file changed since the preview, so nothing was written. Preview again.")
        for path, content in targets.items():
            write_text(path, content, newline="\n")
        for old in stale:
            if _real(old, stat.S_ISREG) and old.suffix == ".tmdl":
                old.unlink()
        return out
