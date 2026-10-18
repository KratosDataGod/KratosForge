"""The proof runtime: what makes a notebook run a controlled experiment, on this PC and in Fabric.

This file is self-contained on purpose. The engine imports it for the run on this PC, and its
source is copied, unchanged, into the notebook published for the run in Fabric. Both sides
therefore follow exactly the same rules:

* **Pinned inputs.** Every table is read at the Delta version it had when the proof started
  (``pins``). A table that didn't exist then doesn't exist for the proof either.
* **Contained outputs.** A write goes to this side's sandbox,
  ``<lakehouse>/Files/_fabric_desktop/proofs/<proof id>/<side>/tables/<schema>/<table>``, never to
  the lakehouse's shared tables. A run that reads a table it wrote reads its own copy. The first
  append to a table starts from a copy of the pinned version, so both sides change the same
  starting state.
* **Recorded reads.** Every table read (with its version) and file read (with its SHA-256) is
  logged, so the comparison can check both sides really started from the same inputs.
* **Fixed seed and time zone.** ``random`` (and NumPy's global generator, if present) are seeded
  and the process time zone is UTC where the platform allows it. These are recorded; code that
  reads the clock is still nondeterministic and is reported before the run.

Nothing here imports Fabric Desktop: only the Python standard library, pyarrow and deltalake,
which both sides have.
"""

import hashlib
import json
import os
import platform
import random
import sys
import time

PROOF_ROOT = "Files/_fabric_desktop/proofs"


class ProofTableMissing(LookupError):
    """A table the proof can't read: it didn't exist when the proof started."""


class ReadOnlyTable:
    """A pinned Delta table as a proof hands it out: reading works as usual; anything that would
    change the shared table (merge, delete, update, restore, vacuum, optimize...) is refused."""

    _CHANGES = {"merge", "delete", "update", "restore", "vacuum", "optimize", "create_checkpoint", "cleanup_metadata",
                "alter", "repair", "create_write_transaction", "set_table_properties"}

    def __init__(self, table):
        object.__setattr__(self, "_table", table)

    def __getattr__(self, name):
        if name in self._CHANGES:
            raise PermissionError(f"A proof doesn't change shared tables ({name} was called). Write results with "
                                  f"lakehouse.write_table; a proof writes them to its own folder.")
        return getattr(self._table, name)

    def __setattr__(self, name, value):
        raise PermissionError("A proof doesn't change shared tables.")

    def __repr__(self):
        return f"ReadOnlyTable({self._table!r})"


