"""Cross-platform verification cases, run on this PC (PAR-002, first increment).

A case is a small dataset, one statement per SQL dialect, and the answer each platform documents for
it: the result's column names and types, and its rows. ``parity_cases.json`` holds the seeded cases.
``run`` runs each case's statement on this PC through the engines KratosForge already has (DuckDB for
DuckDB, Spark, Databricks and Snowflake SQL, the local data engine's T-SQL translation for T-SQL),
compares the answer with the documented one under verify.py's rules, and gives a run record per case
and dialect. Nothing here opens a network connection or signs in anywhere.

Outcomes:

    match        the answer on this PC is the documented one
    unconfirmed  it agrees with the case's expectation, but the platform's documentation doesn't state
                 that answer, so it isn't a match
    differs      another schema or other values; the record names each difference
    refused      the local engine refused the statement by name (a translation it doesn't make)
    error        the statement failed on this PC

Only the normalizations a case names apply, from the closed list ``NORMALIZATIONS``. Anything else
(a decimal's scale, a NULL where a value was expected, row order) is a difference.

A record says how it was made: ``evidence_level`` is "local" for these runs ("fake" for a run against a
fake API, "live" for a run on the platform). ``can_verify`` is the only gate a parity cell may use to
call an area Verified, and it accepts only a matching live run on a platform.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pyarrow as pa

SCHEMA = "parity-run@1"
CASES_FILE = Path(__file__).with_name("parity_cases.json")
DIALECTS = ("spark", "databricks", "snowflake", "tsql", "duckdb")
EVIDENCE_LEVELS = ("local", "fake", "live")
NORMALIZATIONS = {
    "row_order": "Rows are compared in any order.",
    "column_name_case": "Column names are compared ignoring upper and lower case.",
    "integer_width": "Whole-number types (TINYINT to HUGEINT, and DECIMAL(p,0)) count as one type.",
    "decimal_scale_display": "Two DECIMAL types of another precision or scale count as one type. Values still "
                             "compare exactly, so 0.33 and 0.333333 differ.",
}
_CASE_KEYS = {"id", "title", "dataset", "dialects", "note"}
_DATASET_KEYS = {"table", "columns", "rows"}
_EXPECT_KEYS = {"statement", "schema", "rows", "normalize", "unconfirmed", "source", "note"}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ID = re.compile(r"[a-z0-9_]+")
_DECIMAL = re.compile(r"decimal\((\d{1,2}),(\d{1,2})\)")
_TYPES = {"boolean": (pa.bool_(), "BOOLEAN"), "tinyint": (pa.int8(), "TINYINT"), "smallint": (pa.int16(), "SMALLINT"),
          "int": (pa.int32(), "INTEGER"), "bigint": (pa.int64(), "BIGINT"), "hugeint": (pa.decimal128(38, 0), "HUGEINT"),
          "float": (pa.float32(), "FLOAT"), "double": (pa.float64(), "DOUBLE"), "string": (pa.string(), "VARCHAR"),
          "date": (pa.date32(), "DATE"), "timestamp": (pa.timestamp("us"), "TIMESTAMP"),
          "timestamp_tz": (pa.timestamp("us", tz="UTC"), "TIMESTAMPTZ")}
_WHOLE = {"tinyint", "smallint", "int", "bigint", "hugeint"}
_REFUSALS = {"Unsupported", "OutsideSession"}


class CaseError(ValueError):
    """A case file that doesn't follow the format; nothing in it runs."""


# ---------------------------------------------------------------- cases


def _type(name: Any, where: str) -> tuple[pa.DataType, str]:
    if isinstance(name, str) and name in _TYPES:
        return _TYPES[name]
    m = _DECIMAL.fullmatch(name) if isinstance(name, str) else None
    if m and 1 <= int(m[1]) <= 38 and int(m[2]) <= int(m[1]):
        return pa.decimal128(int(m[1]), int(m[2])), f"DECIMAL({m[1]},{m[2]})"
    raise CaseError(f"{where}: {name!r} isn't a type. Types: {', '.join(_TYPES)}, decimal(p,s).")


