"""The first code in an Academy learner process (ACD-005). Not imported by the engine: sandbox.py
reads this file's text and runs it with ``python -I -c``, so the learner process never has the
engine package on its path. Everything it needs comes in one JSON argument.

It guards against accidents, it is not a security boundary (docs/academy/grading-sandbox.md): a
Python audit hook can't see what native code does, so the native readers and writers learners use
(Polars, DuckDB, delta-rs, pyarrow, pandas) are wrapped to refuse URLs and paths outside the
workspace as well. Order matters: the job check, then the import guard and audit hook, then the
wrappers, then the learner's script.
"""

import json
import os
import sys


def _resolver(appcontainer, root=None):
    """How the guards turn a path into the one Windows will open.

    ``os.path.abspath`` first: Windows itself collapses ``.`` and ``..`` in a path before following any
    link (GetFullPathNameW), so the guards do the same, and no ``..`` is ever left in what they compare.
    Then ``realpath`` follows links. Inside an AppContainer it can't: GetFinalPathNameByHandleW is
    denied there, and ``realpath`` would hand back the path as given. So in an AppContainer, a path
    inside the workspace is checked folder by folder from the workspace down, and it fails closed: a
    link (any reparse point), or a folder that can't be checked, is refused."""
    abspath, realpath, lstat, sep, norm = os.path.abspath, os.path.realpath, os.lstat, os.sep, os.path.normcase
    if not appcontainer:
        return lambda p: realpath(abspath(p))
    roots = (abspath(root),) if root else ()   # the workspace: inside an AppContainer, nothing else is writable

    def resolve(p, _roots=roots, _err=PermissionError):
        full = abspath(p)
        for top in _roots:
            f, r = norm(full), norm(top)
            if f != r and not f.startswith(r.rstrip(sep) + sep):
                continue
            cur = top
            for part in full[len(top):].split(sep):
                if not part:
                    continue
                cur = cur + sep + part if not cur.endswith(sep) else cur + part
                try:
                    st = lstat(cur)
                except (FileNotFoundError, NotADirectoryError):
                    break   # what follows doesn't exist yet: nothing there can be a link
                except OSError:
                    raise _err("An Academy exercise can't use a path it can't check.") from None
                if getattr(st, "st_file_attributes", 0) & 0x400:   # FILE_ATTRIBUTE_REPARSE_POINT
                    raise _err("An Academy exercise can't follow a link.")
        return full

    return resolve


def _main() -> None:
    cfg = json.loads(sys.argv[1])
    norm = os.path.normcase   # Windows paths compare without case
    if sys.platform == "win32":
        work = os.path.realpath(os.path.abspath(cfg["workspace"]))
        real = _resolver(bool(cfg.get("appcontainer")), work)
    else:
        real = os.path.realpath
        work = real(cfg["workspace"])
    reads = tuple(norm(real(p)) for p in cfg["read_roots"])
    denied = tuple(norm(real(p)) for p in cfg["denied_roots"])
    blocked = frozenset(cfg["blocked_modules"])
    libs = tuple(norm(real(p)) for p in cfg.get("lib_roots", []))
    sys.path[:0] = [real(p) for p in cfg.get("lib_roots", [])]   # read-only learner libraries

    if sys.platform == "win32" and cfg.get("require_job", True):
        # The engine assigns the Job Object while this process is suspended; refuse to run without it.
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.OpenJobObjectW.restype = wintypes.HANDLE
        k.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        # The engine names its job, or (in an AppContainer, whose object namespace is its own) hands
        # this process a QUERY-only handle to it. Asking about a NULL job would accept any job, such
        # as a CI runner's.
        shared, name = cfg.get("job_handle"), cfg.get("job_name")
        if isinstance(shared, int) and not isinstance(shared, bool) and shared > 0:
            job = shared
        elif cfg.get("appcontainer"):
            job = None   # an AppContainer run without its handle is refused, never looked up by name
        else:
            job = k.OpenJobObjectW(0x0004, False, name) if isinstance(name, str) and name else None   # JOB_OBJECT_QUERY
        inside_job = wintypes.BOOL(False)
        if not job or not k.IsProcessInJob(k.GetCurrentProcess(), job, ctypes.byref(inside_job)) or not inside_job.value:
            sys.stderr.write("academy: the learner process isn't in its job; nothing was run.\n")
            os._exit(3)
        k.CloseHandle(job)   # the learner script gets no handle to the job
        if cfg.get("appcontainer"):
            # The engine says "sandboxed" for this run only if this token really is an AppContainer's.
            adv = ctypes.WinDLL("advapi32", use_last_error=True)
            adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
            adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.POINTER(wintypes.DWORD)]
            token, flag, size = wintypes.HANDLE(), wintypes.DWORD(0), wintypes.DWORD(0)
            ok = adv.OpenProcessToken(k.GetCurrentProcess(), 0x0008, ctypes.byref(token))   # TOKEN_QUERY
            ok = ok and adv.GetTokenInformation(token, 29, ctypes.byref(flag), 4, ctypes.byref(size))   # TokenIsAppContainer
            if token:
                k.CloseHandle(token)
            if not ok or not flag.value:
                sys.stderr.write("academy: the learner process isn't in its AppContainer; nothing was run.\n")
                os._exit(3)

    _guard_imports(blocked)
    _wrap_native(work, real)       # imports the data libraries first: some load native code through ctypes
    _install(norm(work), reads, denied, blocked, libs, real)
    os.chdir(work)
    script = os.path.join(work, cfg["script"])
    with open(script, encoding="utf-8") as fh:
        source = fh.read()
    code = compile(source, script, "exec")
    sys.argv = [script]
    scope = {"__name__": "__main__", "__file__": script, "__builtins__": __builtins__}
    exec(code, scope)  # noqa: S102 - the learner's own exercise script, in this guarded process


