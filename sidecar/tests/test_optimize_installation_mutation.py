"""This installation's id (optimize/installation.py): the time stamp, the OS family, the saved file's
layout, the label's length, a file that can't be read, a new id on reset, and what counts as an id.
Mutation batch 61."""

import datetime as dt
import json

import pytest

from fabric_desktop.optimize import installation
from fabric_desktop.optimize.installation import Installation


def test_now_is_utc_to_the_second():
    stamp = installation.now()
    parsed = dt.datetime.fromisoformat(stamp)
    assert parsed.utcoffset() == dt.timedelta(0) and parsed.microsecond == 0 and stamp.endswith("+00:00")


@pytest.mark.parametrize("system, family", [("Darwin", "macOS"), ("Windows", "Windows"), ("Linux", "Linux"),
                                            ("", "unknown")])
def test_os_family_names_the_family_only(monkeypatch, system, family):
    monkeypatch.setattr(installation.platform, "system", lambda: system)
    assert installation.os_family() == family


def test_the_file_is_written_with_one_space_indents(tmp_path):
    inst = Installation(tmp_path)
    inst.set_label("Build server")
    text = inst.path.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=1) and '\n "installation_id"' in text


def test_a_label_keeps_its_first_80_characters(tmp_path):
    inst = Installation(tmp_path)
    inst.set_label("  " + "x" * 100 + "  ")
    assert inst.label == "x" * 80


def test_a_file_that_cant_be_read_counts_as_none(tmp_path):
    inst = Installation(tmp_path)
    inst.path.mkdir()  # reading it fails with an OSError
    assert inst._load() == {} and inst.label == ""


def test_reset_gives_the_new_id_and_keeps_the_label(tmp_path):
    inst = Installation(tmp_path)
    old = inst.id
    inst.set_label("Lab PC")
    new = inst.reset()
    assert new != old and new == inst.id and inst.label == "Lab PC"


@pytest.mark.parametrize("value", ["not an id", None, 42, "ABCDEF00-0000-4000-8000-000000000000"])
def test_only_a_lowercase_uuid_counts_as_an_id(value):
    assert installation._is_uuid(value) is False


def test_a_lowercase_uuid_counts_as_an_id():
    assert installation._is_uuid("abcdef00-0000-4000-8000-000000000000") is True
