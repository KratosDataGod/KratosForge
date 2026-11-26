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

# Annotations that name these are quoted: this process imports nothing for them (mypy reads the block;
# no "from __future__ import annotations", which the learner's compile() would inherit).
TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any, NoReturn


def _resolver(appcontainer: bool, root: "str | None" = None) -> "Callable[[str], str]":
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

    def resolve(p: str, _roots: "tuple[str, ...]" = roots, _err: "type[Exception]" = PermissionError) -> str:
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

    end_now, end_addr = _ender()   # before the hook, which refuses ctypes from then on
    _guard_imports(blocked)
    # The data libraries are imported and wrapped first (some load native code through ctypes). Their
    # own readers and writers go straight from one call to the other, so no name here holds them.
    _install(norm(work), reads, denied, blocked, libs, real, end_addr, _wrap_native(work, real))
    os.chdir(work)
    script = os.path.join(work, cfg["script"])
    with open(script, encoding="utf-8") as fh:
        source = fh.read()
    code = compile(source, script, "exec")
    sys.argv = [script]
    scope = {"__name__": "__main__", "__file__": script, "__builtins__": __builtins__, "kf_end_now": end_now}
    exec(code, scope)  # noqa: S102 - the learner's own exercise script, in this guarded process


def _ender() -> "tuple[Callable[[int], NoReturn], int]":
    """``kf_end_now(rc)``, for a script that must end at once (pyspark_lab.LEARNER_SCRIPT), and the address
    of the one native call it makes, for the audit hook to allow.

    On Windows ``os._exit`` is ExitProcess: it stops delta-rs's and DuckDB's worker threads wherever they
    are, then runs each library's shutdown code, which can crash (0xC0000005) on what a stopped thread
    left behind, after the learner's code finished. TerminateProcess runs none of it. It is looked up
    here, before the hook refuses ctypes, and only on this process (the pseudo-handle -1)."""
    def flush() -> None:
        for f in (sys.stdout, sys.stderr):
            try:
                f.flush()
            except Exception:   # noqa: BLE001  # silent-ok: a closed stream has nothing left to lose
                pass

    if sys.platform != "win32":
        def end(rc: int, _flush: "Callable[[], None]" = flush, _exit: "Callable[[int], NoReturn]" = os._exit) -> "NoReturn":
            _flush()
            _exit(rc)
        return end, 0
    import ctypes
    k = ctypes.WinDLL("kernel32")
    terminate = k.TerminateProcess
    terminate.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    addr = ctypes.cast(terminate, ctypes.c_void_p).value or 0

    def end_win(rc: int, _flush: "Callable[[], None]" = flush, _terminate: "Any" = terminate,
                _exit: "Callable[[int], NoReturn]" = os._exit) -> "NoReturn":
        _flush()
        _terminate(-1, rc & 0xFFFFFFFF)   # -1: this process
        _exit(rc)   # if TerminateProcess returned
    return end_win, addr


def _guard_imports(blocked: "frozenset[str]") -> None:
    """The engine package and sign-in libraries can't be imported, from here on."""
    import importlib.abc

    class _Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, name: str, path: "Any" = None, target: "Any" = None,
                      _blocked: "frozenset[str]" = blocked) -> None:
            if name.partition(".")[0] in _blocked:
                raise ImportError(f"{name} isn't available in an Academy exercise.")
            return None

    for name in list(sys.modules):
        if name.partition(".")[0] in blocked:
            del sys.modules[name]
    sys.meta_path.insert(0, _Guard())


def _install(work: str, reads: "tuple[str, ...]", denied: "tuple[str, ...]", blocked: "frozenset[str]", libs: "tuple[str, ...]" = (),
             real: "Callable[[str], str]" = os.path.realpath, end_addr: int = 0,
             originals: "tuple[Any, ...]" = ()) -> None:
    """The audit hook. Its state is bound as default arguments of immutable values, and the
    function is kept nowhere the learner's code can name. A second hook holds the data libraries'
    own readers and writers (``originals``, from _wrap_native) and runs them for the wrappers."""
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

    def _paths(args: "tuple[Any, ...]") -> "Iterator[Any]":
        for a in args:
            if isinstance(a, (str, bytes, os.PathLike)):
                yield a

    def hook(event: str, args: "tuple[Any, ...]", _work: str = work, _libs: "tuple[str, ...]" = libs,
             _reads: "tuple[str, ...]" = reads, _denied: "tuple[str, ...]" = denied, _blocked: "frozenset[str]" = blocked,
             _refuse: "tuple[str, ...]" = refuse_names, _write: "tuple[str, ...]" = write_names, _list: "tuple[str, ...]" = list_names,
             _real: "Callable[[str], str]" = real, _fspath: "Callable[[Any], str]" = os.fsdecode,
             _norm: "Callable[[str], str]" = os.path.normcase, _sep: str = os.sep,
             _paths: "Callable[[tuple[Any, ...]], Iterator[Any]]" = _paths,
             _err: "type[Exception]" = PermissionError, _end: int = end_addr) -> None:
        def under(path: str, roots: "tuple[str, ...]") -> bool:
            return any(path == r or path.startswith(r.rstrip(_sep) + _sep) for r in roots)

        def where(p: "Any") -> str:
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
        if (event == "ctypes.call_function" and _end and args[0] == _end and type(args[1]) is tuple
                and len(args[1]) == 2 and type(args[1][0]) is int and args[1][0] == -1
                and type(args[1][1]) is int):   # plain ints: ctypes passes an object's _as_parameter_
            return   # kf_end_now: TerminateProcess on this process only (_ender)
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
    if originals:
        sys.addaudithook(_library_hook(tuple(originals)))