def _value(v: Any, kind: str, where: str) -> Any:
    """``v`` from the case file as the Python value of ``kind``; refuses a value of another kind."""
    if v is None:
        return None
    try:
        if kind == "boolean" and isinstance(v, bool):
            return v
        if kind in _WHOLE and isinstance(v, int) and not isinstance(v, bool):
            return Decimal(v) if kind == "hugeint" else v
        if kind in ("double", "float") and isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if kind == "string" and isinstance(v, str):
            return v
        if kind.startswith("decimal") and isinstance(v, str):
            return Decimal(v)
        if kind == "date" and isinstance(v, str):
            return dt.date.fromisoformat(v)
        if kind in ("timestamp", "timestamp_tz") and isinstance(v, str):
            t = dt.datetime.fromisoformat(v)
            return t.replace(tzinfo=dt.timezone.utc) if kind == "timestamp_tz" else t
    except (ValueError, InvalidOperation):
        pass
    raise CaseError(f"{where}: {v!r} isn't a {kind} value (decimals, dates and timestamps are written as text).")


def _columns(given: Any, where: str) -> list[tuple[str, str]]:
    if not isinstance(given, list) or not given:
        raise CaseError(f"{where} is a list of [name, type] pairs.")
    out = []
    for col in given:
        if not (isinstance(col, list) and len(col) == 2 and isinstance(col[0], str) and _NAME.fullmatch(col[0])):
            raise CaseError(f"{where}: {col!r} isn't a [name, type] pair with a plain column name.")
        _type(col[1], where)
        out.append((col[0], col[1]))
    return out


def _rows(given: Any, columns: list[tuple[str, str]], where: str) -> list[list[Any]]:
    if not isinstance(given, list) or not all(isinstance(r, list) and len(r) == len(columns) for r in given):
        raise CaseError(f"{where}: rows are lists of {len(columns)} values.")
    return [[_value(v, k, where) for v, (_n, k) in zip(r, columns, strict=True)] for r in given]


def _keys(obj: Any, allowed: set[str], required: set[str], where: str) -> None:
    if not isinstance(obj, dict):
        raise CaseError(f"{where} is an object.")
    unknown, missing = set(obj) - allowed, required - set(obj)
    if unknown:
        raise CaseError(f"{where}: unknown key {', '.join(sorted(unknown))}. Keys: {', '.join(sorted(allowed))}.")
    if missing:
        raise CaseError(f"{where}: {', '.join(sorted(missing))} missing.")


def validate(case: Any) -> dict[str, Any]:
    """The case with its values read, or CaseError naming the first thing wrong."""
    _keys(case, _CASE_KEYS, {"id", "title", "dataset", "dialects"}, "A case")
    cid = case["id"]
    if not (isinstance(cid, str) and _ID.fullmatch(cid)):
        raise CaseError(f"A case id is lower-case letters, digits and _, not {cid!r}.")
    ds = case["dataset"]
    _keys(ds, _DATASET_KEYS, _DATASET_KEYS, f"{cid}: dataset")
    if not (isinstance(ds["table"], str) and _NAME.fullmatch(ds["table"])):
        raise CaseError(f"{cid}: the dataset's table is a plain name.")
    cols = _columns(ds["columns"], f"{cid}: dataset columns")
    out = {"id": cid, "title": str(case["title"]), "note": case.get("note", ""),
           "dataset": {"table": ds["table"], "columns": cols, "rows": _rows(ds["rows"], cols, f"{cid}: dataset rows")},
           "dialects": {}}
    if not isinstance(case["dialects"], dict) or not case["dialects"]:
        raise CaseError(f"{cid}: dialects names at least one of {', '.join(DIALECTS)}.")
    for dialect, ex in case["dialects"].items():
        where = f"{cid} ({dialect})"
        if dialect not in DIALECTS:
            raise CaseError(f"{cid}: unknown dialect {dialect!r}. Dialects: {', '.join(DIALECTS)}.")
        _keys(ex, _EXPECT_KEYS, {"statement", "schema", "rows", "source"}, where)
        norms = ex.get("normalize", [])
        if not isinstance(norms, list) or any(n not in NORMALIZATIONS for n in norms):
            raise CaseError(f"{where}: normalize names some of {', '.join(NORMALIZATIONS)}, not {norms!r}.")
        if not isinstance(ex.get("unconfirmed", False), bool):
            raise CaseError(f"{where}: unconfirmed is true or false.")
        if not (isinstance(ex["statement"], str) and ex["statement"].strip() and isinstance(ex["source"], str)
                and ex["source"].strip()):
            raise CaseError(f"{where}: statement and source are text.")
        schema = _columns(ex["schema"], f"{where} schema")
        out["dialects"][dialect] = {"statement": ex["statement"], "schema": schema,
                                    "rows": _rows(ex["rows"], schema, f"{where} rows"), "normalize": list(norms),
                                    "unconfirmed": ex.get("unconfirmed", False), "source": ex["source"],
                                    "note": ex.get("note", "")}
    return out


