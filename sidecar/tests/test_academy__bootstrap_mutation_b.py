"""academy/_bootstrap.py from a mutation run, in this process: the file's functions are loaded on their own (the
file runs ``_main`` when executed whole), and what they would change for real is stood in for. The audit hook is
called directly and never added; the import guard goes on a stand-in ``sys``; the data libraries, DuckDB and
Windows (ctypes.WinDLL) are fakes. No learner process or sandbox is started."""

from __future__ import annotations

import ast
import ctypes
import importlib
import io
import json
import os
import sys
import types
from types import SimpleNamespace

import pytest

from fabric_desktop.academy import sandbox

BLOCKED = frozenset({"fabric_desktop", "msal"})
N = os.path.normcase   # _main hands the hook its folders in Windows' case: lower, with backslashes


def load() -> dict:
    """The bootstrap's top-level code without the closing ``try: _main()``."""
    tree = ast.parse(sandbox.BOOTSTRAP.read_text(encoding="utf-8"))
    body = [n for n in tree.body if not isinstance(n, ast.Try)]
    scope: dict = {"__name__": "kf_bootstrap_under_test", "__builtins__": __builtins__}
    exec(compile(ast.Module(body, []), str(sandbox.BOOTSTRAP), "exec"), scope)  # noqa: S102 - our own file
    return scope


@pytest.fixture
def b():
    return load()


class Exit(Exception):
    """os._exit, stood in for so the test process goes on."""


@pytest.fixture
def no_exit(monkeypatch):
    def _exit(rc):
        raise Exit(rc)
    monkeypatch.setattr(os, "_exit", _exit)


# --------------------------------------------------------------------------- _resolver

def test_outside_an_appcontainer_a_path_is_made_absolute_then_its_links_followed(b, tmp_path, monkeypatch):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    monkeypatch.chdir(tmp_path)
    resolve = b["_resolver"](False)
    assert resolve("link/x.txt") == str(tmp_path / "real" / "x.txt")
    assert resolve("a/../b") == str(tmp_path / "b")


def test_in_an_appcontainer_a_path_is_made_absolute_without_following_links(b, tmp_path):
    work = tmp_path / "w"
    (work / "d").mkdir(parents=True)
    (work / "real").mkdir()
    resolve = b["_resolver"](True, str(work))
    assert resolve(str(work / "d" / ".." / "real" / "x.txt")) == str(work / "real" / "x.txt")
    assert resolve(str(work)) == str(work)
    assert resolve(str(tmp_path / "w2" / "x")) == str(tmp_path / "w2" / "x")   # beside the workspace: not checked
    assert resolve(str(work / "new" / "deeper" / "f")) == str(work / "new" / "deeper" / "f")


def _lstat_with(monkeypatch, bad: dict):
    """os.lstat, answering for the paths in ``bad``: an error to raise, or file attributes to report."""
    real = os.lstat

    def lstat(p):
        if p in bad:
            if isinstance(bad[p], BaseException):
                raise bad[p]
            return SimpleNamespace(st_file_attributes=bad[p])
        return real(p)
    monkeypatch.setattr(os, "lstat", lstat)


def test_in_an_appcontainer_a_link_inside_the_workspace_is_refused(b, tmp_path, monkeypatch):
    work = tmp_path / "w"
    (work / "j").mkdir(parents=True)
    _lstat_with(monkeypatch, {str(work / "j"): 0x400})
    resolve = b["_resolver"](True, str(work))
    for target in (work / "j", work / "j" / "x.txt"):
        with pytest.raises(PermissionError, match="can't follow a link"):
            resolve(str(target))
    assert b["_resolver"](True, str(tmp_path / "w" / ""))(str(work / "ok")) == str(work / "ok")


def test_in_an_appcontainer_other_attributes_are_not_a_link(b, tmp_path, monkeypatch):
    work = tmp_path / "w"
    (work / "d").mkdir(parents=True)
    _lstat_with(monkeypatch, {str(work / "d"): 0x10 | 0x20 | 0x800})
    assert b["_resolver"](True, str(work))(str(work / "d" / "x")) == str(work / "d" / "x")


def test_in_an_appcontainer_a_folder_that_cant_be_checked_is_refused(b, tmp_path, monkeypatch):
    work = tmp_path / "w"
    (work / "d").mkdir(parents=True)
    _lstat_with(monkeypatch, {str(work / "d"): PermissionError("denied")})
    with pytest.raises(PermissionError, match="can't check") as err:
        b["_resolver"](True, str(work))(str(work / "d" / "x"))
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_in_an_appcontainer_what_doesnt_exist_yet_stops_the_check(b, tmp_path, monkeypatch):
    work = tmp_path / "w"
    work.mkdir()
    _lstat_with(monkeypatch, {str(work / "gone"): FileNotFoundError(), str(work / "f"): NotADirectoryError(),
                              str(work / "gone" / "j"): 0x400, str(work / "f" / "j"): 0x400})
    resolve = b["_resolver"](True, str(work))
    assert resolve(str(work / "gone" / "j")) == str(work / "gone" / "j")
    assert resolve(str(work / "f" / "j")) == str(work / "f" / "j")


def test_in_an_appcontainer_without_a_workspace_nothing_is_checked(b, tmp_path, monkeypatch):
    _lstat_with(monkeypatch, {str(tmp_path / "j"): 0x400})
    assert b["_resolver"](True)(str(tmp_path / "j")) == str(tmp_path / "j")
    assert b["_resolver"](True, "")(str(tmp_path / "j")) == str(tmp_path / "j")


def test_in_an_appcontainer_a_workspace_at_a_root_joins_its_parts_with_one_separator(b, monkeypatch):
    seen = []
    root = os.path.abspath(os.sep)   # "/" here, the drive's root ("D:\\") on Windows
    a, ab = os.path.join(root, "a"), os.path.join(root, "a", "b")
    monkeypatch.setattr(os, "lstat", lambda p: seen.append(p) or SimpleNamespace(st_file_attributes=0))
    assert b["_resolver"](True, root)(ab) == ab
    assert seen == [a, ab]