_CALL_EVENT = "academy.library.call"


def _library_hook(originals: "tuple[Any, ...]") -> "Callable[[str, tuple[Any, ...]], None]":
    """The only holder of the data libraries' own readers and writers (_wrap_native). It answers the
    event _through raises once a wrapper has checked its arguments, and puts the result, or the error
    with no traceback into this hook, in the caller's list. The function it calls is let go of before
    the call starts, so code the library calls back (a converter, an object passed in) finds no
    reference to the others in this frame."""
    def hook(event: str, args: "tuple[Any, ...]", _o: "tuple[Any, ...] | None" = originals,
             _bare: "Callable[[BaseException], BaseException]" = _bare_error) -> None:
        if event != _CALL_EVENT:
            return
        if (len(args) != 4 or type(args[0]) is not int or type(args[1]) is not tuple or type(args[2]) is not dict
                or type(args[3]) is not list or args[3]):
            return
        i, a, k, box = args
        if not 0 <= i < len(_o):  # type: ignore[arg-type]
            box.append((True, PermissionError("An Academy exercise can't call that.")))
            return
        try:
            # The function is taken from the tuple, then the name is cleared while the arguments are read.
            box.append((False, _o[i](*a, **(k if (_o := None) is None else k))))  # type: ignore[index]
        except BaseException as exc:  # noqa: BLE001 - handed to the wrapper, which raises it there
            box.append((True, _bare(exc)))

    return hook


def _bare_error(exc: BaseException) -> BaseException:
    """``exc`` and the errors it chains to, with no tracebacks (they would lead into the hook's frame)."""
    seen: "set[int]" = set()
    todo = [exc]
    while todo:
        e = todo.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        e.__traceback__ = None
        todo += [e.__cause__, e.__context__, *getattr(e, "exceptions", ())]  # type: ignore[list-item]
    return exc


def _through(index: int, args: "tuple[Any, ...]", kwargs: "dict[str, Any]",
             _audit: "Callable[..., None]" = sys.audit) -> "Any":
    """Run the library function _library_hook holds at ``index``, with arguments a wrapper checked."""
    box: "list[Any]" = []
    _audit(_CALL_EVENT, index, tuple(args), dict(kwargs), box)
    if len(box) != 1:
        raise PermissionError("An Academy exercise can't call that here.")
    failed, value = box[0]
    if failed:
        raise value
    return value