def _guard_imports(blocked):
    """The engine package and sign-in libraries can't be imported, from here on."""
    import importlib.abc

    class _Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None, _blocked=blocked):
            if name.partition(".")[0] in _blocked:
                raise ImportError(f"{name} isn't available in an Academy exercise.")
            return None

    for name in list(sys.modules):
        if name.partition(".")[0] in blocked:
            del sys.modules[name]
    sys.meta_path.insert(0, _Guard())


def _install(work, reads, denied, blocked, libs=(), real=os.path.realpath):
    """The audit hook. Its state is bound as default arguments of immutable values, and the
    function is kept nowhere the learner's code can name."""
    refuse_names = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
                    "os.fork", "os.forkpty", "os.startfile", "os.kill", "os.killpg", "signal.pthread_kill",
                    "pty.", "ctypes.", "winreg.", "_winapi.", "msvcrt.", "gc.get_objects", "gc.get_referrers",
                    "gc.get_referents", "sys._current_frames", "os.link", "os.symlink", "webbrowser.", "urllib.",
                    "http.", "ftplib.", "smtplib.", "poplib.", "imaplib.", "nntplib.", "telnetlib.", "_ssl.",
                    "ssl.", "os.add_dll_directory")
    write_names = ("os.remove", "os.rename", "os.replace", "os.mkdir", "os.rmdir", "os.chmod", "os.chown",
                   "os.truncate", "os.utime", "os.chflags", "os.mkfifo", "os.mknod", "os.removexattr",
                   "os.setxattr", "shutil.", "os.chdir", "os.putenv", "os.unsetenv")
    list_names = ("os.listdir", "os.scandir", "os.walk", "glob.glob", "os.getxattr", "os.listxattr", "pathlib.Path.glob",
                  "pathlib.Path.rglob")

    def _paths(args):
        for a in args:
            if isinstance(a, (str, bytes, os.PathLike)):
                yield a

    def hook(event, args, _work=work, _libs=libs, _reads=reads, _denied=denied, _blocked=blocked, _refuse=refuse_names,
             _write=write_names, _list=list_names, _real=real, _fspath=os.fsdecode, _norm=os.path.normcase,
             _sep=os.sep, _paths=_paths, _err=PermissionError):
        def under(path, roots):
            return any(path == r or path.startswith(r.rstrip(_sep) + _sep) for r in roots)

        def where(p):
            return _norm(_real(_fspath(p)))

        if event == "import":
            if str(args[0]).partition(".")[0] in _blocked:
                raise _err(f"{args[0]} isn't available in an Academy exercise.")
            return
        if event == "open":
            path, mode, flags = args[0], args[1], args[2]
            if path is None or isinstance(path, int):
                return
            full = where(path)
            writing = (mode is not None and any(c in str(mode) for c in "wax+")) or (flags or 0) & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC)
            if under(full, (_work,)):
                return   # its own folder, which may sit inside the app's data folder
            if not writing and under(full, _libs):
                return   # a learner library the engine built and checked (sandbox.run lib_roots)
            if under(full, _denied):
                raise _err("An Academy exercise can't open files of the app or its grading.")
            if writing:
                raise _err("An Academy exercise can write only in its own folder.")
            if not under(full, _reads):
                raise _err("An Academy exercise can read only its own folder.")
            return
        if event in _refuse or any(r.endswith(".") and event.startswith(r) for r in _refuse):
            raise _err(f"An Academy exercise can't do that ({event}).")
        if event in _write or any(r.endswith(".") and event.startswith(r) for r in _write):
            for p in _paths(args):
                if not under(where(p), (_work,)):
                    raise _err("An Academy exercise can change files only in its own folder.")
            return
        if event in _list:
            for p in _paths(args):
                full = where(p)
                if under(full, _denied) and not under(full, (_work,)):
                    raise _err("An Academy exercise can't look in the app's folders.")
            return

    sys.addaudithook(hook)


