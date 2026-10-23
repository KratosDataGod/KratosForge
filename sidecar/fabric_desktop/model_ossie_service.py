"""Export to Ossie and Import from Ossie on the Semantic model tab (model_ossie.py does the converting).

Export reads a model's definition from the environment's workspace as TMSL (getDefinition, read only) and
converts it on this PC. Import previews first and writes only when ``confirmed``: the model's definition through
updateDefinition (or a new model with ``mode="new"``). No refresh is started. The Ossie file and the model's
TMDL are saved in the metadata project's folder, so they go to Git with the rest of the project.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from . import model_ossie
from .jsonfile import write_text
from .meta.store import OneLakeStore

METHODS = {"meta_model_ossie_models", "meta_model_ossie_export", "meta_model_ossie_save", "meta_model_ossie_read",
           "meta_model_ossie_preview", "meta_model_ossie_import", "meta_model_tmdl_save"}
FOLDER = "semantic"
MODES = ("update", "replace", "new")
_SAFE = re.compile(r"[^A-Za-z0-9 _.-]+")


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
        _, cfg = self._ossie_workspace(env)
        model = self._ossie_model(cfg["workspace_id"], name)
        if model is None:
            raise LookupError(f"There's no semantic model named {name} in {cfg.get('workspace_name') or 'the workspace'}")
        out = model_ossie.export(self._model_bim(cfg["workspace_id"], model), translate_dax=translate_dax)
        return out | {"model": name, "path": f"{FOLDER}/{_SAFE.sub('_', name).strip() or 'model'}.ossie.yaml"}

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
        return {"path": target.relative_to(project.root).as_posix(), "text": target.read_text(encoding="utf-8")}

    def _ossie_import_plan(self, env: str, text: str, name: str, mode: str, remove: list[str] | None,
                           layer: str | None) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError(f"mode is one of {', '.join(MODES)}")
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
        final = model_ossie.merge(current, incoming, remove) if mode == "update" else incoming
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
        written = []
        for path, content in parts.items():
            rel = PurePosixPath(path)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"Fabric returned an unexpected part path: {path}")
            write_text(folder.joinpath(*rel.parts), content)
            written.append(folder.joinpath(*rel.parts))
        removed = []
        for old in sorted(folder.rglob("*")):
            if old.is_file() and old not in written and old.suffix in (".tmdl", ".pbism", ".json"):
                old.unlink()
                removed.append(old.relative_to(project.root).as_posix())
        return {"folder": folder.relative_to(project.root).as_posix(), "files": len(written), "removed": removed}