def _wrap_native(work: str, resolve: "Callable[[str], str]" = os.path.realpath) -> "tuple[Any, ...]":
    """Polars, DuckDB, delta-rs, pyarrow and pandas read and write files from native code the audit
    hook never sees. Their entry points refuse a URL, ``storage_options`` and a path outside the
    workspace. Only modules that are installed are wrapped; each import is checked by the hook.

    A wrapper holds no reference to the function it stands for: not as ``__wrapped__``, a closure
    cell, a default or a global. It holds only that function's number, and _through asks the second
    audit hook (_install), the only holder of the originals, to run it. Returns the originals, in
    that order, for that hook."""
    import importlib

    real, sep, norm = resolve, os.sep, os.path.normcase
    as_given, work = work, norm(work)   # DuckDB compares its allowed directories with case

    def check(value: "Any", what: str) -> None:
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

    originals: "list[Any]" = []

    def held(fn: "Any") -> int:
        """``fn``'s number among the originals the second audit hook keeps."""
        originals.append(fn)
        return len(originals) - 1

    def guard(fn: "Callable[..., Any]", what: str,
              arg_names: "tuple[str, ...]" = ("source", "file", "path", "target", "table_or_uri", "table_uri", "where",
                                           "database", "base_dir", "path_or_paths", "uri")) -> "Callable[..., Any]":
        index = held(fn)

        def wrapper(*args: "Any", **kwargs: "Any") -> "Any":
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
            return _through(index, args, kwargs)
        _describe(wrapper, fn)
        return wrapper

    swapped: dict[int, object] = {}   # id(original) -> wrapped, so the same function reached through a submodule is wrapped too

    def wrap_all(module: "Any", prefixes: "tuple[str, ...]", what: str) -> None:
        for name in dir(module):
            fn = getattr(module, name, None)
            if name.startswith(prefixes) and callable(fn) and not isinstance(fn, type):   # classes: _guard_init
                wrapped = swapped.get(id(fn)) or guard(fn, what)
                try:
                    setattr(module, name, wrapped)
                except (AttributeError, TypeError):
                    continue
                swapped[id(fn)] = wrapped

    def rewire(package: str) -> None:
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

    def maybe(name: str) -> "Any":
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
        write = dl.write_deltalake
        dl.write_deltalake = swapped[id(write)] = guard(write, "a Delta write")   # deltalake.writer gets it too (rewire)
        del write
        _guard_init(dl.DeltaTable, check, "a Delta table", held)
        _lock_query_builder(held)
    for modname in ("pyarrow.parquet", "pyarrow.dataset", "pyarrow.csv", "pyarrow.feather", "pyarrow.orc",
                    "pyarrow.json"):
        mod = maybe(modname)
        if mod is not None:
            wrap_all(mod, ("read_", "write_", "dataset"), "a pyarrow file")
            for cls_name in ("ParquetFile", "ParquetDataset", "ParquetWriter"):
                cls = getattr(mod, cls_name, None)
                if isinstance(cls, type):
                    try:
                        _guard_init(cls, check, "a pyarrow file", held)
                    except (AttributeError, TypeError):
                        setattr(mod, cls_name, guard(cls, "a pyarrow file"))
    pa_fs = maybe("pyarrow.fs")
    if pa_fs is not None:
        # A file system object can't be confined by SubTreeFileSystem (it lets ".." through), so the
        # local one is a subclass whose path methods check every path first. pyarrow's own readers
        # build it by name and keep working; the paths they get were checked by their wrappers.
        # Remote and other file systems are refused.
        _local = pa_fs.LocalFileSystem

        def _checked_method(name: str, n_paths: int) -> "Callable[..., Any]":
            index = held(getattr(_local, name))

            def method(self: "Any", *args: "Any", **kwargs: "Any") -> "Any":
                for p in args[:n_paths]:
                    if isinstance(p, (list, tuple)):
                        for q in p:
                            check(getattr(q, "base_dir", q), "a pyarrow file")
                    else:
                        check(getattr(p, "base_dir", p), "a pyarrow file")
                return _through(index, (self, *args), kwargs)
            method.__name__ = method.__qualname__ = name
            return method

        two = ("move", "copy_file")
        one = ("get_file_info", "create_dir", "delete_dir", "delete_dir_contents", "delete_file",
               "open_input_stream", "open_input_file", "open_output_stream", "open_append_stream", "normalize_path")
        members = {n: _checked_method(n, 2 if n in two else 1) for n in one + two if hasattr(_local, n)}
        members["delete_root_dir_contents"] = _refused("pyarrow.fs delete_root_dir_contents")
        _base = pa_fs.FileSystem
        base_from_uri = held(_base.from_uri)

        def from_uri(uri: str) -> "tuple[Any, str]":
            """A plain path in the workspace only; a local file system comes back checked."""
            check(uri, "a pyarrow file")
            fs, path = _through(base_from_uri, (uri,), {})
            return (checked_local() if type(fs).__name__ == "LocalFileSystem" else fs), path

        members["from_uri"] = staticmethod(from_uri)
        checked_local = type("LocalFileSystem", (_local,), members)

        class _FileSystemMeta(type):   # isinstance and issubclass still answer for the real base class
            def __instancecheck__(cls, obj: object) -> bool:
                return isinstance(obj, _base)

            def __subclasscheck__(cls, sub: type) -> bool:
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
        _lock_duckdb(db, as_given, check)   # check, not guard: nothing a learner can reach leads to the originals
    for package in ("polars", "pyarrow", "pandas", "deltalake"):
        rewire(package)
    out = tuple(originals)
    originals.clear()   # the hook gets the tuple; this list, which held() closes over, keeps nothing
    return out


def _describe(wrapper: "Any", fn: "Any") -> None:
    """The name and help text of ``fn`` on its wrapper, copied as plain strings (no reference to ``fn``)."""
    for attr in ("__name__", "__qualname__", "__doc__"):
        value = getattr(fn, attr, None)
        if isinstance(value, str):
            try:
                setattr(wrapper, attr, str(value))
            except (AttributeError, TypeError):
                pass


