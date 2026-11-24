"""Workspaces > Databricks > Volumes on this PC (Preview): the screen's calls, mixed into ``Service``.

The volumes themselves are databricks_volumes.py's: folders in the app's data folder that Databricks notebooks
and SQL cells on this PC name as ``/Volumes/main/<schema>/<volume>/<path>``. Nothing here reaches Databricks.

Every volume path is read by ``databricks_volumes.local_path`` before anything touches the disk, so ``..``, a
backslash, a drive, a name Windows changes, and a path whose real location (links and junctions followed) is
outside its volume are all refused by name. A folder is removed only after ``check_tree`` finds no link in it.

- ``databricks_volumes``, ``databricks_volume_describe`` and ``databricks_volume_files`` only read.
- ``databricks_volume_create``, ``_rename`` and ``_set_comment`` change one volume.
- ``databricks_volume_drop`` and ``databricks_volume_delete`` remove files at once (nothing keeps a copy), so
  each needs ``confirm``: the screen sends it after its confirm dialog.
- ``databricks_volume_upload`` copies a file on this PC into a volume folder, and ``databricks_volume_download``
  copies a volume's file to a file on this PC. Neither writes over a file that is there without ``overwrite``.
  A download is never written into OneDrive (the rule for files the app stores, tests/test_onedrive_folders.py)
  or into the volumes' own folder.
- ``databricks_volume_new_folder`` makes one folder; ``databricks_volume_reveal`` shows one in File Explorer.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from . import databricks_volumes as dv
from . import known_folders, trees

METHODS = {"databricks_volumes", "databricks_volume_describe", "databricks_volume_files", "databricks_volume_create",
           "databricks_volume_drop", "databricks_volume_rename", "databricks_volume_set_comment",
           "databricks_volume_upload", "databricks_volume_new_folder", "databricks_volume_download",
           "databricks_volume_delete", "databricks_volume_reveal"}
READS = {"databricks_volumes", "databricks_volume_describe", "databricks_volume_files"}
FILES_LIMIT = 2000  # entries a folder lists on the screen; LIST in a SQL cell reads the rest


def _refused(text: str) -> ValueError:
    return ValueError(f"Volumes on this PC: {text}")


def _within(path: str) -> tuple[dv.VolumePath, Path]:
    """A path below a volume's own folder (not the volume itself), and where it is on this PC."""
    where = dv.parse(path)
    if not where.parts:
        raise _refused(f"{where.uc_path()} is the volume itself. Name a file or folder inside it; to remove the "
                       "volume, drop it.")
    return where, dv.local_path(path)


def _local_file(path: str, what: str) -> Path:
    """An absolute path on this PC, as the person picked it in a file dialog."""
    text = str(path or "")
    if not text or "\x00" in text or not os.path.isabs(text):
        raise _refused(f"{what} must be a full path on this PC, such as C:\\Data\\orders.csv.")
    return Path(os.path.abspath(text))


def _copy(source: Path, target: Path) -> int:
    """Copy ``source`` to ``target`` through a new file beside it, so a copy cut short leaves no half file."""
    part = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        shutil.copyfile(source, part, follow_symlinks=True)
        os.replace(part, target)
    finally:
        if part.exists():
            part.unlink()
    return target.stat().st_size


