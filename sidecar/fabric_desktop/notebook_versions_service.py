"""Version history: what the app calls (notebook_versions.py keeps the versions).

Each save from the notebook screen keeps a version (coalesced, see notebook_versions). Restore
keeps the notebook as it is now as a "before restore" version first, then saves the old cells, so
a restore can itself be undone. Everything here reads and writes files on this PC only.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import notebook_versions as versions
from .notebook_sync import SyncBases, merge_cells
from .meta import release
from .redact import describe
from .notebook import assign_cell_ids, from_ipynb, to_ipynb

_log = logging.getLogger(__name__)

METHODS = {"notebook_versions", "notebook_version", "notebook_version_compare", "notebook_checkpoint",
           "notebook_version_restore", "notebook_version_delete", "notebook_sync_merge"}


class NotebookVersionsMixin:
    @property
    def _versions_root(self) -> Path:
        return Path(self.root) / "notebook-versions"  # type: ignore[attr-defined]

    @property
    def _sync_bases(self) -> SyncBases:
        return SyncBases(Path(self.root) / "notebook-sync")  # type: ignore[attr-defined]

    def _remember_base(self, name: str, stamp: str | None, cells: list[dict[str, Any]],
                       ids: list[str] | None = None) -> None:
        """The cells the screen now holds, as of the file's ``stamp``: the base a later merge starts
        from. A failure here never fails the open or save; a merge then falls back to choosing."""
        if not stamp:
            return
        try:
            if ids is not None:
                cells = [dict(c, cell_id=i) for c, i in zip(cells, ids, strict=True)]
            self._sync_bases.remember(name, stamp, cells)
        except Exception as exc:  # noqa: BLE001 - Keep mine / Load its version still work without a base
            _log.warning("Couldn't keep the sync base of %r: %s", name, describe(exc))

    def _sync_preview(self, name: str, cells: list[dict[str, Any]], disk_stamp: str | None) -> dict[str, Any] | None:
        """After a save found the file changed: the merge of both sides, when the base is known."""
        try:
            base = self._sync_bases.find(name, disk_stamp)
            if base is None:
                return None
            theirs = from_ipynb(self._saved_nb(name))["cells"]
            mine = [dict(c) for c in cells]
            assign_cell_ids(mine)
            return merge_cells(base, mine, theirs)
        except Exception as exc:  # noqa: BLE001 - the person still chooses Keep mine or Load its version
            _log.warning("Couldn't merge the two versions of %r: %s", name, describe(exc))
            return None

    def notebook_sync_merge(self, name: str, cells: list[dict[str, Any]], disk_stamp: str,
                            choices: dict[str, str] | None = None, lakehouse_ref: dict[str, str] | None = None,
                            compute: str | None = None, workspace: dict[str, str] | None = None) -> dict[str, Any]:
        """Merges the screen's ``cells`` (as of ``disk_stamp``) with the file another program saved
        since, and saves the merge once every conflict has a choice. Without one for each, nothing
        is saved: {"resolved": False, "conflicts", "cells"} comes back, both versions in each. If the
        file changes again meanwhile, ``changed_on_disk`` comes back as from save_notebook."""
        base = self._sync_bases.find(name, disk_stamp)
        if base is None:
            raise ValueError("KratosForge no longer has the version both started from, so it can't merge "
                             "them. Choose Keep mine or Load its version.")
        loaded = self.notebooks.load(name)  # type: ignore[attr-defined]
        mine = [dict(c) for c in cells]
        assign_cell_ids(mine)
        merged = merge_cells(base, mine, loaded["cells"], choices)
        if not merged["resolved"]:
            return {"name": name, "resolved": False, "conflicts": merged["conflicts"], "cells": merged["cells"],
                    "disk_stamp": loaded["disk_stamp"]}
        out = self.save_notebook(name, merged["cells"], lakehouse_ref, compute, workspace,  # type: ignore[attr-defined]
                                 loaded["disk_stamp"])
        if out.get("changed_on_disk"):
            return out
        return out | {"resolved": True, "conflicts": [], "cells": merged["cells"]}

    def _saved_nb(self, name: str) -> dict[str, Any]:
        path = self.notebooks.path(name)  # type: ignore[attr-defined]
        if not path.is_file():
            raise ValueError(f"There's no notebook named {name!r}.")
        return json.loads(path.read_text(encoding="utf-8"))

    def _keep_version(self, name: str) -> None:
        """After a save: keep the saved file as a version. A failure here never fails the save."""
        try:
            versions.record(self._versions_root, name, self._saved_nb(name))
        except Exception as exc:  # noqa: BLE001 - the notebook is already saved; a version is never worth failing it
            _log.warning("Couldn't keep a version of %r: %s", name, describe(exc))

    def _keep_both_sides(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None,
                         compute: str | None, workspace: dict[str, str] | None, unreadable: bool) -> str | None:
        """save_notebook found the file changed on disk, so nothing was saved. Both sides are kept
        before anyone chooses, and never merged into the automatic versions: the file as the other
        program left it ("changed outside"; a byte copy when it isn't a notebook any more, whose path
        is returned) and the cells the app tried to save ("not saved"). Closing the app, or loading
        the other version, then loses neither."""
        copy = None
        try:
            if unreadable:
                copy = str(versions.keep_copy(self._versions_root, name, self.notebooks.path(name)))  # type: ignore[attr-defined]
            else:
                versions.record(self._versions_root, name, self._saved_nb(name), "changed outside")
        except Exception as exc:  # noqa: BLE001 - nothing is saved over it now either way
            _log.warning("Couldn't keep the outside version of %r: %s", name, describe(exc))
        try:
            versions.record(self._versions_root, name, to_ipynb(cells, lakehouse_ref, compute or "default", workspace),
                            "not saved")
        except Exception as exc:  # noqa: BLE001 - the screen still has the cells and keeps asking
            _log.warning("Couldn't keep the unsaved cells of %r: %s", name, describe(exc))
        return copy

    def notebook_versions(self, name: str) -> dict[str, Any]:
        """The notebook's versions, newest first; ``current`` marks those matching the saved file."""
        return versions.listing(self._versions_root, name, self._saved_nb(name))

    def notebook_version(self, name: str, version_id: str) -> dict[str, Any]:
        rec = versions.get(self._versions_root, name, version_id)
        return {"id": rec["id"], "at": rec["at"], "kind": rec["kind"], "label": rec.get("label") or "",
                **versions.as_notebook(rec)}

    def notebook_version_compare(self, name: str, version_id: str | None = None,
                                 cells: list[dict[str, Any]] | None = None, against: str = "version") -> dict[str, Any]:
        """How the notebook now (``cells`` as on screen, else the saved file) differs from a version,
        or with ``against="head"`` from its last Git commit. Source only, by cell."""
        now = to_ipynb(cells, None, "default", None) if cells is not None else self._saved_nb(name)
        if against == "head":
            text = release.committed_file(self.notebooks.path(name))  # type: ignore[attr-defined]
            if text is None:
                return {"name": name, "against": "head", "changes": [],
                        "problem": "This notebook isn't in a Git commit, so there's nothing to compare it with."}
            return {"name": name, "against": "head", "changes": versions.compare(json.loads(text), now), "problem": None}
        if against != "version":
            raise ValueError('against is "version" or "head"')
        rec = versions.get(self._versions_root, name, version_id or "")
        return {"name": name, "against": rec["id"], "at": rec["at"], "label": rec.get("label") or "",
                "changes": versions.compare(rec["nb"], now), "problem": None}

    def notebook_checkpoint(self, name: str, label: str) -> dict[str, Any]:
        """Keeps the saved notebook as a named checkpoint (kept until deleted)."""
        return versions.record(self._versions_root, name, self._saved_nb(name), "checkpoint", label) or {}

    def notebook_version_restore(self, name: str, version_id: str) -> dict[str, Any]:
        """Puts a version's cells back. The notebook as it is now is kept first, as "before restore".
        The lakehouse, compute and publish workspace stay as they are now."""
        rec = versions.get(self._versions_root, name, version_id)
        current = self._saved_nb(name)
        versions.record(self._versions_root, name, current, "before restore")
        old = versions.as_notebook(rec)
        now = self.notebooks.load(name)  # type: ignore[attr-defined]
        self.notebooks.save(name, old["cells"], now["lakehouse"])  # type: ignore[attr-defined]
        self._keep_version(name)
        return self.open_notebook(name) | {"restored": rec["id"], "restored_at": rec["at"]}  # type: ignore[attr-defined]

    def notebook_version_delete(self, name: str, version_id: str) -> dict[str, Any]:
        versions.delete(self._versions_root, name, version_id)
        return {"deleted": version_id}