def load(path: Path | None = None) -> list[dict[str, Any]]:
    """Every case in the file, validated; ids are unique."""
    raw = json.loads((path or CASES_FILE).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"cases"} or not isinstance(raw["cases"], list):
        raise CaseError('A case file is {"cases": [...]}.')
    cases = [validate(c) for c in raw["cases"]]
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise CaseError(f"Case ids repeat: {sorted({i for i in ids if ids.count(i) > 1})}.")
    return cases


# ---------------------------------------------------------------- running on this PC


def _literal(v: Any, kind: str) -> str:
    if v is None:
        return "NULL"
    text = "TRUE" if v is True else "FALSE" if v is False else str(v)
    if isinstance(v, (str, dt.date)):
        text = "'" + str(v).replace("'", "''") + "'"
    return f"CAST({text} AS {_type(kind, '')[1]})"


def dataset_sql(dataset: dict[str, Any]) -> list[str]:
    """DuckDB statements that make the case's table with its rows."""
    cols = dataset["columns"]
    out = [f"CREATE TABLE {dataset['table']} ({', '.join(f'{n} {_type(k, n)[1]}' for n, k in cols)})"]
    if dataset["rows"]:
        values = ", ".join("(" + ", ".join(_literal(v, k) for v, (_n, k) in zip(r, cols, strict=True)) + ")"
                           for r in dataset["rows"])
        out.append(f"INSERT INTO {dataset['table']} VALUES {values}")
    return out


def _on_duckdb(case: dict[str, Any], dialect: str) -> tuple[str, Any]:
    """("ok", table) or (outcome, reason), through the DuckDB engine session cells use."""
    from .engines.contract import CellRequest, SessionConfig
    from .engines.local_sql import DuckDBAdapter
    session = DuckDBAdapter().open_session(SessionConfig(notebook=f"parity-{case['id']}"))
    try:
        made = session.execute(CellRequest(";\n".join(dataset_sql(case["dataset"])), "sql", "duckdb"))
        if made.status != "ok":
            reason = _error(made.outputs)[1].rstrip()
            return "error", f"The dataset wasn't made. {reason}" + ("" if reason.endswith(".") else ".")
        res = session.execute(CellRequest(case["dialects"][dialect]["statement"], "sql", dialect, result_rows=10_000))
        if res.status == "ok" and res.table is not None:
            return "ok", res.table
        if res.status == "ok":
            return "error", "The statement returned no result."
        name, text = _error(res.outputs)
        return ("refused" if name in _REFUSALS else "error"), f"{name}: {text}"
    finally:
        session.close()


def _error(outputs: list[dict[str, Any]]) -> tuple[str, str]:
    errors = [o for o in outputs if o.get("type") == "error"] or [{}]
    return str(errors[-1].get("ename", "Error")), str(errors[-1].get("evalue", ""))