class DatabricksVolumesMixin:
    def databricks_volumes(self, schema: str | None = None) -> dict[str, Any]:
        """The volumes on this PC (only ``schema``'s when given), and the folder they are kept in."""
        found = dv.volumes(dv.check_name("schema", schema) if schema else None)
        return {"root": str(dv.root() / dv.CATALOG), "volumes": [v.as_dict() for v in found]}

    def databricks_volume_describe(self, schema: str, volume: str) -> dict[str, Any]:
        return dv.describe(dv.check_name("schema", schema), dv.check_name("volume", volume)).as_dict()

    def databricks_volume_files(self, path: str, limit: int | None = None) -> dict[str, Any]:
        """What a folder in a volume holds, folders first; at most ``limit`` (FILES_LIMIT) entries."""
        cap = max(1, min(int(limit or FILES_LIMIT), FILES_LIMIT))
        found = dv.files(path, cap + 1)
        return {"path": dv.parse(path).uc_path(), "entries": [f.as_dict() for f in found[:cap]],
                "truncated": len(found) > cap}

    def databricks_volume_create(self, schema: str, volume: str, comment: str | None = None) -> dict[str, Any]:
        """Make a managed volume (its folder and comment). A volume of that name stops it: VOLUME_ALREADY_EXISTS."""
        schema, volume = (schema or "").strip(), (volume or "").strip()
        dv.create(schema, volume, (comment or "").strip() or None)
        return dv.describe(dv.check_name("schema", schema), dv.check_name("volume", volume)).as_dict()

    def databricks_volume_drop(self, schema: str, volume: str, confirm: bool = False) -> dict[str, Any]:
        """Remove a volume and every file in it. Needs ``confirm``: this PC keeps no copy to undrop."""
        schema, volume = dv.check_name("schema", schema), dv.check_name("volume", volume)
        if confirm is not True:
            raise _refused(f"dropping {dv.CATALOG}.{schema}.{volume} removes its files at once and can't be undone, "
                           "so it needs your confirmation. Nothing was removed.")
        dv.drop(schema, volume)  # a link inside is removed as a link, never followed
        return {"dropped": f"{dv.CATALOG}.{schema}.{volume}"}

    def databricks_volume_rename(self, schema: str, volume: str, new_name: str) -> dict[str, Any]:
        schema, volume = dv.check_name("schema", schema), dv.check_name("volume", volume)
        dv.rename(schema, volume, new_name.strip())
        return dv.describe(schema, dv.check_name("volume", new_name.strip())).as_dict()

    def databricks_volume_set_comment(self, schema: str, volume: str, comment: str | None = None) -> dict[str, Any]:
        schema, volume = dv.check_name("schema", schema), dv.check_name("volume", volume)
        dv.set_comment(schema, volume, (comment or "").strip() or None)
        return dv.describe(schema, volume).as_dict()

    def databricks_volume_upload(self, source: str, path: str, overwrite: bool = False) -> dict[str, Any]:
        """Copy the file ``source`` on this PC into the volume folder ``path``, under its own name. When a file of
        that name is there, nothing is written without ``overwrite`` and the answer says so."""
        src = _local_file(source, "The file to upload")
        if not src.is_file():
            raise _refused(f"{src} isn't a file on this PC. Pick a file to upload.")
        folder = dv.local_path(path)
        if not folder.is_dir():
            raise _refused(f"{dv.parse(path).uc_path()} isn't a folder in the volume.")
        target_path = dv.parse(path).uc_path().rstrip("/") + "/" + src.name
        target = dv.local_path(target_path)  # the name is read as any volume path's is
        if target.is_dir():
            raise _refused(f"{target_path} is a folder, so the file wasn't copied over it.")
        if target.exists() and overwrite is not True:
            return {"exists": True, "path": target_path}
        if os.path.normcase(os.path.realpath(src)) == os.path.normcase(os.path.realpath(target)):
            raise _refused(f"{src} is already {target_path}.")
        return {"path": target_path, "size": _copy(src, target)}

    def databricks_volume_new_folder(self, path: str) -> dict[str, Any]:
        where, target = _within(path)
        if target.exists() or os.path.islink(target):
            raise _refused(f"{where.uc_path()} is already there.")
        if not target.parent.is_dir():
            raise _refused(f"the folder that would hold {where.uc_path()} isn't there.")
        target.mkdir()
        return {"path": where.uc_path()}

    def databricks_volume_download(self, path: str, save_to: str, overwrite: bool = False) -> dict[str, Any]:
        """Copy a volume's file to ``save_to``, a file on this PC outside OneDrive and outside the volumes'
        folder. A file that is there is kept unless ``overwrite`` (the Save dialog asks first)."""
        where, src = _within(path)
        if not src.is_file():
            raise _refused(f"{where.uc_path()} isn't a file. Download copies one file at a time.")
        out = _local_file(save_to, "Where to save the file")
        if (refusal := known_folders.onedrive_refusal(out, "a file downloaded from a volume")):
            raise _refused(refusal)
        volumes = os.path.realpath(dv.root())
        if dv._inside(volumes, os.path.realpath(out)):
            raise _refused(f"{out} is inside the volumes' own folder. Use Upload to copy a file into a volume.")
        if not out.parent.is_dir():
            raise _refused(f"{out.parent} isn't a folder on this PC.")
        if out.is_dir():
            raise _refused(f"{out} is a folder. Name a file to save to.")
        if out.exists() and overwrite is not True:
            return {"exists": True, "saved_to": str(out)}
        return {"saved_to": str(out), "size": _copy(src, out)}

    def databricks_volume_delete(self, paths: list[str], confirm: bool = False) -> dict[str, Any]:
        """Remove files and folders in a volume at once. Needs ``confirm``. Every path is checked first, so a
        refusal removes nothing; a folder that holds a link is refused (what it points at may be outside)."""
        if confirm is not True:
            raise _refused("deleting files in a volume can't be undone, so it needs your confirmation. Nothing was "
                           "removed.")
        found = [_within(str(p)) for p in paths or []]
        if not found:
            raise _refused("pick the files to delete.")
        for where, target in found:
            if not target.exists() and not os.path.islink(target):
                raise FileNotFoundError(f"{where.uc_path()} doesn't exist in the volume {where.full_name}.")
            if target.is_dir() and not dv._is_link(target):
                dv.check_tree(target, where.uc_path())
        for _where, target in found:
            if target.is_dir() and not dv._is_link(target):
                trees.remove(target)
            else:
                target.unlink()
        return {"deleted": [w.uc_path() for w, _ in found]}

    def databricks_volume_reveal(self, path: str) -> dict[str, Any]:
        """Show a volume (or a file or folder in one) in File Explorer. Files are shown, never opened."""
        from .notebook_resources_service import _reveal
        where = dv.parse(path)
        target = dv.local_path(path)
        if not target.exists():
            raise FileNotFoundError(f"{where.uc_path()} doesn't exist in the volume {where.full_name}.")
        _reveal(target, select=bool(where.parts))
        return {"shown": str(target)}
