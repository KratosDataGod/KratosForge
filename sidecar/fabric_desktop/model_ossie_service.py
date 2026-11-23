"""Export to Ossie and Import from Ossie on the Semantic model tab (model_ossie.py does the converting).

Export reads a model's definition from the environment's workspace as TMSL (getDefinition, read only) and
converts it on this PC. Import previews first and writes only when ``confirmed``: the model's definition through
updateDefinition (or a new model with ``mode="new"``). No refresh is started. The Ossie file and the model's
TMDL are saved in the metadata project's folder, so they go to Git with the rest of the project.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, cast

from . import model_ossie, ossie_validate, tmdl
from .fabric_api import definition_text
from .jsonfile import write_text
from .meta.store import OneLakeStore
from .notebook_files_service import _is_link
from .notebook_resources_service import _reveal

METHODS = {"meta_model_ossie_models", "meta_model_ossie_export", "meta_model_ossie_save", "meta_model_ossie_read",
           "meta_model_ossie_preview", "meta_model_ossie_import", "meta_model_tmdl_save",
           "meta_model_ossie_folders", "meta_model_ossie_export_folder", "meta_model_ossie_to_tmdl",
           "meta_model_semantic_view_save", "meta_model_semantic_view_status",
           "meta_model_semantic_view_reveal"}
FOLDER = "semantic"
VIEW_FOLDER = "semantic-views"  # semantic-views/<view>/: the local copy of a Fabric Semantic View (OSSIE-005a)
# A lakehouse's Context/ (a view's source YAML) and SystemContext/ (its generated TMDL) are Fabric's; nothing is ever
# written to a path with either as a folder name, compared as Windows compares names.
_LAKEHOUSE_CONTEXT = frozenset({"context", "systemcontext"})
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


VIEW_MAX_ENTRIES = 200  # files a saved view folder may hold before the status refuses to read it (Fabric takes 20)
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


def _no_context(path: Path) -> None:
    """Refuse a target with ``Context`` (or ``SystemContext``) as a folder or file name anywhere in its path, as
    given or resolved: a Semantic View's copy is written only in the project, never where Fabric reads a view."""
    for parts in (path.parts, path.resolve().parts):
        hit = next((p for p in parts if p.casefold() in _LAKEHOUSE_CONTEXT), None)
        if hit is not None:
            raise ValueError(f"{path.as_posix()} has a folder named {hit}, like a lakehouse's {hit}/ folder, and "
                             "KratosForge never writes there, so nothing was written. Pick another view name or "
                             "project folder. You publish a view by hand in Fabric.")


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
            rel = f.relative_to(folder).as_posix()
            try:
                files[rel] = f.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError(f"{path}/{rel} isn't UTF-8 text, so the model can't be read. "
                                 "Save it as UTF-8, then try again.") from None
        bim, read_report = tmdl.to_bim(files, name)
    elif _real(folder / "model.bim", stat.S_ISREG):
        if (folder / "model.bim").stat().st_size > tmdl.MAX_CHARS * 4:
            raise ValueError(f"{name}'s model.bim is too large to read on this PC")
        try:
            bim, read_report = json.loads((folder / "model.bim").read_text(encoding="utf-8-sig")), []
        except RecursionError:
            raise ValueError(f"{path}/model.bim is nested too deeply to be a semantic model") from None
        except UnicodeDecodeError:
            raise ValueError(f"{path}/model.bim isn't UTF-8 text, so the model can't be read. Save it as UTF-8, then "
                             "try again.") from None
        except ValueError as exc:  # json's own error: where the JSON breaks
            raise ValueError(f"{path}/model.bim isn't valid JSON ({exc}), so the model can't be read. Fix the JSON, "
                             "then try again.") from None
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
        bim = definition_text(got).get("model.bim")  # a part that isn't whole base64 is FabricError, never empty
        if bim is None:
            raise RuntimeError(f"Fabric didn't return {model.get('displayName') or 'the model'}'s model.bim")
        return json.loads(bim)

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
        out = model_ossie.export(self._model_bim(cfg["workspace_id"], model), translate_dax=translate_dax, validate=True)
        del out["round_trip"]  # its result is in the fidelity report; the Round-trip check card is for folders
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
            raise LookupError(f"There's no model folder {path}. Pick one from {FOLDER}/<name>.SemanticModel.")
        name = folder.name.removesuffix(".SemanticModel")
        bim, read_report = _folder_bim(folder, name, path)
        out = model_ossie.export(bim, translate_dax=translate_dax, read_report=read_report, validate=True)
        return out | {"model": name, "path": f"{FOLDER}/{_SAFE.sub('_', name).strip() or 'model'}.ossie.yaml"}

    def _ossie_path(self, project: Any, path: str, suffixes: tuple[str, ...]) -> Path:
        rel = PurePosixPath(path.replace("\\", "/"))
        # a ":" anywhere is a drive (C:/x, C:x) that Windows would join in place of the project, or a stream
        if (rel.is_absolute() or ".." in rel.parts or not rel.parts or not rel.name.endswith(suffixes)
                or ":" in rel.as_posix()):
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
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{path} isn't UTF-8 text, so it isn't an Ossie file. Save it as UTF-8, then try again.") from None
        model_ossie.parse(text)  # refuse here what Import would refuse, before the UI shows it
        return {"path": target.relative_to(project.root).as_posix(), "text": text}

    def _ossie_import_plan(self, env: str, text: str, name: str, mode: str, remove: list[str] | None,
                           layer: str | None) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError(f"mode is one of {', '.join(MODES)}")
        model_ossie.require()  # before any Fabric call, even when a name is given and the file isn't parsed yet
        project, cfg = self._ossie_workspace(env)
        doc = model_ossie.parse(text)  # read once: the conversion, the merge, the reports and the schema check share it
        refusal = model_ossie.not_convertible(doc)
        if refusal:  # a known profile the converter can't read: no Fabric call, nothing converted
            return {"refused": refusal, "doc": doc, "cfg": cfg, "name": (name or model_ossie.model_name(doc)).strip()}
        name = (name or doc["name"]).strip()
        store = self._store(project, env)  # type: ignore[attr-defined]
        lakehouse = project.layer(self._model_layer(project, layer)).lakehouse  # type: ignore[attr-defined]
        source = None
        if isinstance(store, OneLakeStore) and lakehouse in store.lakehouse_ids:
            source = {"workspaceId": store.workspace_of(lakehouse), "itemId": store.lakehouse_ids[lakehouse]}
        incoming, report = model_ossie.to_tmsl(doc, source=source)
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
        final = model_ossie.merge(cast(dict[str, Any], current), incoming, remove, model_ossie.stashed(doc),
                                  report) if mode == "update" else incoming
        final["name"] = name
        return {"final": final, "existing": existing, "cfg": cfg, "name": name, "report": report, "doc": doc,
                "fidelity": model_ossie.safe_fidelity(doc, incoming, report,
                                                      model_ossie.round_trip_check(incoming)),
                "changes": model_ossie.plan(current, final),
                "removable": model_ossie.removable(current, incoming) if mode == "update" else []}

    def meta_model_ossie_preview(self, env: str = "test", text: str = "", name: str = "", mode: str = "update",
                                 remove: list[str] | None = None, layer: str | None = None) -> dict[str, Any]:
        """What importing the Ossie document would change, and what can't be carried over. Writes nothing.

        ``removable`` lists what an update keeps although the file doesn't have it; pass any of them in
        ``remove`` to take them out. ``schema_validation`` is the file checked against the pinned Ossie JSON Schema
        for its version (ossie_validate.validate), on this PC. It informs only: Import doesn't wait on it, because
        the converter already refuses what it can't read.

        A file of a profile the converter can't read yet (Ossie 0.3.0.dev0, Snowflake's 0.1.1) is still checked:
        the answer has ``convertible`` false, ``refused`` (why, by name), the schema check, and no changes."""
        p = self._ossie_import_plan(env, text, name, mode, remove, layer)
        if "refused" in p:
            return {"env": env, "name": p["name"], "mode": mode, "exists": None,
                    "workspace_name": p["cfg"].get("workspace_name", ""), "changes": [], "removable": [],
                    "report": [], "fidelity": None, "convertible": False, "refused": p["refused"],
                    "schema_validation": ossie_validate.validate(p["doc"])}
        return {"convertible": True, "env": env, "name": p["name"], "mode": mode, "exists": p["existing"] is not None,
                "workspace_name": p["cfg"].get("workspace_name", ""), "changes": p["changes"],
                "removable": p["removable"], "report": p["report"], "fidelity": p["fidelity"],
                "schema_validation": ossie_validate.validate(p["doc"])}

    def meta_model_ossie_import(self, env: str = "test", text: str = "", name: str = "", mode: str = "update",
                                remove: list[str] | None = None, layer: str | None = None,
                                confirmed: bool = False) -> dict[str, Any]:
        """Write the Ossie document into the model. Without ``confirmed`` the answer is ``{"confirm": {...}}``
        and nothing is written, so the user sees what changes first. No refresh runs."""
        p = self._ossie_import_plan(env, text, name, mode, remove, layer)
        if "refused" in p:
            raise ValueError(p["refused"])
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
        # A part that isn't whole base64 is FabricError: written empty, or left out and removed as stale, it would lose the model.
        parts = definition_text(got, "utf-8")
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
        doc = model_ossie.parse(text)  # read once
        refusal = model_ossie.not_convertible(doc)
        if refusal:  # Ossie 0.3.0.dev0 or Snowflake's 0.1.1: read, but never converted as if it were 0.2
            raise ValueError(refusal)
        name = (name or str(doc["name"])).strip()
        root = Path(project.root)
        folder = root / FOLDER / f"{_folder_name(name)}.SemanticModel"
        definition = folder / "definition"
        _no_links(root, definition)
        incoming, report = model_ossie.to_tmsl(doc)
        current: dict[str, Any] | None = None
        read_report: list[dict[str, str]] = []
        if (_real(definition, stat.S_ISDIR) and _tmdl_files(definition)) or _real(folder / "model.bim", stat.S_ISREG):
            current, read_report = _folder_bim(folder, name, folder.relative_to(root).as_posix())
        _refuse_unreadable(read_report)  # before the confirm too, so an old token never writes over such a file
        kept, kept_rows = _kept_cultures(folder, read_report)
        report = [r for r in read_report if r.get("file") not in kept] + kept_rows + report
        final = model_ossie.merge(current, incoming, remove, model_ossie.stashed(doc), report) if current else incoming
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
        fid = model_ossie.safe_fidelity(doc, incoming, report, model_ossie.round_trip_check(incoming))
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
               "removed": [p.relative_to(root).as_posix() for p in stale], "report": report, "fidelity": fid,
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

    def _view_source(self, project: Any, text: str, path: str) -> Any:
        """What a Semantic View is made from: ``text`` (Ossie YAML or JSON, or a model.bim as JSON) or ``path``, a
        model folder in the project (semantic/<name>.SemanticModel, read on this PC)."""
        if bool(text) == bool(path):
            raise ValueError("Give either the Ossie file's or model.bim's text, or a model folder in the project")
        if path:
            rel = PurePosixPath(path.replace("\\", "/")).as_posix()
            folder = next((p for p in self._model_folders(project) if p.relative_to(project.root).as_posix() == rel),
                          None)
            if folder is None:
                raise LookupError(f"There's no model folder {path}. Pick one from {FOLDER}/<name>.SemanticModel.")
            return _folder_bim(folder, folder.name.removesuffix(".SemanticModel"), path)[0]
        if len(text) > tmdl.MAX_CHARS * 4:
            raise ValueError("This text is too large to be a semantic model")
        return text

    def meta_model_semantic_view_save(self, view_name: str = "", text: str = "", path: str = "",
                                      split: str = "single", confirmed: bool = False, token: str = "") -> dict[str, Any]:
        """A Fabric Semantic View's YAML files written to semantic-views/<view>/ in the project's folder (OSSIE-005a,
        Preview), made on this PC from ``text`` (an Ossie 0.2.0.dev0 document, or a model.bim as JSON) or ``path``
        (a model folder in the project) by ``semantic_view.semantic_view``. Nothing is sent to Fabric and nothing is
        published: publishing is done by hand in Fabric. No path with a ``Context`` folder is ever written.

        Without ``confirmed`` nothing is written: the answer lists the files, the ``.yaml`` files in that folder
        that would be removed (Fabric merges every ``.yaml`` file in a view's folder, so a stale one would join
        the view), the report and a token. With it and the preview's token, every file is written whole (temporary
        file, then swapped in) and only regular ``.yaml`` files the preview listed are removed. The token binds the
        confirm to what the preview showed: if the files or the removals changed since, nothing is written. Links
        and junctions are never followed.

        The token is only a staleness binding, not an authorization: anyone can compute it from the same inputs.
        The approval is the confirmed call itself, which the audit log records before it runs (``confirmed`` is
        one of ``audit.GUARD_PARAMS``, so the call fails closed when it can't be recorded).

        For later (OSSIE-005b): a screen that calls this with removals must show a danger confirmDialog listing
        every file it removes before it sends ``confirmed``, and that screen needs a UX review first."""
        from . import ossie_profiles, semantic_view

        model_ossie.require()
        project = self._project()  # type: ignore[attr-defined]
        root = Path(project.root)
        name = semantic_view.folder_name(view_name)  # refuses a path, .. and device names before anything is read
        folder = root / VIEW_FOLDER / name
        _no_context(folder)
        _no_links(root, folder)
        out = semantic_view.semantic_view(self._view_source(project, text, path), view_name=view_name, split=split)
        files = out["files"]
        if len(files) > ossie_profiles.FABRIC_MAX_FILES:  # the emitter never makes more; refused if it ever did
            raise ValueError(f"{len(files)} files is more than the {ossie_profiles.FABRIC_MAX_FILES} a Fabric "
                             "semantic view takes, so nothing was written")
        found = _tmdl_files(folder, suffixes=("",)) if _real(folder, stat.S_ISDIR) else []
        existing = _by_key(folder, [f for f in found if f.name.lower().endswith(".yaml")])
        targets: dict[Path, str] = {}
        for rel, content in files.items():  # every target is checked before anything is written
            target = _in_place(folder, rel, existing)
            _no_context(target)
            _no_links(root, target.parent)
            if os.path.lexists(target) and not _real(target, stat.S_ISREG):
                raise ValueError(f"{target.relative_to(root).as_posix()} is a link or a folder, so it isn't overwritten")
            if not target.resolve().is_relative_to(folder.resolve()):
                raise ValueError(f"{rel} would be written outside {folder.relative_to(root).as_posix()}, so nothing "
                                 "was written.")
            targets[target] = content
        stale = sorted(f for group in existing.values() for f in group if f not in targets)
        status = {p: ("same" if _same_text(p, c) else "changed") if _real(p, stat.S_ISREG) else "new"
                  for p, c in targets.items()}
        summary = {"view": name, "folder": folder.relative_to(root).as_posix(),
                   "files": sorted(p.relative_to(root).as_posix() for p in targets),
                   "removed": [p.relative_to(root).as_posix() for p in stale],
                   "file_changes": sorted([{"path": p.relative_to(folder).as_posix(), "change": status[p]}
                                           for p in targets], key=lambda f: f["path"])
                   + [{"path": p.relative_to(folder).as_posix(), "change": "removed"} for p in stale],
                   "report": out["report"], "published": False, "publish": semantic_view.PUBLISH}
        digest = hashlib.sha256(json.dumps([summary["folder"], summary["removed"], sorted(
            (p.relative_to(root).as_posix(), c) for p, c in targets.items())], ensure_ascii=False).encode()).hexdigest()
        if not confirmed:
            return {"confirm": summary | {"token": digest}}
        if not hmac.compare_digest(str(token).encode(), digest.encode()):
            raise ValueError("The folder or the definition changed since the preview, so nothing was written. "
                             "Preview again.")
        for target, content in targets.items():
            write_text(target, content, newline="\n")
        for old in stale:
            _no_links(root, old.parent)  # checked again just before the unlink
            if _real(old, stat.S_ISREG) and old.resolve().is_relative_to(folder.resolve()):
                old.unlink()
        return summary

    def _view_files(self, folder: Path) -> dict[str, str | bytes]:
        """Every file in a saved view's folder (path in the folder -> its text), read as Fabric would merge them:
        ``.yaml`` files in subfolders too. A link or junction anywhere in the folder is refused, never followed.
        More than VIEW_MAX_ENTRIES entries, more than MAX_DEPTH folders down, or more YAML than Fabric's 16 MB
        is refused before a file is read. Files that aren't ``.yaml`` are listed with no content: Fabric ignores
        them and validate_files only names them."""
        from . import ossie_profiles

        found: list[Path] = []
        stack: list[tuple[Path, int]] = [(folder, 0)]
        seen = 0
        while stack:
            here, depth = stack.pop()
            if depth > MAX_DEPTH:
                raise ValueError(f"{here.relative_to(folder).as_posix()} is nested too deeply for a semantic view")
            with os.scandir(here) as entries:
                for e in entries:
                    seen += 1
                    if seen > VIEW_MAX_ENTRIES:
                        raise ValueError(f"The view's folder holds more than {VIEW_MAX_ENTRIES} files or folders, "
                                         f"and a Fabric semantic view takes {ossie_profiles.FABRIC_MAX_FILES} .yaml "
                                         "files, so it isn't read. Keep only the view's files there.")
                    path = Path(e.path)
                    if _real(path, stat.S_ISDIR):
                        stack.append((path, depth + 1))
                    elif _real(path, stat.S_ISREG):
                        found.append(path)
                    else:
                        raise ValueError(f"{path.relative_to(folder).as_posix()} is a link, a junction or not a "
                                         "file, so the view's folder isn't read")
        yaml_files = [f for f in found if f.name.lower().endswith(".yaml")]
        if sum(os.lstat(f).st_size for f in yaml_files) > ossie_profiles.FABRIC_MAX_YAML_BYTES:
            raise ValueError("The view's .yaml files hold more than 16 MB, and Fabric takes at most 16 MB of YAML "
                             "combined, so they aren't read. Take out what the view doesn't need.")
        out: dict[str, str | bytes] = {}
        left = ossie_profiles.FABRIC_MAX_YAML_BYTES
        for f in sorted(found):
            rel = f.relative_to(folder).as_posix()
            if f not in yaml_files:
                out[rel] = b""
                continue
            with open(f, "rb") as fh:  # capped: a file that grew after the size check can't take more
                data = fh.read(left + 1)
            left -= len(data)
            if left < 0:
                raise ValueError("The view's .yaml files hold more than 16 MB, and Fabric takes at most 16 MB of "
                                 "YAML combined, so they aren't read. Take out what the view doesn't need.")
            try:  # a CRLF copy (Git on Windows) reads as the same file
                out[rel] = data.decode("utf-8-sig").replace("\r\n", "\n")
            except UnicodeDecodeError:  # kept as bytes: validate_files reports it, the table marks it unreadable
                out[rel] = data
        return out

    def meta_model_semantic_view_status(self, view_name: str = "", path: str = "") -> dict[str, Any]:
        """The saved copy of a Fabric Semantic View in semantic-views/<view>/ (OSSIE-005b, Preview), for the
        publish-steps card: what each file holds (``files_summary``) and the files checked as Fabric reads them
        (``ossie_profiles.validate_files``, profile fabric-semantic-view). Reads only, on this PC: nothing is
        written, nothing is sent to Fabric and nothing is published. ``views`` lists every saved view's folder.

        With ``path`` (a model folder in the project, semantic/<name>.SemanticModel, as Save view files takes)
        the model is converted again on this PC, with the saved files' layout, and ``source`` says whether the
        saved files are what it gives now (``same``) and what the view leaves out (``left_out``, from the
        report). Opening the folder is ``meta_model_semantic_view_reveal``, which is audited."""
        from . import ossie_profiles, semantic_view

        project = self._project()  # type: ignore[attr-defined]
        root = Path(project.root)
        base = root / VIEW_FOLDER
        views = self._saved_views(base)
        if not view_name:
            return {"views": views, "folder": VIEW_FOLDER}
        name = semantic_view.folder_name(view_name)  # refuses a path, .. and device names before anything is read
        folder = base / name
        _no_links(root, folder)
        out: dict[str, Any] = {"views": views, "view": name, "folder": folder.relative_to(root).as_posix(),
                               "exists": _real(folder, stat.S_ISDIR), "files": [], "files_summary": [],
                               "validation": None, "valid": False, "source": None, "published": False,
                               "publish": semantic_view.PUBLISH}
        if not out["exists"]:
            return out
        model_ossie.require()  # the YAML reader, before a file is read
        files = self._view_files(folder)
        yaml_files = {k: v for k, v in files.items() if k.lower().endswith(".yaml")}
        out["files"] = sorted(files)
        out["files_summary"] = [_file_summary(rel, text) if isinstance(text, str)
                                else {"path": rel, "bytes": len(text), "unreadable": True}
                                for rel, text in sorted(yaml_files.items())]
        if files:
            out["validation"] = ossie_profiles.validate_files(files, "fabric-semantic-view")
            out["valid"] = out["validation"]["valid"]
        if path and yaml_files:
            split = ("schema_metrics" if set(yaml_files) == {semantic_view.SCHEMA_FILE, semantic_view.METRICS_FILE}
                     else "by_dataset" if semantic_view.MODEL_FILE in yaml_files else "single")
            made = semantic_view.semantic_view(self._view_source(project, "", path), view_name=view_name, split=split)
            report = made["report"]
            out["source"] = {
                "path": path, "split": split, "same": made["files"] == yaml_files,
                "left_out": [{"object": r["object"], "kind": r["kind"], "construct": r["stripped"],
                              "excluded": r["excluded"], "rule": r["rule"], "reason": r["reason"],
                              "source": r["source"]} for r in report["objects"] if r["stripped"] or r["excluded"]],
                "warnings": report["warnings"]}
        return out

    @staticmethod
    def _saved_views(base: Path) -> list[str]:
        """The folder names under semantic-views/, at most VIEW_MAX_ENTRIES of them read."""
        if not _real(base, stat.S_ISDIR):
            return []
        names: list[str] = []
        with os.scandir(base) as entries:
            for i, e in enumerate(entries):
                if i >= VIEW_MAX_ENTRIES:
                    raise ValueError(f"{VIEW_FOLDER}/ holds more than {VIEW_MAX_ENTRIES} entries, so it isn't read")
                if _real(Path(e.path), stat.S_ISDIR):
                    names.append(e.name)
        return sorted(names, key=str.lower)

    def meta_model_semantic_view_reveal(self, view_name: str) -> dict[str, Any]:
        """Show one saved view's folder, semantic-views/<view>/, in File Explorer (OSSIE-005b, Preview). Only when
        it exists in place: nothing is made, a link or junction on the way is refused, and no other path can be
        opened. Audited, as the other reveal methods are, because it starts File Explorer on this PC."""
        from . import semantic_view

        root = Path(self._project().root)  # type: ignore[attr-defined]
        folder = root / VIEW_FOLDER / semantic_view.folder_name(view_name)
        rel = folder.relative_to(root).as_posix()
        _no_links(root, folder)
        if not _real(folder, stat.S_ISDIR):
            raise ValueError(f"{rel} isn't there yet. Save the view's files first.")
        _no_links(root, folder)  # checked again just before Explorer opens it
        _reveal(folder, select=False)
        return {"folder": rel, "shown": str(folder)}