# --------------------------------------------------------------------------- the audit hook

def hook(b, work, reads=(), denied=(), libs=(), end_addr=0):
    added = []
    b["sys"] = SimpleNamespace(addaudithook=added.append)
    def roots(ps):   # as _main hands them over: followed, and in Windows' case
        return tuple(N(os.path.realpath(p)) for p in ps)
    b["_install"](N(os.path.realpath(work)), roots(reads), roots(denied), BLOCKED, roots(libs), os.path.realpath,
                  end_addr)
    (h,) = added
    return h


@pytest.fixture
def places(tmp_path):
    p = SimpleNamespace(app=tmp_path / "app", reads=tmp_path / "py", libs=tmp_path / "libs", other=tmp_path / "other")
    p.work = p.app / "academy" / "w"
    for d in (p.work, p.reads, p.libs, p.other):
        d.mkdir(parents=True)
    return p


def test_the_hook_refuses_an_import_of_a_blocked_package_only(b, places):
    h = hook(b, places.work)
    for name in ("fabric_desktop", "fabric_desktop.auth", "msal"):
        with pytest.raises(PermissionError, match=f"^{name} isn't available in an Academy exercise.$"):
            h("import", (name, None, None, None, None))
    assert h("import", ("msal_extensions", None, None, None, None)) is None
    assert h("import", ("json", None, None, None, None)) is None


def test_the_hook_lets_its_own_folder_be_read_and_written(b, places):
    h = hook(b, places.work, reads=[places.reads], denied=[places.app])
    for path in (places.work, places.work / "x.csv", str(places.work / "x.csv").encode()):
        for mode in ("r", "w", "a", "x", "r+", None):
            assert h("open", (path, mode, 0)) is None
    assert h("open", (None, "w", 0)) is None and h("open", (5, "w", 0)) is None


def test_the_hook_refuses_the_apps_files_and_writes_outside_its_folder(b, places):
    h = hook(b, places.work, reads=[places.reads, places.app], denied=[places.app])
    with pytest.raises(PermissionError, match="can't open files of the app or its grading"):
        h("open", (places.app / "settings.json", "r", 0))
    with pytest.raises(PermissionError, match="can't open files of the app"):
        h("open", (places.app, "r", 0))
    for mode in ("w", "a", "x", "r+", "rb+", "wb"):
        with pytest.raises(PermissionError, match="^An Academy exercise can write only in its own folder.$"):
            h("open", (places.reads / "x", mode, 0))
    for flag in (os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_APPEND, os.O_TRUNC):
        with pytest.raises(PermissionError, match="write only in its own folder"):
            h("open", (places.reads / "x", None, flag))
        with pytest.raises(PermissionError, match="write only in its own folder"):
            h("open", (places.reads / "x", "r", flag))
    assert h("open", (places.reads / "x", "r", os.O_RDONLY)) is None
    assert h("open", (places.reads / "x", None, None)) is None
    assert h("open", (places.reads, "rb", 0)) is None


def test_the_hook_reads_only_its_read_roots(b, places):
    h = hook(b, places.work, reads=[places.reads])
    with pytest.raises(PermissionError, match="^An Academy exercise can read only its own folder.$"):
        h("open", (places.other / "x", "r", 0))
    with pytest.raises(PermissionError, match="read only its own folder"):
        h("open", (str(places.reads) + "2", "r", 0))   # a sibling with the same start isn't inside
    with pytest.raises(PermissionError, match="read only its own folder"):
        hook(b, places.work)("open", (places.reads / "x", "r", 0))


def test_the_hook_lets_a_learner_library_be_read_never_written(b, places):
    h = hook(b, places.work, libs=[places.libs], denied=[places.libs])
    assert h("open", (places.libs / "lib.py", "r", 0)) is None
    with pytest.raises(PermissionError, match="can't open files of the app"):
        h("open", (places.libs / "lib.py", "w", 0))
    with pytest.raises(PermissionError, match="write only in its own folder"):
        hook(b, places.work, libs=[places.libs])("open", (places.libs / "lib.py", "a", 0))


def test_the_hook_compares_the_followed_path(b, places):
    (places.work / "out").symlink_to(places.other)
    h = hook(b, places.work)
    with pytest.raises(PermissionError, match="write only in its own folder"):
        h("open", (places.work / "out" / "x", "w", 0))
    (places.other / "in").symlink_to(places.work)
    assert h("open", (places.other / "in" / "x", "w", 0)) is None


def test_the_hook_refuses_network_processes_and_native_calls_by_prefix_or_name(b, places):
    h = hook(b, places.work)
    for event in ("socket.connect", "subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
                  "os.fork", "os.kill", "ctypes.dlopen", "winreg.OpenKey", "_winapi.CreateProcess", "urllib.Request",
                  "http.client.connect", "smtplib.connect", "ssl.wrap_socket", "_ssl.x", "webbrowser.open",
                  "gc.get_objects", "sys._current_frames", "os.symlink", "os.link", "pty.spawn", "os.add_dll_directory",
                  "msvcrt.x", "signal.pthread_kill", "os.startfile", "os.killpg", "os.forkpty", "ftplib.connect",
                  "gc.get_referrers", "gc.get_referents", "poplib.x", "imaplib.x", "nntplib.x", "telnetlib.x"):
        with pytest.raises(PermissionError, match=rf"^An Academy exercise can't do that \({event}\).$"):
            h(event, ())
    for event in ("os.systemx", "socketx.y", "gc.collect", "os.getcwd", "compile", "exec"):
        assert h(event, ()) is None


def test_the_hook_lets_kf_end_now_end_this_process_only(b, places):
    h = hook(b, places.work, end_addr=4242)
    assert h("ctypes.call_function", (4242, (-1, 3))) is None
    for args in ((4243, (-1, 3)), (4242, (-2, 3)), (4242, [-1, 3]), (4242, (-1, 3, 0)), (4242, (-1,)),
                 (4242, (True, 3)), (4242, (-1, True)), (4242, (-1.0, 3)), (4242, (-1, "3"))):
        with pytest.raises(PermissionError, match="can't do that"):
            h("ctypes.call_function", args)
    with pytest.raises(PermissionError, match="can't do that"):
        hook(b, places.work)("ctypes.call_function", (0, (-1, 3)))


