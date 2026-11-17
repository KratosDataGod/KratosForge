"""Mutation-testing gaps in engines/registry.py (docs/quality/mutation-testing.md, batch 49)."""

import importlib.metadata as md

import pytest

from fabric_desktop.engines import registry


def test_an_unknown_engine_is_a_value_error_that_lists_the_engines():
    with pytest.raises(ValueError) as caught:
        registry.get("no-such-engine")
    assert str(caught.value) == f"Unknown engine 'no-such-engine'. Engines: {', '.join(registry.ENGINES)}."


def test_local_spark_is_ready_only_with_pyspark_and_java(monkeypatch):
    monkeypatch.setattr(registry.md, "version", lambda name: "3.5.1")
    monkeypatch.setattr(registry, "trusted_cli", lambda name: "C:/java/bin/java.exe")
    assert registry.local_spark_status() == {"pyspark": "3.5.1", "java": True, "ready": True,
                                             "detail": "Apache Spark could run on this PC."}

    def missing(name):
        raise md.PackageNotFoundError(name)

    monkeypatch.setattr(registry.md, "version", missing)
    monkeypatch.setattr(registry, "trusted_cli", lambda name: None)
    assert registry.local_spark_status() == {"pyspark": None, "java": False, "ready": False,
                                             "detail": "Not installed: PySpark, Java."}