def _wrap_native(work, resolve=os.path.realpath):
    """Polars, DuckDB, delta-rs, pyarrow and pandas read and write files from native code the audit
    hook never sees. Their entry points refuse a URL, ``storage_options`` and a path outside the
    workspace. Only modules that are installed are wrapped; each import is checked by the hook."""
    import functools
    import importlib

    real, sep, norm = resolve, os.sep, os.path.normcase
    as_given, work = work, norm(work)   # DuckDB compares its allowed directories with case

    def check(value, what):
        if isinstance(value, (list, tuple)):
            for v in value:
                check(v, what)
            return
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if isinstance(value, bytes):
            value = os.fsdecode(value)
        if not isinstance(value, str):
            return  # an open file, a buffer or a data object: Python's own open was checked
        if "://" in value or value.startswith(("\\\\", "//")):
            raise PermissionError(f"An Academy exercise works on files in its own folder, not {what} at a URL.")
        full = norm(real(os.path.join(work, value)))
        if full != work and not full.startswith(work.rstrip(sep) + sep):
            raise PermissionError(f"An Academy exercise can use only files in its own folder ({what}).")

    def guard(fn, what, arg_names=("source", "file", "path", "target", "table_or_uri", "table_uri", "where",
                                    "database", "base_dir", "path_or_paths", "uri")):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if kwargs.get("storage_options") or kwargs.get("filesystem"):
                raise PermissionError("An Academy exercise can't use remote storage options.")
            first = args[0] if args else None
            if first is not None and not _is_frame(first):
                check(first, what)
            elif len(args) > 1 and _is_frame(first):
                check(args[1], what)
            for name in arg_names:
                if name in kwargs:
                    check(kwargs[name], what)
            return fn(*args, **kwargs)
        return wrapper

    swapped: dict[int, object] = {}   # id(original) -> wrapped, so the same function reached through a submodule is wrapped too

    def wrap_all(module, prefixes, what):
        for name in dir(module):
            fn = getattr(module, name, None)
            if name.startswith(prefixes) and callable(fn) and not isinstance(fn, type):   # classes: _guard_init
                wrapped = swapped.get(id(fn)) or guard(fn, what)
                try:
                    setattr(module, name, wrapped)
                except (AttributeError, TypeError):
                    continue
                swapped[id(fn)] = wrapped

    def rewire(package):
        """Every module of ``package`` (polars.io.csv.functions and the like) that holds one of the
        original functions gets the wrapped one instead."""
        for modname, mod in list(sys.modules.items()):
            if mod is None or not (modname == package or modname.startswith(package + ".")):
                continue
            for name, value in list(vars(mod).items()):
                if (callable(value) and not isinstance(value, type) and id(value) in swapped
                        and value is not swapped[id(value)]):
                    try:
                        setattr(mod, name, swapped[id(value)])
                    except (AttributeError, TypeError):
                        pass

    def maybe(name):
        try:
            return importlib.import_module(name)
        except ImportError:
            return None

    pl = maybe("polars")
    if pl is not None:
        wrap_all(pl, ("read_", "scan_"), "a Polars read")
        for cls in (pl.DataFrame, pl.LazyFrame):
            wrap_all(cls, ("write_", "sink_"), "a Polars write")
    dl = maybe("deltalake")
    if dl is not None:
        dl.write_deltalake = guard(dl.write_deltalake, "a Delta write")
        _guard_init(dl.DeltaTable, check, "a Delta table")
        _lock_query_builder()
    for modname in ("pyarrow.parquet", "pyarrow.dataset", "pyarrow.csv", "pyarrow.feather", "pyarrow.orc",
                    "pyarrow.json"):
        mod = maybe(modname)
        if mod is not None:
            wrap_all(mod, ("read_", "write_", "dataset"), "a pyarrow file")
            for cls_name in ("ParquetFile", "ParquetDataset", "ParquetWriter"):
                cls = getattr(mod, cls_name, None)
                if isinstance(cls, type):
                    try:
                        _guard_init(cls, check, "a pyarrow file")
                    except (AttributeError, TypeError):
                        setattr(mod, cls_name, guard(cls, "a pyarrow file"))
    pa_fs = maybe("pyarrow.fs")
    if pa_fs is not None:
        # A file system object can't be confined by SubTreeFileSystem (it lets ".." through), so the
        # local one is a subclass whose path methods check every path first. pyarrow's own readers
        # build it by name and keep working; the paths they get were checked by their wrappers.
        # Remote and other file systems are refused.
        _local = pa_fs.LocalFileSystem

        def _checked_method(name, n_paths):
            original = getattr(_local, name)

            def method(self, *args, **kwargs):
                for p in args[:n_paths]:
                    if isinstance(p, (list, tuple)):
                        for q in p:
                            check(getattr(q, "base_dir", q), "a pyarrow file")
                    else:
                        check(getattr(p, "base_dir", p), "a pyarrow file")
                return original(self, *args, **kwargs)
            method.__name__ = name
            return method

        two = ("move", "copy_file")
        one = ("get_file_info", "create_dir", "delete_dir", "delete_dir_contents", "delete_file",
               "open_input_stream", "open_input_file", "open_output_stream", "open_append_stream", "normalize_path")
        members = {n: _checked_method(n, 2 if n in two else 1) for n in one + two if hasattr(_local, n)}
        members["delete_root_dir_contents"] = _refused("pyarrow.fs delete_root_dir_contents")
        _base = pa_fs.FileSystem

        def from_uri(uri):
            """A plain path in the workspace only; a local file system comes back checked."""
            check(uri, "a pyarrow file")
            fs, path = _base.from_uri(uri)
            return (checked_local() if isinstance(fs, _local) else fs), path

        members["from_uri"] = staticmethod(from_uri)
        checked_local = type("LocalFileSystem", (_local,), members)

        class _FileSystemMeta(type):   # isinstance and issubclass still answer for the real base class
            def __instancecheck__(cls, obj):
                return isinstance(obj, _base)

            def __subclasscheck__(cls, sub):
                return issubclass(sub, _base)

        checked_base = _FileSystemMeta("FileSystem", (), {"from_uri": staticmethod(from_uri)})
        for name in dir(pa_fs):
            if name.endswith("FileSystem") and name not in ("FileSystem", "LocalFileSystem"):
                setattr(pa_fs, name, _refused(f"pyarrow.fs.{name}; read and write files by their names"))
        # pyarrow._fs (where the classes live) and every pyarrow module that imported them by name
        # get the checked ones; pyarrow's native code holds its own references and isn't affected.
        types = {id(_local): checked_local, id(_base): checked_base}
        for modname, mod in list(sys.modules.items()):
            if mod is not None and (modname == "pyarrow" or modname.startswith("pyarrow.")):
                for name, value in list(vars(mod).items()):
                    if id(value) in types and isinstance(value, type):
                        try:
                            setattr(mod, name, types[id(value)])
                        except (AttributeError, TypeError):
                            pass
        for name in ("resolve_s3_region", "initialize_s3", "copy_files"):
            if hasattr(pa_fs, name):
                setattr(pa_fs, name, _refused(f"pyarrow.fs.{name}"))
    pa = maybe("pyarrow")
    if pa is not None:
        lib = sys.modules.get("pyarrow.lib")
        for name in ("OSFile", "memory_map", "create_memory_map", "input_stream", "output_stream"):
            fn = getattr(pa, name, None)
            if fn is not None:
                wrapped = guard(fn, "a pyarrow file")
                setattr(pa, name, wrapped)
                if lib is not None and getattr(lib, name, None) is fn:
                    setattr(lib, name, wrapped)   # pyarrow.lib.OSFile is a class, so rewire skips it
                swapped[id(fn)] = wrapped
    pd = maybe("pandas")
    if pd is not None:
        wrap_all(pd, ("read_parquet", "read_feather", "read_orc", "read_csv", "read_json", "read_excel"), "a pandas read")
        wrap_all(pd.DataFrame, ("to_parquet", "to_feather", "to_orc"), "a pandas write")
    db = maybe("duckdb")
    if db is not None:
        _lock_duckdb(db, as_given, guard)
    for package in ("polars", "pyarrow", "pandas", "deltalake"):
        rewire(package)


