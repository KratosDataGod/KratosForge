"""Boundary kill test for spark_stream (mutation testing, batch 167)."""
from __future__ import annotations

import pytest

from tests.test_spark_stream import lake  # noqa: F401 - a fixture
from tests.test_spark_stream_edges import k, outputs  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_a_second_run_with_no_new_commit_reads_nothing(k, lake):  # noqa: F811
    code = ("q = spark.readStream.table('dbo.bronze').writeStream.option('checkpointLocation', 'Files/cp/b')"
            ".toTable('dbo.silver')\n")
    status, text = outputs(k, code)
    assert status == "ok" and "from versions 0..0" in text, text
    status, text = outputs(k, code)
    assert status == "ok", text
    assert "no new commits after version 0" in text and "versions 1..0" not in text, text
