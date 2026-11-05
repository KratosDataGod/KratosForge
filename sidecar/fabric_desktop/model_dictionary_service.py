"""The data dictionary card on the Semantic model tab (model_dictionary.py builds it).

Reads the metadata project and the catalog. Export writes a new CSV in Downloads; Save to the project writes
semantic/<model>.dictionary.csv for Git. Import previews first and saves only when ``confirmed``, through the
same catalog call as the About card. Update model then writes the descriptions into the model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import model_dictionary, sql_exec
from .jsonfile import write_text
from .meta import mesh
from .redact import describe

METHODS = {"meta_model_dictionary", "meta_model_dictionary_export", "meta_model_dictionary_save",
           "meta_model_dictionary_preview", "meta_model_dictionary_import"}
FOLDER = "semantic"

_log = logging.getLogger(__name__)


class ModelDictionaryMixin:
    def _saved_catalog(self, project: Any) -> tuple[dict[str, dict[str, Any]], str | None]:
        """What the catalog saved for the project's tables and columns, and why it couldn't be read."""
        try:
            return self._catalog().call("enrichment", project=mesh.project_name(project)), None  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the starter text in Git still makes a dictionary
            _log.info("Catalog not read for the data dictionary: %s", describe(exc))
            return {}, describe(exc)

    def _dictionary(self, layer: str | None) -> tuple[Any, str, list[dict[str, Any]], str | None]:
        project = self._project()  # type: ignore[attr-defined]
        layer = self._model_layer(project, layer)  # type: ignore[attr-defined]
        saved, problem = self._saved_catalog(project)
        return project, layer, model_dictionary.entries(project, layer, saved), problem

    def model_descriptions(self, project: Any, layer: str) -> dict[str, dict[str, str]]:
        """The descriptions Update model writes into the layer's model."""
        saved, _ = self._saved_catalog(project)
        items = model_dictionary.entries(project, layer, saved)
        return model_dictionary.descriptions(items)

    def meta_model_dictionary(self, layer: str | None = None) -> dict[str, Any]:
        """Every table, column and measure of the layer's model with its description. Reads only."""
        _, layer, items, problem = self._dictionary(layer)
        return {"layer": layer, "model": f"SM_{layer.title()}", "items": items,
                "counts": model_dictionary.counts(items), "problem": problem}

    def meta_model_dictionary_export(self, layer: str | None = None) -> dict[str, Any]:
        """The dictionary as a new CSV in Downloads; never replaces a file."""
        _, layer, items, _ = self._dictionary(layer)
        folder = Path.home() / "Downloads"
        folder.mkdir(parents=True, exist_ok=True)
        path = sql_exec._reserve(folder, f"SM_{layer.title()} data dictionary", ".csv")
        path.write_text(model_dictionary.to_csv(items), encoding="utf-8-sig")  # the mark tells Excel it's UTF-8
        return {"path": str(path), "rows": len(items)}

    def meta_model_dictionary_save(self, layer: str | None = None) -> dict[str, Any]:
        """The dictionary as semantic/SM_<Layer>.dictionary.csv in the project's folder, ready to commit."""
        project, layer, items, _ = self._dictionary(layer)
        target = Path(project.root, FOLDER, f"SM_{layer.title()}.dictionary.csv")
        write_text(target, "﻿" + model_dictionary.to_csv(items))
        return {"path": target.relative_to(project.root).as_posix(), "rows": len(items)}

    def meta_model_dictionary_preview(self, text: str = "", layer: str | None = None) -> dict[str, Any]:
        """What importing an edited dictionary would change. Writes nothing."""
        _, layer, items, problem = self._dictionary(layer)
        plan = model_dictionary.plan_import(items, model_dictionary.read_csv(text))
        return {"layer": layer, "problem": problem} | plan

    def meta_model_dictionary_import(self, text: str = "", layer: str | None = None,
                                     confirmed: bool = False) -> dict[str, Any]:
        """Save the file's descriptions and classifications to the catalog. Without ``confirmed`` the answer is
        ``{"confirm": {...}}`` and nothing is saved, so the person sees the changes first."""
        _, layer, items, problem = self._dictionary(layer)
        plan = model_dictionary.plan_import(items, model_dictionary.read_csv(text))
        if problem:
            raise RuntimeError(f"The catalog can't be reached, so nothing was saved ({problem}). Try again.")
        if not confirmed:
            return {"confirm": {"changes": len(plan["changes"]), "skipped": len(plan["skipped"])}}
        targets: dict[tuple[str, str], dict[str, str]] = {}
        for c in plan["changes"]:
            targets.setdefault((c["entity"], c["column"]), {})[c["field"]] = c["new"]
        saved = 0
        for (entity, column), fields in targets.items():
            try:
                self.mesh_save_about(entity, fields, column)  # type: ignore[attr-defined]
            except Exception as exc:
                raise RuntimeError(f"Saved {saved} of {len(targets)}; {entity}{'.' + column if column else ''} "
                                   f"wasn't saved ({describe(exc)}). Import the file again to save the rest.") from exc
            saved += 1
        return {"saved": saved, "changes": plan["changes"], "skipped": plan["skipped"]}
