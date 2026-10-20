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
from .meta import release
from .redact import describe
from .notebook import to_ipynb

_log = logging.getLogger(__name__)

METHODS = {"notebook_versions", "notebook_version", "notebook_version_compare", "notebook_checkpoint",
           "notebook_version_restore", "notebook_version_delete"}


class NotebookVersionsMixin:
    @property
    def _versions_root(self) -> Path:
        return Path(self.root) / "notebook-versions"  # type: ignore[attr-defined]

    def _saved_nb(self, name: str) -> dict[str, Any]:
        path = self.notebooks.path(name)  # type: ignore[attr-defined]
        if not path.is_file():
            raise ValueError(f"There's no notebook named {name!r}.")
        return json.loads(path.read_text(encoding="utf-8"))

    def _keep_version(self, name: str) -> None:
        """After a save: keep the saved file as a version. A failure here never fails the save."""
        try:
            versions.record(self._versions_root, name, self._saved_nb(name))
        except (OSError, ValueError) as exc:
            _log.warning("Couldn't keep a version of %r: %s", name, describe(exc))

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