def test_the_hook_lets_files_change_only_in_its_folder(b, places):
    h = hook(b, places.work)
    for event in ("os.remove", "os.rename", "os.mkdir", "shutil.rmtree", "shutil.copyfile", "os.chdir", "os.putenv",
                  "os.unsetenv", "os.chmod", "os.utime", "os.truncate", "os.replace", "os.rmdir", "os.chown",
                  "os.chflags", "os.mkfifo", "os.mknod", "os.removexattr", "os.setxattr"):
        assert h(event, (str(places.work / "a"), 7, None)) is None
        with pytest.raises(PermissionError, match="^An Academy exercise can change files only in its own folder.$"):
            h(event, (str(places.work / "a"), places.other / "b"))
        with pytest.raises(PermissionError, match="change files only in its own folder"):
            h(event, (str(places.other / "b").encode(),))
    assert h("os.removex", (str(places.other),)) is None


def test_the_hook_keeps_listings_out_of_the_apps_folders_but_not_its_own(b, places):
    h = hook(b, places.work, denied=[places.app])
    for event in ("os.listdir", "os.scandir", "os.walk", "glob.glob", "os.getxattr", "os.listxattr",
                  "pathlib.Path.glob", "pathlib.Path.rglob"):
        with pytest.raises(PermissionError, match="^An Academy exercise can't look in the app's folders.$"):
            h(event, (None, str(places.app)))
        assert h(event, (str(places.work),)) is None
        assert h(event, (str(places.other), 3)) is None


# --------------------------------------------------------------------------- the import guard and kf_end_now

def test_the_import_guard_drops_blocked_modules_and_refuses_them_from_then_on(b):
    first = object()
    fake = SimpleNamespace(modules={"fabric_desktop": 1, "fabric_desktop.auth": 2, "msal": 3, "msalx": 4, "json": 5},
                           meta_path=[first])
    b["sys"] = fake
    b["_guard_imports"](BLOCKED)
    assert set(fake.modules) == {"msalx", "json"}
    guard = fake.meta_path[0]
    assert fake.meta_path[1] is first and len(fake.meta_path) == 2
    with pytest.raises(ImportError, match=r"^fabric_desktop\.x isn't available in an Academy exercise\.$"):
        guard.find_spec("fabric_desktop.x")
    assert guard.find_spec("msalx") is None and guard.find_spec("json", None, None) is None


class Stream:
    def __init__(self, broken=False):
        self.broken, self.flushed = broken, 0

    def flush(self):
        self.flushed += 1
        if self.broken:
            raise ValueError("closed")


def test_kf_end_now_flushes_both_streams_then_exits(b, monkeypatch):
    ended = []
    monkeypatch.setattr(os, "_exit", ended.append)
    out, err = Stream(broken=True), Stream()
    b["sys"] = SimpleNamespace(platform="linux", stdout=out, stderr=err)
    end, addr = b["_ender"]()
    assert addr == 0
    end(7)
    assert ended == [7] and (out.flushed, err.flushed) == (1, 1)


def test_on_windows_kf_end_now_terminates_this_process(b, monkeypatch):
    calls, ended = [], []
    proto = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
    terminate = proto(lambda handle, rc: calls.append((handle, rc)) or 1)

    def dll(name, **kw):
        assert name == "kernel32" and kw == {}
        return SimpleNamespace(TerminateProcess=terminate)

    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    monkeypatch.setattr(os, "_exit", ended.append)
    out, err = Stream(), Stream()
    b["sys"] = SimpleNamespace(platform="win32", stdout=out, stderr=err)
    end, addr = b["_ender"]()
    assert addr == ctypes.cast(terminate, ctypes.c_void_p).value and addr
    assert terminate.argtypes == (ctypes.c_void_p, ctypes.c_uint)
    end(-1)
    assert calls == [(ctypes.c_void_p(-1).value, 0xFFFFFFFF)] and ended == [-1]
    assert (out.flushed, err.flushed) == (1, 1)


# --------------------------------------------------------------------------- small helpers

def test_frames_are_known_by_their_type_name(b):
    for name in ("DataFrame", "LazyFrame", "Table", "RecordBatch"):
        assert b["_is_frame"](type(name, (), {})())
    assert not b["_is_frame"]("DataFrame") and not b["_is_frame"](type("Frame", (), {})())


def test_a_refused_call_names_what_it_refused(b):
    with pytest.raises(PermissionError, match=r"^An Academy exercise can't use pyarrow\.fs\.x\.$"):
        b["_refused"]("pyarrow.fs.x")(1, k=2)


def test_a_guarded_class_checks_its_table_and_refuses_storage_options(b):
    made, checked = [], []

    class Table:
        def __init__(self, table_uri, version=None, **kw):
            made.append((table_uri, version, kw))

    b["_guard_init"](Table, lambda v, what: checked.append((v, what)), "a Delta table")
    Table("t", 3, without_files=True)
    Table("u", storage_options={})
    assert made == [("t", 3, {"without_files": True}), ("u", None, {"storage_options": {}})]
    assert checked == [("t", "a Delta table"), ("u", "a Delta table")]
    with pytest.raises(PermissionError, match="^An Academy exercise can't use remote storage options.$"):
        Table("v", storage_options={"account": "x"})
    assert len(made) == 2 and len(checked) == 2


# --------------------------------------------------------------------------- DuckDB and deltalake's QueryBuilder

class Con:
    def __init__(self, fail=False):
        self.ran, self.fail = [], fail

    def execute(self, sql):
        if self.fail:
            raise RuntimeError("no default connection")
        self.ran.append(sql)


