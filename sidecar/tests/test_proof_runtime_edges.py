"""The proof runtime: the read-only guard, seeded sandboxes, randomness and the saved log, in a
temporary folder. Nothing reaches a lakehouse."""

import json
import os
import sys
import time

import pytest

from fabric_desktop import proof_runtime as pr


def runtime(**config):
    return pr.ProofRuntime({"id": "p1"} | config, "local")


def test_a_pinned_table_cant_be_changed_or_given_attributes():
    class Table:
        version = 3

    t = pr.ReadOnlyTable(Table())
    assert t.version == 3
    with pytest.raises(PermissionError, match="merge was called"):
        t.merge  # noqa: B018
    with pytest.raises(PermissionError, match="doesn't change shared tables"):
        t.version = 4
    assert repr(t).startswith("ReadOnlyTable(<")


def test_a_seeded_table_reads_from_the_sandbox_as_this_sides_own(tmp_path):
    rt = runtime(seeded=["DBO.Orders"])
    rt.root = str(tmp_path)
    assert rt.log["writes"] == {"dbo.orders": ["seeded"]}
    uri, version = rt.read_location("dbo", "Orders")
    assert uri.endswith("proofs/p1/local/tables/dbo/orders") and version is None


def test_without_numpy_only_random_is_seeded(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "numpy", None)  # an import of it fails
    rt = runtime(seed=7)
    rt.bind(tmp_path)
    rt.restore()
    assert rt.log["seeded"] == ["random"]


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="the time zone can only be set where time.tzset exists")
def test_restore_puts_back_the_time_zone_the_process_had(monkeypatch, tmp_path):
    monkeypatch.setenv("TZ", "Europe/Paris")
    try:
        rt = runtime().bind(tmp_path)
        assert os.environ["TZ"] == "UTC" and rt.log["timezone"] == "UTC"
        rt.restore()
        assert os.environ["TZ"] == "Europe/Paris"
        rt.restore()  # a second restore does nothing
        assert os.environ["TZ"] == "Europe/Paris"
    finally:
        monkeypatch.undo()
        time.tzset()  # the rest of this process runs in the zone it had before, even when an assert failed


def test_the_environment_lists_every_distribution_or_none_when_owners_cant_be_read(monkeypatch):
    import importlib.metadata as md

    everything = runtime().environment()
    assert "pytest" in everything["packages"]
    monkeypatch.setattr(md, "packages_distributions", lambda: (_ for _ in ()).throw(OSError("unreadable")))
    named = runtime().environment(["pytest", "not_a_module_here"])
    assert list(named["packages"]) == ["pytest"]


def test_save_writes_the_log_through_the_mount(tmp_path):
    rt = runtime()
    rt.root = "abfss://ws@onelake/lh"
    rt.mount = str(tmp_path / "mnt")
    log = rt.save(["pytest"])
    saved = json.loads((tmp_path / "mnt" / pr.PROOF_ROOT / "p1" / "local" / "log.json").read_text(encoding="utf-8"))
    assert saved["proof"] == "p1" and saved["environment"]["packages"] == log["environment"]["packages"]
    assert saved["seconds"] >= 0