def _is_frame(value: object) -> bool:
    return type(value).__name__ in ("DataFrame", "LazyFrame", "Table", "RecordBatch")


def _refused(name: str) -> "Callable[..., NoReturn]":
    def refuse(*_a: "Any", **_k: "Any") -> "NoReturn":
        raise PermissionError(f"An Academy exercise can't use {name}.")
    return refuse


def _guard_init(cls: "Any", check: "Callable[[Any, str], None]", what: str, held: "Callable[[Any], int]") -> None:
    index = held(cls.__init__)

    def __init__(self: "Any", table_uri: "Any", *args: "Any", **kwargs: "Any") -> None:
        if kwargs.get("storage_options"):
            raise PermissionError("An Academy exercise can't use remote storage options.")
        check(table_uri, what)
        _through(index, (self, table_uri, *args), kwargs)

    _describe(__init__, cls.__init__)
    cls.__init__ = __init__


def _lock_query_builder(held: "Callable[[Any], int]") -> None:
    """deltalake's QueryBuilder runs DataFusion SQL, which reads and writes files from native code
    (CREATE EXTERNAL TABLE ... LOCATION, COPY ... TO). It runs one SELECT or WITH statement over the
    Delta tables registered with it, and its session settings are limited to execution ones. The
    native builders, and the functions that make and use them, are held by the second audit hook
    (_wrap_native); a QueryBuilder holds only its builder's number."""
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

    def allowed(sql: "Any") -> str:
        text = comments.sub(" ", str(sql))
        bare = strings.sub("''", text).strip().rstrip(";").strip()
        if ";" in bare or not re.match(r"(?is)\s*(select|with)\b", bare) or banned.search(bare):
            raise PermissionError("An Academy exercise runs one SELECT (or WITH ... SELECT) over the tables "
                                  "registered with the QueryBuilder.")
        return str(sql)

    def settings(session_config: "Any") -> "dict[str, str] | None":
        if session_config is None:
            return None
        out = {str(k): str(v) for k, v in dict(session_config).items()}
        for key in out:
            if not key.startswith("datafusion.execution."):
                raise PermissionError("An Academy exercise can set only datafusion.execution settings.")
        return out

    builders: "dict[int, Any]" = {}

    def new(session_config: "Any") -> int:
        builders[len(builders) + 1] = real(settings(session_config))
        return len(builders)

    def register(number: "Any", table_name: "Any", delta_table: "Any") -> None:
        builders[number].register(table_name=str(table_name), delta_table=delta_table)

    def run(number: "Any", sql: "Any") -> "Any":
        return builders[number].execute(allowed(sql))

    to_new, to_register, to_execute = held(new), held(register), held(run)

    def builder(number: int) -> "Any":
        """What QueryBuilder calls on its builder; the native builder stays with the hook."""
        class _Builder:
            __slots__ = ()

            def register(self, table_name: str, delta_table: "Any") -> None:
                _through(to_register, (number, table_name, delta_table), {})

            def execute(self, sql: "Any") -> "Any":
                return _through(to_execute, (number, allowed(sql)), {})

        return _Builder()

    def __init__(self: "Any", session_config: "dict[str, Any] | None" = None) -> None:
        self._query_builder = builder(_through(to_new, (settings(session_config),), {}))

    def execute(self: "Any", sql: "Any") -> "Any":
        return self._query_builder.execute(allowed(sql))

    qb.__init__, qb.execute = __init__, execute
    refuse = _refused("deltalake's PyQueryBuilder; use deltalake.QueryBuilder")
    for modname in ("deltalake.query", "deltalake._internal", "deltalake"):
        mod = _sys.modules.get(modname)
        if mod is not None and getattr(mod, "PyQueryBuilder", None) is real:
            setattr(mod, "PyQueryBuilder", refuse)  # noqa: B010 - replaces deltalake's own


def _lock_duckdb(db: "Any", work: str, check: "Callable[[Any, str], None]") -> None:
    """Every DuckDB connection, the module's default one included, reads only the workspace."""
    settings = ("SET allowed_directories = ['" + work.replace("'", "''") + "']",
                "SET enable_external_access = false", "SET memory_limit = '1GB'", "SET lock_configuration = true")
    connect = db.connect

    def locked(database: "Any" = ":memory:", *args: "Any", **kwargs: "Any") -> "Any":
        if database not in (":memory:", "", None) and not str(database).startswith(":memory:"):
            check(database, "a DuckDB database")
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
