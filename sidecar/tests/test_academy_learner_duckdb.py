"""Academy: DuckDB in the learner process keeps its extensions outside the exercise folder.

Learner DuckDB can't install or auto-load extensions, through a connection of its own, the module's
default connection or the PySpark shim; the extensions built into DuckDB (parquet, json) still read the
workspace's files. A local listener stands in for an extension repository and a file server.
"""

from __future__ import annotations

import glob
import os
import re
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from fabric_desktop.academy import exercises, pyspark_lab, sandbox
from fabric_desktop.academy import workspace as ws

PACK = "dbx-de-associate"


@pytest.fixture(autouse=True)
def _v1(monkeypatch: pytest.MonkeyPatch) -> None:
    # The v1 learner process, as delta_lab, pyspark_lab and python_lab without an AppContainer use it.
    monkeypatch.setattr(sandbox, "USE_APPCONTAINER", False)


class _Listener:
    """A TCP listener on 127.0.0.1 that counts the connections it is offered."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                con, _ = self.sock.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            self.connections += 1
            con.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


@pytest.fixture
def listener() -> Iterator[_Listener]:
    srv = _Listener()
    try:
        yield srv
    finally:
        srv.close()


def _extension_folders(work: Path) -> list[str]:
    """Extension folders a run could have made: DuckDB's default under HOME, and the one the bootstrap names."""
    found = [str(p) for p in (work / ".tmp").glob(".duckdb*")]
    anchor = os.path.splitdrive(str(work))[0] + os.sep
    return found + glob.glob(os.path.join(anchor, "kf-no-extensions-*"))


def _stopped_by_duckdb(stderr: str) -> bool:
    """The traceback ends in a DuckDB error (or the bootstrap's PermissionError), not some other failure."""
    return re.search(r"^(?:_?duckdb\.\w*(?:Exception|Error)|PermissionError)\b", stderr, re.M) is not None


_PYTHON_CASES = [
    "import duckdb; duckdb.connect().sql(\"INSTALL httpfs FROM '{url}/x'\")",
    "import duckdb; duckdb.connect(':memory:').execute(\"FORCE INSTALL httpfs FROM '{url}/x'\")",
    "import duckdb; duckdb.connect(config={{'extension_directory': '.tmp/ext'}}).sql(\"INSTALL httpfs FROM '{url}/x'\")",
    "import duckdb; duckdb.sql(\"INSTALL httpfs FROM '{url}/x'\")",
    "import duckdb; duckdb.sql(\"FORCE INSTALL httpfs FROM '{url}/x'\")",
    "import duckdb; duckdb.install_extension('httpfs', repository_url='{url}/r')",
    "import duckdb; duckdb.execute(\"INSTALL json FROM '{url}/x'\")",
    "import duckdb; duckdb.sql(\"SELECT * FROM read_csv('{url}/x.csv')\").fetchall()",
    "import duckdb; duckdb.connect().sql(\"SELECT * FROM read_csv('{url}/x.csv')\").fetchall()",
    "import duckdb; duckdb.sql(\"SELECT * FROM '{url}/x.parquet'\").fetchall()",
    "import duckdb; duckdb.sql(\"LOAD httpfs\")",
    "import duckdb; duckdb.connect(config={{'custom_extension_repository': '{url}/r', 'TimeZone': 'UTC'}})",
    "import duckdb; duckdb.connect(':memory:', False, {{'custom_extension_repository': '{url}/r', 'TimeZone': 'UTC'}})",
    ("import duckdb; duckdb.connect(config={{'autoinstall_known_extensions': True, 'AUTOLOAD_KNOWN_EXTENSIONS': True, "
     "'custom_extension_repository': '{url}/r'}}).sql(\"SELECT * FROM '{url}/x.parquet'\").fetchall()"),
]


