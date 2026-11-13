"""notebookutils.lakehouse.loadTable on this PC: a CSV or Parquet file (or folder) in the attached
lakehouse's Files/ loaded into one of its tables, as Fabric's Load to Tables does."""

import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable

from fabric_desktop import onelake
from fabric_desktop.notebook import Kernel
from tests.test_spark_writer import lake  # noqa: F401 - a fixture


@pytest.fixture
def files(lake, monkeypatch):  # noqa: F811
    """The lakehouse's Files/ is a folder beside its Tables/."""
    root = lake.parent

    def download(self, ws, lh, rel, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, dest)
        return dest

    def listing(self, ws, lh, directory="Files", recursive=False, max_results=None):
        base = root / directory
        found = base.rglob("*") if recursive else base.iterdir()
        return [{"name": p.name, "path": p.relative_to(root).as_posix(), "is_dir": p.is_dir()} for p in found]
    monkeypatch.setattr(onelake.FileStore, "download", download)
    monkeypatch.setattr(onelake.FileStore, "list", listing)
    (root / "Files" / "in" / "more").mkdir(parents=True)
    (root / "Files" / "sales.csv").write_text("id;region\n1;EU\n2;US\n", encoding="utf-8")
    (root / "Files" / "in" / "a.csv").write_text("id,region\n10,EU\n", encoding="utf-8")
    (root / "Files" / "in" / "_SUCCESS").write_text("", encoding="utf-8")
    (root / "Files" / "in" / "more" / "b.csv").write_text("id,region\n11,US\n", encoding="utf-8")
    pq.write_table(pa.table({"id": [7], "amount": [1.5]}), root / "Files" / "p.parquet")
    return lake


def rows(lake, table):
    return sorted(DeltaTable(str(lake / "dbo" / table)).to_pyarrow_table().to_pylist(), key=lambda r: r["id"])


def run(code):
    out = Kernel().run(code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


def test_a_csv_file_overwrites_a_table_with_its_columns(files):
    status, text = run("notebookutils.lakehouse.loadTable({'relativePath': 'Files/sales.csv', 'pathType': 'File', "
                       "'mode': 'Overwrite', 'formatOptions': {'format': 'Csv', 'header': True, 'delimiter': ';'}}, "
                       "'sales')")
    assert status == "ok", text
    assert rows(files, "sales") == [{"id": 1, "region": "EU"}, {"id": 2, "region": "US"}]


def test_a_folder_appends_and_recursive_takes_subfolders(files):
    load = ("notebookutils.lakehouse.loadTable({{'relativePath': 'Files/in', 'pathType': 'Folder', 'mode': 'Append', "
            "'recursive': {}}}, 'sales')")
    assert run(load.format(False))[0] == "ok"
    assert [r["id"] for r in rows(files, "sales")] == [1, 2, 3, 10]
    assert run(load.format(True))[0] == "ok"
    assert [r["id"] for r in rows(files, "sales")] == [1, 2, 3, 10, 10, 11]


def test_a_parquet_file_makes_a_new_table(files):
    status, text = run("notebookutils.lakehouse.loadTable({'relativePath': 'Files/p.parquet', "
                       "'formatOptions': {'format': 'Parquet'}}, 'dbo.amounts')")
    assert status == "ok", text
    assert rows(files, "amounts") == [{"id": 7, "amount": 1.5}]


@pytest.mark.parametrize("call, says", [
    ("{'relativePath': 'Files/sales.csv'}, 'sales', 'LH_Other'", "runs in Fabric"),
    ("{'relativePath': 'Files/sales.csv'}, 'sales', '', 'ws2'", "runs in Fabric"),
    ("{'relativePath': 'Tables/dbo/sales'}, 'sales'", "path under Files/"),
    ("{'relativePath': 'Files/../x.csv'}, 'sales'", "path under Files/"),
    ("{'relativePath': 'Files/sales.csv', 'mode': 'Merge'}, 'sales'", "Overwrite or Append"),
    ("{'relativePath': 'Files/sales.csv', 'formatOptions': {'format': 'Json'}}, 'sales'", "Csv or Parquet"),
    ("'Files/sales.csv', 'sales'", "loadOption is a dict"),
])
def test_what_loadtable_refuses_leaves_the_table_as_it_was(files, call, says):
    before = rows(files, "sales")
    status, text = run(f"notebookutils.lakehouse.loadTable({call})")
    assert status == "error" and says in text, text
    assert rows(files, "sales") == before
