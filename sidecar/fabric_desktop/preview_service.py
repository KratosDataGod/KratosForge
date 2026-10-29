"""Workspaces page previews: what you click shows in a side panel. Read only, run on this PC."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from . import lakehouse, preview
from .onelake import FileStore, FileTooBig
from .retention import PREVIEWS


def preview_dest(folder: Path, path: str) -> Path:
    """Where a previewed file is downloaded: a fixed name in its own folder. The remote name never
    becomes part of the local path, so no name (a drive, a leading backslash, a UNC share) can point it elsewhere."""
    ext = PurePosixPath(path).suffix
    dest = folder / ("preview" + (ext if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext) else ""))
    if dest.parent != folder:
        raise ValueError(f"{path} can't be previewed")
    return dest


class PreviewMixin:
    def preview_table(self, lakehouse_ref: dict[str, str], table: str, limit: int = preview.ROWS) -> dict[str, Any]:
        self._attach(lakehouse_ref)  # type: ignore[attr-defined]
        return preview.table_preview(lakehouse.open_table(table), limit)

    def preview_file(self, lakehouse_ref: dict[str, str], path: str, size: int = 0,
                     limit: int = preview.ROWS) -> dict[str, Any]:
        kind = preview.file_kind(path)
        if kind == "other":
            return {"kind": "other"}
        if size > preview.MAX_FILE_BYTES:
            return {"kind": "too_big", "size": size}
        if not path.startswith("Files/") or ".." in path.split("/"):
            raise ValueError(f"{path} is not a file under Files/")
        ws, lh = lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"]
        # Its own folder, removed as soon as the preview is read: nothing stays on this PC. One left
        # behind (the engine stopped mid-preview) goes at the next start (retention.py).
        folder = Path(self.root) / PREVIEWS / uuid.uuid4().hex  # type: ignore[attr-defined]
        dest = preview_dest(folder, path)
        try:
            try:
                FileStore(self.auth.storage_token).download(ws, lh, path, dest,  # type: ignore[attr-defined]
                                                            max_bytes=preview.MAX_FILE_BYTES)
            except FileTooBig:
                # The listing didn't give its size (or gave it wrong): stopped at the limit instead.
                return {"kind": "too_big", "size": size or preview.MAX_FILE_BYTES + 1}
            return preview.file_preview(dest, path, limit)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def preview_notebook(self, workspace_id: str, item_id: str) -> dict[str, Any]:
        return preview.notebook_cells(self.fabric.get_item_definition(workspace_id, item_id, "ipynb"))  # type: ignore[attr-defined]
