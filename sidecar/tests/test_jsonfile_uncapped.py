"""Shared JSON file paths (jsonfile.py) that the uncapped mutation run (batch 215,
docs/quality/mutation-testing.md) showed no test checked: a failed write whose temporary file is
already gone still raises its own error, and unlink waits out a busy file with the same backoff as
replace."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import jsonfile


def _replace_after_temp_vanished(src, dst):
    """A virus scanner took the temporary file away, and the swap then failed."""
    Path(src).unlink()
    raise OSError("the swap failed")


@pytest.mark.parametrize("write", [lambda p: jsonfile.write_text(p, "new"), lambda p: jsonfile.write_bytes(p, b"new")],
                         ids=["text", "bytes"])
def test_a_failed_write_whose_temp_file_is_gone_raises_its_own_error(tmp_path, monkeypatch, write):
    monkeypatch.setattr(jsonfile, "replace", _replace_after_temp_vanished)
    target = tmp_path / "settings.json"
    with pytest.raises(OSError, match="the swap failed"):
        write(target)
    assert list(tmp_path.iterdir()) == []


def test_unlink_waits_out_a_busy_file_doubling_each_time(tmp_path, monkeypatch):
    target = tmp_path / "busy.json"
    target.write_text("{}", encoding="utf-8")
    refusals = iter(range(3))
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self == target and next(refusals, None) is not None:
            raise PermissionError(13, "in use")
        real_unlink(self, missing_ok=missing_ok)

    waits: list[float] = []
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(jsonfile.time, "sleep", waits.append)
    jsonfile.unlink(target)
    assert not target.exists()
    assert waits == [0.05, 0.1, 0.2]