def _on_tsql(case: dict[str, Any]) -> tuple[str, Any]:
    """T-SQL through localdata/tsql.translate, the translation the local data engine runs, on DuckDB with
    the session's refusals (no external access), dbo first as in a Warehouse."""
    import duckdb

    from .duckdb_rows import keep_try_cast
    from .localdata import tsql
    con = keep_try_cast(duckdb.connect(config={"enable_external_access": False, "autoinstall_known_extensions": False,
                                               "autoload_known_extensions": False}))
    try:
        con.execute("CREATE SCHEMA dbo")
        con.execute("SET search_path = 'dbo,main'")
        for text in dataset_sql(case["dataset"]):
            con.execute(text)
        columns: dict[str, dict[str, dict[str, str]]] = {}
        for schema, table, column, kind in con.execute("SELECT schema_name, table_name, column_name, data_type "
                                                       "FROM duckdb_columns() WHERE NOT internal").fetchall():
            columns.setdefault(schema, {}).setdefault(table, {})[column] = kind
        try:
            texts, _notes = tsql.translate(case["dialects"]["tsql"]["statement"], columns)
        except ValueError as exc:
            return "refused", f"{type(exc).__name__}: {exc}"
        try:
            for text in texts[:-1]:
                con.execute(text)
            return "ok", con.sql(texts[-1]).to_arrow_table() if texts else None
        except duckdb.Error as exc:
            return "error", f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
    finally:
        con.close()


def _logical(t: pa.DataType) -> str:
    for name, (arrow, _duck) in _TYPES.items():
        if t == arrow and name != "hugeint":
            return name
    if pa.types.is_large_string(t):
        return "string"
    if pa.types.is_decimal(t):
        return f"decimal({t.precision},{t.scale})"
    if pa.types.is_timestamp(t):
        return "timestamp_tz" if t.tz else "timestamp"
    return str(t)


def _same_type(want: str, got: str, norms: list[str]) -> bool:
    def whole(k: str) -> bool:
        return k in _WHOLE or bool(_DECIMAL.fullmatch(k) and k.endswith(",0)"))
    if want == got or ("integer_width" in norms and whole(want) and whole(got)):
        return True
    return "decimal_scale_display" in norms and bool(_DECIMAL.fullmatch(want) and _DECIMAL.fullmatch(got))


def _json(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (Decimal, dt.date)):
        return str(v)
    return v