def _is_frame(value):
    return type(value).__name__ in ("DataFrame", "LazyFrame", "Table", "RecordBatch")


def _refused(name):
    def refuse(*_a, **_k):
        raise PermissionError(f"An Academy exercise can't use {name}.")
    return refuse


def _guard_init(cls, check, what):
    original = cls.__init__

    def __init__(self, table_uri, *args, **kwargs):
        if kwargs.get("storage_options"):
            raise PermissionError("An Academy exercise can't use remote storage options.")
        check(table_uri, what)
        original(self, table_uri, *args, **kwargs)

    cls.__init__ = __init__


def _lock_query_builder():
    """deltalake's QueryBuilder runs DataFusion SQL, which reads and writes files from native code
    (CREATE EXTERNAL TABLE ... LOCATION, COPY ... TO). It runs one SELECT or WITH statement over the
    Delta tables registered with it, and its session settings are limited to execution ones."""
    import re
    import sys as _sys
    query = _sys.modules.get("deltalake.query")
    qb = getattr(query, "QueryBuilder", None)
    real = getattr(query, "PyQueryBuilder", None)
    if qb is None or real is None:
        return
    comments = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
    strings = re.compile(r"'(?:[^']|'')*'")
    banned = re.compile(r"\b(copy|create|location|insert|delete|update|drop|alter|set|external|merge|load|install|"
                        r"attach|prepare|execute|explain\s+analyze)\b", re.I)

    def allowed(sql):
        text = comments.sub(" ", str(sql))
        bare = strings.sub("''", text).strip().rstrip(";").strip()
        if ";" in bare or not re.match(r"(?is)\s*(select|with)\b", bare) or banned.search(bare):
            raise PermissionError("An Academy exercise runs one SELECT (or WITH ... SELECT) over the tables "
                                  "registered with the QueryBuilder.")
        return sql

    def builder(session_config):
        """The native builder stays in this closure; the instance holds only what the class calls."""
        inner = real(session_config)

        class _Builder:
            __slots__ = ()

            def register(self, table_name, delta_table):
                return inner.register(table_name=table_name, delta_table=delta_table)

            def execute(self, sql):
                return inner.execute(allowed(sql))

        return _Builder()

    def __init__(self, session_config=None):
        for key in (session_config or {}):
            if not str(key).startswith("datafusion.execution."):
                raise PermissionError("An Academy exercise can set only datafusion.execution settings.")
        self._query_builder = builder(session_config)

    original = qb.execute

    def execute(self, sql):
        return original(self, allowed(sql))

    qb.__init__, qb.execute = __init__, execute
    refuse = _refused("deltalake's PyQueryBuilder; use deltalake.QueryBuilder")
    for modname in ("deltalake.query", "deltalake._internal", "deltalake"):
        mod = _sys.modules.get(modname)
        if mod is not None and getattr(mod, "PyQueryBuilder", None) is real:
            setattr(mod, "PyQueryBuilder", refuse)  # noqa: B010 - replaces deltalake's own


