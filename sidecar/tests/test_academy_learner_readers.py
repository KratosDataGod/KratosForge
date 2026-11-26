"""Academy: learner data readers open only files in the exercise folder.

The Polars, pandas, pyarrow and deltalake readers and writers in the learner process check their
arguments first, and a wrapper holds no reference to the library function it stands for: not as
``__wrapped__``, a closure cell, a default or a global. A local listener stands in for a remote store.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from fabric_desktop.academy import sandbox
from fabric_desktop.academy import workspace as ws


@pytest.fixture(autouse=True)
def _v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "USE_APPCONTAINER", False)


class _Listener:
    """A TCP listener on 127.0.0.1 that counts the connections it is offered."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
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


def _run(tmp_path: Path, code: str) -> dict:
    root = tmp_path / "data"
    work = ws.workspace_dir(root, "dp-700", "E22")
    work.mkdir(parents=True, exist_ok=True)
    (work / "answer.py").write_text(code, encoding="utf-8")
    return sandbox.run(work, "answer.py", root)


# Everything a wrapper could keep a library function in, gathered from the wrapper itself.
_HELD = '''
import types

def held_by(w):
    """Callables reachable from w's __wrapped__, closure cells, defaults, keyword defaults and globals."""
    out = []
    for name in ("__wrapped__", "__func__"):
        v = getattr(w, name, None)
        if v is not None:
            out.append(v)
    out += [c.cell_contents for c in (getattr(w, "__closure__", None) or ()) if c.cell_contents is not None]
    out += list(getattr(w, "__defaults__", None) or ())
    out += list((getattr(w, "__kwdefaults__", None) or {}).values())
    out += list((getattr(w, "__globals__", None) or {}).values())
    return [v for v in out if callable(v) and v is not w]

tried = 0
def each(w, call):
    global tried
    for v in held_by(w):
        for inner in [v] + held_by(v):
            try:
                tried += 1
                call(inner)
            except BaseException:
                pass
'''

_S3 = ("{{'AWS_ENDPOINT_URL': 'http://127.0.0.1:{port}', 'AWS_ALLOW_HTTP': 'true', 'AWS_ACCESS_KEY_ID': 'a', "
       "'AWS_SECRET_ACCESS_KEY': 'b', 'AWS_REGION': 'us-east-1'}}")
_PA_S3 = "s3://bucket/x.parquet?endpoint_override=127.0.0.1:{port}&scheme=http&region=us-east-1&anonymous=true"

_ROUTES = {
    "polars scan": "import polars as pl\neach(pl.scan_parquet, lambda f: f('{url}/x.parquet').collect())",
    "polars read": "import polars as pl\neach(pl.read_parquet, lambda f: f('{url}/x.parquet'))",
    "polars csv": "import polars as pl\neach(pl.scan_csv, lambda f: f('{url}/x.csv').collect())",
    "polars write": ("import polars as pl\ndf = pl.DataFrame({{'a': [1]}})\n"
                     "each(pl.DataFrame.write_parquet, lambda f: f(df, 's3://b/x.parquet', storage_options=" + _S3 + "))"),
    "polars sink": ("import polars as pl\nlf = pl.LazyFrame({{'a': [1]}})\n"
                    "each(pl.LazyFrame.sink_parquet, lambda f: f(lf, 's3://b/x.parquet', storage_options=" + _S3 + "))"),
    "pandas": "import pandas as pd\neach(pd.read_parquet, lambda f: f('" + _PA_S3 + "'))",
    "pandas write": ("import pandas as pd\ndf = pd.DataFrame({{'a': [1]}})\n"
                     "each(pd.DataFrame.to_parquet, lambda f: f(df, '" + _PA_S3 + "'))"),
    "pyarrow read": "import pyarrow.parquet as pq\neach(pq.read_table, lambda f: f('" + _PA_S3 + "'))",
    "pyarrow dataset": "import pyarrow.dataset as ds\neach(ds.dataset, lambda f: f('" + _PA_S3 + "').to_table())",
    "pyarrow file": "import pyarrow.parquet as pq\neach(pq.ParquetFile.__init__, lambda f: f(object.__new__(pq.ParquetFile), '" + _PA_S3 + "'))",
    "pyarrow fs": "import pyarrow.fs as fs\neach(fs.FileSystem.from_uri, lambda f: f('" + _PA_S3 + "')[0].get_file_info('bucket/x.parquet'))",
    "pyarrow local fs": ("import pyarrow.fs as fs\nlocal = fs.LocalFileSystem()\n"
                         "each(type(local).open_input_stream, lambda f: f(local, '/etc/hostname').read())"),
    "delta table": ("import deltalake\n"
                    "each(deltalake.DeltaTable.__init__, lambda f: f(object.__new__(deltalake.DeltaTable), 's3://b/t', storage_options=" + _S3 + "))"),
    "delta write": ("import deltalake, pyarrow as pa\n"
                    "each(deltalake.write_deltalake, lambda f: f('s3://b/t', pa.table({{'a': [1]}}), storage_options=" + _S3 + "))"),
    "query builder": ("import deltalake\nqb = deltalake.QueryBuilder()\n"
                      "each(type(qb._query_builder).execute, lambda f: f(qb._query_builder, \"CREATE EXTERNAL TABLE t STORED AS PARQUET LOCATION 's3://b/x'\"))\n"
                      "each(deltalake.QueryBuilder.__init__, lambda f: f(qb, {{'datafusion.execution.batch_size': '8'}}))"),
}