def _renamed(v: Any) -> Any:
    """verify calls the sides pc and fabric; here they are the actual answer and the expected one."""
    words = {"pc": "actual", "fabric": "expected", "only_pc": "only_actual", "only_fabric": "only_expected"}
    if isinstance(v, dict):
        return {words.get(k, k): _renamed(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_renamed(x) for x in v]
    return words.get(v, v) if isinstance(v, str) else v


def compare(actual: pa.Table, expect: dict[str, Any]) -> list[dict[str, Any]]:
    """Each difference between ``actual`` and the expectation: schema first, then values (verify.compare,
    exact numbers, timestamps to the microsecond). Empty when they agree."""
    from . import verify
    norms = expect["normalize"]
    want = expect["schema"]
    got = [(f.name, _logical(f.type)) for f in actual.schema]
    diffs: list[dict[str, Any]] = []
    fold = (lambda s: s.lower()) if "column_name_case" in norms else (lambda s: s)
    if [fold(n) for n, _k in got] != [fold(n) for n, _k in want]:
        return [{"what": "columns", "expected": [n for n, _k in want], "actual": [n for n, _k in got]}]
    diffs += [{"what": "type", "column": wn, "expected": wk, "actual": gk}
              for (wn, wk), (_gn, gk) in zip(want, got, strict=True) if not _same_type(wk, gk, norms)]
    names = [f"c{i}" for i in range(len(want))]
    expected = pa.table([pa.array([r[i] for r in expect["rows"]], _type(k, n)[0]) for i, (n, k) in enumerate(want)],
                        names=names)
    actual = actual.rename_columns(names)
    keys: list[str] = []
    if "row_order" not in norms:
        expected = expected.append_column("row_", pa.array(range(expected.num_rows), pa.int64()))
        actual = actual.append_column("row_", pa.array(range(actual.num_rows), pa.int64()))
        keys = ["row_"]
    result = verify.compare(actual, expected, {"keys": keys, "abs": 0, "rel": 0})
    if result["result"] != "match":
        back = dict(zip(names, (n for n, _k in want), strict=True))
        examples = json.loads(json.dumps(result.get("examples", []), default=str))
        for ex in examples:
            for part in ("columns", "row"):
                if part in ex:
                    ex[part] = {back.get(k, k): v for k, v in ex[part].items()}
        diffs.append({"what": "values", "counts": _renamed(result.get("counts", {})), "rows": _renamed(examples)})
    return diffs


def versions() -> dict[str, str]:
    import duckdb
    import sqlglot
    return {"duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__, "python": sys.version.split()[0],
            "connector": "n/a"}


def run_one(case: dict[str, Any], dialect: str) -> dict[str, Any]:
    """The run record of ``case`` in ``dialect`` on this PC."""
    from .engines.local_sql import DUCKDB
    expect = case["dialects"][dialect]
    status, got = _on_tsql(case) if dialect == "tsql" else _on_duckdb(case, dialect)
    record: dict[str, Any] = {
        "schema": SCHEMA, "platform": "local", "location": "this_pc", "evidence_level": "local",
        "engine": {"id": DUCKDB.id, "dialect": dialect,
                   "translation": "localdata/tsql.py" if dialect == "tsql" else
                   "none" if dialect == "duckdb" else "engines/local_sql.py"},
        "versions": versions(), "case": case["id"], "title": case["title"],
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "expected": {"schema": [list(c) for c in expect["schema"]],
                     "rows": [[_json(v) for v in r] for r in expect["rows"]], "normalize": expect["normalize"],
                     "unconfirmed": expect["unconfirmed"], "source": expect["source"]},
        "actual": None, "written_to": None}
    if status != "ok" or got is None:
        return record | {"outcome": status if status != "ok" else "error", "detail": got or "No result."}
    record["actual"] = {"schema": [[f.name, _logical(f.type)] for f in got.schema],
                        "rows": [[_json(v) for v in r.values()] for r in got.to_pylist()]}
    diffs = compare(got, expect)
    outcome = "differs" if diffs else "unconfirmed" if expect["unconfirmed"] else "match"
    return record | {"outcome": outcome, "differences": diffs}


def run(cases: list[dict[str, Any]] | None = None, case_id: str | None = None,
        dialect: str | None = None) -> list[dict[str, Any]]:
    """A record for each (case, dialect), all on this PC."""
    cases = load() if cases is None else cases
    if case_id is not None and case_id not in {c["id"] for c in cases}:
        raise CaseError(f"No case {case_id!r}. Cases: {', '.join(c['id'] for c in cases)}.")
    if dialect is not None and dialect not in DIALECTS:
        raise CaseError(f"Unknown dialect {dialect!r}. Dialects: {', '.join(DIALECTS)}.")
    return [run_one(c, d) for c in cases if case_id in (None, c["id"])
            for d in c["dialects"] if dialect in (None, d)]


def write(records: list[dict[str, Any]], out_dir: Path) -> Path:
    """The records as one JSON file in ``out_dir``, each naming where it was written. Records hold no
    credentials; keep them out of shared analytical storage all the same."""
    from . import jsonfile
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"parity-run-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    for r in records:
        r["written_to"] = str(path)
    jsonfile.write_json(path, {"schema": SCHEMA, "records": records})
    return path


def can_verify(record: dict[str, Any]) -> bool:
    """Whether ``record`` may mark a parity cell Verified: only a matching run made live on a platform.
    A run on this PC or against a fake never can, whatever its outcome."""
    return (record.get("evidence_level") == "live" and record.get("platform") not in (None, "", "local")
            and record.get("location") != "this_pc" and record.get("outcome") == "match")
