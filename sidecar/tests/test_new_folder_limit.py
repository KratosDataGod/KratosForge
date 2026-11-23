"""A new folder of 248 to 259 characters, on a PC without long paths, is refused by name.

Windows makes a folder only up to 248 characters (room for an 8.3 file name in it), not 260, so
saving a.json into a new folder of 250 characters failed with "The system cannot find the path
specified" though the file's own path was under 260: the long-path message didn't apply, and the
person was sent looking for a folder that is there. Off Windows, os.mkdir is played by a stand-in
that refuses from 248 characters as CreateDirectoryW does.
"""

from __future__ import annotations

import os

import pytest

from fabric_desktop import jsonfile, support_bundle, win_paths

FOLDER_TEXT = ("couldn't be saved, because the folder {folder} it goes in would have a path that is {n} characters, "
               "past the 248 Windows allows for a new folder on this PC while long paths are turned off. Move it to "
               "a folder with a shorter path, or ask IT to turn on long paths.")


@pytest.fixture
def windows_folders(monkeypatch):
    real = os.mkdir

    def mkdir(path, *args, **kwargs):
        if len(os.path.abspath(os.fspath(path))) >= win_paths.FOLDER_LIMIT:
            exc = FileNotFoundError(2, "The filename or extension is too long", os.fspath(path))
            exc.winerror = 206  # type: ignore[attr-defined]
            raise exc
        return real(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", mkdir)
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)


def _parent(tmp_path, length):
    """An existing folder whose path is ``length`` characters."""
    pad = length - len(str(tmp_path)) - 1
    assert pad > 0
    parent = tmp_path / ("p" * pad)
    parent.mkdir()
    assert len(str(parent)) == length
    return parent


def test_a_file_in_a_new_folder_past_248_characters_names_the_folder(tmp_path, windows_folders):
    folder = _parent(tmp_path, 240) / "Reports2"  # 249 characters
    target = folder / "a.json"  # 256: under 260
    with pytest.raises(OSError) as caught:
        jsonfile.write_json(target, {})
    text = str(caught.value)
    assert text == "a.json " + FOLDER_TEXT.format(folder="Reports2", n=249), text
    assert str(tmp_path) not in text and not folder.exists()


def test_a_new_folder_itself_is_named_by_its_own_path(tmp_path, windows_folders):
    folder = _parent(tmp_path, 240) / "Exports2"
    with pytest.raises(OSError) as caught, jsonfile.long_path_named(folder, "made"):
        folder.mkdir(parents=True, exist_ok=True)
    assert str(caught.value).startswith("Exports2 couldn't be made, because its path is 249 characters, past the 248 ")


@pytest.mark.parametrize("case", ["short", "exists", "no_parent", "elsewhere", "long_paths_on"])
def test_only_a_new_folder_windows_refuses_for_its_length_is_named(tmp_path, case):
    parent = _parent(tmp_path, 240)
    folder = parent / ("R" if case == "short" else "Reports2")
    if case == "exists":
        folder.mkdir()
    if case == "no_parent":
        folder = parent / "gone" / "Rep"  # 252, but its own folder isn't there either
    exc = FileNotFoundError(2, "The system cannot find the path specified", str(folder))
    exc.winerror = 3  # type: ignore[attr-defined]
    target = (tmp_path / "other.json") if case == "elsewhere" else folder / "a.json"
    assert win_paths.too_long(exc, target, "saved", case == "long_paths_on") is None
    if case == "long_paths_on":  # the same failure with long paths off is named
        assert win_paths.too_long(exc, target, "saved", False) is not None