@pytest.mark.parametrize("route", list(_ROUTES))
def test_a_learner_reader_holds_no_library_function(tmp_path: Path, listener: _Listener, route: str) -> None:
    """Whatever a wrapper keeps (__wrapped__, a closure cell, a default, a global) gives no library
    reader or writer that skips the checks; nothing connects to the listener."""
    pytest.importorskip("deltalake")
    url = f"http://127.0.0.1:{listener.port}"
    body = _ROUTES[route].format(url=url, port=listener.port)
    res = _run(tmp_path, _HELD + body + "\nprint('tried', tried)\n")
    assert res["status"] == "ok", res["stderr"]
    assert res["stdout"].strip().startswith("tried"), res["stdout"]
    assert listener.connections == 0


_WALK = '''
import sys, types, functools

def wrappers():
    import polars as pl, pandas as pd, pyarrow as pa, pyarrow.parquet as pq, pyarrow.dataset as pds, deltalake
    import pyarrow.fs as pfs
    out = [getattr(m, n) for m in (pl, pd, pq, pds, pa) for n in dir(m)
           if n.startswith(("read_", "scan_", "write_", "dataset", "OSFile", "memory_map", "create_memory_map",
                            "input_stream", "output_stream")) and callable(getattr(m, n, None))]
    for cls in (pl.DataFrame, pl.LazyFrame, pd.DataFrame):
        out += [getattr(cls, n) for n in dir(cls) if n.startswith(("write_", "sink_", "to_parquet", "to_feather", "to_orc"))]
    out += [deltalake.write_deltalake, deltalake.DeltaTable.__init__, pq.ParquetFile.__init__,
            deltalake.QueryBuilder.__init__, deltalake.QueryBuilder.execute, pfs.FileSystem.from_uri]
    out += [getattr(pfs.LocalFileSystem, n) for n in ("open_input_stream", "open_output_stream", "get_file_info")]
    return [w for w in out if isinstance(w, types.FunctionType)]

ours = wrappers()
names = {w.__qualname__ for w in ours}
ids = {id(w) for w in ours}
LIBS = ("polars", "pandas", "pyarrow", "deltalake")
import typing
# typing.overload keeps each @overload stub (a signature with no body); those aren't the functions.
stubs = {id(f) for by_name in getattr(typing, "_overload_registry", {}).values()
         for by_line in by_name.values() for f in by_line.values()}
seen, todo, found = set(), list(ours) + list(sys.modules.values()), []
while todo:
    o = todo.pop()
    if id(o) in seen:
        continue
    seen.add(id(o))
    if isinstance(o, types.FunctionType):
        if id(o) not in ids and id(o) not in stubs and o.__qualname__ in names and str(o.__module__).startswith(LIBS):
            found.append(o.__module__ + "." + o.__qualname__)
        todo += [c.cell_contents for c in (o.__closure__ or ()) if c.cell_contents is not None]
        todo += list(o.__defaults__ or ()) + list((o.__kwdefaults__ or {}).values())
        if hasattr(o, "__wrapped__"):
            todo.append(o.__wrapped__)
        todo.append(o.__globals__)
    elif isinstance(o, types.ModuleType):
        todo += list(vars(o).values())
    elif isinstance(o, type):
        todo += list(vars(o).values())
    elif isinstance(o, (staticmethod, classmethod)):
        todo.append(o.__func__)
    elif isinstance(o, functools.partial):
        todo += [o.func, *o.args, *o.keywords.values()]
    elif isinstance(o, property):
        todo += [o.fget, o.fset, o.fdel]
    elif isinstance(o, dict) and len(o) < 5000:
        todo += list(o.values())
    elif isinstance(o, (list, tuple, set, frozenset)) and len(o) < 5000:
        todo += list(o)
print(len(ours), sorted(set(found)))
'''