SETTINGS = ["SET allowed_directories = ['/w/it''s']", "SET enable_external_access = false",
            "SET memory_limit = '1GB'", "SET lock_configuration = true"]


def _db(default):
    opened = []

    def connect(database=":memory:", *args, **kwargs):
        con = Con()
        opened.append((database, args, kwargs, con))
        return con
    return SimpleNamespace(connect=connect, default_connection=default, sql=lambda q: q, cursor=lambda: 1), opened


def test_every_duckdb_connection_is_locked_to_the_workspace(b):
    default = Con()
    db, opened = _db(lambda: default)
    checked = []

    def guard(fn, what):
        return lambda v: checked.append((v, what)) or fn(v)

    b["_lock_duckdb"](db, "/w/it's", guard)
    assert default.ran == SETTINGS
    for database in (":memory:", "", None, ":memory:named"):
        db.connect(database)
    assert checked == []
    db.connect("data.duckdb", 1, read_only=True)
    assert checked == [("data.duckdb", "a DuckDB database")]
    assert opened[-1][:3] == ("data.duckdb", (1,), {"read_only": True})
    assert all(con.ran == SETTINGS for *_, con in opened)
    db.connect()
    assert opened[-1][0] == ":memory:"
    assert db.sql("x") == "x"   # the default connection was locked, so module calls stay


def test_a_default_connection_that_is_an_attribute_is_locked_too(b):
    default = Con()
    db, _ = _db(default)
    b["_lock_duckdb"](db, "/w/it's", lambda fn, what: fn)
    assert default.ran == SETTINGS


def test_without_a_locked_default_connection_duckdbs_module_calls_are_refused(b):
    db, _ = _db(lambda: Con(fail=True))
    b["_lock_duckdb"](db, "/w", lambda fn, what: fn)
    for name in ("sql", "cursor"):
        with pytest.raises(PermissionError, match=rf"can't use duckdb\.{name}; use duckdb\.connect\(\)"):
            getattr(db, name)()
    assert not hasattr(db, "query")
    db.connect()   # connect itself still works, locked


@pytest.fixture
def delta(monkeypatch):
    """A stand-in deltalake.query with its QueryBuilder and native PyQueryBuilder, in sys.modules."""
    ran = []

    class Py:
        def __init__(self, config):
            ran.append(("config", config))

        def register(self, table_name, delta_table):
            ran.append(("register", table_name, delta_table))
            return "registered"

        def execute(self, sql):
            ran.append(("inner", sql))
            return "rows"

    class QueryBuilder:
        def __init__(self, session_config=None):
            raise AssertionError("replaced")

        def execute(self, sql):
            ran.append(("outer", sql))
            return "outer rows"

    query = types.ModuleType("deltalake.query")
    query.QueryBuilder, query.PyQueryBuilder = QueryBuilder, Py
    internal = types.ModuleType("deltalake._internal")
    internal.PyQueryBuilder = Py
    top = types.ModuleType("deltalake")
    top.PyQueryBuilder = object()   # not the native one: left alone
    for name, mod in (("deltalake.query", query), ("deltalake._internal", internal), ("deltalake", top)):
        monkeypatch.setitem(sys.modules, name, mod)
    return SimpleNamespace(query=query, internal=internal, top=top, ran=ran, Py=Py, QueryBuilder=QueryBuilder)


def test_the_query_builder_runs_one_select_only(b, delta):
    b["_lock_query_builder"]()
    qb = delta.QueryBuilder({"datafusion.execution.batch_size": 10})
    assert delta.ran == [("config", {"datafusion.execution.batch_size": 10})]
    for sql in ("SELECT 1", " with t as (select 1) select * from t; ", "select 'drop table x' -- copy\n",
                "SELECT /* insert */ 2", "SELECT 'a;b'"):
        assert qb.execute(sql) == "outer rows"
    assert [r[1] for r in delta.ran[1:]] == ["SELECT 1", " with t as (select 1) select * from t; ",
                                             "select 'drop table x' -- copy\n", "SELECT /* insert */ 2", "SELECT 'a;b'"]
    for sql in ("SELECT 1; SELECT 2", "COPY t TO 'x'", "CREATE EXTERNAL TABLE t LOCATION 'x'", "selectx 1",
                "SELECT * FROM t WHERE x IN (DELETE FROM t)", "select 1 /* a\n */ ; drop table t", "EXPLAIN ANALYZE SELECT 1",
                "SELECT 1 UNION SELECT set", "with x as (insert into t values (1)) select 1"):
        with pytest.raises(PermissionError, match=r"runs one SELECT \(or WITH \.\.\. SELECT\)"):
            qb.execute(sql)
    assert _inner_is_checked(qb, delta)


def _inner_is_checked(qb, delta):
    """The native builder is reached only through register and execute, and its execute is checked too."""
    inner = qb._query_builder
    assert not hasattr(inner, "__dict__")
    assert inner.register("t", "dt") == "registered" and delta.ran[-1] == ("register", "t", "dt")
    assert inner.execute("select 1") == "rows" and delta.ran[-1] == ("inner", "select 1")
    with pytest.raises(PermissionError, match="runs one SELECT"):
        inner.execute("drop table t")
    return True


def test_the_query_builder_takes_execution_settings_only(b, delta):
    b["_lock_query_builder"]()
    delta.QueryBuilder()
    delta.QueryBuilder({})
    with pytest.raises(PermissionError, match="^An Academy exercise can set only datafusion.execution settings.$"):
        delta.QueryBuilder({"datafusion.execution.x": 1, "datafusion.catalog.location": "s3://x"})


def test_the_native_query_builder_is_refused_where_it_is_reachable(b, delta):
    keep = delta.top.PyQueryBuilder
    b["_lock_query_builder"]()
    for mod in (delta.query, delta.internal):
        with pytest.raises(PermissionError, match="can't use deltalake's PyQueryBuilder; use deltalake.QueryBuilder"):
            mod.PyQueryBuilder({})
    assert delta.top.PyQueryBuilder is keep