def _lock_duckdb(db, work, guard):
    """Every DuckDB connection, the module's default one included, reads only the workspace."""
    settings = ("SET allowed_directories = ['" + work.replace("'", "''") + "']",
                "SET enable_external_access = false", "SET memory_limit = '1GB'", "SET lock_configuration = true")
    connect = db.connect

    def locked(database=":memory:", *args, **kwargs):
        if database not in (":memory:", "", None) and not str(database).startswith(":memory:"):
            guard(lambda d: d, "a DuckDB database")(database)
        con = connect(database, *args, **kwargs)
        for s in settings:
            con.execute(s)
        return con

    db.connect = locked
    try:
        default = db.default_connection() if callable(getattr(db, "default_connection", None)) else db.default_connection
        for s in settings:
            default.execute(s)
    except Exception:  # noqa: BLE001 - fail closed: the module-level calls would use an unlocked connection
        for name in ("sql", "query", "execute", "executemany", "read_csv", "read_json", "read_parquet",
                     "from_csv_auto", "from_parquet", "from_query", "query_df", "table", "view", "values",
                     "from_df", "from_arrow", "install_extension", "load_extension", "cursor"):
            if hasattr(db, name):
                setattr(db, name, _refused(f"duckdb.{name}; use duckdb.connect()"))


try:
    _main()
except SystemExit:
    raise
except BaseException:  # noqa: BLE001 - the learner's error, shown as Python would, then a non-zero exit
    import traceback
    traceback.print_exc()
    sys.exit(1)
