"""EA-001 beyond publish: a metadata run's appends and MERGEs, a notebook published to Fabric, and
Spark INSERT's NaN all follow the same rule (localdata.delta.conversions)."""

import os
import re
import subprocess
import sys

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable

from fabric_desktop import fabric_run
from fabric_desktop.meta.store import LocalStore
from test_prove import FAKE_NOTEBOOKUTILS


# ---- metadata runs

@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path, "dev")


def test_a_metadata_append_that_would_change_values_stops(store):
    store.write("LH_Silver", "dbo", "t", pl.DataFrame({"id": [1], "qty": [3]}), mode="overwrite")
    with pytest.raises(ValueError, match=r"dbo\.t would change values.*qty is double here but bigint in the table"):
        store.write("LH_Silver", "dbo", "t", pl.DataFrame({"id": [2], "qty": [2.75]}), mode="append")
    assert DeltaTable(store.uri("LH_Silver", "dbo", "t")).version() == 0


def test_a_metadata_append_that_widens_still_writes(store):
    store.write("LH_Silver", "dbo", "t", pl.DataFrame({"id": [1]}, schema={"id": pl.Int64}), mode="overwrite")
    store.write("LH_Silver", "dbo", "t", pl.DataFrame({"id": [2]}, schema={"id": pl.Int32}), mode="append")
    assert sorted(DeltaTable(store.uri("LH_Silver", "dbo", "t")).to_pyarrow_table()["id"].to_pylist()) == [1, 2]


def test_a_metadata_overwrite_still_replaces_the_schema(store):
    store.write("LH_Silver", "dbo", "t", pl.DataFrame({"qty": [3]}), mode="overwrite")
    store.write("LH_Silver", "dbo", "t", pl.DataFrame({"qty": [2.75]}), mode="overwrite")
    assert DeltaTable(store.uri("LH_Silver", "dbo", "t")).to_pyarrow_table()["qty"].to_pylist() == [2.75]


# ---- a notebook published to Fabric: the shim's write_table

def test_the_published_shim_refuses_the_same_appends(tmp_path):
    lh = tmp_path / "lh"
    (lh / "Tables" / "dbo").mkdir(parents=True)
    fake = tmp_path / "fake_fabric"
    fake.mkdir()
    (fake / "notebookutils.py").write_text(FAKE_NOTEBOOKUTILS, encoding="utf-8")
    shim = fabric_run.shim_cell({"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH"})["source"]
    shim, n = re.subn(r"^_fabric_desktop_module\((.*)\)$",
                      lambda m: f"_fabric_desktop_module({m.group(1)}, root={str(lh)!r}, mount={str(lh)!r})", shim, flags=re.M)
    assert n == 1
    script = tmp_path / "published.py"
    script.write_text(shim + """
from fabric_desktop import lakehouse
import pyarrow as pa
lakehouse.write_table(pa.table({"n": pa.array([1], pa.int64())}), "dbo.t")
lakehouse.write_table(pa.table({"n": pa.array([2], pa.int32())}), "dbo.t", mode="append")
try:
    lakehouse.write_table(pa.table({"n": [2.75]}), "dbo.t", mode="append")
except ValueError as e:
    print("REFUSED", e)
print(sorted(lakehouse.read_table("dbo.t")["n"].to_list()), flush=True)
import os
os._exit(0)  # past the runtime's thread pools, which can abort at interpreter exit
""", encoding="utf-8")
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, cwd=tmp_path,
                         env=dict(os.environ, PYTHONPATH=str(fake)))
    assert out.returncode == 0, out.stderr
    assert "REFUSED Writing to dbo.t would change values" in out.stdout and "n is double here but int64 in the table" in out.stdout
    assert out.stdout.strip().endswith("[1, 2]")


def test_the_shim_carries_the_pcs_rule_not_a_copy():
    source = fabric_run.shim_cell({"workspace_id": "w", "lakehouse_id": "l", "name": "n"})["source"]
    assert "def _keeps_values" in source and "def conversions" in source


# ---- Spark INSERT: NaN is NaN after a cast

def test_an_insert_of_nan_into_a_float_column_writes(monkeypatch):
    from test_append_conversions import _table_with
    assign = _table_with(monkeypatch, pa.schema([("x", pa.float32())]))
    out = assign(pa.table({"x": pa.array([float("nan"), 1.5, None])}), "dbo.t")
    assert out.schema.field("x").type == pa.float32()
