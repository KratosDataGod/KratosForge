"""The proof runtime's Spark side: what makes a Spark notebook in Fabric run as a proof (PB-015).

In Fabric, ``spark`` is Fabric's own Spark, which reads and writes the lakehouse's tables by
name. For a proof, this file (copied unchanged into the first cell of the published copy, like
proof_runtime.py) points every way the notebook reaches a table at the proof's rules instead:

* **Reads** of a table (``spark.sql``, ``spark.table``, ``spark.read.table``, ``DeltaTable.forName``,
  a ``Tables/`` path) read the version pinned when the proof started, or this side's own copy once
  the notebook has changed it.
* **Changes** (MERGE INTO, UPDATE, DELETE, INSERT, CREATE TABLE, ``saveAsTable``, a ``Tables/``
  path) go to this side's copy in the proof's folder. The first change to a table that existed
  copies the pinned version in first, so both sides change the same starting state, as the
  lakehouse module does on this PC (lakehouse.change_table).
* **Refused**: anything that would change the shared lakehouse some other way (DROP, TRUNCATE,
  ALTER, ``insertInto``, ``writeTo``, writes into ``Files/``, writes to another lakehouse).

SQL is rewritten with sqlglot, but only the table names are replaced in the notebook's own text,
so Spark runs the statement as written. The bookkeeping (pins, the log, where the copies go) is
the ProofRuntime that the lakehouse module also uses, so a notebook mixing both stays consistent.

Nothing here imports Fabric Desktop: only the standard library, sqlglot, pyspark and delta.
"""

import os
import re

_PATH_FORMATS = {"delta", "parquet", "csv", "json", "orc", "text", "avro", "binaryfile"}
_SAFE_COMMANDS = {"SHOW", "DESCRIBE", "DESC", "EXPLAIN", "SET", "RESET", "CLEAR", "CACHE", "UNCACHE", "ADD", "LIST"}
_REFUSED = "A proof keeps every change in its own folder"


class ProofRefused(PermissionError):
    """Something a proof can't keep in its own folder."""


def _quote(uri):
    return "delta.`" + str(uri).replace("`", "``") + "`"


