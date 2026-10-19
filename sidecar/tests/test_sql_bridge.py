from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _run(op: str, payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "fabric_desktop.sql_bridge", op],
        input=json.dumps(payload) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_classify_read_only():
    out = _run("classify", {"query": "SELECT 1", "dialect": "spark", "allow": ["read_only"]})
    assert out["permission"] == "read_only"
    assert out["refused"] is None


def test_classify_refuses_fabric_mutation():
    out = _run("classify", {"query": "GRANT SELECT ON t TO u", "dialect": "spark", "allow": ["read_only"]})
    assert out["refused"] is not None


def test_translate_to_duckdb():
    out = _run("translate", {"query": "SELECT 1", "dialect": "spark"})
    assert out["statements"] == ["SELECT 1"]