@pytest.mark.parametrize("code", _PYTHON_CASES)
def test_learner_duckdb_cannot_fetch_extensions(tmp_path: Path, listener: _Listener, code: str) -> None:
    """Each attempt stops with an error, and nothing connects to the listener."""
    root = tmp_path / "data"
    work = ws.workspace_dir(root, "dp-700", "E22")
    work.mkdir(parents=True)
    (work / "answer.py").write_text(code.format(url=f"http://127.0.0.1:{listener.port}") + "\n", encoding="utf-8")
    res = sandbox.run(work, "answer.py", root)
    assert res["status"] == "error", res
    assert _stopped_by_duckdb(res["stderr"]), res["stderr"]
    assert listener.connections == 0
    assert _extension_folders(work) == []


_SPARK_CASES = [
    "spark.sql(\"INSTALL httpfs FROM '{url}/x'\")",
    "spark.sql(\"FORCE INSTALL httpfs FROM '{url}/x'\")",
    "spark.sql(\"SELECT * FROM read_csv('{url}/x.csv')\").collect()",
    "spark.conn.sql(\"INSTALL httpfs FROM '{url}/x'\")",
    "import duckdb; duckdb.sql(\"INSTALL httpfs FROM '{url}/x'\")",
]


@pytest.mark.parametrize("code", _SPARK_CASES)
def test_learner_duckdb_cannot_fetch_extensions_through_the_pyspark_shim(tmp_path: Path, listener: _Listener,
                                                                         code: str) -> None:
    """The same through the PySpark shim's session, in pyspark_lab's learner process."""
    work = tmp_path / "data" / "academy" / "w"
    work.mkdir(parents=True)
    pyspark_lab.prepare(work, exercises.file_text(PACK, "E19", "setup.sql"))
    run = pyspark_lab.run_learner(work, code.format(url=f"http://127.0.0.1:{listener.port}"),
                                  data_root=tmp_path / "data")
    assert run["status"] != "ok", run
    assert _stopped_by_duckdb(run["stderr"]), run["stderr"]
    assert listener.connections == 0
    assert _extension_folders(work) == []


def test_learner_duckdb_still_reads_local_parquet_csv_and_json(tmp_path: Path) -> None:
    """The extensions built into DuckDB still read and write the workspace's files, on both kinds of connection."""
    root = tmp_path / "data"
    work = ws.workspace_dir(root, "dp-700", "E22")
    work.mkdir(parents=True)
    (work / "people.csv").write_text("id,name\n1,Ada\n2,Grace\n", encoding="utf-8")
    (work / "people.json").write_text('[{"id": 1, "tags": {"a": 1}}, {"id": 2, "tags": {"a": 2}}]', encoding="utf-8")
    (work / "answer.py").write_text(
        "import duckdb\n"
        "duckdb.sql(\"COPY (SELECT * FROM read_csv('people.csv')) TO 'people.parquet' (FORMAT parquet)\")\n"
        "con = duckdb.connect()\n"
        "print(con.sql(\"SELECT count(*) FROM read_parquet('people.parquet')\").fetchall()[0][0])\n"
        "print(duckdb.sql(\"SELECT sum(tags.a) FROM read_json('people.json')\").fetchall()[0][0])\n"
        "print(con.sql(\"SELECT json_extract('{\\\"k\\\": 7}', '$.k')\").fetchall()[0][0])\n"
        "print(con.sql(\"SELECT strftime(TIMESTAMPTZ '2026-01-02 03:04:05+00', '%Y')\").fetchall()[0][0])\n",
        encoding="utf-8")
    res = sandbox.run(work, "answer.py", root)
    assert res["status"] == "ok", res["stderr"]
    assert res["stdout"].split() == ["2", "3", "7", "2026"]


def test_the_pyspark_shim_still_reads_and_writes_delta_tables(tmp_path: Path) -> None:
    work = tmp_path / "data" / "academy" / "w"
    work.mkdir(parents=True)
    pyspark_lab.prepare(work, exercises.file_text(PACK, "E19", "setup.sql"))
    run = pyspark_lab.run_learner(
        work,
        "df = spark.read.table('bronze.orders')\n"
        "df.write.format('delta').mode('overwrite').saveAsTable('bronze.copy')\n"
        "print(spark.read.table('bronze.copy').count())\n",
        data_root=tmp_path / "data")
    assert run["status"] == "ok", run["stderr"]
    assert run["stdout"].strip() == "13"