class ProofRuntime:
    def __init__(self, config, side):
        self.id = config["id"]
        self.side = side
        self.pins = dict(config.get("pins") or {})
        self.flat = bool(config.get("flat"))
        self.seed = int(config.get("seed", 0))
        self.root = None
        self.mount = None
        self._options = lambda: {}
        self.log = {"proof": self.id, "side": side, "reads": {}, "files": {}, "writes": {},
                    "seed": self.seed, "timezone": None, "started": time.time()}
        # Tables already in this side's sandbox when it starts (a replay seeded from a bundle's sample):
        # reads and writes of these go to the sandbox copy, as if this side had written them.
        for key in config.get("seeded") or []:
            self.log["writes"][key.lower()] = ["seeded"]

    # ---- where things are

    def bind(self, root, options=None, mount=None):
        """``root`` is the lakehouse (``abfss://ws@onelake.../lh`` or a folder in tests);
        ``options`` returns the storage options for it; ``mount`` is the lakehouse's local mount
        (``/lakehouse/default`` in Fabric), used to save the log."""
        self.root = str(root).rstrip("/")
        self.mount = mount
        if options is not None:
            self._options = options
        self._fix_randomness()
        return self

    def options(self):
        return self._options()

    @staticmethod
    def key(schema, table):
        return f"{(schema or 'dbo').lower()}.{table.lower()}"

    def sandbox(self, rel=""):
        return f"{self.root}/{PROOF_ROOT}/{self.id}/{self.side}" + (f"/{rel}" if rel else "")

    def sandbox_table(self, key):
        schema, table = key.split(".", 1)
        return self.sandbox(f"tables/{schema}/{table}")

    def shared_table(self, schema, table):
        if self.flat:
            return f"{self.root}/Tables/{table}"
        return f"{self.root}/Tables/{schema or 'dbo'}/{table}"

    def _pin(self, key):
        """The pinned version of ``key``; pins are kept lower case like keys."""
        for k, v in self.pins.items():
            if k.lower() == key:
                return k, v
        return None, None

    # ---- what the lakehouse module asks

    def read_location(self, schema, table):
        """(uri, version) to read ``schema.table``: this side's own output, or the pinned input."""
        key = self.key(schema, table)
        if key in self.log["writes"]:
            return self.sandbox_table(key), None
        name, version = self._pin(key)
        if name is None:
            raise ProofTableMissing(f"{schema}.{table} didn't exist when this proof started, so it can't be read in it.")
        self.log["reads"][name] = version
        return self.shared_table(*name.split(".", 1)), version  # the table's own spelling: paths are case-sensitive

    def open(self, schema, table, open_delta):
        """The table to read, opened with ``open_delta(uri, version)``: read-only when it is a
        shared table (pinned), as it is when it is this side's own output."""
        uri, version = self.read_location(schema, table)
        opened = open_delta(uri, version)
        return opened if version is None else ReadOnlyTable(opened)

    def write_location(self, schema, table, mode, write_delta):
        """The sandbox uri to write ``schema.table`` to. Before the first append to a table that
        existed at the start, the pinned version is copied in with ``write_delta(uri, arrow)``, so
        the append changes the same starting state on both sides."""
        key = self.key(schema, table)
        uri = self.sandbox_table(key)
        if mode == "append" and key not in self.log["writes"]:
            name, version = self._pin(key)
            if name is not None:
                from deltalake import DeltaTable
                start = DeltaTable(self.shared_table(*name.split(".", 1)), version=version,
                                   storage_options=self.options()).to_pyarrow_table()
                self.log["reads"][name] = version
                write_delta(uri, start)
        self.log["writes"].setdefault(key, []).append(mode)
        return uri

    def file_read(self, rel, local_path):
        """Hash a file the notebook reads (``rel`` as the notebook named it: Files/...)."""
        h = hashlib.sha256()
        size = 0
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
        self.log["files"][rel] = {"sha256": h.hexdigest(), "bytes": size}

    # ---- reproducibility

    def _fix_randomness(self):
        random.seed(self.seed)
        seeded = ["random"]
        try:
            import numpy as np
            np.random.seed(self.seed)
            seeded.append("numpy")
        except ImportError:  # NumPy is optional; without it there is nothing to seed
            pass
        self.log["seeded"] = seeded
        tz = "not changed"
        if hasattr(time, "tzset"):
            self._old_tz = os.environ.get("TZ")
            os.environ["TZ"] = "UTC"
            time.tzset()
            tz = "UTC"
        self.log["timezone"] = tz

    def restore(self):
        """Put the process time zone back (on this PC, where the engine keeps running)."""
        if hasattr(self, "_old_tz"):
            if self._old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = self._old_tz
            time.tzset()
            del self._old_tz

    def environment(self, modules=None):
        """Python and the versions of the distributions that provide ``modules`` (all when None)."""
        import importlib.metadata as md
        packages = {}
        try:
            owners = md.packages_distributions()
        except Exception:  # noqa: BLE001
            owners = {}
        names = set()
        if modules is None:
            names = {d.metadata.get("Name") for d in md.distributions() if d.metadata.get("Name")}
        else:
            for m in modules:
                names.update(owners.get(m) or [m])
        for n in names:
            try:
                packages[n.lower()] = md.version(n)
            except Exception:  # noqa: BLE001  # silent-ok: a module name that isn't an installed distribution has no version
                pass
        return {"python": platform.python_version(), "implementation": platform.python_implementation(),
                "platform": platform.system(), "packages": dict(sorted(packages.items()))}

    def finish(self, modules=None):
        self.log["seconds"] = round(time.time() - self.log["started"], 3)
        self.log["environment"] = self.environment(modules)
        return self.log

    def save(self, modules=None):
        """Write this side's log next to its outputs, through the lakehouse mount (in Fabric)."""
        log = self.finish(modules)
        base = self.mount or self.root
        folder = f"{base}/{PROOF_ROOT}/{self.id}/{self.side}"
        os.makedirs(folder, exist_ok=True)
        with open(f"{folder}/log.json", "w", encoding="utf-8") as f:
            json.dump(log, f)
        return log
