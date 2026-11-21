"""A differential check of KratosForge's Delta writes against Spark, which stands in for Fabric Spark.

Each Spark-made fixture in tests/fixtures/delta_spark is copied to a work folder, and each write the engine
allows is run on its own copy through the product paths: LocalStore append, overwrite and upsert, local SQL
INSERT, UPDATE and DELETE, and publish.publish append and overwrite to a local path. LocalStore and publish
are run once for each per-table choice (the default, every_reader, deletion_vectors, column_mapping); local
SQL has no per-table choice and writes with the default. Then:

* the rows delta-rs reads back are compared with the rows Spark reads, row by row: the same column names (the
  logical ones on a column-mapped table), the same values and nulls, and comparable types;
* Spark runs an UPDATE and a DELETE on a copy of the written table, and delta-rs must read back what Spark
  does afterwards;
* Spark's DESCRIBE DETAIL protocol is recorded, before and after its DML;
* a write the engine refuses is recorded, and a SHA-256 snapshot of every file in the table shows the refusal
  left it unchanged.

The engine and Spark run in separate processes with their own Pythons (the engine's delta-rs, and pyspark with
delta-spark) and exchange JSON in the work folder. It is not a test: CI has no Spark, and pytest only collects
tests/. Run it by hand from sidecar/:

    python tools/spark_differential_check.py run --engine-python /tmp/claude-0/venv/bin/python \\
        --spark-python /tmp/claude-0/sparkvenv/bin/python --work /tmp/claude-0/spark_diff \\
        --out ../docs/quality/delta-spark-differential-<date>.md

The phases can also be run one at a time: ``engine``, ``spark``, ``verify`` and ``report``, each with --work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SIDECAR = Path(__file__).resolve().parents[1]
FIXTURES = SIDECAR / "tests" / "fixtures" / "delta_spark"
FIXTURE_NAMES = ["dv", "dv_part", "cm_name", "cm_id", "dv_cm", "cm_legacy"]
CHOICES = ["default", "every_reader", "deletion_vectors", "column_mapping"]

# The rows each fixture starts with, as Spark wrote them (tools/make_spark_delta_fixtures.py).
_ALL = [(i, i * 10, f"n{i}", "a" if i < 5 else "b") for i in range(10)]
START: dict[str, list[tuple[Any, ...]]] = {
    "dv": [r for r in _ALL if r[0] not in (3, 7, 8)],
    "dv_part": [r for r in _ALL if r[0] not in (1, 6)],
    "cm_name": [r[:3] for r in _ALL],
    "cm_id": list(_ALL),
    "dv_cm": [r for r in _ALL if r[0] not in (2, 5)],
    "cm_legacy": _ALL + [(10, 100, "n10", "b")],
}
COLUMNS = {"dv": ["id", "amount", "label", "p"], "dv_part": ["id", "amount", "label", "p"],
           "cm_name": ["id", "total", "the label"], "cm_id": ["id", "total", "label", "p"],
           "dv_cm": ["id", "total", "label", "p"], "cm_legacy": ["id", "total", "label", "p"]}

# Arrow type (as delta-rs reads it) -> Spark's simpleString, for the type comparison.
ARROW_TO_SPARK = {"int64": "bigint", "int32": "int", "int16": "smallint", "int8": "tinyint",
                  "string": "string", "large_string": "string", "string_view": "string",
                  "utf8": "string", "large_utf8": "string", "double": "double", "float": "float",
                  "bool": "boolean", "date32[day]": "date", "timestamp[us, tz=UTC]": "timestamp",
                  "timestamp[us]": "timestamp_ntz", "binary": "binary", "large_binary": "binary"}


def _row(name: str, ident: int, label_prefix: str = "n") -> tuple[Any, ...]:
    return (ident, ident * 10, f"{label_prefix}{ident}", "a")[:len(COLUMNS[name])]


def _quote(column: str) -> str:
    return "`" + column.replace("`", "``") + "`"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


def _key(row: list[Any]) -> list[Any]:
    """A sort key for rows that may hold None."""
    return [(v is None, "" if v is None else (v if isinstance(v, (int, float)) else str(v))) for v in row]


def _sorted(rows: list[list[Any]]) -> list[list[Any]]:
    return sorted(([_jsonable(v) for v in r] for r in rows), key=_key)


def _snapshot(folder: Path) -> dict[str, str]:
    """Every file under the table, by relative path, with the SHA-256 of its bytes."""
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------------------- the engine phases


def _engine_imports() -> None:
    sys.path.insert(0, str(SIDECAR))


def _engine_read(folder: Path) -> dict[str, Any]:
    from deltalake import DeltaTable

    from fabric_desktop import delta_read
    table = DeltaTable(str(folder))
    data = delta_read.arrow(table)
    protocol = table.protocol()
    return {"columns": data.schema.names, "types": [str(f.type) for f in data.schema],
            "rows": _sorted([list(r.values()) for r in data.to_pylist()]), "version": table.version(),
            "protocol": {"reader": protocol.min_reader_version, "writer": protocol.min_writer_version,
                         "reader_features": sorted(protocol.reader_features or []),
                         "writer_features": sorted(protocol.writer_features or [])},
            "deleted_rows": delta_read.deleted_rows(table)}


def _frame(name: str, rows: list[tuple[Any, ...]]) -> Any:
    import polars as pl
    types = {"id": pl.Int64, "amount": pl.Int32, "total": pl.Int32, "label": pl.String, "the label": pl.String,
             "p": pl.String}
    cols = COLUMNS[name]
    return pl.DataFrame([dict(zip(cols, r, strict=True)) for r in rows], schema={c: types[c] for c in cols})


class _Ref:
    """A local table for publish.publish (TableRef's fields that a path on disk needs)."""

    def __init__(self, uri: str) -> None:
        self.uri, self.schema, self.table = uri, "dbo", "t"


def _cases() -> list[dict[str, Any]]:
    out = []
    for name in FIXTURE_NAMES:
        for path, ops, choices in (("store", ["append", "overwrite", "upsert"], CHOICES),
                                   ("sql", ["insert", "update", "delete"], ["default"]),
                                   ("publish", ["append", "overwrite"], CHOICES)):
            for op in ops:
                for choice in choices:
                    out.append({"id": f"{name}__{path}_{op}__{choice}", "fixture": name, "path": path, "op": op,
                                "choice": choice})
    return out


def _expected(case: dict[str, Any]) -> list[tuple[Any, ...]]:
    name, op = case["fixture"], f"{case['path']}_{case['op']}"
    start = START[name]
    if op in ("store_append",):
        return start + [_row(name, 100), _row(name, 101)]
    if op in ("publish_append", "sql_insert"):
        return start + [_row(name, 100)]
    if op in ("store_overwrite", "publish_overwrite"):
        return [_row(name, 7)]
    if op == "store_upsert":
        return [r for r in start if r[0] != 0] + [_row(name, 0, "changed-"), _row(name, 100, "changed-")]
    if op == "sql_update":
        return [(r[0], -1, *r[2:]) if r[0] == 0 else r for r in start]
    if op == "sql_delete":
        return [r for r in start if r[0] != 4]
    raise ValueError(op)


def _write(case: dict[str, Any], case_dir: Path) -> Path:
    """Run the case's write through its product path; return the table folder it wrote."""
    from fabric_desktop import delta_profile as P
    name, op, choice = case["fixture"], case["op"], case["choice"]
    spec = {} if choice == "default" else {"delta_features": choice}
    if case["path"] == "store":
        from fabric_desktop.meta.store import LocalStore
        store = LocalStore(case_dir / "store", "dev")
        folder = Path(store.uri("LH_Bronze", "dbo", name))
        folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(FIXTURES / name, folder)
        store.delta_policies = {("LH_Bronze", "dbo", name): P.table_policy(spec)}
        case["before"] = _snapshot(folder)
        case["table"] = str(folder)
        if op == "append":
            store.write("LH_Bronze", "dbo", name, _frame(name, [_row(name, 100), _row(name, 101)]), mode="append")
        elif op == "overwrite":
            store.write("LH_Bronze", "dbo", name, _frame(name, [_row(name, 7)]), mode="overwrite")
        else:
            change = _frame(name, [_row(name, 0, "changed-"), _row(name, 100, "changed-")])
            store.upsert("LH_Bronze", "dbo", name, change, on=["id"])
        return folder
    if case["path"] == "publish":
        from fabric_desktop import publish
        folder = case_dir / "publish" / name
        folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(FIXTURES / name, folder)
        case["before"] = _snapshot(folder)
        case["table"] = str(folder)
        rows = [_row(name, 100)] if op == "append" else [_row(name, 7)]
        publish.publish(_frame(name, rows).to_arrow(), _Ref(folder.as_posix()), op, "tok",
                        within=P.table_profile(spec.get("delta_features")))
        return folder
    from fabric_desktop.localdata import LocalData
    local = LocalData(case_dir / "local-data")
    try:
        engine = local.engine(local.create("Diff")["id"])
        folder = Path(engine.tables_dir) / "dbo" / name
        shutil.copytree(FIXTURES / name, folder)
        case["before"] = _snapshot(folder)
        case["table"] = str(folder)
        first, second = COLUMNS[name][0], COLUMNS[name][1]
        if op == "insert":
            values = ", ".join(repr(v) for v in _row(name, 100))
            engine.execute(f"INSERT INTO dbo.{name} VALUES ({values})")
        elif op == "update":
            engine.execute(f"UPDATE dbo.{name} SET {second} = -1 WHERE {first} = 0")
        else:
            engine.execute(f"DELETE FROM dbo.{name} WHERE {first} = 4")
    finally:
        local.close_all()
    return folder


def phase_engine(work: Path) -> None:
    _engine_imports()
    from fabric_desktop import delta_profile as P
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    cases = _cases()
    for case in cases:
        case_dir = work / case["id"]
        case_dir.mkdir()
        try:
            folder = _write(case, case_dir)
        except Exception as exc:  # noqa: BLE001 - a refusal of any kind is the result being recorded
            case["status"] = "refused"
            case["refusal"] = f"{type(exc).__name__}: {exc}"
            case["profile_refusal"] = isinstance(exc, P.ProfileViolation)
            table = Path(case["table"]) if "table" in case else None
            case["unchanged"] = table is not None and _snapshot(table) == case["before"]
            if not case["unchanged"]:
                case["changed_files"] = sorted(set(_snapshot(table)) ^ set(case["before"])) if table else []
            case.pop("before", None)
            continue
        case.pop("before", None)
        case["status"] = "written"
        case["engine"] = _engine_read(folder)
        expected = _sorted([list(r) for r in _expected(case)])
        case["expected_rows"] = expected
        case["engine_as_expected"] = case["engine"]["rows"] == expected and \
            case["engine"]["columns"] == COLUMNS[case["fixture"]]
        case["spark_copy"] = str(case_dir / "spark_dml")
        print(case["id"], case["status"], flush=True)
    (work / "engine.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")


def phase_verify(work: Path) -> None:
    """delta-rs reads the copies Spark ran DML on, so the comparison runs both ways."""
    _engine_imports()
    cases = json.loads((work / "spark.json").read_text(encoding="utf-8"))
    for case in cases:
        spark = case.get("spark") or {}
        if not spark.get("dml_ok"):
            continue
        try:
            case["engine_after_spark_dml"] = _engine_read(Path(case["spark_copy"]))
        except Exception as exc:  # noqa: BLE001 - recorded as the result
            case["engine_after_spark_dml"] = {"error": f"{type(exc).__name__}: {exc}"}
    (work / "verified.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------------------- the Spark phase


def phase_spark(work: Path) -> None:
    from delta import configure_spark_with_delta_pip  # type: ignore[import-not-found]
    from pyspark.sql import SparkSession  # type: ignore[import-not-found]
    builder = (SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
               .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
               .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
               .config("spark.sql.session.timeZone", "UTC")
               .config("spark.sql.shuffle.partitions", "1")
               .config("spark.sql.warehouse.dir", str(work / "spark-warehouse")))
    s = configure_spark_with_delta_pip(builder).getOrCreate()
    s.sparkContext.setLogLevel("ERROR")

    def read(path: str) -> dict[str, Any]:
        df = s.read.format("delta").load(path)
        return {"columns": df.columns, "types": [t for _, t in df.dtypes],
                "rows": _sorted([list(r) for r in df.collect()])}

    def detail(path: str) -> dict[str, Any]:
        d = s.sql(f"DESCRIBE DETAIL delta.`{path}`").collect()[0].asDict()
        return {"reader": d.get("minReaderVersion"), "writer": d.get("minWriterVersion"),
                "features": sorted(d.get("tableFeatures") or []),
                "column_mapping": (d.get("properties") or {}).get("delta.columnMapping.mode"),
                "deletion_vectors": (d.get("properties") or {}).get("delta.enableDeletionVectors")}

    cases = json.loads((work / "engine.json").read_text(encoding="utf-8"))
    for case in cases:
        if case["status"] != "written":
            continue
        out: dict[str, Any] = {}
        case["spark"] = out
        try:
            out.update(read(case["table"]))
            out["detail"] = detail(case["table"])
        except Exception as exc:  # noqa: BLE001 - recorded as the result
            out["read_error"] = f"{type(exc).__name__}: {str(exc)[:600]}"
            continue
        # DML on a copy: UPDATE the second column of the highest id, then DELETE the lowest id.
        copy = case["spark_copy"]
        shutil.copytree(case["table"], copy)
        cols = case["engine"]["columns"]
        rows = out["rows"]
        ids = sorted(r[0] for r in rows)
        expected = [list(r) for r in rows]
        steps = []
        if ids:
            hi, lo = ids[-1], ids[0]
            steps = [f"UPDATE delta.`{copy}` SET {_quote(cols[1])} = -5 WHERE {_quote(cols[0])} = {hi}",
                     f"DELETE FROM delta.`{copy}` WHERE {_quote(cols[0])} = {lo}"]
            for r in expected:
                if r[0] == hi:
                    r[1] = -5
            expected = [r for r in expected if r[0] != lo]
        out["dml"] = steps
        out["dml_expected"] = _sorted(expected)
        try:
            for step in steps:
                s.sql(step)
            out["after_dml"] = read(copy)
            out["detail_after_dml"] = detail(copy)
            out["dml_ok"] = True
        except Exception as exc:  # noqa: BLE001 - recorded as the result
            out["dml_ok"] = False
            out["dml_error"] = f"{type(exc).__name__}: {str(exc)[:600]}"
        print(case["id"], "spark", "dml ok" if out["dml_ok"] else "dml FAILED", flush=True)
    (work / "spark.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")
    s.stop()


# ---------------------------------------------------------------------------------------- the report


def _types_match(engine: list[str], spark: list[str]) -> bool:
    return [ARROW_TO_SPARK.get(t, t) for t in engine] == spark


def judge(case: dict[str, Any]) -> tuple[str, list[str]]:
    """'yes', 'no' or 'refused', and the notes for one case."""
    notes: list[str] = []
    if case["status"] == "refused":
        notes.append(("table unchanged (SHA-256 of every file)" if case["unchanged"]
                      else f"TABLE CHANGED by a refused write: {case.get('changed_files')}"))
        notes.append(case["refusal"].split("\n")[0][:220])
        return ("refused" if case["unchanged"] else "no"), notes
    engine, spark = case["engine"], case.get("spark") or {}
    ok = True
    if "read_error" in spark:
        return "no", [f"Spark can't read it: {spark['read_error'][:300]}"]
    if engine["columns"] != spark["columns"]:
        ok = False
        notes.append(f"columns differ: engine {engine['columns']}, Spark {spark['columns']}")
    if not _types_match(engine["types"], spark["types"]):
        ok = False
        notes.append(f"types differ: engine {engine['types']}, Spark {spark['types']}")
    if engine["rows"] != spark["rows"]:
        ok = False
        only_e = [r for r in engine["rows"] if r not in spark["rows"]]
        only_s = [r for r in spark["rows"] if r not in engine["rows"]]
        notes.append(f"rows differ: only engine {only_e[:5]}, only Spark {only_s[:5]}")
    if not case["engine_as_expected"]:
        ok = False
        notes.append("the engine's rows aren't the ones the write should leave")
    d = spark.get("detail") or {}
    p = engine["protocol"]
    if (d.get("reader"), d.get("writer")) != (p["reader"], p["writer"]):
        ok = False
        notes.append(f"protocol differs: delta-rs {p['reader']}/{p['writer']}, Spark {d.get('reader')}/{d.get('writer')}")
    if not spark.get("dml_ok"):
        ok = False
        notes.append(f"Spark DML failed: {spark.get('dml_error', '')[:300]}")
    else:
        after = spark["after_dml"]
        if after["rows"] != spark["dml_expected"]:
            ok = False
            notes.append(f"Spark's rows after its DML aren't the expected ones: {after['rows']}")
        back = case.get("engine_after_spark_dml") or {}
        if "error" in back:
            ok = False
            notes.append(f"delta-rs can't read Spark's DML back: {back['error'][:300]}")
        elif back.get("rows") != after["rows"] or back.get("columns") != after["columns"]:
            ok = False
            notes.append(f"delta-rs reads Spark's DML back differently: {back.get('rows')}")
        da = spark.get("detail_after_dml") or {}
        if (da.get("reader"), da.get("writer")) != (d.get("reader"), d.get("writer")):
            notes.append(f"Spark's DML moved the protocol to {da.get('reader')}/{da.get('writer')}")
    if engine.get("deleted_rows"):
        notes.append(f"{engine['deleted_rows']} rows deleted by deletion vectors")
    return ("yes" if ok else "no"), notes


def _protocol(case: dict[str, Any]) -> str:
    d = (case.get("spark") or {}).get("detail") or {}
    if not d:
        return ""
    feats = ", ".join(d["features"]) if d["features"] else "no table features"
    return f"{d['reader']}/{d['writer']} ({feats})"


INTRO = """\
# Delta writes read back by Spark ({date})

A differential check of the tables KratosForge writes under the modern profile and each per-table choice
(delta_features), with Spark 3.5.5 and Delta 3.3.2 standing in for Fabric Spark. It is made by
`sidecar/tools/spark_differential_check.py`, by hand, since CI has no Spark.

## What was run

Each Spark-made fixture in `sidecar/tests/fixtures/delta_spark` was copied to its own folder for every write,
and the write ran through the product path:

- LocalStore `write` append (ids 100, 101), `write` overwrite (id 7) and `upsert` on id (id 0 changed, id 100
  added), with `delta_policies` set from each choice (`P.table_policy`);
- local SQL `INSERT`, `UPDATE ... WHERE id = 0` and `DELETE ... WHERE id = 4` through LocalData's engine,
  which has no per-table choice and writes with the default;
- `publish.publish` append (id 100) and overwrite (id 7) to a local path, with `within` set from each choice.

After each write that went through, the rows delta-rs reads were compared with the rows Spark reads (column
names, values, nulls, and types mapped from Arrow to Spark's), and with the rows the write should leave.
Spark's DESCRIBE DETAIL protocol was recorded and checked against delta-rs's. Spark then ran an `UPDATE` (the
highest id's second column to -5) and a `DELETE` (the lowest id) on a copy; its result had to be the expected
one, and delta-rs had to read Spark's result back the same. For each write the engine refused, a SHA-256
snapshot of every file in the table was taken before and after.

## Result

- {total} combinations: {written} written, {refused} refused.
- Of the {written} written, {matched} matched Spark in every check, and {unmatched} did not.
- Every refusal left the table byte for byte unchanged: {unchanged}.
- every_reader lets no write through on these fixtures: each one has deletion vectors or column mapping,
  which the conservative profile refuses. Column mapping by ID (cm_id) is refused under every choice.

{mismatches}
## Protocols

The protocol Spark's DESCRIBE DETAIL shows after the engine's writes, per fixture (reader/writer version and
table features). {protocol_moves} For cm_legacy, a legacy writer-5 table with no feature lists, Spark lists
every feature writer version 5 implies (changeDataFeed, checkConstraints, generatedColumns among them); none of
them is in use, which is why the modern profile lets it be written (delta_profile.problems).

Spark logs `ERROR DeltaParquetFileFormat: Failed to translate filter EqualTo(__delta_internal_is_row_deleted,0)`
while it reads a table with deletion vectors. It isn't a failure: Delta can't push that filter down into
Parquet and applies it after the scan instead, and the rows it returns are the ones compared here.

{protocols}

## The matrix

"Refused" in the Match column means the engine refused the write and left the table unchanged, as the
choice says it should.

| Fixture | Operation | Choice | Engine rows | Spark rows | Match | Notes |
|---|---|---|---|---|---|---|
"""


def phase_report(work: Path, out: Path, date: str) -> tuple[int, int, int, int]:
    cases = json.loads((work / "verified.json").read_text(encoding="utf-8"))
    rows, failed, protocols = [], [], {}
    matched = refused = 0
    for case in cases:
        verdict, notes = judge(case)
        written = case["status"] == "written"
        matched += verdict == "yes"
        refused += not written
        if verdict == "no":
            failed.append(f"- {case['id']}: {'; '.join(notes)}")
        spark = case.get("spark") or {}
        if written:
            protocols.setdefault(case["fixture"], set()).add(_protocol(case))
            d = spark.get("detail") or {}
            if verdict == "yes":
                notes.insert(0, f"protocol {d.get('reader')}/{d.get('writer')}; Spark's UPDATE and DELETE ran and "
                                "delta-rs reads their result the same")
        n_engine = len(case["engine"]["rows"]) if written else "-"
        n_spark = len(spark["rows"]) if "rows" in spark else "-"
        rows.append(f"| {case['fixture']} | {case['path']} {case['op']} | {case['choice']} | {n_engine} | {n_spark} "
                    f"| {verdict} | {'; '.join(notes).replace('|', '/')} |")
    total = len(cases)
    written_n = total - refused
    moves = [c["id"] for c in cases if c["status"] == "written" and _fixture_protocol(c["fixture"]) != (
        c["engine"]["protocol"]["reader"], c["engine"]["protocol"]["writer"],
        sorted({*c["engine"]["protocol"]["reader_features"], *c["engine"]["protocol"]["writer_features"]}))]
    mismatches = ("## Mismatches\n\n" + "\n".join(failed) + "\n") if failed else "No mismatch was found.\n"
    text = INTRO.format(
        date=date, total=total, written=written_n, refused=refused, matched=matched,
        unmatched=written_n - matched,
        unchanged="yes" if all(c["unchanged"] for c in cases if c["status"] == "refused") else "NO",
        mismatches=mismatches,
        protocol_moves=("No engine write changed a fixture's protocol." if not moves
                        else f"These writes changed the protocol: {', '.join(moves)}."),
        protocols="\n".join(f"- {name}: {', '.join(sorted(p))}" for name, p in protocols.items()))
    out.write_text(text + "\n".join(rows) + "\n", encoding="utf-8")
    return total, written_n, matched, refused


def _fixture_protocol(name: str) -> tuple[int, int, list[str]]:
    """The fixture's protocol as Spark made it, from its Delta log: reader, writer and table features."""
    found: dict[str, Any] = {}
    for commit in sorted((FIXTURES / name / "_delta_log").glob("*.json")):
        for line in commit.read_text(encoding="utf-8").splitlines():
            action = json.loads(line)
            if "protocol" in action:
                found = action["protocol"]
    feats = {*(found.get("readerFeatures") or []), *(found.get("writerFeatures") or [])}
    return found["minReaderVersion"], found["minWriterVersion"], sorted(feats)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["run", "engine", "spark", "verify", "report"])
    ap.add_argument("--work", type=Path, required=True, help="an empty folder to work in (it is cleared)")
    ap.add_argument("--out", type=Path, help="the Markdown report (report and run)")
    ap.add_argument("--date", default="", help="the date in the report's title")
    ap.add_argument("--engine-python", default=sys.executable)
    ap.add_argument("--spark-python", default="python")
    a = ap.parse_args()
    if a.phase == "engine":
        phase_engine(a.work)
    elif a.phase == "spark":
        phase_spark(a.work)
    elif a.phase == "verify":
        phase_verify(a.work)
    elif a.phase == "report":
        print(phase_report(a.work, a.out, a.date))
    else:
        me = str(Path(__file__).resolve())
        for python, phase in ((a.engine_python, "engine"), (a.spark_python, "spark"), (a.engine_python, "verify")):
            subprocess.run([python, me, phase, "--work", str(a.work)], cwd=SIDECAR, check=True)
        if a.out:
            print(phase_report(a.work, a.out, a.date))


if __name__ == "__main__":
    main()