def test_no_library_reader_is_reachable_from_learner_code(tmp_path: Path) -> None:
    """From the wrappers and every loaded module: no Polars, pandas, pyarrow or deltalake function the
    bootstrap wraps is reachable by its own name, apart from its wrapper."""
    pytest.importorskip("deltalake")
    res = _run(tmp_path, _WALK)
    assert res["status"] == "ok", res["stderr"]
    count, found = res["stdout"].strip().split(" ", 1)
    assert int(count) > 30
    assert found == "[]"


def test_learner_readers_and_writers_still_work_on_local_files(tmp_path: Path) -> None:
    """The reads and writes exercises use, through every library, in the exercise folder."""
    pytest.importorskip("deltalake")
    res = _run(tmp_path, '''
import polars as pl, pandas as pd, pyarrow as pa, pyarrow.parquet as pq, pyarrow.dataset as pds, pyarrow.csv as pcsv
import pyarrow.fs as pfs, deltalake
df = pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
df.write_parquet("p.parquet"); df.write_csv("p.csv"); df.write_ndjson("p.json")
df.lazy().sink_parquet("s.parquet")
print(pl.read_parquet("p.parquet").height, pl.scan_csv("p.csv").collect().height, pl.read_ndjson("p.json").height,
      pl.scan_parquet("s.parquet").select(pl.len()).collect().item())
pdf = pd.read_csv("p.csv"); pdf.to_parquet("q.parquet")
print(len(pd.read_parquet("q.parquet")), pq.read_table("q.parquet").num_rows, pds.dataset("p.parquet").count_rows(),
      pcsv.read_csv("p.csv").num_rows, pq.ParquetFile("p.parquet").metadata.num_rows)
import os
fs, path = pfs.FileSystem.from_uri(os.path.abspath("p.parquet"))
print(type(fs).__name__, fs.get_file_info(path).size > 0)
with pa.OSFile("raw.bin", "wb") as f:
    f.write(b"xyz")
print(pa.memory_map("raw.bin").read())
deltalake.write_deltalake("t", df.to_arrow())
deltalake.write_deltalake("t", df.to_arrow(), mode="append")
dt = deltalake.DeltaTable("t")
print(dt.version(), pl.read_delta("t").height)
qb = deltalake.QueryBuilder().register("t", dt)
print(qb.execute("SELECT count(*) AS n FROM t").read_all().column(0).to_pylist())
df.write_delta("u")
print(pl.scan_delta("u").collect().height)
try:
    pl.read_parquet("nope.parquet")
except Exception as exc:
    print(type(exc).__name__, exc.__traceback__ is not None)
''')
    assert res["status"] == "ok", res["stderr"]
    lines = res["stdout"].splitlines()
    assert lines[0] == "3 3 3 3"
    assert lines[1] == "3 3 3 3 3"
    assert lines[2] == "LocalFileSystem True"
    assert lines[3] == "b'xyz'"
    assert lines[4] == "1 6"
    assert lines[5] == "[6]"
    assert lines[6] == "3"
    assert lines[7].endswith("True"), lines[7]   # the learner's own error keeps its traceback


def test_code_a_reader_calls_back_finds_no_other_library_function(tmp_path: Path) -> None:
    """A converter pandas calls during a read sees the frames above it; none holds the others."""
    pytest.importorskip("deltalake")
    res = _run(tmp_path, '''
import sys, pandas as pd
open("a.csv", "w").write("x\\n1\\n2\\n")
found = []
def spy(value):
    frame = sys._getframe()
    while frame is not None:
        for v in list(frame.f_locals.values()):
            if isinstance(v, tuple) and sum(callable(x) and str(getattr(x, "__module__", "")).startswith(
                    ("polars", "pandas", "pyarrow", "deltalake")) for x in v) > 3:
                found.append(frame.f_code.co_name)
        frame = frame.f_back
    return int(value)
print(pd.read_csv("a.csv", converters={"x": spy})["x"].sum(), found)
''')
    assert res["status"] == "ok", res["stderr"]
    assert res["stdout"].strip() == "3 []"