def test_without_deltalakes_query_module_or_builders_nothing_is_changed(b, delta, monkeypatch):
    monkeypatch.delattr(delta.query, "PyQueryBuilder")
    b["_lock_query_builder"]()
    assert delta.internal.PyQueryBuilder is delta.Py and delta.query.QueryBuilder.execute(None, "drop") == "outer rows"
    monkeypatch.setattr(delta.query, "PyQueryBuilder", delta.Py, raising=False)
    monkeypatch.delattr(delta.query, "QueryBuilder")
    b["_lock_query_builder"]()
    assert delta.internal.PyQueryBuilder is delta.Py


# --------------------------------------------------------------------------- _main

class Fn:
    """One Windows function: argtypes and restype are set by the bootstrap; calls go to ``impl``."""

    def __init__(self, impl):
        self.impl, self.argtypes, self.restype = impl, None, None

    def __call__(self, *args):
        return self.impl(*args)


class Win:
    """kernel32's and advapi32's calls for the job and AppContainer checks."""

    def __init__(self, in_job=True, call_ok=True, app_flag=1, token_ok=True, info_ok=True, token=77):
        self.calls, self.in_job, self.call_ok = [], in_job, call_ok
        self.app_flag, self.token_ok, self.info_ok, self.token = app_flag, token_ok, info_ok, token

    def dll(self, name, use_last_error=False):
        assert use_last_error
        k = {"GetCurrentProcess": lambda: -1, "OpenJobObjectW": self.open_job, "IsProcessInJob": self.is_in_job,
             "CloseHandle": lambda h: self.calls.append(("close", getattr(h, "value", h))) or 1}
        a = {"OpenProcessToken": self.open_token, "GetTokenInformation": self.token_info}
        return SimpleNamespace(**{n: Fn(f) for n, f in {"kernel32": k, "advapi32": a}[name].items()})

    def open_job(self, access, inherit, name):
        self.calls.append(("open", access, inherit, name))
        return 99

    def is_in_job(self, process, job, ref):
        self.calls.append(("in_job", process, job))
        ref._obj.value = self.in_job
        return self.call_ok

    def open_token(self, process, access, ref):
        self.calls.append(("token", process, access))
        ref._obj.value = self.token
        return self.token_ok

    def token_info(self, token, cls, ref, size, ref_size):
        self.calls.append(("info", token.value, cls, size))
        ref._obj.value = self.app_flag
        return self.info_ok


@pytest.fixture
def main_env(b, tmp_path, monkeypatch, no_exit):
    """_main with its steps recorded and a stand-in sys; returns (run, record)."""
    work = tmp_path / "w"
    work.mkdir()
    (work / "s.py").write_text("RAN.append((__name__, __file__, kf_end_now))\n", encoding="utf-8")
    (tmp_path / "libs").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "libs")
    rec = SimpleNamespace(steps=[], ran=[], stderr=io.StringIO())
    end = object()
    b["_ender"] = lambda: (rec.steps.append("ender"), (end, 55))[1]
    b["_guard_imports"] = lambda blocked: rec.steps.append(("guard", blocked))
    b["_wrap_native"] = lambda w, real: rec.steps.append(("wrap", w, real))
    b["_install"] = lambda *a: rec.steps.append(("install", *a))
    b["__builtins__"] = dict(vars(__builtins__) if isinstance(__builtins__, types.ModuleType) else __builtins__,
                             RAN=rec.ran)
    rec.end, rec.work, rec.tmp = end, work, tmp_path
    monkeypatch.chdir(tmp_path)
    win = Win()
    monkeypatch.setattr(ctypes, "WinDLL", win.dll, raising=False)
    rec.win = win

    def run(platform="linux", **cfg):
        conf = {"workspace": str(work), "script": "s.py", "read_roots": [str(tmp_path / "link")],
                "denied_roots": [str(tmp_path)], "blocked_modules": ["msal"],
                "lib_roots": [str(tmp_path / "link")]} | cfg
        fake = SimpleNamespace(argv=["-c", json.dumps(conf)], platform=platform, path=["first"], stderr=rec.stderr)
        b["sys"] = fake
        rec.sys = fake
        b["_main"]()
    return run, rec


def test_main_runs_the_steps_in_order_then_the_learners_script(main_env, b):
    run, rec = main_env
    run()
    libs = str(rec.tmp / "libs")
    assert rec.steps[0] == "ender" and rec.steps[1] == ("guard", frozenset({"msal"}))
    assert rec.steps[2] == ("wrap", str(rec.work), os.path.realpath)
    assert rec.steps[3] == ("install", N(str(rec.work)), (N(libs),), (N(str(rec.tmp)),), frozenset({"msal"}),
                            (N(libs),), os.path.realpath, 55)
    script = os.path.join(str(rec.work), "s.py")
    assert rec.ran == [("__main__", script, rec.end)]
    assert rec.sys.path == [libs, "first"] and rec.sys.argv == [script]
    assert os.getcwd() == str(rec.work)


def test_main_without_learner_libraries_adds_nothing_to_the_path(main_env):
    run, rec = main_env
    run(lib_roots=[])
    assert rec.sys.path == ["first"] and rec.steps[3][5] == ()


def test_main_on_windows_refuses_to_run_outside_its_named_job(main_env):
    run, rec = main_env
    rec.win.in_job = False
    with pytest.raises(Exit, match="3"):
        run("win32", job_name="Local\\job")
    assert rec.stderr.getvalue() == "academy: the learner process isn't in its job; nothing was run.\n"
    assert rec.win.calls == [("open", 0x0004, False, "Local\\job"), ("in_job", -1, 99)] and rec.ran == []


@pytest.mark.parametrize("cfg", [{}, {"job_name": ""}, {"job_name": 5}, {"job_handle": 0, "job_name": ""},
                                 {"appcontainer": True, "job_name": "Local\\job"},
                                 {"appcontainer": True, "job_handle": -4}, {"appcontainer": True, "job_handle": True},
                                 {"appcontainer": True, "job_handle": "12"}])
