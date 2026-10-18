"""Test and Prod run the files of the release they're on, and roll back to an earlier one."""

import json
import shutil

import polars as pl
import pytest

from fabric_desktop import receipts
from fabric_desktop.meta import snapshot
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "repo" / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    s.meta_set_environment("test", target="local")
    local = s._project().local_data
    shutil.copytree(local / "dev" / "LH_Bronze" / "Files", local / "test" / "LH_Bronze" / "Files")  # Test's landing files
    return s


def store(svc, env="test"):
    return svc._store(svc._project(), env)


def edit(svc, old, new):
    text = svc.meta_entity("silver.orders")["text"]
    assert old in text
    svc.meta_save_entity("silver.orders", text.replace(old, new, 1))


def two_releases(svc):
    """Test on v1, then on v2, which changes silver.orders' logic. Returns (v1, v2)."""
    v1 = svc.meta_promote("dev", "test")["version"]
    edit(svc, 'expr = "amount - coalesce(discount, 0)"', 'expr = "amount"')
    assert all(r["status"] == "ok" for r in svc.meta_run(["silver.orders"], backfill="full"))
    v2 = svc.meta_promote("dev", "test")["version"]
    assert v1 != v2
    return v1, v2


def test_a_promote_keeps_the_release_files_once(svc):
    v1 = svc.meta_promote("dev", "test")["version"]
    kept = store(svc).meta("release_files")
    assert set(kept["version"]) == {v1}
    paths = set(kept["path"])
    assert "layers.toml" in paths and "metadata/entities/silver.orders.toml" in paths
    assert "environments.toml" not in paths and not any(p.startswith(".local") for p in paths)
    svc.meta_promote("dev", "test")  # the same version again: nothing more is kept
    assert store(svc).meta("release_files").height == kept.height


def test_test_runs_its_release_not_the_folder(svc):
    v1 = svc.meta_promote("dev", "test")["version"]
    edit(svc, 'expr = "amount - coalesce(discount, 0)"', 'expr = "amount"')  # the folder moves on; Test is still on v1
    assert svc._project().version != v1
    results = svc.meta_run(env="test")
    assert [r["status"] for r in results] == ["ok"] * 6, results
    log = store(svc).meta("run_log")
    assert set(log["metadata"]) == {v1}
    # the released copy keeps Test's tables where the project keeps them, not inside the copy
    assert (svc._project().local_data / "test" / "LH_Silver").is_dir()
    released = svc._project().local_data / "releases" / "test" / v1
    assert (released / "layers.toml").exists() and not (released / ".local").exists()


def test_roll_back_to_an_earlier_release(svc):
    v1, v2 = two_releases(svc)
    listed = svc.meta_releases("test")
    assert [(r["version"], r["current"], r["kept"], r["verified"]) for r in listed["releases"]] == \
        [(v2, True, True, True), (v1, False, True, True)]

    preview = svc.meta_rollback_preview("test", v1)
    assert preview["from"] == v2 and preview["to"] == v1
    [orders] = preview["entities"]
    assert orders["entity"] == "silver.orders" and orders["logic"] is True

    out = svc.meta_rollback("test", v1)
    assert out["version"] == v1 and out["replaced"] == v2
    row = store(svc).meta("deployments").row(-1, named=True)
    assert row["version"] == v1 and row["source_env"] == "test"
    body = json.loads(row["receipt"])
    assert receipts.receipt_id(body) == row["receipt_id"] == out["receipt_id"]
    assert body["project"]["metadata_version"] == v1
    assert body["rollback"]["from_version"] == v2
    assert body["rollback"]["to_receipt_id"] == listed["releases"][1]["receipt_id"]

    assert all(r["status"] == "ok" for r in svc.meta_run(env="test"))
    assert store(svc).meta("run_log")["metadata"][-1] == v1
    # and forward again: v2 is a release Test keeps too
    svc.meta_rollback("test", v2)
    assert svc.meta_releases("test")["releases"][0]["version"] == v2


def test_rollback_refuses_what_it_cant_do(svc):
    v1, v2 = two_releases(svc)
    with pytest.raises(GateError, match="already on"):
        svc.meta_rollback("test", v2)
    with pytest.raises(GateError, match="never released"):
        svc.meta_rollback("test", "0000000")
    with pytest.raises(ValueError, match="Dev runs this folder"):
        svc.meta_rollback("dev", v1)
    assert store(svc).meta("deployments")["version"][-1] == v2


def test_a_release_promoted_before_releases_kept_files_cant_be_rolled_back_to(svc):
    v1, v2 = two_releases(svc)
    s = store(svc)
    s.write("LH_Meta", "meta", "release_files", s.meta("release_files").filter(pl.col("version") != v1),
            mode="overwrite")
    assert [r["kept"] for r in svc.meta_releases("test")["releases"]] == [True, False]
    with pytest.raises(GateError, match="promoted before releases kept them"):
        svc.meta_rollback("test", v1)


def test_altered_release_files_are_refused(svc):
    v1, v2 = two_releases(svc)
    s = store(svc)
    files = s.meta("release_files").with_columns(
        pl.when((pl.col("version") == v1) & (pl.col("path") == "layers.toml"))
        .then(pl.lit("IyBjaGFuZ2VkCg=="))  # "# changed\n", not what the hash says
        .otherwise(pl.col("content")).alias("content"))
    s.write("LH_Meta", "meta", "release_files", files, mode="overwrite")
    with pytest.raises(ValueError, match="doesn't match its recorded hash"):
        svc.meta_rollback("test", v1)
    assert s.meta("deployments")["version"][-1] == v2


def test_a_restored_release_is_written_once_and_reused(svc):
    v1 = svc.meta_promote("dev", "test")["version"]
    project = svc._project()
    first = snapshot.restore(project, store(svc), v1)
    marker = first.root / "layers.toml"
    stamp = marker.stat().st_mtime_ns
    again = snapshot.restore(project, store(svc), v1)
    assert again.root == first.root and marker.stat().st_mtime_ns == stamp and again.version == v1