class ProofSpark:
    def __init__(self, proof, spark, name, schemas):
        """``proof`` is the bound ProofRuntime; ``name`` the attached lakehouse's name."""
        self.proof = proof
        self.spark = spark
        self.name = name or ""
        self.schemas = bool(schemas)
        self.views = set()   # temp views the notebook made: names that aren't lakehouse tables
        self.opened = set()  # tables opened with DeltaTable.forName before anything else changed them
        self.orig = {}

    # ---- names and paths

    def parts(self, parts):
        """(schema, table) for a table name's parts, as lakehouse.table_ref reads them."""
        parts = [str(p) for p in parts]
        if parts and parts[0].lower() == self.name.lower() and (len(parts) == 3 or len(parts) == 2 and not self.schemas):
            parts = parts[1:]
        if not parts or not all(parts) or any(c in p for p in parts for c in "/\\") or any(set(p) == {"."} for p in parts):
            raise ValueError(f"{'.'.join(parts)!r} isn't a table name.")
        if len(parts) > 2 or (len(parts) == 2 and not self.schemas and parts[0].lower() != "dbo"):
            raise ProofRefused(f"{'.'.join(parts)} isn't a table of the attached lakehouse ({self.name}). A proof "
                               "pins only the attached lakehouse's tables.")
        return (parts[0], parts[1]) if len(parts) == 2 else ("dbo", parts[0])

    def split(self, name):
        return self.parts([p.strip("`") for p in re.findall(r"`(?:[^`]|``)+`|[^.]+", str(name))])

    def local(self, name):
        """Whether a one-part name is a temp view the notebook made, not a lakehouse table."""
        return name.lower() in self.views

    def lakehouse_path(self, path):
        """What a path is to the proof: ("table", schema, table), ("files", rel), ("sandbox", None),
        ("onelake", None) for another lakehouse, or None for a path outside OneLake."""
        p = str(path).replace("\\", "/").rstrip("/")
        if p.lower().startswith("file:"):  # file:/lakehouse/default/... is the mount by another name
            p = "/" + p[5:].lstrip("/")
        root, mount = self.proof.root.rstrip("/"), (self.proof.mount or "/lakehouse/default").rstrip("/")
        rel = None
        for base in (root, mount):
            if p == base or p.startswith(base + "/"):
                rel = p[len(base):].lstrip("/")
                break
        if rel is None and (p in ("Tables", "Files") or p.startswith(("Tables/", "Files/"))):
            rel = p
        if rel is None:
            return ("onelake", None) if "onelake." in p.lower() else None
        sandbox = self.proof.sandbox().split(root, 1)[-1].lstrip("/")
        if rel == sandbox or rel.startswith(sandbox + "/"):
            return ("sandbox", None)
        if rel.startswith("Files/") or rel == "Files":
            return ("files", rel)
        bits = [b for b in rel.split("/")[1:] if b]
        if rel.startswith("Tables") and (len(bits) == 2 and self.schemas or len(bits) == 1):
            schema, table = (bits[0], bits[1]) if len(bits) == 2 else ("dbo", bits[0])
            return ("table", schema, table)
        raise ProofRefused(f"{path} is inside the lakehouse but isn't a table's folder. In a proof, read and write "
                           "tables by name or by their Tables/ folder.")

    # ---- the proof's state

    def key(self, schema, table):
        return self.proof.key(schema, table)

    def exists(self, schema, table):
        key = self.key(schema, table)
        return key in self.proof.log["writes"] or self.proof._pin(key)[0] is not None

    def read(self, schema, table):
        """(uri, version) to read: the pinned shared table, or this side's own copy."""
        return self.proof.read_location(schema, table)

    def target(self, schema, table, mode, seed=True):
        """The sandbox uri for a change to ``schema.table``. Before the first change to a table that
        existed, the pinned version is copied in (``seed``), as lakehouse.change_table does on this PC."""
        key = self.key(schema, table)
        uri = self.proof.sandbox_table(key)
        writes = self.proof.log["writes"]
        if key not in writes:
            name, version = self.proof._pin(key)
            if seed and name is not None:
                self.proof.log["reads"][name] = version
                shared = self.proof.shared_table(*name.split(".", 1))
                reader = self.spark.read.format("delta")
                if version is not None:
                    reader = reader.option("versionAsOf", version)
                start = self.orig["load"](reader, shared)
                self.orig["save"](start.write.format("delta").mode("overwrite"), uri)
        if mode != "open":
            self.opened.discard(key)
        writes.setdefault(key, []).append(mode)
        return uri

    def frame(self, schema, table):
        uri, version = self.read(schema, table)
        reader = self.spark.read.format("delta")
        if version is not None:
            reader = reader.option("versionAsOf", version)
        return self.orig["load"](reader, uri)

    def file_read(self, path):
        """Hash a Files/ file the notebook reads with Spark, as the lakehouse module does."""
        where = self.lakehouse_path(path)
        if where and where[0] == "files":
            local = f"{(self.proof.mount or '/lakehouse/default').rstrip('/')}/{where[1]}"
            if os.path.isfile(local):
                self.proof.file_read(where[1], local)

    def finish(self):
        """Tables only opened with DeltaTable.forName and never changed aren't writes: drop them,
        so they compare like the PC, where opening one writes nothing."""
        for key in sorted(self.opened):
            try:
                version = self.orig["sql"](self.spark, f"DESCRIBE HISTORY {_quote(self.proof.sandbox_table(key))} LIMIT 1") \
                    .collect()[0]["version"]
            except Exception:  # noqa: BLE001  # silent-ok: a copy we can't read stays listed, as a write
                continue
            if version == 0 and set(self.proof.log["writes"].get(key, [])) == {"open"}:
                self.proof.log["writes"].pop(key, None)

    # ---- SQL

    def rewrite(self, query):
        """``query`` with every lakehouse table pointed at the proof, or None when there is
        nothing to run (CREATE TABLE IF NOT EXISTS of a table that exists)."""
        import sqlglot
        from sqlglot import exp
        try:
            trees = [t for t in sqlglot.parse(query, read="spark") if t is not None]
        except Exception as exc:  # noqa: BLE001 - sqlglot's own errors, reported plainly
            raise ProofRefused(f"A proof couldn't read this SQL, so it can't keep it to the proof's folder: {exc}") from None
        if not trees:
            return query
        if len(trees) > 1:
            raise ValueError("spark.sql runs one statement at a time.")
        tree = trees[0]
        kind = type(tree).__name__
        if kind == "Copy":
            raise ProofRefused(f"{_REFUSED}; COPY INTO loads files into a table some other way. Read the files and "
                               "write with INSERT INTO or saveAsTable.")
        if kind == "Command":
            word = str(tree.this or "").split()[0].upper() if str(tree.this or "").split() else ""
            if word in _SAFE_COMMANDS:
                return query
            raise ProofRefused(f"{_REFUSED}; it can't run {word or 'this statement'} there.")
        if kind in ("Drop", "TruncateTable", "Alter", "AlterTable", "Use", "Refresh", "Analyze"):
            dropped = tree.args.get("tables") or ([tree.this] if tree.this is not None else [])
            if kind == "Drop" and str(tree.args.get("kind", "")).upper() == "VIEW" and dropped \
                    and all(len(t.parts) == 1 and self.local(t.name) for t in dropped):
                self.views.difference_update(t.name.lower() for t in dropped)
                return query
            raise ProofRefused(f"{_REFUSED}; it doesn't drop, truncate, alter or switch tables (the statement "
                               f"was {kind.upper()}).")
        target, mode, seed = None, None, True
        if kind in ("Merge", "Update", "Delete"):
            target, mode = tree.this, kind.lower()
        elif kind == "Insert":
            target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
            if not isinstance(target, exp.Table):
                raise ProofRefused(f"{_REFUSED}; INSERT ... DIRECTORY writes files outside it.")
            mode = "overwrite" if tree.args.get("overwrite") else "append"
        elif kind == "Create":
            what = str(tree.args.get("kind", "")).upper()
            target_ = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
            if what == "VIEW":
                temporary = any(type(p).__name__ == "TemporaryProperty"
                                for p in (tree.args.get("properties") or exp.Properties()).expressions)
                if not temporary:
                    raise ProofRefused(f"{_REFUSED}; a lasting view would be made in the lakehouse. Use a TEMP VIEW.")
                self.views.add(target_.name.lower())
            elif what == "TABLE":
                if any(type(p).__name__ == "LocationProperty"
                       for p in (tree.args.get("properties") or exp.Properties()).expressions):
                    raise ProofRefused(f"{_REFUSED}; a table with its own LOCATION would be written there.")
                target, mode, seed = target_, "overwrite", False
            else:
                raise ProofRefused(f"{_REFUSED}; it doesn't create {what.lower() or 'that'} objects.")
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        edits = []
        if target is not None:
            schema, table = self._table_parts(target)
            if kind == "Create":
                exists = self.exists(schema, table)
                if exists and tree.args.get("exists"):
                    return None
                if exists and not tree.args.get("replace"):
                    raise ValueError(f"Table {schema}.{table} already exists.")
            elif not self.exists(schema, table):
                raise ValueError(f"Table {schema}.{table} doesn't exist, so {kind.upper()} has nothing to change.")
            edits.append((target, _quote(self.target(schema, table, mode, seed))))
        for t in tree.find_all(exp.Table):
            if t is target or not isinstance(t.this, exp.Identifier):
                continue
            db = (t.db or "").lower()
            if db in _PATH_FORMATS and t.this.quoted and not t.catalog:
                where = self.lakehouse_path(t.name)
                if where is None:
                    continue
                if where[0] == "sandbox":
                    continue
                if where[0] == "files":
                    self.file_read(t.name)
                    continue
                if where[0] != "table" or db != "delta":
                    raise ProofRefused(f"{t.name} is read as {db} files inside the lakehouse; in a proof, read tables "
                                       "by name.")
                schema, table = where[1], where[2]
            else:
                names = [p.name for p in t.parts]
                if len(names) == 1 and (names[0].lower() in ctes or self.local(names[0])):
                    continue
                schema, table = self.parts(names)
            if t.args.get("version") is not None and self.key(schema, table) in self.proof.log["writes"]:
                raise ProofRefused(f"{schema}.{table} was changed in this proof, so its history is the proof's "
                                   "copy's; time travel on it can't match the shared table's.")
            uri, version = self.read(schema, table)
            text = _quote(uri)
            if version is not None and t.args.get("version") is None and kind != "Describe":
                text += f" VERSION AS OF {int(version)}"
            edits.append((t, text))
        return self._apply(query, edits)

    def _table_parts(self, t):
        from sqlglot import exp
        if (t.db or "").lower() == "delta" and isinstance(t.this, exp.Identifier) and t.this.quoted and not t.catalog:
            where = self.lakehouse_path(t.name)
            if where is None or where[0] != "table":
                raise ProofRefused(f"{_REFUSED}; {t.name} isn't a table of the attached lakehouse.")
            return where[1], where[2]
        return self.parts([p.name for p in t.parts])

    @staticmethod
    def _apply(query, edits):
        spans = []
        for node, text in edits:
            ids = node.parts
            first, last = ids[0].meta, ids[-1].meta
            if "start" not in first or "end" not in last:
                raise ProofRefused("A proof couldn't find a table name's place in the SQL.")
            spans.append((first["start"], last["end"] + 1, text))
        for start, end, text in sorted(spans, reverse=True):
            query = query[:start] + text + query[end:]
        return query

    # ---- binding to Spark

    def install(self):
        """Patch Spark's entry points to tables for the rest of the run."""
        from pyspark.sql import DataFrame
        from pyspark.sql.catalog import Catalog
        from pyspark.sql.readwriter import DataFrameReader, DataFrameWriter
        session = type(self.spark)
        me = self
        o = self.orig
        o.update(sql=session.sql, table=session.table, load=DataFrameReader.load, save=DataFrameWriter.save,
                 mode=DataFrameWriter.mode, tableExists=Catalog.tableExists)

        def sql(self_, sqlQuery, args=None, **kwargs):  # noqa: N803 - Spark's names
            text = me.rewrite(sqlQuery)
            if text is None:
                return o["sql"](self_, "SELECT 1 AS skipped LIMIT 0")
            return o["sql"](self_, text, args, **kwargs) if args is not None else o["sql"](self_, text, **kwargs)
        session.sql = sql
        session.table = lambda self_, tableName: me.frame(*me.split(tableName))  # noqa: N803

        def reader_table(self_, tableName):  # noqa: N803
            return me.frame(*me.split(tableName))
        DataFrameReader.table = reader_table

        o["reader_option"], o["reader_options"] = DataFrameReader.option, DataFrameReader.options

        def reader_option(self_, key, value):
            if str(key).lower() == "path":
                self_._fd_path = value
            return o["reader_option"](self_, key, value)

        def reader_options(self_, **options):
            for k, v in options.items():
                if str(k).lower() == "path":
                    self_._fd_path = v
            return o["reader_options"](self_, **options)
        DataFrameReader.option = reader_option
        DataFrameReader.options = reader_options

        def load(self_, path=None, format=None, schema=None, **options):  # noqa: A002
            # A path given as an option (option("path", ...), or path= here) is read like one passed to load().
            seen = path if path is not None else next(
                (v for k, v in options.items() if str(k).lower() == "path"), None) or getattr(self_, "_fd_path", None)
            if seen is not None and not isinstance(seen, (list, tuple)):
                where = me.lakehouse_path(seen)
                if where and where[0] == "table":
                    return me.frame(where[1], where[2])
                me.file_read(seen)
            return o["load"](self_, path, format, schema, **options)
        DataFrameReader.load = load

        for fmt in ("parquet", "csv", "json", "orc", "text"):
            original = getattr(DataFrameReader, fmt)

            def read_files(self_, *paths, _original=original, _fmt=fmt, **options):
                # parquet takes any number of paths; the others take one (or a list) and then options.
                for p in paths if _fmt == "parquet" else paths[:1]:
                    for one in (p if isinstance(p, (list, tuple)) else [p]):
                        where = me.lakehouse_path(one)
                        if where and where[0] == "table":
                            raise ProofRefused(f"{one} is a table's folder; in a proof, read it with spark.read.table.")
                        me.file_read(one)
                return _original(self_, *paths, **options)
            setattr(DataFrameReader, fmt, read_files)

        def mode(self_, saveMode):  # noqa: N803
            self_._fd_mode = saveMode
            return o["mode"](self_, saveMode)
        DataFrameWriter.mode = mode

        def no_path(options):
            if any(str(k).lower() == "path" for k in options):
                raise ProofRefused(f"{_REFUSED}; it doesn't take a write's path as an option. Pass the path to "
                                   "save(), or write the table by name.")
        o["option"], o["options"] = DataFrameWriter.option, DataFrameWriter.options

        def writer_option(self_, key, value):
            no_path({key: value})
            return o["option"](self_, key, value)

        def writer_options(self_, **options):
            no_path(options)
            return o["options"](self_, **options)
        DataFrameWriter.option = writer_option
        DataFrameWriter.options = writer_options

        def save_as_table(self_, name, format=None, mode=None, partitionBy=None, **options):  # noqa: A002, N803
            no_path(options)
            return me.write_table(self_, *me.split(name), format, mode, partitionBy, options)
        DataFrameWriter.saveAsTable = save_as_table

        def save(self_, path=None, format=None, mode=None, partitionBy=None, **options):  # noqa: A002, N803
            no_path(options)
            if path is None:
                raise ProofRefused(f"{_REFUSED}; pass the path to save(), so the proof can see where it writes.")
            where = me.lakehouse_path(path)
            if where and where[0] == "table":
                return me.write_table(self_, where[1], where[2], format, mode, partitionBy, options)
            if where and where[0] in ("files", "onelake"):
                raise ProofRefused(f"{_REFUSED}; it doesn't write {path} in OneLake.")
            return o["save"](self_, path, format, mode, partitionBy, **options)
        DataFrameWriter.save = save

        for fmt in ("parquet", "csv", "json", "orc", "text"):
            original = getattr(DataFrameWriter, fmt)

            def write_files(self_, path, *args, _original=original, **kwargs):
                where = me.lakehouse_path(path)
                if where and where[0] != "sandbox":
                    raise ProofRefused(f"{_REFUSED}; it doesn't write {path} in OneLake.")
                return _original(self_, path, *args, **kwargs)
            setattr(DataFrameWriter, fmt, write_files)

        def refuse(what):
            def refused(*args, **kwargs):
                raise ProofRefused(f"{_REFUSED}; {what}")
            return refused
        DataFrameWriter.insertInto = refuse("insertInto isn't supported in a proof. Use saveAsTable(mode=\"append\") "
                                            "or INSERT INTO.")
        DataFrame.writeTo = refuse("writeTo isn't supported in a proof. Use saveAsTable.")
        Catalog.createTable = refuse("spark.catalog.createTable isn't supported in a proof. Use saveAsTable.")
        Catalog.createExternalTable = refuse("spark.catalog.createExternalTable isn't supported in a proof.")
        from pyspark.sql.streaming import DataStreamWriter
        DataStreamWriter.start = refuse("a stream's writes can't be pinned or compared in a proof.")
        DataStreamWriter.toTable = refuse("a stream's writes can't be pinned or compared in a proof.")

        def table_exists(self_, tableName, dbName=None):  # noqa: N803
            names = [dbName, tableName] if dbName else [p.strip("`") for p in re.findall(r"`(?:[^`]|``)+`|[^.]+", tableName)]
            if len(names) == 1 and me.local(names[0]):
                return o["tableExists"](self_, tableName, dbName)
            try:
                return me.exists(*me.parts(names))
            except ProofRefused:
                return o["tableExists"](self_, tableName, dbName)
        Catalog.tableExists = table_exists

        for view in ("createOrReplaceTempView", "createTempView", "createGlobalTempView", "createOrReplaceGlobalTempView"):
            original = getattr(DataFrame, view, None)
            if original is None:
                continue

            def make_view(self_, name, _original=original):
                me.views.add(str(name).lower())
                return _original(self_, name)
            setattr(DataFrame, view, make_view)

        try:
            from delta.tables import DeltaTable
        except ImportError:  # no Delta Python API: nothing to patch
            return self
        o["forPath"] = DeltaTable.forPath.__func__

        def for_name(cls, sparkSession, tableOrViewName):  # noqa: N803
            return me.open_delta(cls, sparkSession, *me.split(tableOrViewName))

        def for_path(cls, sparkSession, path, *args, **kwargs):  # noqa: N803
            where = me.lakehouse_path(path)
            if where and where[0] == "table":
                return me.open_delta(cls, sparkSession, where[1], where[2])
            if where and where[0] != "sandbox":
                raise ProofRefused(f"{_REFUSED}; {path} isn't a table of the attached lakehouse.")
            return o["forPath"](cls, sparkSession, path, *args, **kwargs)
        DeltaTable.forName = classmethod(for_name)
        DeltaTable.forPath = classmethod(for_path)
        for builder in ("create", "createIfNotExists", "createOrReplace", "replace"):
            if hasattr(DeltaTable, builder):
                setattr(DeltaTable, builder, classmethod(refuse(f"DeltaTable.{builder} isn't supported in a proof. "
                                                                "Use CREATE TABLE or saveAsTable.")))
        return self

    def open_delta(self, cls, spark, schema, table):
        """DeltaTable.forName in a proof: this side's copy, so a change through it stays there."""
        if not self.exists(schema, table):
            raise ValueError(f"Table {schema}.{table} doesn't exist.")
        key = self.key(schema, table)
        if key not in self.proof.log["writes"]:
            self.opened.add(key)
        return self.orig["forPath"](cls, spark, self.target(schema, table, "open"))

    def write_table(self, writer, schema, table, format, mode, partitionBy, options):  # noqa: A002, N803
        """saveAsTable (or a save to a Tables/ folder) into this side's copy, with Spark's modes."""
        if format and str(format).lower() != "delta":
            raise ValueError("Lakehouse tables are Delta tables. Leave out format, or use format(\"delta\").")
        how = str(mode or getattr(writer, "_fd_mode", None) or "errorifexists").lower()
        how = {"error": "errorifexists", "default": "errorifexists"}.get(how, how)
        exists = self.exists(schema, table)
        if how == "errorifexists" and exists:
            raise ValueError(f"Table {schema}.{table} already exists. Use mode(\"overwrite\") or mode(\"append\").")
        if how == "ignore" and exists:
            return None
        if how == "append" and not exists:
            how = "overwrite"
        uri = self.target(schema, table, "append" if how == "append" else "overwrite", seed=how == "append")
        if how != "append":
            options = dict(options, overwriteSchema="true")
        return self.orig["save"](writer, uri, "delta", "append" if how == "append" else "overwrite",
                                 partitionBy, **options)


def bind(proof, spark, name, schemas):
    """Make ``spark`` run as a proof from here on; returns the ProofSpark."""
    return ProofSpark(proof, spark, name, schemas).install()