def test_main_on_windows_without_a_job_to_ask_about_runs_nothing(main_env, cfg):
    run, rec = main_env
    with pytest.raises(Exit, match="3"):
        run("win32", **cfg)
    assert "isn't in its job" in rec.stderr.getvalue() and rec.ran == []
    assert not any(c[0] in ("in_job", "open") for c in rec.win.calls)


def test_main_on_windows_a_failed_job_question_runs_nothing(main_env):
    run, rec = main_env
    rec.win.call_ok = False
    with pytest.raises(Exit, match="3"):
        run("win32", job_name="j")
    assert rec.ran == []


def test_main_on_windows_in_its_job_closes_the_handle_and_runs(main_env):
    run, rec = main_env
    run("win32", job_name="j")
    assert rec.win.calls == [("open", 4, False, "j"), ("in_job", -1, 99), ("close", 99)]
    assert len(rec.ran) == 1 and rec.steps[3][6] is not os.path.realpath   # Windows' resolver, not realpath


def test_main_on_windows_a_shared_handle_is_used_as_the_job(main_env):
    run, rec = main_env
    run("win32", job_handle=12, appcontainer=True, job_name="j")
    assert rec.win.calls[:3] == [("in_job", -1, 12), ("close", 12), ("token", -1, 0x0008)]
    assert rec.win.calls[3:] == [("info", 77, 29, 4), ("close", 77)] and len(rec.ran) == 1


def test_main_on_windows_can_be_told_no_job_is_needed(main_env):
    run, rec = main_env
    run("win32", require_job=False)
    assert rec.win.calls == [] and len(rec.ran) == 1


@pytest.mark.parametrize("kw", [{"app_flag": 0}, {"token_ok": False}, {"info_ok": False}])
def test_main_in_an_appcontainer_checks_its_token_is_one(main_env, kw):
    run, rec = main_env
    for k, v in kw.items():
        setattr(rec.win, k, v)
    with pytest.raises(Exit, match="3"):
        run("win32", job_handle=12, appcontainer=True)
    assert rec.stderr.getvalue() == "academy: the learner process isn't in its AppContainer; nothing was run.\n"
    assert rec.ran == [] and ("close", 77) in rec.win.calls
    if not kw.get("token_ok", True):
        assert not any(c[0] == "info" for c in rec.win.calls)


def test_main_in_an_appcontainer_without_a_token_closes_nothing_more(main_env):
    run, rec = main_env
    rec.win.token, rec.win.token_ok = 0, False
    with pytest.raises(Exit):
        run("win32", job_handle=12, appcontainer=True)
    assert rec.win.calls == [("in_job", -1, 12), ("close", 12), ("token", -1, 8)]


def test_main_on_windows_uses_the_appcontainer_resolver_for_its_paths(main_env, monkeypatch):
    run, rec = main_env
    seen = []
    real = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p: seen.append(p) or real(p))
    run("win32", job_handle=12, appcontainer=True, read_roots=[str(rec.work / "r")])
    assert rec.steps[3][2] == (N(str(rec.work / "r")),) and str(rec.work / "r") in seen


def test_main_follows_a_linked_workspace_and_needs_no_library_list(main_env, b):
    run, rec = main_env
    (rec.tmp / "wl").symlink_to(rec.work)
    cfg = {"workspace": str(rec.tmp / "wl"), "script": "s.py", "read_roots": [], "denied_roots": [], "blocked_modules": []}
    b["sys"] = rec.sys = SimpleNamespace(argv=["-c", json.dumps(cfg)], platform="linux", path=[], stderr=rec.stderr)
    b["_main"]()
    assert rec.steps[2][1] == str(rec.work) and rec.steps[3][1:6] == (N(str(rec.work)), (), (), frozenset(), ())
    assert rec.sys.path == []


# --------------------------------------------------------------------------- the native readers and writers

def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _rec(log, name):
    def fn(*args, **kwargs):
        log.append((name, args, kwargs))
        return name
    fn.__name__ = name
    return fn


