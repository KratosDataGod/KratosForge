"""Desktop data folder on each operating system."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fabric_desktop.paths import data_dir, desktop_data_base


def test_localappdata_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    assert data_dir() == tmp_path / "la" / "FabricDesktop"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS data folder")
def test_darwin_uses_application_support(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert data_dir() == Path.home() / "Library" / "Application Support" / "FabricDesktop"
    assert desktop_data_base() == Path.home() / "Library" / "Application Support"
