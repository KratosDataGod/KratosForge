"""``%autoreload`` and ``%aimport`` on this PC: reload your own modules when their files change.

IPython's autoreload extension reloads modules before each cell runs, so editing helpers.py and
running the next cell uses the new code. Here the same modes are kept per kernel:

- 0 (``off``): nothing is reloaded.
- 1 (``explicit``): only modules named with ``%aimport``.
- 2 (``all``) and 3 (``complete``): every module that is your own file, except ``%aimport -name``.

"Your own" means a .py file outside Python's own folders and installed packages, and outside
KratosForge's engine: the engine and libraries are never reloaded. After a reload, names in the
notebook that were taken from the module (``from helpers import double``) point at the new code.
Objects made before the reload keep their old class, as in IPython without its deep patching.
"""

from __future__ import annotations

import importlib
import os
import site
import sys
import sysconfig
import time
import traceback
from types import ModuleType
from typing import Any

_MODES = {"0": 0, "off": 0, "1": 1, "explicit": 1, "2": 2, "all": 2, "3": 3, "complete": 3}


def _system_roots() -> tuple[str, ...]:
    paths = sysconfig.get_paths()
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, paths.get("stdlib", ""), paths.get("purelib", ""),
             paths.get("platlib", ""), os.path.dirname(os.path.abspath(__file__))}
    try:
        roots.add(site.getusersitepackages())  # pip install --user: a library, not your code
    except Exception:  # noqa: BLE001  # silent-ok: an embedded Python may have no user site
        pass
    return tuple(os.path.normcase(os.path.abspath(r)) + os.sep for r in roots if r)


class Reloader:
    def __init__(self) -> None:
        self.mode = 0
        self.explicit: set[str] = set()
        self.skipped: set[str] = set()
        self._seen: dict[str, float] = {}
        self._since = time.time()  # when the last check ran: a module first seen now was imported after it
        self._roots = _system_roots()

    def own_file(self, module: ModuleType) -> str | None:
        """The module's .py file when it is the user's own code, else None."""
        file = getattr(module, "__file__", None)
        if not file or not file.endswith(".py"):
            return None
        path = os.path.normcase(os.path.abspath(file))
        return None if path.startswith(self._roots) else path

    def command(self, arg: str) -> None:
        """``%autoreload`` (reload changed modules now) or ``%autoreload <mode>``."""
        word = arg.strip().split()[0].lower() if arg.strip() else ""
        if not word or word == "now":
            self.check(namespace=None, force_mode=2)
            return
        if word not in _MODES:
            raise ValueError(f"%autoreload takes 0, 1, 2 or 3 (off, explicit, all, complete), not {word!r}.")
        self.mode = _MODES[word]
        self._remember()

    def aimport(self, arg: str, namespace: dict[str, Any]) -> str | None:
        """``%aimport`` lists; ``%aimport a, b`` imports and marks them; ``%aimport -a`` skips one."""
        names = [n.strip() for n in arg.replace(",", " ").split() if n.strip()]
        if not names:
            return (f"Modules to reload: {', '.join(sorted(self.explicit)) or 'none'}\n"
                    f"Modules to skip: {', '.join(sorted(self.skipped)) or 'none'}")
        for name in names:
            if name.startswith("-"):
                self.skipped.add(name[1:])
                self.explicit.discard(name[1:])
                continue
            module = importlib.import_module(name)
            self.explicit.add(name)
            self.skipped.discard(name)
            namespace[name.split(".")[0]] = sys.modules[name.split(".")[0]]
            if (file := self.own_file(module)) is not None:
                self._seen[name] = os.path.getmtime(file)
        return None

    def _candidates(self, mode: int) -> list[tuple[str, ModuleType, str]]:
        out = []
        for name, module in list(sys.modules.items()):
            if module is None or name in self.skipped or name == "__main__":
                continue
            if mode == 1 and name not in self.explicit:
                continue
            if (file := self.own_file(module)) is not None:
                out.append((name, module, file))
        return out

    def _remember(self) -> None:
        for name, _module, file in self._candidates(2):
            try:
                self._seen.setdefault(name, os.path.getmtime(file))
            except OSError:
                pass  # silent-ok: a file that's gone can't be reloaded either

    def check(self, namespace: dict[str, Any] | None, force_mode: int | None = None) -> list[str]:
        """Before a cell: reload modules whose file changed, then repoint the notebook's names."""
        mode = self.mode if force_mode is None else force_mode
        since, self._since = self._since, time.time()
        if mode == 0:
            return []
        reloaded = []
        for name, module, file in self._candidates(mode):
            try:
                mtime = os.path.getmtime(file)
            except OSError:
                continue
            # A module imported since the last check counts as unchanged up to then.
            before = self._seen.setdefault(name, min(mtime, since))
            if mtime <= before:
                continue
            self._seen[name] = mtime
            old = dict(vars(module))
            try:
                importlib.reload(module)
            except Exception:  # noqa: BLE001 - shown, as IPython does, and the cell still runs
                print(f"[autoreload of {name} failed]\n{traceback.format_exc(limit=-1)}", file=sys.stderr)
                continue
            reloaded.append(name)
            if namespace is not None:
                _repoint(namespace, module, old)
        return reloaded


# Values Python shares between unrelated names (False, 10, "x"): the same object in the module and in
# the notebook doesn't mean the notebook took it from the module.
_SHARED = (bool, int, float, complex, str, bytes, tuple, frozenset, type(None))


def _repoint(namespace: dict[str, Any], module: ModuleType, old: dict[str, Any]) -> None:
    """``from m import f`` names bound to m's old objects now name its new ones. A plain value such
    as a number or string is repointed only under its own name (``from m import LIMIT``), so a
    notebook's ``flag = False`` doesn't follow the module's ``DEBUG``."""
    by_id = {id(v): k for k, v in old.items() if not k.startswith("__")}
    for key, value in list(namespace.items()):
        if isinstance(value, _SHARED):
            attr = key if key in old and old[key] is value else None
        else:
            attr = by_id.get(id(value))
        if attr is not None and hasattr(module, attr):
            namespace[key] = getattr(module, attr)