@pytest.fixture
def native(b, tmp_path, monkeypatch):
    """Stand-in Polars, deltalake, pyarrow, pandas and DuckDB, wrapped by _wrap_native over a stand-in sys."""
    log = []
    work = tmp_path / "w"
    work.mkdir()

    class DataFrame:
        write_csv = _rec(log, "write_csv")
        to_parquet = _rec(log, "to_parquet")
        head = _rec(log, "head")

    class LazyFrame:
        sink_parquet = _rec(log, "sink_parquet")

    pl = _mod("polars", read_csv=_rec(log, "read_csv"), scan_parquet=_rec(log, "scan_parquet"), col=_rec(log, "col"),
              read_Thing=type("read_Thing", (), {}), DataFrame=DataFrame, LazyFrame=LazyFrame)
    pl_sub = _mod("polars.io.csv.functions", read_csv=pl.read_csv, other=pl.col, Kind=type("Kind", (), {}))

    class DeltaTable:
        def __init__(self, table_uri, version=None):
            log.append(("DeltaTable", table_uri))

    dl = _mod("deltalake", write_deltalake=_rec(log, "write_deltalake"), DeltaTable=DeltaTable)

    class ParquetFile:
        def __init__(self, source, **kw):
            log.append(("ParquetFile", source))

    pq = _mod("pyarrow.parquet", read_table=_rec(log, "read_table"), write_table=_rec(log, "write_table"),
              ParquetFile=ParquetFile, ParquetDataset=bytearray)
    ds = _mod("pyarrow.dataset", dataset=_rec(log, "dataset"), field=_rec(log, "field"))

    class FileSystem:
        @staticmethod
        def from_uri(uri):
            log.append(("from_uri", uri))
            return (LocalFileSystem() if "local" in uri else "remote-fs"), uri

    class LocalFileSystem(FileSystem):
        def get_file_info(self, paths):
            log.append(("get_file_info", paths))
            return "info"

        def move(self, src, dest):
            log.append(("move", src, dest))

        def open_input_stream(self, path):
            log.append(("open_input_stream", path))
            return "stream"

    class S3FileSystem(FileSystem):
        pass

    fs = _mod("pyarrow.fs", LocalFileSystem=LocalFileSystem, FileSystem=FileSystem, S3FileSystem=S3FileSystem,
              initialize_s3=_rec(log, "initialize_s3"), copy_files=_rec(log, "copy_files"))
    pa_fs_inner = _mod("pyarrow._fs", LocalFileSystem=LocalFileSystem, FileSystem=FileSystem, other=1)
    os_file = _rec(log, "OSFile")
    pa = _mod("pyarrow", OSFile=os_file, memory_map=_rec(log, "memory_map"))
    pa_lib = _mod("pyarrow.lib", OSFile=os_file, memory_map=object())

    class PdFrame:
        to_parquet = _rec(log, "pd_to_parquet")
    PdFrame.__name__ = "DataFrame"
    pd = _mod("pandas", read_csv=_rec(log, "pd_read_csv"), read_sql=_rec(log, "read_sql"), DataFrame=PdFrame)

    default = Con()
    db = SimpleNamespace(connect=lambda database=":memory:", *a, **k: Con(), default_connection=lambda: default)

    mods = {"polars": pl, "deltalake": dl, "pyarrow.parquet": pq, "pyarrow.dataset": ds, "pyarrow.fs": fs,
            "pyarrow": pa, "pandas": pd, "duckdb": db}

    def import_module(name):
        if name in mods:
            return mods[name]
        raise ImportError(name)

    modules = dict(mods, **{"polars.io.csv.functions": pl_sub, "pyarrow._fs": pa_fs_inner, "pyarrow.lib": pa_lib,
                            "polarsx": _mod("polarsx", read_csv=pl.read_csv), "pandas.gone": None,
                            "pyarrowx": _mod("pyarrowx", LocalFileSystem=LocalFileSystem),
                            "fsspec.like": _mod("fsspec.like", FileSystem=FileSystem)})
    monkeypatch.setattr(importlib, "import_module", import_module)
    monkeypatch.delitem(sys.modules, "deltalake.query", raising=False)
    b["sys"] = SimpleNamespace(modules=modules)
    b["_wrap_native"](str(work))
    monkeypatch.undo()
    return SimpleNamespace(log=log, work=work, out=tmp_path / "out", pl=pl, pl_sub=pl_sub, dl=dl, pq=pq, ds=ds, fs=fs,
                           pa_fs_inner=pa_fs_inner, pa=pa, pa_lib=pa_lib, pd=pd, db=db, default=default,
                           modules=modules, originals=SimpleNamespace(read_csv=os_file and mods["polars"].read_csv,
                                                                       LocalFileSystem=LocalFileSystem,
                                                                       FileSystem=FileSystem, os_file=os_file))


OUTSIDE = r"can use only files in its own folder \({}\)"


def test_polars_reads_are_checked_and_other_functions_left_alone(native):
    n = native
    assert n.pl.read_csv("a.csv") == "read_csv" and n.pl.scan_parquet(str(n.work / "b")) == "scan_parquet"
    assert n.pl.read_csv(n.work) == "read_csv" and n.pl.read_csv(None) == "read_csv"
    for bad in (str(n.out / "x.csv"), "../x.csv", str(n.work) + "2/x", [str(n.work / "a"), str(n.out)],
                (str(n.out),), str(n.out).encode(), n.out):
        with pytest.raises(PermissionError, match=OUTSIDE.format("a Polars read")):
            n.pl.read_csv(bad)
    assert n.pl.read_csv([str(n.work / "a"), b"b"]) == "read_csv" and n.pl.read_csv(io.BytesIO(b"")) == "read_csv"
    assert n.pl.col("../../x") == "col" and n.pl.read_csv.__name__ == "read_csv"
    assert isinstance(n.pl.read_Thing, type) and "wrapper" not in repr(n.pl.read_Thing)


@pytest.mark.parametrize("url", ["s3://b/x", "abfss://c@a.dfs.core.windows.net/x", "\\\\server\\share\\x", "//server/x"])
def test_a_url_or_share_is_refused(native, url):
    with pytest.raises(PermissionError, match=r"^An Academy exercise works on files in its own folder, not a Polars read at a URL\.$"):
        native.pl.read_csv(url)


def test_storage_options_and_file_systems_are_refused_and_named_arguments_checked(native):
    n = native
    for kw in ({"storage_options": {"k": 1}}, {"filesystem": object()}):
        with pytest.raises(PermissionError, match="^An Academy exercise can't use remote storage options.$"):
            n.pl.read_csv("a.csv", **kw)
    assert n.pl.read_csv("a.csv", storage_options={}, filesystem=None) == "read_csv"
    for name in ("source", "file", "path", "target", "table_or_uri", "table_uri", "where", "database", "base_dir",
                 "path_or_paths", "uri"):
        with pytest.raises(PermissionError, match="own folder"):
            n.pl.read_csv(**{name: str(n.out)})
        assert n.pl.read_csv(**{name: "in.csv"}) == "read_csv"
    assert n.pl.read_csv(other=str(n.out)) == "read_csv"


def test_frame_writes_check_the_path_after_the_frame(native):
    n = native
    df = n.pl.DataFrame()
    assert df.write_csv("o.csv") == "write_csv" and n.log[-1][1][1] == "o.csv"
    with pytest.raises(PermissionError, match=OUTSIDE.format("a Polars write")):
        df.write_csv(str(n.out / "o.csv"))
    with pytest.raises(PermissionError, match="a Polars write"):
        n.pl.LazyFrame().sink_parquet(str(n.out))
    assert df.write_csv() == "write_csv" and df.head("../../x") == "head"
    assert n.pl.DataFrame.to_parquet(df, str(n.out)) == "to_parquet"   # not a Polars write name: left alone


def test_the_same_function_reached_through_a_submodule_is_wrapped_too(native):
    n = native
    assert n.pl_sub.read_csv is n.pl.read_csv
    with pytest.raises(PermissionError, match="a Polars read"):
        n.pl_sub.read_csv(str(n.out))
    assert n.pl_sub.other("../x") == "col" and isinstance(n.pl_sub.Kind, type)
    assert n.modules["polarsx"].read_csv is not n.pl.read_csv   # another package: not rewired


