"""jsonfile.py: a folder Windows refuses, Windows Security stopping a swap, set-aside copies that can't be
removed or made, and a settings file that parses as something other than an object."""

from __future__ import annotations

import json
import logging
import os
import tempfile

import pytest

from fabric_desktop import jsonfile


def windows_error(cls, winerror, text="refused"):
    exc = cls(13 if cls is PermissionError else 22, text)  # errno 13 would make any OSError a PermissionError
    exc.winerror = winerror
    return exc


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(jsonfile.time, "sleep", lambda s: None)


def test_a_folder_windows_wont_let_the_app_write_in_is_named(monkeypatch, tmp_path):
    def refuse(**kwargs):
        raise windows_error(PermissionError, 5, "Access is denied")
    monkeypatch.setattr(tempfile, "mkstemp", refuse)
    with pytest.raises(PermissionError, match="Windows won't let KratosForge write in data") as caught:
        jsonfile.write_json(tmp_path / "data" / "settings.json", {"a": 1})
    assert caught.value.winerror == 5 and caught.value.path.endswith("settings.json")
    assert isinstance(caught.value.__cause__, PermissionError)


def test_any_other_temp_file_failure_is_raised_as_it_is(monkeypatch, tmp_path):
    error = OSError(28, "No space left on device")
    monkeypatch.setattr(tempfile, "mkstemp", lambda **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(OSError) as caught:
        jsonfile.write_bytes(tmp_path / "pic.png", b"\x89PNG")
    assert caught.value is error


def test_windows_security_stopping_the_swap_is_said_at_once_and_the_temp_file_goes(monkeypatch, tmp_path):
    calls = []

    def blocked(src, dst):
        calls.append(dst)
        raise windows_error(OSError, 225, "Operation did not complete successfully because the file contains a virus")
    monkeypatch.setattr(jsonfile.os, "replace", blocked)
    target = tmp_path / "settings.json"
    with pytest.raises(OSError, match="Windows Security blocked it"):
        jsonfile.write_text(target, "{}")
    assert len(calls) == 1  # not retried: only a PermissionError is waited out
    assert list(tmp_path.iterdir()) == []  # the temporary file is removed


def test_a_permission_error_is_retried_then_explained(monkeypatch, tmp_path):
    calls = []

    def held(src, dst):
        calls.append(dst)
        raise windows_error(PermissionError, 225)
    monkeypatch.setattr(jsonfile.os, "replace", held)
    with pytest.raises(PermissionError, match="Windows Security blocked it"):
        jsonfile.replace(tmp_path / "a", tmp_path / "b")
    assert len(calls) == jsonfile.REPLACE_ATTEMPTS


def test_unlink_waits_out_a_brief_refusal(monkeypatch, tmp_path):
    target = tmp_path / "gone.json"
    target.write_text("{}")
    real, refused = type(target).unlink, []

    def flaky(self, missing_ok=False):
        if len(refused) < 2:
            refused.append(self)
            raise PermissionError(13, "in use")
        return real(self, missing_ok=missing_ok)
    monkeypatch.setattr(type(target), "unlink", flaky)
    jsonfile.unlink(target)
    assert not target.exists() and len(refused) == 2


def test_an_old_copy_that_cant_be_removed_stays_and_is_logged(monkeypatch, tmp_path, caplog):
    path = tmp_path / "settings.json"
    for n in range(jsonfile.KEEP_SET_ASIDE):
        (tmp_path / f"settings.json.corrupt-2020010{n + 1}T000000000000Z").write_text("x")
    oldest = tmp_path / "settings.json.corrupt-20200101T000000000000Z"
    real = type(path).unlink

    def stuck(self, missing_ok=False):
        if self == oldest:
            raise PermissionError(13, "in use")
        return real(self, missing_ok=missing_ok)
    monkeypatch.setattr(type(path), "unlink", stuck)
    path.write_text("{torn")
    with caplog.at_level(logging.INFO, logger=jsonfile.__name__):
        assert jsonfile.read_json(path, {"empty": True}) == {"empty": True}
    assert oldest.exists()
    assert any("Couldn't remove the old set-aside copy" in r.getMessage() for r in caplog.records)
    # Only the oldest was due to go (KEEP_SET_ASIDE with the new copy): the others and the new copy stay.
    assert len(list(tmp_path.glob("settings.json.corrupt-*"))) == jsonfile.KEEP_SET_ASIDE + 1


def test_a_torn_file_that_cant_be_set_aside_reads_as_the_default(monkeypatch, tmp_path, caplog):
    path = tmp_path / "s.json"
    path.write_text("{torn")
    monkeypatch.setattr(jsonfile, "set_aside", lambda p: (_ for _ in ()).throw(PermissionError(13, "in use")))
    with caplog.at_level(logging.WARNING, logger=jsonfile.__name__):
        assert jsonfile.read_json(path, []) == []
    assert path.read_text() == "{torn"
    assert "could not be read or moved aside" in caplog.text


@pytest.mark.parametrize("content", [[1, 2], 3, "text", None])
def test_a_settings_file_that_isnt_an_object_is_set_aside_and_reads_empty(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(content))
    assert jsonfile.read_object(path) == {}
    assert not path.exists()
    (copy,) = tmp_path.glob("settings.json.corrupt-*")
    assert json.loads(copy.read_text()) == content


def test_one_that_isnt_an_object_and_cant_be_moved_is_left_and_logged(monkeypatch, tmp_path, caplog):
    path = tmp_path / "settings.json"
    path.write_text("[1]")
    monkeypatch.setattr(jsonfile.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(13, "in use")))
    with caplog.at_level(logging.WARNING, logger=jsonfile.__name__):
        assert jsonfile.read_object(path) == {}
    assert path.read_text() == "[1]"
    assert "isn't one JSON object and could not be moved aside" in caplog.text


def test_an_object_reads_as_it_is(tmp_path):
    path = tmp_path / "settings.json"
    jsonfile.write_json(path, {"a": [1]}, indent=None)
    assert path.read_text() == '{"a": [1]}' and jsonfile.read_object(path) == {"a": [1]}
    assert os.listdir(tmp_path) == ["settings.json"]