def _file_summary(rel: str, text: str) -> dict[str, Any]:
    """One saved view file's row for the card's table, counted as semantic_view's files_summary counts: its path,
    UTF-8 bytes, datasets, relationships, metrics and the metrics with DAX. Read with the loader that refuses
    aliases. A file that isn't one YAML object gets ``unreadable`` instead of counts; validate_files says why."""
    try:
        doc = model_ossie.load_text(text)
    except (ValueError, TypeError):  # narrow: load_text's refusals; the check names the problem
        doc = None
    row: dict[str, Any] = {"path": rel, "bytes": len(text.encode("utf-8"))}
    if not isinstance(doc, dict):
        return row | {"unreadable": True}

    def items(key: str) -> list[Any]:
        value = doc.get(key)
        return value if isinstance(value, list) else []

    metrics = [m for m in items("metrics") if isinstance(m, dict)]

    def has_dax(m: dict[str, Any]) -> bool:
        expression = m.get("expression")
        dialects = expression.get("dialects") if isinstance(expression, dict) else None
        return any(isinstance(e, dict) and e.get("dialect") == "DAX" and e.get("expression")
                   for e in (dialects if isinstance(dialects, list) else []))

    return row | {"datasets": len(items("datasets")), "relationships": len(items("relationships")),
                  "metrics": len(metrics), "dax_metrics": sum(1 for m in metrics if has_dax(m))}