def test_delta_writes_and_tables_are_checked(native):
    n = native
    with pytest.raises(PermissionError, match=OUTSIDE.format("a Delta write")):
        n.dl.write_deltalake(str(n.out), None)
    n.dl.DeltaTable("t")
    with pytest.raises(PermissionError, match=OUTSIDE.format("a Delta table")):
        n.dl.DeltaTable(str(n.out))
    assert ("DeltaTable", "t") in n.log


def test_pyarrow_files_are_checked(native):
    n = native
    for fn in (n.pq.read_table, n.pq.write_table, n.ds.dataset):
        with pytest.raises(PermissionError, match=OUTSIDE.format("a pyarrow file")):
            fn(str(n.out))
    assert n.ds.field("../x") == "field"
    assert n.pq.write_table(type("Table", (), {})(), "o.parquet") == "write_table"
    with pytest.raises(PermissionError, match="a pyarrow file"):
        n.pq.write_table(type("Table", (), {})(), str(n.out))
    n.pq.ParquetFile("a.parquet")
    with pytest.raises(PermissionError, match="a pyarrow file"):
        n.pq.ParquetFile(str(n.out))
    with pytest.raises(PermissionError, match="a pyarrow file"):
        n.pq.ParquetDataset(str(n.out))
    assert n.pq.ParquetDataset(b"ab") == bytearray(b"ab")


def test_pyarrows_local_file_system_checks_every_path(native):
    n = native
    local = n.fs.LocalFileSystem()
    assert isinstance(local, n.originals.LocalFileSystem) and n.fs.LocalFileSystem is not n.originals.LocalFileSystem
    assert local.get_file_info(["a", "b"]) == "info" and local.open_input_stream("a") == "stream"
    local.move("a", "b")
    for call in (lambda: local.get_file_info([str(n.out)]), lambda: local.get_file_info(str(n.out)),
                 lambda: local.move("a", str(n.out)), lambda: local.move(str(n.out), "a"),
                 lambda: local.open_input_stream(str(n.out)),
                 lambda: local.get_file_info(SimpleNamespace(base_dir=str(n.out))),
                 lambda: local.get_file_info([SimpleNamespace(base_dir=str(n.out))])):
        with pytest.raises(PermissionError, match="a pyarrow file"):
            call()
    assert local.get_file_info(SimpleNamespace(base_dir="in")) == "info"
    assert local.open_input_stream.__name__ == "open_input_stream" and not hasattr(local, "delete_file")
    with pytest.raises(PermissionError, match="can't use pyarrow.fs delete_root_dir_contents"):
        local.delete_root_dir_contents()


def test_pyarrows_file_system_from_uri_is_checked_and_its_local_one_too(native):
    n = native
    fs, path = n.fs.FileSystem.from_uri("local-x")
    assert isinstance(fs, n.fs.LocalFileSystem) and path == "local-x"
    assert n.fs.LocalFileSystem.from_uri("remote-y") == ("remote-fs", "remote-y")
    with pytest.raises(PermissionError, match="a pyarrow file"):
        n.fs.FileSystem.from_uri("s3://x")
    assert isinstance(n.originals.LocalFileSystem(), n.fs.FileSystem) and not isinstance(1, n.fs.FileSystem)
    assert issubclass(n.originals.LocalFileSystem, n.fs.FileSystem) and not issubclass(int, n.fs.FileSystem)
    with pytest.raises(PermissionError, match=r"can't use pyarrow\.fs\.S3FileSystem; read and write files by their names"):
        n.fs.S3FileSystem()
    for name in ("initialize_s3", "copy_files"):
        with pytest.raises(PermissionError, match=rf"can't use pyarrow\.fs\.{name}\."):
            getattr(n.fs, name)()
    assert not hasattr(n.fs, "resolve_s3_region")
    assert n.pa_fs_inner.LocalFileSystem is n.fs.LocalFileSystem and n.pa_fs_inner.FileSystem is not n.originals.FileSystem
    assert n.pa_fs_inner.FileSystem.from_uri("local-z")[1] == "local-z" and n.pa_fs_inner.other == 1
    assert n.modules["pyarrowx"].LocalFileSystem is n.originals.LocalFileSystem   # not pyarrow's: left alone
    assert n.modules["fsspec.like"].FileSystem is n.originals.FileSystem


def test_pyarrows_own_file_openers_are_checked_where_pyarrow_lib_holds_them(native):
    n = native
    for fn in (n.pa.OSFile, n.pa.memory_map, n.pa_lib.OSFile):
        with pytest.raises(PermissionError, match="a pyarrow file"):
            fn(str(n.out))
    assert n.pa_lib.OSFile is n.pa.OSFile and not callable(n.pa_lib.memory_map)
    assert n.pa.OSFile("in") == "OSFile"


def test_pandas_reads_and_writes_are_checked(native):
    n = native
    with pytest.raises(PermissionError, match=OUTSIDE.format("a pandas read")):
        n.pd.read_csv(str(n.out))
    with pytest.raises(PermissionError, match=OUTSIDE.format("a pandas write")):
        n.pd.DataFrame().to_parquet(str(n.out))
    assert n.pd.read_sql(str(n.out)) == "read_sql"


def test_duckdb_is_locked_to_the_workspace(native):
    assert native.default.ran[0] == "SET allowed_directories = ['" + str(native.work) + "']"
    with pytest.raises(PermissionError, match=OUTSIDE.format("a DuckDB database")):
        native.db.connect(str(native.out / "x.duckdb"))


def test_main_on_windows_a_shared_handle_of_1_is_a_handle(main_env):
    run, rec = main_env
    run("win32", job_handle=1, appcontainer=True)
    assert rec.win.calls[:2] == [("in_job", -1, 1), ("close", 1)] and len(rec.ran) == 1
